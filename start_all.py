"""Barcha botlarni bir vaqtda ishga tushirish."""
import subprocess
import sys
import time
import signal

bots = [
    {"name": "ShaxsiyBot", "env": ".env"},
    {"name": "Super Boshliq", "env": ".env.superboshliq"},
]

processes = []

def stop_all(sig=None, frame=None):
    print("\nBarcha botlar to'xtatilmoqda...")
    for p, bot in zip(processes, bots):
        p.terminate()
        print(f"  {bot['name']} to'xtatildi")
    sys.exit(0)

signal.signal(signal.SIGINT, stop_all)
signal.signal(signal.SIGTERM, stop_all)

for bot in bots:
    p = subprocess.Popen(
        [sys.executable, "main.py", "--env", bot["env"]],
        cwd="d:/hasanxon/shaxsiy_bot",
    )
    processes.append(p)
    print(f"{bot['name']} ishga tushdi (PID: {p.pid})")
    time.sleep(2)

print(f"\n{len(bots)} ta bot ishlayapti. Ctrl+C bilan to'xtatish.")

# Botlarni kuzatish
while True:
    for i, (p, bot) in enumerate(zip(processes, bots)):
        if p.poll() is not None:
            print(f"{bot['name']} to'xtadi! Qayta ishga tushirilmoqda...")
            p = subprocess.Popen(
                [sys.executable, "main.py", "--env", bot["env"]],
                cwd="d:/hasanxon/shaxsiy_bot",
            )
            processes[i] = p
            print(f"{bot['name']} qayta ishga tushdi (PID: {p.pid})")
    time.sleep(5)
