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
        cmd = f"sudo journalctl -u shaxsiy-bot --since '{since}' --no-pager | grep -i 'error\\|crash\\|fail' || true"
        result = self.run_cmd(cmd, timeout=15)
        if not result or result == "(bo'sh natija)" or len(result.strip()) < 5:
            return "xato topilmadi"
        return result

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
    """Mirzo-style monitoring — muammoni aniqlash, tuzatish, xabar berish."""
    log.info("Health monitor ishga tushdi (har %ds)", check_interval)
    sv = Supervisor()
    consecutive_errors = 0
    reported_issues = set()  # Bir xil muammoni takror xabar qilmaslik

    while True:
        try:
            await asyncio.sleep(check_interval)
            issues = []
            fixes = []

            # 1. Service ishlayaptimi?
            status = sv.check_status()
            if "active (running)" not in status:
                consecutive_errors += 1
                if consecutive_errors >= 2:
                    sv.restart_bots()
                    fixes.append("Bot to'xtagan edi — avtomatik restart qildim")
                    consecutive_errors = 0
            else:
                consecutive_errors = 0

            # 2. Xatolar tekshirish (high demand ni o'tkazib yuborish)
            error_log = sv.check_errors(minutes=check_interval // 60 + 1)
            if error_log != "xato topilmadi":
                # Faqat haqiqiy xatolarni filtrlash
                real_errors = [
                    line.strip() for line in error_log.split("\n")
                    if line.strip()
                    and "high demand" not in line.lower()
                    and "timeout" not in line.lower()
                    and "rate limit" not in line.lower()
                    and "rate_limit" not in line.lower()
                    and "quota" not in line.lower()
                    and "fallback" not in line.lower()
                    and "429" not in line
                    and "gemini" not in line.lower()
                    and "barcha urinish" not in line.lower()
                    and "namoz" not in line.lower()
                    and ("ERROR" in line or "crash" in line.lower())
                ]
                if real_errors:
                    error_summary = "\n".join(real_errors[:3])
                    issue_key = error_summary[:80]
                    if issue_key not in reported_issues:
                        issues.append(f"Xatolar:\n{error_summary}")
                        reported_issues.add(issue_key)

            # 3. Disk to'lib ketganmi?
            disk_info = sv.run_cmd("df -h / | tail -1")
            if disk_info:
                parts = disk_info.split()
                if len(parts) >= 5:
                    usage = parts[4].replace("%", "")
                    try:
                        if int(usage) > 90:
                            issues.append(f"Disk 90% dan oshdi! ({parts[4]})")
                    except ValueError:
                        pass

            # 4. RAM tekshirish
            mem_info = sv.run_cmd("free -m | grep Mem")
            if mem_info:
                parts = mem_info.split()
                if len(parts) >= 3:
                    try:
                        total = int(parts[1])
                        available = int(parts[6]) if len(parts) > 6 else int(parts[3])
                        if available < total * 0.1:  # 10% dan kam qolsa
                            issues.append(f"RAM kam qoldi! ({available}MB / {total}MB)")
                    except (ValueError, IndexError):
                        pass

            # 5. Bot jarayonlari bormi?
            procs = sv.run_cmd("pgrep -c -f 'python3.*main.py'")
            try:
                proc_count = int(procs.strip())
                if proc_count < 2:  # 2 ta bot ishlashi kerak
                    issues.append(f"Faqat {proc_count} bot jarayoni ishlayapti (2 kerak)")
                    sv.restart_bots()
                    fixes.append("Etishmayotgan bot jarayonlari uchun restart qildim")
            except ValueError:
                pass

            # Choyxonaga xabar yuborish (agar muammo bo'lsa)
            if issues or fixes:
                msg_parts = []
                if fixes:
                    msg_parts.append("🔧 <b>Tuzatdim:</b>\n" + "\n".join(f"• {f}" for f in fixes))
                if issues:
                    msg_parts.append("⚠️ <b>Muammolar:</b>\n" + "\n".join(f"• {i}" for i in issues))
                msg = "\n\n".join(msg_parts)
                try:
                    await bot.send_message(owner_id, msg, parse_mode="HTML")
                except Exception:
                    pass

            # Eski reported issues tozalash (1 soatdan keyin qayta xabar qilishi mumkin)
            if len(reported_issues) > 50:
                reported_issues.clear()

        except Exception as e:
            log.error("Health monitor xatosi: %s", e)
