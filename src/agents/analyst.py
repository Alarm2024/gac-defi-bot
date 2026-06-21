import re
import os

def analyze_logs(log_path):
    if not os.path.exists(log_path): return
    with open(log_path, 'r') as f:
        logs = f.read()
    
    # Identify common redundant string patterns in agent logs
    patterns = re.findall(r'\[.+?\]\s+([A-Z]{3,}\s+)+', logs)
    print(f"Bloat Patterns: {list(set(patterns))[:10]}")

if __name__ == "__main__":
    analyze_logs("executor.log")
