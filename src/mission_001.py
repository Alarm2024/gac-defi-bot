import os
import psutil
import shutil
from datetime import datetime

def run_mission():
    # 1. Audit
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    total, used, free = shutil.disk_usage("/")
    disk_pct = (used / total) * 100

    # 2. Security (Process check)
    suspicious = any("malware" in p.info['name'].lower() for p in psutil.process_iter(['name']))

    # 3. Report
    status = "SYSTEM NOMINAL"
    if cpu > 80 or mem > 80 or disk_pct > 80 or suspicious:
        status = f"CRITICAL - CPU:{cpu}%, MEM:{mem}%, DISK:{disk_pct}%, SUSPICIOUS_PROC:{suspicious}"
    
    with open("mission_status.log", "w") as f:
        f.write(f"{datetime.now()}: {status}\n")

if __name__ == "__main__":
    run_mission()
