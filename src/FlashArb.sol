// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Minimal external ABIs. Routers are fixed at deployment; no arbitrary calls.
interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address,uint256) external returns (bool);
    function approve(address,uint256) external returns (bool);
}
interface IAavePool {
    function flashLoanSimple(address receiverAddress,address asset,uint256 amount,bytes calldata params,uint16 referralCode) external;
}
interface IUniRouter02 {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}
interface IPancakeV3Router {
    struct ExactInputSingleParams {
        address tokenIn; address tokenOut; uint24 fee; address recipient;
        uint256 deadline; uint256 amountIn; uint256 amountOutMinimum; uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata) external payable returns (uint256);
}

/// @title FlashArb
/// @notice Closed-asset, closed-router Aave V3 flash-loan executor. NOT audited.
/// @dev Borrows USDC, buys WETH on router A, sells WETH on router B, repays USDC.
contract FlashArb {
    error NotOwner();
    error NotOperator();
    error Paused();
    error Busy();
    error BadInput();
    error CallbackUnauthorized();
    error InsufficientProfit();
    error TokenCallFailed();

    address public immutable POOL;
    address public immutable USDC;
    address public immutable WETH;
    address public immutable UNI_ROUTER;
    address public immutable PANCAKE_ROUTER;
    address public owner;
    address public operator;
    bool public paused = true; // Fail-closed until explicit activation.
    bool private active;
    uint256 private baselineUsdc;

    struct Plan {
        uint8 firstDex;
        uint24 firstFee;
        uint8 secondDex;
        uint24 secondFee;
        uint256 minWeth;
        uint256 minUsdc;
        uint256 minProfit;
        uint256 deadline;
    }

    event Executed(uint256 borrowed, uint256 premium, uint256 profit, uint8 buyDex, uint8 sellDex);
    event OperatorChanged(address indexed operator);
    event PauseChanged(bool paused);
    event OwnerChanged(address indexed owner);

    modifier onlyOwner() { if (msg.sender != owner) revert NotOwner(); _; }
    modifier onlyOperator() { if (msg.sender != owner && msg.sender != operator) revert NotOperator(); _; }

    constructor(address pool_, address usdc_, address weth_, address uni_, address pancake_, address owner_) {
        if (pool_ == address(0) || usdc_ == address(0) || weth_ == address(0)
            || uni_ == address(0) || pancake_ == address(0) || owner_ == address(0)
            || usdc_ == weth_ || uni_ == pancake_) revert BadInput();
        POOL = pool_; USDC = usdc_; WETH = weth_;
        UNI_ROUTER = uni_; PANCAKE_ROUTER = pancake_; owner = owner_;
    }

    function setOperator(address newOperator) external onlyOwner { operator = newOperator; emit OperatorChanged(newOperator); }
    function setPaused(bool value) external onlyOwner { if (active) revert Busy(); paused = value; emit PauseChanged(value); }
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert BadInput();
        owner = newOwner; emit OwnerChanged(newOwner);
    }
    function rescue(address token, uint256 amount) external onlyOwner {
        if (active || token == address(0)) revert BadInput();
        _safeTransfer(token, owner, amount);
    }

    function startArbitrage(uint256 amount, uint8 firstDex, uint24 firstFee, uint8 secondDex,
        uint24 secondFee, uint256 minWeth, uint256 minUsdc, uint256 minProfit,
        uint256 deadline) external onlyOperator {
        if (paused) revert Paused();
        if (active) revert Busy();
        if (amount == 0 || minWeth == 0 || minUsdc == 0 || minProfit == 0
            || firstDex > 1 || secondDex > 1 || firstFee == 0 || secondFee == 0
            || (firstDex == secondDex && firstFee == secondFee)
            || deadline < block.timestamp || deadline > block.timestamp + 180) revert BadInput();
        active = true;
        baselineUsdc = IERC20(USDC).balanceOf(address(this));
        bytes memory params = abi.encode(Plan(firstDex, firstFee, secondDex, secondFee,
                                             minWeth, minUsdc, minProfit, deadline));
        IAavePool(POOL).flashLoanSimple(address(this), USDC, amount, params, 0);
        active = false;
        baselineUsdc = 0;
    }

    function executeOperation(address asset, uint256 amount, uint256 premium,
        address initiator, bytes calldata params) external returns (bool) {
        if (msg.sender != POOL || initiator != address(this) || !active || asset != USDC)
            revert CallbackUnauthorized();
        Plan memory p = abi.decode(params, (Plan));
        if (block.timestamp > p.deadline) revert BadInput();
        if (IERC20(USDC).balanceOf(address(this)) < baselineUsdc + amount)
            revert BadInput();
        uint256 wethOut = _swap(p.firstDex, USDC, WETH, p.firstFee, amount, p.minWeth, p.deadline);
        _swap(p.secondDex, WETH, USDC, p.secondFee, wethOut, p.minUsdc, p.deadline);
        uint256 due = amount + premium;
        uint256 balance = IERC20(USDC).balanceOf(address(this));
        if (balance < baselineUsdc + due + p.minProfit) revert InsufficientProfit();
        uint256 profit = balance - baselineUsdc - due;
        _forceApprove(USDC, POOL, due);
        _safeTransfer(USDC, owner, profit);
        emit Executed(amount, premium, profit, p.firstDex, p.secondDex);
        return true;
    }

    function _swap(uint8 dex, address tokenIn, address tokenOut, uint24 fee,
        uint256 amount, uint256 minOut, uint256 deadline) internal returns (uint256) {
        address router = dex == 0 ? UNI_ROUTER : PANCAKE_ROUTER;
        _forceApprove(tokenIn, router, amount);
        if (dex == 0) {
            return IUniRouter02(router).exactInputSingle(IUniRouter02.ExactInputSingleParams({
                tokenIn: tokenIn, tokenOut: tokenOut, fee: fee, recipient: address(this),
                amountIn: amount, amountOutMinimum: minOut, sqrtPriceLimitX96: 0
            }));
        }
        return IPancakeV3Router(router).exactInputSingle(IPancakeV3Router.ExactInputSingleParams({
            tokenIn: tokenIn, tokenOut: tokenOut, fee: fee, recipient: address(this),
            deadline: deadline, amountIn: amount, amountOutMinimum: minOut, sqrtPriceLimitX96: 0
        }));
    }

    function _forceApprove(address token, address spender, uint256 amount) internal {
        _optionalReturn(token, abi.encodeWithSelector(IERC20.approve.selector, spender, 0));
        _optionalReturn(token, abi.encodeWithSelector(IERC20.approve.selector, spender, amount));
    }
    function _safeTransfer(address token, address to, uint256 amount) internal {
        _optionalReturn(token, abi.encodeWithSelector(IERC20.transfer.selector, to, amount));
    }
    function _optionalReturn(address token, bytes memory data) internal {
        (bool ok, bytes memory ret) = token.call(data);
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TokenCallFailed();
    }
}
