// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/FlashLiquidator.sol";

interface ILqVm {
    function envString(string calldata) external returns(string memory);
    function readFile(string calldata) external returns(string memory);
    function parseJsonUint(string calldata,string calldata) external pure returns(uint256);
    function parseJsonAddress(string calldata,string calldata) external pure returns(address);
    function createSelectFork(string calldata,uint256) external returns(uint256);
    function toString(uint256) external pure returns(string memory);
    function writeFile(string calldata,string calldata) external;
}
interface ILqAddressesProvider {function getPool() external view returns(address);}
interface IUniswapV3Quoter {
    struct QuoteExactInputSingleParams {
        address tokenIn;address tokenOut;uint256 amountIn;uint24 fee;uint160 sqrtPriceLimitX96;
    }
    function quoteExactInputSingle(QuoteExactInputSingleParams calldata)
        external returns(uint256 amountOut,uint160 sqrtPriceAfter,
                         uint32 initializedTicksCrossed,uint256 gasEstimate);
}

/// @notice Full real-protocol Aave→liquidationCall→Uniswap/Pancake→flash repayment FORK attempt.
/// @dev STRICT: never marks success unless on-chain simulator transaction returns, loan repaid,
///      actual USDC profit > gas sensitivity bound. Never broadcast this script.
contract LiquidationForkProof {
    ILqVm constant vm=ILqVm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address constant USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH=0x4200000000000000000000000000000000000006;
    address constant PROVIDER=0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address constant UNI_ROUTER=0x2626664c2603336E57B271c5C0b26F421741e481;
    address constant PANCAKE_ROUTER=0x1b81D678ffb9C0263b24A97847620C99d213eB14;
    address constant UNI_QUOTER=0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a;

    event Proof(uint256 indexed blockNumber,address indexed borrower,
                uint256 profit,uint256 gasUsed,uint256 modeledGasCost,uint256 afterGasProfit);

    function run() external {
        string memory payload=vm.readFile(vm.envString("LQ_CANDIDATE_PATH"));
        uint256 blockNumber=vm.parseJsonUint(payload,".block");
        require(blockNumber>1,"bad block");
        // B-1 is not necessarily exact historical state at the mined transaction index.
        vm.createSelectFork(vm.envString("BASE_RPC_URL"),blockNumber-1);
        require(block.chainid==8453,"not Base");
        address pool=ILqAddressesProvider(PROVIDER).getPool();
        FlashLiquidator liq=new FlashLiquidator(
            pool,USDC,WETH,UNI_ROUTER,PANCAKE_ROUTER,address(this));
        liq.setPaused(false);
        uint256 beforeBalance=ILiquidationERC20(USDC).balanceOf(address(this));
        address borrower=vm.parseJsonAddress(payload,".borrower");
        uint256 borrowed=vm.parseJsonUint(payload,".amount_usdc");
        uint8 sellDex=uint8(vm.parseJsonUint(payload,".sell_dex"));
        uint24 sellFee=uint24(vm.parseJsonUint(payload,".sell_fee"));
        uint256 gasBefore=gasleft();
        liq.liquidate(borrower,borrowed,sellDex,sellFee,
            vm.parseJsonUint(payload,".min_usdc"),5e6,block.timestamp+30);
        uint256 gasUsed=gasBefore-gasleft();
        uint256 profit=ILiquidationERC20(USDC).balanceOf(address(this))-beforeBalance;
        require(profit>0,"no real fork protocol token profit");
        // Price gas in USDC at actual forked pool state (0.1 WETH direct V3 quote).
        // This is still a conservative *model* and not an actual Base inclusion receipt.
        (uint256 tenPercentEthQuoted,,,) = IUniswapV3Quoter(UNI_QUOTER)
            .quoteExactInputSingle(IUniswapV3Quoter.QuoteExactInputSingleParams(
                WETH,USDC,1e17,500,0));
        require(tenPercentEthQuoted>0,"historical WETH/USDC quote missing");
        // Deliberately pessimistic: basefee*3 + 2 gwei tip with 35% headroom,
        // plus a flat 10 USDC buffer for L1 data/inclusion variation.
        uint256 gasPrice=block.basefee*3+2 gwei;
        uint256 pricedGas=((gasUsed+120000)*gasPrice*tenPercentEthQuoted*135+
                            (1e17*100-1))/(1e17*100);
        uint256 totalGasUsd=pricedGas+10e6;
        require(profit>totalGasUsd,"no positive fork profit after conservative gas model");
        uint256 net=profit-totalGasUsd;
        vm.writeFile("validation/liquidation-fork/fork-profit-proof.json",
            string.concat(
                '{"status":"FORK_ESTIMATED_AFTER_GAS_POSITIVE",',
                '"real_aave_loan_liquidation_swap_and_repayment":true,',
                '"state_exactness":"B_MINUS_1_NOT_TRANSACTION_INDEX",',
                '"block":',vm.toString(blockNumber),
                ',"borrowed_usdc_units":',vm.toString(borrowed),
                ',"usdc_profit_units":',vm.toString(profit),
                ',"gas_used_estimate":',vm.toString(gasUsed),
                ',"modeled_gas_usdc_units":',vm.toString(totalGasUsd),
                ',"net_after_gas_usdc_units":',vm.toString(net),
                ',"broadcast":false}'
            )
        );
        emit Proof(blockNumber,borrower,profit,gasUsed,totalGasUsd,net);
    }
}
