"""Small Windows launcher; dependency setup errors remain visible and logged."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent


def main():
    os.chdir(ROOT)
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    print("Opening Course Transcript...", flush=True)
    if not python.is_file():
        print("Setting up the local runtime...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)
    check = subprocess.run([str(python), "-c", "import tkinter,playwright,yt_dlp,Cryptodome"], capture_output=True)
    if check.returncode:
        print("Installing components (first run only)...", flush=True)
        pip_check = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True)
        pip = [str(python), "-m", "pip"] if pip_check.returncode == 0 else [sys.executable, "-m", "pip", "--python", str(python)]
        subprocess.run([*pip, "install", "-r", str(ROOT / "requirements.txt")], check=True)
    runtime_log = ROOT / "data" / "app-error.log"
    runtime_log.parent.mkdir(parents=True, exist_ok=True)
    with runtime_log.open("w", encoding="utf-8") as log:
        result = subprocess.run([str(python), str(ROOT / "app.py")], stderr=log)
    if result.returncode:
        print("The window could not start. Details: " + str(runtime_log), flush=True)
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log = ROOT / "data" / "startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        print("Startup failed. Details: " + str(log), flush=True)
        traceback.print_exc()
        sys.exit(1)
