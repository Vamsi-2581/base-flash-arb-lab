// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashArb.sol";

interface Vm { function prank(address) external; function warp(uint256) external; }

contract MockToken is IERC20 {
    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amount) external { balanceOf[to] += amount; }
    function approve(address spender, uint256 amount) external override returns (bool) {
        allowance[msg.sender][spender] = amount; return true;
    }
    function transfer(address to, uint256 amount) external override returns (bool) {
        _move(msg.sender, to, amount); return true;
    }
    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "allowance");
        allowance[from][msg.sender] -= amount;
        _move(from, to, amount); return true;
    }
    function _move(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "balance");
        balanceOf[from] -= amount; balanceOf[to] += amount;
    }
}

contract MockPool {
    MockToken public immutable asset;
    uint256 public premiumBps = 5;
    constructor(MockToken a) { asset = a; }
    function flashLoanSimple(address receiver, address requestedAsset, uint256 amount,
       bytes calldata params, uint16) external {
        require(requestedAsset == address(asset), "asset");
        uint256 balBefore = asset.balanceOf(address(this));
        require(asset.transfer(receiver, amount), "transfer");
        uint256 premium = (amount * premiumBps + 9999) / 10000;
        bool ok = FlashArb(receiver).executeOperation(requestedAsset, amount, premium, msg.sender, params);
        require(ok, "callback");
        require(asset.transferFrom(receiver, address(this), amount + premium), "repay");
        require(asset.balanceOf(address(this)) >= balBefore + premium, "repayment");
    }
}

/// @dev Deliberately simple deterministic mocks, not a real concentrated-liquidity model.
contract MockUni {
    MockToken public immutable usdc;
    MockToken public immutable weth;
    constructor(MockToken u, MockToken w) { usdc = u; weth = w; }
    function exactInputSingle(IUniRouter02.ExactInputSingleParams calldata p)
      external returns (uint256 out) {
        out = _swap(p.tokenIn, p.tokenOut, p.amountIn, p.recipient, p.amountOutMinimum);
    }
    function _swap(address tokenIn, address tokenOut, uint256 amount, address to, uint256 minOut)
      internal returns (uint256 out) {
        require(tokenIn == address(usdc) && tokenOut == address(weth), "pair");
        require(usdc.transferFrom(msg.sender, address(this), amount), "in");
        // 1 USDC raw unit -> 5e8 WETH wei (~2,000 USDC / WETH)
        out = amount * 500_000_000;
        require(out >= minOut, "minOut");
        weth.mint(to, out);
    }
}
contract MockPancake {
    MockToken public immutable usdc;
    MockToken public immutable weth;
    constructor(MockToken u, MockToken w) { usdc = u; weth = w; }
    function exactInputSingle(IPancakeV3Router.ExactInputSingleParams calldata p)
      external returns (uint256 out) {
        require(block.timestamp <= p.deadline, "deadline");
        require(p.tokenIn == address(weth) && p.tokenOut == address(usdc), "pair");
        require(weth.transferFrom(msg.sender, address(this), p.amountIn), "in");
        out = p.amountIn * 2100 * 1e6 / 1e18;
        require(out >= p.amountOutMinimum, "minOut");
        usdc.mint(p.recipient, out);
    }
}

contract FlashArbTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    MockToken usdc;
    MockToken weth;
    MockPool pool;
    MockUni uni;
    MockPancake pancake;
    FlashArb arb;
    uint256 private constant LOAN = 1000 * 1e6;

    function setUp() public {
        usdc = new MockToken(); weth = new MockToken();
        pool = new MockPool(usdc);
        uni = new MockUni(usdc, weth); pancake = new MockPancake(usdc, weth);
        arb = new FlashArb(address(pool), address(usdc), address(weth),
                           address(uni), address(pancake), address(this));
        usdc.mint(address(pool), 1_000_000 * 1e6);
        arb.setPaused(false);
    }
    function run(uint256 minProfit) internal {
        arb.startArbitrage(LOAN, 0, 500, 1, 500,
          1e17, LOAN, minProfit, block.timestamp + 60);
    }
    function testSuccessfulTradeAndRepayment() public {
        uint256 poolBefore = usdc.balanceOf(address(pool));
        run(10e6);
        uint256 prem = (LOAN * 5 + 9999) / 10000;
        require(usdc.balanceOf(address(pool)) == poolBefore + prem, "pool premium");
        require(usdc.balanceOf(address(this)) == 50e6 - prem, "owner profit");
        require(usdc.balanceOf(address(arb)) == 0, "loan fully repaid");
    }
    function testMinimumProfitRevertsAtomically() public {
        uint256 poolBefore = usdc.balanceOf(address(pool));
        (bool ok,) = address(this).call(abi.encodeWithSelector(this.tryRun.selector, 51e6));
        require(!ok, "should revert");
        require(usdc.balanceOf(address(pool)) == poolBefore, "atomic rollback");
        require(usdc.balanceOf(address(this)) == 0, "no profit");
    }
    function tryRun(uint256 profit) external { run(profit); }
    function testPausedReverts() public {
        arb.setPaused(true);
        (bool ok,) = address(this).call(abi.encodeWithSelector(this.tryRun.selector, 1e6));
        require(!ok, "paused");
    }
    function testBadDexAndDuplicatePoolRevert() public {
        (bool a,) = address(arb).call(abi.encodeWithSelector(arb.startArbitrage.selector,
          LOAN, 2, uint24(500), 1, uint24(500), 1, LOAN, 1, block.timestamp + 60));
        (bool b,) = address(arb).call(abi.encodeWithSelector(arb.startArbitrage.selector,
          LOAN, 0, uint24(500), 0, uint24(500), 1, LOAN, 1, block.timestamp + 60));
        require(!a && !b, "invalid route");
    }
    function testNonOperatorBlocked() public {
        vm.prank(address(0xBEEF));
        (bool ok,) = address(arb).call(abi.encodeWithSelector(arb.startArbitrage.selector,
          LOAN, 0, uint24(500), 1, uint24(500), 1, LOAN, 1, block.timestamp + 60));
        require(!ok, "access control");
    }
    function testCallbackUnauthorized() public {
        (bool ok,) = address(arb).call(abi.encodeWithSelector(arb.executeOperation.selector,
          address(usdc), LOAN, 0, address(arb), bytes("")));
        require(!ok, "callback auth");
    }
    function testExpiredReverts() public {
        vm.warp(200);
        (bool ok,) = address(arb).call(abi.encodeWithSelector(arb.startArbitrage.selector,
          LOAN, 0, uint24(500), 1, uint24(500), 1, LOAN, 1, 199));
        require(!ok, "deadline");
    }
    function testDustNotCountedAsProfit() public {
        usdc.mint(address(arb), 100e6);
        run(10e6);
        // Older deposited USDC remains in the executor; cannot be claimed as new profit.
        require(usdc.balanceOf(address(arb)) == 100e6, "dust preserved");
        require(usdc.balanceOf(address(this)) == 49_500_000, "new profit only");
    }
    function testSlippageMinOutReverts() public {
        (bool ok,) = address(arb).call(abi.encodeWithSelector(arb.startArbitrage.selector,
          LOAN, 0, uint24(500), 1, uint24(500), 1e18, LOAN, 1e6, block.timestamp + 60));
        require(!ok, "min out");
    }
}
