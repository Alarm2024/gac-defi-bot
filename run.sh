#!/bin/bash

# --- Project Mars - Production Service Script ---

echo "[*] Initializing Multi-Agent System..."

# Load .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Fallback for RPC_URL if not set or is placeholder
if [ -z "$RPC_URL" ] || [[ "$RPC_URL" == *"your_key_here"* ]]; then
    export RPC_URL="https://eth-mainnet.public.blastapi.io"
fi

# 1. Validation
if [ -z "$GEMINI_API_KEY" ] || [ -z "$BOT_TOKEN" ] || [ -z "$RPC_URL" ]; then
    echo "[!] ERROR: Required environment variables (GEMINI_API_KEY, BOT_TOKEN, RPC_URL) not set."
    exit 1
fi

# 2. Process Management
export PRODUCTION=true
echo "[*] Starting components..."

# Start main bot
nohup python3 core/carbon_miner.py > bot.log 2>&1 &

# Start Agents
nohup python3 src/agents/sentinel.py > sentinel.log 2>&1 &
nohup python3 src/agents/executor.py > executor.log 2>&1 &
nohup python3 src/agents/strategist.py > strategist.log 2>&1 &

echo "[*] System deployed."

