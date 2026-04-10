"""Barcha botlarni bir vaqtda ishga tushirish.
Self-healing: exponential backoff + circuit breaker.
"""
import subprocess
import sys
import time
import signal
import os
from collections import defaultdict

bot_dir = os.path.dirname(os.path.abspath(__file__))

bots = [
    {"name": "ShaxsiyBot", "env": ".env"},
    {"name": "Super Boshliq", "env": ".env.superboshliq"},
    {"name": "Aziza", "env": ".env.aziza"},
]

processes = []
# Restart tracking
restart_times: dict[int, list[float]] = defaultdict(list)
restart_counts: dict[int, int] = defaultdict(int)

MAX_RESTARTS_IN_WINDOW = 10  # 10 daqiqada max 10 restart
WINDOW_SECONDS = 600  # 10 daqiqa
MAX_BACKOFF = 64  # max kutish vaqti


def stop_all(sig=None, frame=None):
    print("\nBarcha botlar to'xtatilmoqda...")
    for p in processes:
        try:
            p.terminate()
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGINT, stop_all)
signal.signal(signal.SIGTERM, stop_all)


def start_bot(index: int) -> subprocess.Popen:
    bot = bots[index]
    p = subprocess.Popen(
        [sys.executable, "main.py", "--env", bot["env"]],
        cwd=bot_dir,
    )
    print(f"[START] {bot['name']} (PID: {p.pid})")
    return p


def should_restart(index: int) -> tuple[bool, float]:
    """Circuit breaker: juda ko'p restart bo'lsa to'xtatish.
    Returns: (restart_kerakmi, kutish_vaqti)
    """
    now = time.time()
    bot_name = bots[index]["name"]

    # Eski restartlarni tozalash
    restart_times[index] = [t for t in restart_times[index] if now - t < WINDOW_SECONDS]

    if len(restart_times[index]) >= MAX_RESTARTS_IN_WINDOW:
        print(f"[CIRCUIT BREAKER] {bot_name}: {WINDOW_SECONDS}s da {MAX_RESTARTS_IN_WINDOW} marta restart — TO'XTATILDI")
        return False, 0

    # Exponential backoff
    restart_counts[index] += 1
    backoff = min(2 ** restart_counts[index], MAX_BACKOFF)

    restart_times[index].append(now)
    return True, backoff


# Boshlang'ich ishga tushirish
for i, bot in enumerate(bots):
    p = start_bot(i)
    processes.append(p)
    time.sleep(2)

print(f"\n{len(bots)} ta bot ishlayapti. Ctrl+C bilan to'xtatish.\n")

# Kuzatish loop
while True:
    for i, (p, bot) in enumerate(zip(processes, bots)):
        if p.poll() is not None:
            exit_code = p.returncode
            print(f"[CRASH] {bot['name']} to'xtadi (exit code: {exit_code})")

            can_restart, backoff = should_restart(i)
            if can_restart:
                if backoff > 2:
                    print(f"[BACKOFF] {bot['name']}: {backoff}s kutilmoqda...")
                    time.sleep(backoff)
                processes[i] = start_bot(i)
                # Agar bot 30+ soniya ishlasa, restart countni tiklash
            else:
                # Circuit breaker — bu botni o'chirish
                processes[i] = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(999999)"],
                )  # placeholder — crash loop oldini olish

    # Agar bot uzoq ishlasa, restart countni tiklash
    for i, p in enumerate(processes):
        if p.poll() is None:  # ishlayapti
            restart_counts[i] = max(0, restart_counts[i] - 1)

    time.sleep(5)
