// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashLiquidator.sol";

interface IShadowVm {
    function envOr(string calldata key, bool fallbackValue) external returns (bool);
    function envString(string calldata key) external returns (string memory);
    function readFile(string calldata path) external returns (string memory);
    function parseJsonUint(string calldata json, string calldata key) external pure returns (uint256);
    function parseJsonAddress(string calldata json, string calldata key) external pure returns (address);
    function createSelectFork(string calldata url, uint256 blockNumber) external returns (uint256);
    function writeFile(string calldata path, string calldata content) external;
    function toString(uint256 value) external pure returns (string memory);
}
interface IShadowAaveProvider { function getPool() external view returns (address); }

/// @notice Current mainnet-state local Fork attempt. This is NEVER a broadcast script.
/// @dev Only called by an opt-in CI step once account-wide HF and exact USDC debt /
///      WETH aToken collateral have been verified in one pinned block. No guarantee
///      of eventual inclusion, true Gas, or Aave borrower state by next block.
contract LiveShadowForkTest {
    IShadowVm constant vm=IShadowVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant AAVE_PROVIDER=0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH=0x4200000000000000000000000000000000000006;
    address constant UNI=0x2626664c2603336E57B271c5C0b26F421741e481;
    address constant PANCAKE=0x1b81D678ffb9C0263b24A97847620C99d213eB14;

    function testCurrentReadOnlyForkLiquidation() public {
        if (!vm.envOr("RUN_SHADOW_LIVE_FORK",false)) return;
        string memory body=vm.readFile(vm.envString("SHADOW_CANDIDATE_PATH"));
        uint256 blockNumber=vm.parseJsonUint(body,".snapshot_block");
        uint256 amount=vm.parseJsonUint(body,".borrowed_usdc_units");
        address borrower=vm.parseJsonAddress(body,".borrower");
        require(amount>=100e6 && amount<=5_000e6,"invalid loan");
        vm.createSelectFork(vm.envString("BASE_RPC_URL"),blockNumber);
        require(block.chainid==8453,"not Base");
        address pool=IShadowAaveProvider(AAVE_PROVIDER).getPool();
        FlashLiquidator executor=new FlashLiquidator(
            pool,USDC,WETH,UNI,PANCAKE,address(this));
        executor.setPaused(false);
        uint256 initial=ILiquidationERC20(USDC).balanceOf(address(this));
        uint256 beforeGas=gasleft();
        // minOut=1 is acceptable ONLY in a local EVM experiment. Never use
        // this limit for a real pending transaction without exact quote+slippage.
        executor.liquidate(borrower,amount,0,100,1,5e6,block.timestamp+30);
        uint256 gasUsed=beforeGas-gasleft();
        uint256 profit=ILiquidationERC20(USDC).balanceOf(address(this))-initial;
        require(profit>=5e6,"min historical token-profit not returned");
        vm.writeFile("validation/shadow-live/atomic-simulation.json",
            string.concat(
              '{"status":"FULL_ATOMIC_CURRENT_BLOCK_FORK_TOKEN_PROFIT_ONLY",',
              '"block":',vm.toString(blockNumber),
              ',"borrowed_usdc_units":',vm.toString(amount),
              ',"profit_usdc_units_after_flash_repayment":',vm.toString(profit),
              ',"gas_used_estimate":',vm.toString(gasUsed),
              ',"base_l1_fee_included":false,',
              '"broadcast":false,"not_authorized_for_live":true}'
            )
        );
    }
}
