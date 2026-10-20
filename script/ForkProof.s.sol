// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashArb.sol";

interface VmFork {
    function envString(string calldata) external returns (string memory);
    function readFile(string calldata) external returns (string memory);
    function parseJsonUint(string calldata, string calldata) external pure returns (uint256);
    function createSelectFork(string calldata, uint256) external returns (uint256);
    function toString(uint256) external pure returns (string memory);
    function writeFile(string calldata, string calldata) external;
}
interface IPoolAddressesProvider { function getPool() external view returns (address); }

/// @notice Reproduces one candidate on a pinned Base block; no broadcast calls.
/// @dev forge script script/ForkProof.s.sol:ForkProof -vvv
contract ForkProof {
    VmFork constant vm = VmFork(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant UNI_ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;
    address constant PANCAKE_ROUTER = 0x1b81D678ffb9C0263b24A97847620C99d213eB14;

    event ForkVerified(uint256 blockNumber, uint256 borrowed, uint256 realizedProfit);

    function run() external {
        string memory body = vm.readFile(vm.envString("CANDIDATE_PATH"));
        uint256 blockNumber = vm.parseJsonUint(body, ".block");
        vm.createSelectFork(vm.envString("BASE_RPC_URL"), blockNumber);
        require(block.chainid == 8453, "Wrong chain");
        address pool = IPoolAddressesProvider(PROVIDER).getPool();
        FlashArb executor = new FlashArb(pool, USDC, WETH, UNI_ROUTER, PANCAKE_ROUTER, address(this));
        executor.setPaused(false);
        uint256 oldBalance = IERC20(USDC).balanceOf(address(this));
        // Pinned block may have timestamp much earlier than scan time; use fork timestamp.
        uint256 remainingGasBefore = gasleft();
        executor.startArbitrage(
            vm.parseJsonUint(body, ".amount_usdc"),
            uint8(vm.parseJsonUint(body, ".first_dex")),
            uint24(vm.parseJsonUint(body, ".first_fee")),
            uint8(vm.parseJsonUint(body, ".second_dex")),
            uint24(vm.parseJsonUint(body, ".second_fee")),
            vm.parseJsonUint(body, ".min_weth"),
            vm.parseJsonUint(body, ".min_usdc"),
            vm.parseJsonUint(body, ".min_profit_usdc"),
            block.timestamp + 60
        );
        uint256 operationGas = remainingGasBefore - gasleft();
        uint256 profit = IERC20(USDC).balanceOf(address(this)) - oldBalance;
        require(profit >= vm.parseJsonUint(body, ".min_profit_usdc"), "Insufficient realized profit");
        // Structured proof is written ONLY after a real forked Aave/DEX transaction
        // returns and passes the on-chain profit assertion.
        vm.writeFile("validation/fork-proof.json",
            string.concat(
                '{"status":"FORK_EXECUTION_SUCCESS","block":', vm.toString(blockNumber),
                ',"profit_usdc_units":', vm.toString(profit),
                ',"operation_gas_used":', vm.toString(operationGas),
                ',"loan_usdc_units":', vm.toString(vm.parseJsonUint(body, ".amount_usdc")),
                '}'
            )
        );
        emit ForkVerified(blockNumber, vm.parseJsonUint(body, ".amount_usdc"), profit);
    }
}
