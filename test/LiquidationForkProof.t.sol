// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../script/LiquidationForkProof.s.sol";

interface ILiquidationForkEnv {
    function envOr(string calldata,bool) external returns(bool);
}

/// @notice Stable-address Forge TEST wrapper, not ephemeral forge script.
/// @dev Default CI does not run RPC forks without RUN_HISTORICAL_FORK=true.
contract LiquidationForkProofTest is LiquidationForkProof {
    ILiquidationForkEnv constant envVm =
        ILiquidationForkEnv(address(uint160(uint256(keccak256("hevm cheat code")))));

    function testHistoricalRealProtocolLiquidation() public {
        if (!envVm.envOr("RUN_HISTORICAL_FORK",false)) return;
        // Address(this) belongs to a persistent Forge test contract.
        // The inherited run() deploys the actual flash-loan receiver,
        // calls real Base Aave & DEX router and writes profit proof on success.
        this.run();
    }
}
