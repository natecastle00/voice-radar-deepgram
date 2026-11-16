import subprocess
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
stdout_f = open(os.path.join(BASE_DIR, "stdout.log"), "w", encoding="utf-8")
stderr_f = open(os.path.join(BASE_DIR, "stderr.log"), "w", encoding="utf-8")

CREATE_NO_WINDOW = 0x08000000

cmd = [sys.executable, os.path.join(BASE_DIR, "dictation.py")]

p = subprocess.Popen(
    cmd,
    stdout=stdout_f,
    stderr=stderr_f,
    stdin=subprocess.DEVNULL,
    creationflags=CREATE_NO_WINDOW,
    env=os.environ.copy(),
    cwd=BASE_DIR
)
print("Spawned hidden python.exe PID:", p.pid)
