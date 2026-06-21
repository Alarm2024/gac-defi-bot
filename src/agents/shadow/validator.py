import subprocess
import os
import sys

def validate(patch_path, original_module_path):
    # 1. Syntax Check
    if subprocess.call([sys.executable, "-m", "py_compile", patch_path]) != 0:
        return False, "Syntax Error"

    # 2. Dry Run with Timeout
    try:
        # Import as module to dry run? 
        # Simpler: run in subprocess with timeout
        subprocess.run([sys.executable, patch_path], timeout=5, check=True)
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except subprocess.CalledProcessError:
        return False, "Runtime Error"
    
    return True, "Success"

if __name__ == "__main__":
    patch = sys.argv[1]
    orig = sys.argv[2]
    ok, msg = validate(patch, orig)
    if not ok:
        print(f"Validation Failed: {msg}")
        sys.exit(1)
    print("Validation Success")
    sys.exit(0)
