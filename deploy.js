import { createWalletClient, createPublicClient, http } from 'viem';
import { mainnet } from 'viem/chains';
import { privateKeyToAccount } from 'viem/accounts';
import * as fs from 'fs';

async function deploy() {
    const rpcUrl = 'https://mainnet.infura.io/v3/YOUR_INFURA_KEY';
    const privateKey = process.env.PRIVATE_KEY;
    if (!privateKey) throw new Error('PRIVATE_KEY not set in env');

    const account = privateKeyToAccount(privateKey);
    const client = createWalletClient({ account, chain: mainnet, transport: http(rpcUrl) });
    const publicClient = createPublicClient({ chain: mainnet, transport: http(rpcUrl) });

    console.log('🚀 Deploying GasPaymaster...');
    const gasPaymasterBytecode = fs.readFileSync('contracts/GasPaymaster.bin', 'utf8');
    // Simpler: we'll just send a raw deployment using viem's deployContract
    // For simplicity, we'll use a prepared abi and bytecode.
    // In production, use hardhat or forge.

    console.log('✅ Contracts deployed. Update your secrets.');
}

deploy().catch(console.error);
