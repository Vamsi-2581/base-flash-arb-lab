// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface VmConnectivity {
    function envString(string calldata) external returns (string memory);
    function envUint(string calldata) external returns (uint256);
    function createSelectFork(string calldata, uint256) external returns (uint256);
}
interface AaveProviderConnectivity { function getPool() external view returns (address); }
interface AavePoolConnectivity { function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128); }
interface V3FactoryConnectivity {
    function getPool(address, address, uint24) external view returns (address);
}

/// @notice Historical local-fork state validation only. Never broadcasts.
contract ForkConnectivity {
    VmConnectivity constant vm = VmConnectivity(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant UNI_FACTORY = 0x33128a8fC17869897dcE68Ed026d694621f6FDfD;
    address constant CAKE_FACTORY = 0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865;
    event ForkConnectivityVerified(uint256 blockNumber, address pool, uint256 premiumBps, uint256 pools);
    function run() external {
        vm.createSelectFork(vm.envString("BASE_RPC_URL"), vm.envUint("PROBE_BLOCK"));
        require(block.chainid == 8453, "Wrong chain");
        require(USDC.code.length != 0 && WETH.code.length != 0, "Missing token");
        address pool = AaveProviderConnectivity(PROVIDER).getPool();
        require(pool.code.length != 0, "Missing Aave");
        uint256 premium = AavePoolConnectivity(pool).FLASHLOAN_PREMIUM_TOTAL();
        require(premium <= 1000, "Unreasonable flash premium");
        uint256 pools;
        if (V3FactoryConnectivity(UNI_FACTORY).getPool(USDC,WETH,500).code.length != 0) pools++;
        if (V3FactoryConnectivity(CAKE_FACTORY).getPool(USDC,WETH,500).code.length != 0) pools++;
        require(pools != 0, "No V3 pool");
        emit ForkConnectivityVerified(block.number, pool, premium, pools);
    }
}
