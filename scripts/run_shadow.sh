#!/bin/bash
export PYTHONPATH=$PYTHONPATH:/home/wyndhamdesert/Guardian-Trade-BNB

echo "[+] Starting Guardian-Trade in SHADOW-TESTING mode..."
python3 src/simulate_bot.py
