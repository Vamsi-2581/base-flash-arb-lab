// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashArb.sol";

interface VmDeploy {
    function envAddress(string calldata) external returns (address);
    function startBroadcast() external;
    function stopBroadcast() external;
}
interface IAaveProvider { function getPool() external view returns (address); }

/// @notice Base only. Deployment remains paused. Never broadcasts unless --broadcast.
contract DeployBase {
    VmDeploy private constant vm = VmDeploy(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant UNI_ROUTER = 0x2626664c2603336E57B271c5C0b26F421741e481;
    address constant PANCAKE_ROUTER = 0x1b81D678ffb9C0263b24A97847620C99d213eB14;
    event ExecutorDeployed(address indexed executor);

    function run() external returns (FlashArb executor) {
        require(block.chainid == 8453, "Only Base Mainnet");
        address owner = vm.envAddress("OWNER_ADDRESS");
        require(owner != address(0), "No owner");
        address pool = IAaveProvider(PROVIDER).getPool();
        require(pool != address(0), "No Aave Pool");
        vm.startBroadcast();
        executor = new FlashArb(pool, USDC, WETH, UNI_ROUTER, PANCAKE_ROUTER, owner);
        vm.stopBroadcast();
        emit ExecutorDeployed(address(executor));
    }
}
