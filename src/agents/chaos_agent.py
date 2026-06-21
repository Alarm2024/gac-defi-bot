import os
import time

def inject_bottleneck(target_file):
    with open(target_file, 'a') as f:
        f.write("\n# CHAOS INJECTION: REDUNDANT LOOP\nfor i in range(1000000): pass\n")
    print(f"[!] Chaos Injected into {target_file}")

if __name__ == "__main__":
    inject_bottleneck("src/agents/executor.py")
