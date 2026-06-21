import time
import random
import json
import os
from datetime import datetime
import requests
from dotenv import load_dotenv
from web3 import Web3
from google import genai
import threading
import sys
import signal
import psutil
import shutil
import subprocess

# Load credentials
load_dotenv()

class CarbonMiner:
    """
    Guardian Angel v3.8 - Multi-instance Stability & Signal Handling
    """
    def __init__(self, is_api_instance=False):
        self.wallet = os.getenv("PRIVATE_KEY")
        self.public_address = "0x08312f8381f059f5a8a13236CF10b54c08f9C991"
        self.rpc_url = os.getenv("RPC_URL")
        self.bot_token = os.getenv("BOT_TOKEN")
        self.chat_id = os.getenv("CHAT_ID")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.ai_cooldown_until = 0
        self.total_mitigated = 0
        self.realized_profit = 0
        self.certificates = []
        self.hunt_mode = False
        self.ai_audit_enabled = True # Enabled by default if AI is active
        
        self.is_api_instance = is_api_instance
        self.lock_file = ".bot.lock"
        self.last_heartbeat_time = 0
        
        if not self.is_api_instance:
            self.acquire_lock()
            self.load_state()
            # Graceful shutdown handler
            signal.signal(signal.SIGINT, self.cleanup_and_exit)
            signal.signal(signal.SIGTERM, self.cleanup_and_exit)
        
        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url)) if self.rpc_url else None
        
        # Initialize Gemini using the modern google-genai Client SDK
        self.ai_active = False
        self.client = None
        self.ai_model = "gemini-2.0-flash"
        if self.gemini_key and self.gemini_key != "your_gemini_api_key_here":
            try:
                self.client = genai.Client(api_key=self.gemini_key)
                self.ai_active = True
                print(f"[✔] AI Engine Forced: {self.ai_model} (google-genai SDK)", flush=True)
            except Exception as e:
                print(f"[!] AI Init Error: {e}", flush=True)

        self.check_connectivity()

    def cleanup_and_exit(self, signum, frame):
        """Removes lock file on exit."""
        if os.path.exists(self.lock_file):
            os.remove(self.lock_file)
        sys.exit(0)

    def acquire_lock(self):
        """Prevents the 409 Conflict by ensuring only one instance runs."""
        if os.path.exists(self.lock_file):
            with open(self.lock_file, "r") as f:
                old_pid = f.read().strip()
                if old_pid and os.path.exists(f"/proc/{old_pid}"):
                    print(f"\n[❌] FATAL: Bot is already running with PID {old_pid}")
                    print(f"[💡] Run: kill {old_pid} OR pkill -f carbon_miner.py\n")
                    sys.exit(1)
        
        with open(self.lock_file, "w") as f:
            f.write(str(os.getpid()))

    def load_state(self):
        state_path = "bot_state.json"
        if os.path.exists(state_path):
            try:
                with open(state_path, "r") as f:
                    state = json.load(f)
                    self.total_mitigated = state.get("total_mitigated", 0)
                    self.realized_profit = state.get("realized_profit", 0)
                    self.certificates = state.get("certificates", [])
                print(f"[✔] State Loaded: {len(self.certificates)} assets cached.", flush=True)
            except Exception as e:
                print(f"[!] State Load Error: {e}", flush=True)

    def save_state(self):
        try:
            with open("bot_state.json", "w") as f:
                json.dump({
                    "total_mitigated": self.total_mitigated,
                    "realized_profit": self.realized_profit,
                    "certificates": self.certificates
                }, f, indent=4)
        except Exception as e:
            print(f"[!] State Save Error: {e}", flush=True)

    def check_connectivity(self):
        print(f"[*] Initializing Guardian Angel Oracle...")
        if self.w3 and self.w3.is_connected():
            print(f"[✔] Blockchain: ONLINE")
        else:
            error_msg = f"[!] CRITICAL: Blockchain offline (RPC: {self.rpc_url})."
            if os.getenv("PRODUCTION") == "true":
                print(error_msg)
                raise Exception(error_msg)
            else:
                print(f"{error_msg} Running in Simulation Mode.")

    def send_telegram(self, message):
        if not self.bot_token or not self.chat_id:
            print("[!] Telegram Error: Missing token or chat_id", flush=True)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code != 200:
                print(f"[!] Telegram API Error ({r.status_code}): {r.text}", flush=True)
        except Exception as e:
            print(f"[!] Telegram Exception: {e}", flush=True)

    def generate_audit(self, summary):
        """Uses Gemini to generate an audit report for carbon mitigation."""
        if not self.ai_active:
            return None
        
        prompt = f"Carbon Audit: {summary}. Is this mitigation significant and verified according to standard protocols? Provide a one-sentence professional audit summary."
        try:
            print(f"[AI] Requesting Audit: {summary}", flush=True)
            response = self.client.models.generate_content(
                model=self.ai_model,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            print(f"[!] AI Audit Error: {e}", flush=True)
            return None

    def mine_carbon(self):
        """Simulates carbon mitigation and minting."""
        location = random.choice(["Zone-A", "Zone-B", "Zone-C"])
        baseline = round(random.uniform(100, 1000), 2)
        current = round(random.uniform(baseline * 0.1, baseline * 0.9), 2)
        mitigation = round(baseline - current, 2)
        
        summary = f"{location} mitigated {mitigation} tonnes."
        audit = self.generate_audit(summary)
        
        cert_id = f"VCU-{int(time.time())}-{random.randint(1000, 9999)}"
        certificate = {
            "cert_id": cert_id,
            "location": location,
            "mitigation_tonnes": mitigation,
            "audit": audit,
            "timestamp": datetime.now().isoformat()
        }
        
        self.certificates.append(certificate)
        self.total_mitigated += mitigation
        self.save_state()
        
        msg = f"[💎] ASSET MINTED: {cert_id} ({mitigation} Tonnes)\nAudit: {audit if audit else 'N/A'}"
        print(msg, flush=True)
        self.send_telegram(msg)

    def run(self):
        """Main loop."""
        print(f"--- Guardian Angel v3.7 Active ---", flush=True)
        
        # Start background task thread
        threading.Thread(target=self.background_tasks, daemon=True).start()
        print(f"[*] Background Task Thread Started.", flush=True)
        
        # Start Telegram bot thread if token is present
        if self.bot_token:
            threading.Thread(target=self.telegram_listener, daemon=True).start()
            print(f"[*] Starting Telegram Listener (ID: {self.chat_id})...", flush=True)

        while True:
            try:
                print(f"[*] Running background mining cycle...", flush=True)
                self.mine_carbon()
                # Wait between cycles
                time.sleep(random.randint(30, 60))
            except Exception as e:
                print(f"[!] Main Loop Error: {e}", flush=True)
                time.sleep(10)

    def background_tasks(self):
        """Internal monitoring and heartbeat."""
        while True:
            self.last_heartbeat_time = time.time()
            time.sleep(10)

    def telegram_listener(self):
        """Minimalistic Telegram polling for remote control."""
        last_update_id = 0
        print(f"[.] Polling Telegram... (Last ID: {last_update_id})", flush=True)
        while True:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {"offset": last_update_id + 1, "timeout": 30}
                r = requests.get(url, params=params, timeout=35)
                if r.status_code == 200:
                    updates = r.json().get("result", [])
                    for update in updates:
                        last_update_id = update["update_id"]
                        msg = update.get("message", {})
                        text = msg.get("text", "")
                        if text == "/status":
                            status_msg = f"System Status: ONLINE\nTotal Mitigated: {self.total_mitigated} Tonnes"
                            self.send_telegram(status_msg)
            except Exception as e:
                print(f"[!] Telegram Listener Error: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    miner = CarbonMiner()
    miner.run()
