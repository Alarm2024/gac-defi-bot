#!/bin/bash
# Production Bot Monitor Script

PROJECT_DIR="/home/wyndhamdesert"
SCRIPT_PATH="$PROJECT_DIR/src/simulate_bot.py"
LOG_FILE="$PROJECT_DIR/logs/bot.log"
PYTHON_BIN="/usr/bin/python3"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Check if process is running
if ! pgrep -f "$SCRIPT_PATH" > /dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - Bot is not running. Restarting..." >> "$LOG_FILE"
    
    # Restart bot in background
    cd "$PROJECT_DIR" || exit
    nohup $PYTHON_BIN "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1 &
    
    if [ $? -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Bot restarted successfully." >> "$LOG_FILE"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') - Bot restart FAILED." >> "$LOG_FILE"
    fi
else
    # Bot is running, silent exit
    exit 0
fi
