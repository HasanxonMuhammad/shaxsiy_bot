"""
Supervisor — Mirzo-style bot monitoring va boshqarish.
Botlar sog'lig'ini kuzatadi, crash bo'lsa owner ga xabar beradi.
"""
import asyncio
import logging
import subprocess
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


class Supervisor:
    """Bot monitoring va boshqarish toollari."""

    def __init__(self):
        self._last_health_check = None
        self._crash_count = 0

    def run_cmd(self, cmd: str, timeout: int = 10) -> str:
        """Xavfsiz shell buyruq bajarish (faqat ruxsat berilganlar)."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            output = result.stdout + result.stderr
            return output.strip()[:2000] if output else "(bo'sh natija)"
        except subprocess.TimeoutExpired:
            return "Buyruq vaqti tugadi (timeout)"
        except Exception as e:
            return f"Xato: {e}"

    def check_logs(self, lines: int = 30, filter_str: str = "") -> str:
        """Oxirgi loglarni o'qish."""
        cmd = f"sudo journalctl -u shaxsiy-bot --no-pager -n {min(lines, 100)}"
        if filter_str:
            cmd += f" | grep -i '{filter_str}'"
        return self.run_cmd(cmd, timeout=15)

    def check_status(self) -> str:
        """Systemd service holatini tekshirish."""
        return self.run_cmd("sudo systemctl status shaxsiy-bot --no-pager | head -15")

    def restart_bots(self) -> str:
        """Botlarni qayta ishga tushirish."""
        result = self.run_cmd("sudo systemctl restart shaxsiy-bot", timeout=15)
        return f"Restart bajarildi.\n{result}" if not result.startswith("Xato") else result

    def git_pull(self) -> str:
        """Git pull — yangi kodni olish."""
        return self.run_cmd("cd /home/hasanxon/shaxsiy_bot && git pull", timeout=30)

    def git_pull_restart(self) -> str:
        """Git pull + restart — yangilanish + qayta ishga tushirish."""
        pull = self.run_cmd("cd /home/hasanxon/shaxsiy_bot && git pull", timeout=30)
        restart = self.run_cmd("sudo systemctl restart shaxsiy-bot", timeout=15)
        return f"Git pull:\n{pull}\n\nRestart:\n{restart}"

    def disk_status(self) -> str:
        """Disk, RAM, CPU holati."""
        disk = self.run_cmd("df -h / | tail -1")
        mem = self.run_cmd("free -h | head -2")
        uptime = self.run_cmd("uptime")
        procs = self.run_cmd("pgrep -af 'python3.*main.py' | head -5")
        return f"Disk: {disk}\n\nRAM:\n{mem}\n\nUptime: {uptime}\n\nBot jarayonlari:\n{procs}"

    def check_errors(self, minutes: int = 30) -> str:
        """Oxirgi N daqiqadagi xatolarni tekshirish."""
        since = (datetime.utcnow() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
        cmd = f"sudo journalctl -u shaxsiy-bot --since '{since}' --no-pager | grep -i 'error\\|xato\\|crash\\|fail'"
        result = self.run_cmd(cmd, timeout=15)
        if not result or result == "(bo'sh natija)":
            return f"Oxirgi {minutes} daqiqada xato topilmadi"
        return f"Oxirgi {minutes} daqiqadagi xatolar:\n{result}"

    def edit_file(self, filepath: str, old: str, new: str) -> str:
        """Faylni tahrirlash (prompt, config)."""
        import os
        base = "/home/hasanxon/shaxsiy_bot/"
        full_path = os.path.join(base, filepath)
        # Xavfsizlik — faqat shaxsiy_bot papkasi ichida
        if not os.path.abspath(full_path).startswith(os.path.abspath(base)):
            return "Xavfsizlik: faqat shaxsiy_bot papkasi ichidagi fayllar"
        if not os.path.exists(full_path):
            return f"Fayl topilmadi: {filepath}"
        try:
            text = open(full_path, "r", encoding="utf-8").read()
            if old not in text:
                return f"'{old[:50]}...' matni faylda topilmadi"
            text = text.replace(old, new, 1)
            open(full_path, "w", encoding="utf-8").write(text)
            return f"Fayl yangilandi: {filepath}"
        except Exception as e:
            return f"Tahrirlash xatosi: {e}"

    def read_file(self, filepath: str) -> str:
        """Faylni o'qish."""
        import os
        base = "/home/hasanxon/shaxsiy_bot/"
        full_path = os.path.join(base, filepath)
        if not os.path.abspath(full_path).startswith(os.path.abspath(base)):
            return "Xavfsizlik: faqat shaxsiy_bot papkasi ichidagi fayllar"
        if not os.path.exists(full_path):
            return f"Fayl topilmadi: {filepath}"
        try:
            text = open(full_path, "r", encoding="utf-8").read()
            if len(text) > 3000:
                return text[:3000] + f"\n\n... ({len(text)} belgi)"
            return text
        except Exception as e:
            return f"O'qish xatosi: {e}"


async def health_monitor(bot, owner_id: int, check_interval: int = 300):
    """Har 5 daqiqada bot sog'lig'ini tekshirish, muammo bo'lsa owner ga xabar."""
    log.info("Health monitor ishga tushdi (har %ds)", check_interval)
    sv = Supervisor()
    consecutive_errors = 0

    while True:
        try:
            await asyncio.sleep(check_interval)

            # Systemd status tekshirish
            status = sv.check_status()
            if "active (running)" not in status:
                consecutive_errors += 1
                if consecutive_errors >= 2:
                    # Avtomatik restart
                    sv.restart_bots()
                    try:
                        await bot.send_message(
                            owner_id,
                            f"⚠️ <b>Supervisor xabar:</b>\n\n"
                            f"Bot to'xtagan edi — avtomatik restart qilindi.\n"
                            f"Xatolar soni: {consecutive_errors}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    consecutive_errors = 0
            else:
                consecutive_errors = 0

            # Xatolarni tekshirish
            errors = sv.check_errors(minutes=check_interval // 60 + 1)
            if "xato topilmadi" not in errors and "high demand" not in errors.lower():
                try:
                    await bot.send_message(
                        owner_id,
                        f"⚠️ <b>Xatolar aniqlandi:</b>\n\n<pre>{errors[:500]}</pre>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception as e:
            log.error("Health monitor xatosi: %s", e)
