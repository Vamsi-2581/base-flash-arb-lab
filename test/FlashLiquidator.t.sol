// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashLiquidator.sol";

interface VmLq { function prank(address) external; function txGasPrice(uint256) external; }

contract LqToken is ILiquidationERC20 {
    mapping(address=>uint256) public override balanceOf;
    mapping(address=>mapping(address=>uint256)) public allowance;
    function mint(address to,uint256 amount) external { balanceOf[to]+=amount; }
    function approve(address spender,uint256 amount) external returns(bool){
        allowance[msg.sender][spender]=amount;return true;
    }
    function transfer(address to,uint256 amount) external returns(bool){
        _move(msg.sender,to,amount);return true;
    }
    function transferFrom(address from,address to,uint256 amount) external returns(bool){
        require(allowance[from][msg.sender]>=amount,"no approval");
        allowance[from][msg.sender]-=amount;_move(from,to,amount);return true;
    }
    function _move(address from,address to,uint256 amount) internal {
        require(balanceOf[from]>=amount,"insufficient token");
        balanceOf[from]-=amount;balanceOf[to]+=amount;
    }
}
contract LqPool {
    LqToken public immutable usdc;
    LqToken public immutable weth;
    bool public liquidatable=true;
    constructor(LqToken u,LqToken w){usdc=u;weth=w;}
    function setLiquidatable(bool x) external {liquidatable=x;}
    function flashLoanSimple(address receiver,address asset,uint256 amount,bytes calldata params,uint16) external {
        require(asset==address(usdc));
        uint256 start=usdc.balanceOf(address(this));
        usdc.transfer(receiver,amount);
        uint256 premium=(amount*5+9999)/10000;
        require(FlashLiquidator(receiver).executeOperation(asset,amount,premium,msg.sender,params));
        usdc.transferFrom(receiver,address(this),amount+premium);
        require(usdc.balanceOf(address(this))>=start+premium,"missing repayment");
    }
    function liquidationCall(address collateral,address debt,address user,uint256 amount,bool receiveAToken) external {
        require(liquidatable && !receiveAToken && user!=address(0),"not liquidatable");
        require(collateral==address(weth) && debt==address(usdc),"wrong reserve");
        usdc.transferFrom(msg.sender,address(this),amount);
        weth.mint(msg.sender,amount*525_000_000);
    }
}
contract LqUniRouter {
    LqToken public immutable weth;LqToken public immutable usdc;
    constructor(LqToken w,LqToken u){weth=w;usdc=u;}
    function exactInputSingle(ILiquidationUniRouter.ExactInputSingleParams calldata p) external returns(uint256 out){
        require(p.tokenIn==address(weth)&&p.tokenOut==address(usdc),"pair");
        weth.transferFrom(msg.sender,address(this),p.amountIn);
        out=p.amountIn*2100*1e6/1e18;
        require(out>=p.amountOutMinimum,"slip");
        usdc.mint(p.recipient,out);
    }
}
contract LqPancakeRouter {
    LqToken public immutable weth;LqToken public immutable usdc;
    constructor(LqToken w,LqToken u){weth=w;usdc=u;}
    function exactInputSingle(ILiquidationPancakeRouter.ExactInputSingleParams calldata p) external returns(uint256 out){
        require(p.tokenIn==address(weth)&&p.tokenOut==address(usdc),"pair");
        require(block.timestamp<=p.deadline,"expired");
        weth.transferFrom(msg.sender,address(this),p.amountIn);
        out=p.amountIn*2100*1e6/1e18;
        require(out>=p.amountOutMinimum,"slip");
        usdc.mint(p.recipient,out);
    }
}
contract FlashLiquidatorTest {
    VmLq constant vm=VmLq(address(uint160(uint256(keccak256("hevm cheat code")))));
    LqToken usdc; LqToken weth; LqPool pool;
    LqUniRouter uni;LqPancakeRouter cake;
    FlashLiquidator liq;
    uint256 constant AMOUNT=1000e6;
    address constant BORROWER=address(0xBEEF);
    function setUp() public {
        usdc=new LqToken(); weth=new LqToken();
        pool=new LqPool(usdc,weth);
        uni=new LqUniRouter(weth,usdc);cake=new LqPancakeRouter(weth,usdc);
        liq=new FlashLiquidator(address(pool),address(usdc),address(weth),
            address(uni),address(cake),address(this));
        usdc.mint(address(pool),1_000_000e6);
        liq.setPaused(false);
    }
    function _execute(uint8 dex,uint256 minOut,uint256 minProfit) internal {
        liq.liquidate(BORROWER,AMOUNT,dex,500,minOut,minProfit,block.timestamp+30);
    }
    function testLiquidationFlashRepaysAndRealizesProfit() public {
        uint256 old=usdc.balanceOf(address(pool));
        _execute(0,AMOUNT,80e6);
        // Mock pool also receives the 1,000 USDC used to extinguish borrower debt.\n        require(usdc.balanceOf(address(pool))==old+AMOUNT+500000,"premium not repaid");
        require(usdc.balanceOf(address(this))==102e6,"profit accounting");
        require(usdc.balanceOf(address(liq))==0,"loan dust");
    }
    function testPancakeRouteWorks() public {
        _execute(1,AMOUNT,80e6);
        require(usdc.balanceOf(address(this))==102e6,"pancake profit");
    }
    function tryLiquidate(uint256 minOut,uint256 profit) external {
        _execute(0,minOut,profit);
    }
    function testMinProfitAndSlippageRevertAtomically() public {
        uint256 old=usdc.balanceOf(address(pool));
        (bool ok1,)=address(this).call(abi.encodeWithSelector(this.tryLiquidate.selector,AMOUNT,105e6));
        (bool ok2,)=address(this).call(abi.encodeWithSelector(this.tryLiquidate.selector,1200e6,1));
        require(!ok1&&!ok2,"must reject both");
        require(usdc.balanceOf(address(pool))==old,"atomic rollback");
    }
    function testNonLiquidatableBorrowerReverts() public {
        pool.setLiquidatable(false);
        uint256 old=usdc.balanceOf(address(pool));
        (bool ok,)=address(this).call(abi.encodeWithSelector(this.tryLiquidate.selector,AMOUNT,5e6));
        require(!ok&&usdc.balanceOf(address(pool))==old,"must fail");
    }
    function testDustPreservedAndNotProfit() public {
        usdc.mint(address(liq),25e6);
        weth.mint(address(liq),1e18);
        _execute(0,AMOUNT,5e6);
        require(usdc.balanceOf(address(liq))==25e6,"old USDC consumed");
        require(weth.balanceOf(address(liq))==1e18,"old collateral sold");
        require(usdc.balanceOf(address(this))==102e6,"dust counted");
    }
    function testBlockedCallerAndCallback() public {
        vm.prank(address(0xCAFE));
        (bool ok,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,AMOUNT,0,uint24(500),AMOUNT,1,block.timestamp+60));
        require(!ok,"unprivileged execution");
        (bool callback,)=address(liq).call(abi.encodeWithSelector(liq.executeOperation.selector,
            address(usdc),AMOUNT,0,address(liq),bytes("")));
        require(!callback,"callback should fail");
    }
    function testProductionLimitsRejectLargeLoanAndTinyProfit() public {
        (bool overLoan,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,5_001e6,uint8(0),uint24(500),AMOUNT,80e6,block.timestamp+30));
        (bool smallProfit,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,AMOUNT,uint8(0),uint24(500),AMOUNT,4e6,block.timestamp+30));
        (bool longDeadline,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,AMOUNT,uint8(0),uint24(500),AMOUNT,5e6,block.timestamp+46));
        (bool badFee,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,AMOUNT,uint8(0),uint24(2500),AMOUNT,5e6,block.timestamp+30));
        require(!overLoan&&!smallProfit&&!longDeadline&&!badFee,"limits bypassed");
    }
    function testHighGasPriceRejects() public {
        vm.txGasPrice(6 gwei);
        (bool ok,)=address(liq).call(abi.encodeWithSelector(liq.liquidate.selector,
            BORROWER,AMOUNT,uint8(0),uint24(500),AMOUNT,5e6,block.timestamp+30));
        require(!ok,"gas cap bypass");
    }
    function testPauseDefaultAfterSeparateDeployment() public {
        FlashLiquidator fresh=new FlashLiquidator(address(pool),address(usdc),address(weth),
            address(uni),address(cake),address(this));
        require(fresh.paused(),"fail closed");
    }
}
