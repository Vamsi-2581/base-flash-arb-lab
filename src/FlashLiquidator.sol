// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Standalone, deliberately constrained Base Aave V3 USDC/WETH liquidation research executor.
/// @dev NOT AUDITED, paused by default. Tests/fork only. No generic arbitrary-call execution.
interface ILiquidationERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address,uint256) external returns (bool);
    function transfer(address,uint256) external returns (bool);
}
interface ILiquidationAavePool {
    function flashLoanSimple(address,address,uint256,bytes calldata,uint16) external;
    function liquidationCall(address,address,address,uint256,bool) external;
}
interface ILiquidationUniRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}
interface ILiquidationPancakeRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}

contract FlashLiquidator {
    error Unauthorized();
    error Paused();
    error Busy();
    error BadInput();
    error BadCallback();
    error InsufficientProfit();
    error TokenFailed();

    address public immutable POOL;
    address public immutable USDC;
    address public immutable WETH;
    address public immutable UNI_ROUTER;
    address public immutable PANCAKE_ROUTER;
    address public immutable owner;
    // Defensive limits for a small-budget research operator. These do not
    // replace exact eth_call/Fork profit checks or wallet-level gas limits.
    uint256 public constant MAX_BORROW_USDC = 5_000 * 1e6;
    uint256 public constant MIN_PROFIT_USDC = 5 * 1e6;
    uint256 public constant MAX_TX_GAS_PRICE = 5 gwei;
    uint256 public constant MAX_DEADLINE_WINDOW = 45;
    uint256 public constant MAX_FLASH_PREMIUM_BPS = 20;

    bool public paused = true;
    bool private active;
    uint256 private baselineDebt;
    uint256 private baselineCollateral;

    struct Params {
        address borrower;
        uint256 coverAmount;
        uint8 sellDex;
        uint24 poolFee;
        uint256 minUsdcOut;
        uint256 minProfit;
        uint256 deadline;
    }

    event LiquidationExecuted(
        address indexed borrower, uint256 borrowed, uint256 loanPremium,
        uint256 wethSeized, uint256 usdcProfit, uint8 dex, uint24 fee
    );

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(address pool_,address usdc_,address weth_,address uni_,
                address pancake_,address owner_) {
        if (pool_==address(0)||usdc_==address(0)||weth_==address(0)||
            uni_==address(0)||pancake_==address(0)||owner_==address(0)||
            usdc_==weth_||uni_==pancake_) revert BadInput();
        POOL=pool_;USDC=usdc_;WETH=weth_;
        UNI_ROUTER=uni_;PANCAKE_ROUTER=pancake_;owner=owner_;
    }
    function setPaused(bool value) external onlyOwner {
        if (active) revert Busy();
        paused=value;
    }
    function rescue(address token,uint256 amount) external onlyOwner {
        if (active||token==address(0))revert BadInput();
        _transfer(token,owner,amount);
    }

    /// @dev coverAmount intentionally equals flash amount; Aave may consume less on close-factor limits.
    function liquidate(address borrower,uint256 coverAmount,uint8 sellDex,uint24 fee,
                       uint256 minUsdcOut,uint256 minProfit,uint256 deadline) external onlyOwner {
        if (paused) revert Paused();
        if (active) revert Busy();
        if (borrower==address(0)||coverAmount==0||coverAmount>MAX_BORROW_USDC||sellDex>1||
            (fee!=100 && fee!=500 && fee!=2500 && fee!=3000 && fee!=10000)||
            (sellDex==0 && fee==2500)||
            minUsdcOut==0||minProfit<MIN_PROFIT_USDC||
            tx.gasprice>MAX_TX_GAS_PRICE||
            deadline<block.timestamp||deadline>block.timestamp+MAX_DEADLINE_WINDOW)revert BadInput();
        active=true;
        baselineDebt=ILiquidationERC20(USDC).balanceOf(address(this));
        baselineCollateral=ILiquidationERC20(WETH).balanceOf(address(this));
        ILiquidationAavePool(POOL).flashLoanSimple(address(this),USDC,coverAmount,
            abi.encode(Params(borrower,coverAmount,sellDex,fee,minUsdcOut,minProfit,deadline)),0);
        active=false;
        baselineDebt=0;
        baselineCollateral=0;
    }

    function executeOperation(address asset,uint256 amount,uint256 premium,
                              address initiator,bytes calldata payload) external returns (bool) {
        if (msg.sender!=POOL||initiator!=address(this)||!active||asset!=USDC)
            revert BadCallback();
        Params memory p=abi.decode(payload,(Params));
        if (block.timestamp>p.deadline||amount!=p.coverAmount)revert BadInput();
        if (premium>amount*MAX_FLASH_PREMIUM_BPS/10000) revert BadInput();
        if (ILiquidationERC20(USDC).balanceOf(address(this))<baselineDebt+amount)
            revert BadCallback();
        _approve(USDC,POOL,amount);
        ILiquidationAavePool(POOL).liquidationCall(WETH,USDC,p.borrower,amount,false);
        uint256 collateralAfter=ILiquidationERC20(WETH).balanceOf(address(this));
        if (collateralAfter<=baselineCollateral)revert InsufficientProfit();
        uint256 seized=collateralAfter-baselineCollateral;
        address router=p.sellDex==0?UNI_ROUTER:PANCAKE_ROUTER;
        _approve(WETH,router,seized);
        if (p.sellDex==0) {
            ILiquidationUniRouter(router).exactInputSingle(
                ILiquidationUniRouter.ExactInputSingleParams(
                    WETH,USDC,p.poolFee,address(this),seized,p.minUsdcOut,0));
        } else {
            ILiquidationPancakeRouter(router).exactInputSingle(
                ILiquidationPancakeRouter.ExactInputSingleParams(
                    WETH,USDC,p.poolFee,address(this),p.deadline,seized,p.minUsdcOut,0));
        }
        uint256 due=amount+premium;
        uint256 balance=ILiquidationERC20(USDC).balanceOf(address(this));
        if (balance<baselineDebt+due+p.minProfit)revert InsufficientProfit();
        uint256 profit=balance-baselineDebt-due;
        _approve(USDC,POOL,due);
        _transfer(USDC,owner,profit);
        emit LiquidationExecuted(p.borrower,amount,premium,seized,profit,p.sellDex,p.poolFee);
        return true;
    }
    function _approve(address token,address spender,uint256 amount) internal {
        _tokenCall(token,abi.encodeWithSelector(ILiquidationERC20.approve.selector,spender,0));
        _tokenCall(token,abi.encodeWithSelector(ILiquidationERC20.approve.selector,spender,amount));
    }
    function _transfer(address token,address to,uint256 amount) internal {
        _tokenCall(token,abi.encodeWithSelector(ILiquidationERC20.transfer.selector,to,amount));
    }
    function _tokenCall(address token,bytes memory payload) internal {
        (bool ok,bytes memory result)=token.call(payload);
        if (!ok||(result.length!=0 && (result.length!=32||!abi.decode(result,(bool)))))
            revert TokenFailed();
    }
}
