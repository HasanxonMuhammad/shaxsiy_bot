"""
Namoz vaqtlari eslatma tizimi — namozvaqti.uz (O'zbekiston rasmiy).
Har bir namoz vaqtida guruh va kanalga chiroyli eslatma yuboradi.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

import aiohttp

log = logging.getLogger(__name__)

NAMOZVAQTI_URL = "https://namozvaqti.uz/shahar/toshkent"

# Namoz ro'yxati — idx = namozvaqti.uz dagi times[] massiv indeksi
NAMOZ_LIST = [
    {"key": "bomdod", "uz": "Bomdod", "emoji": "🌅", "idx": 0},
    {"key": "peshin", "uz": "Peshin", "emoji": "☀️", "idx": 2},
    {"key": "asr",    "uz": "Asr",    "emoji": "🌤", "idx": 3},
    {"key": "shom",   "uz": "Shom",   "emoji": "🌅", "idx": 4},
    {"key": "xufton", "uz": "Xufton", "emoji": "🌙", "idx": 5},
]

# Niso 103 oyati
NISO_103 = "إِنَّ ٱلصَّلَوٰةَ كَانَتۡ عَلَى ٱلۡمُؤۡمِنِينَ كِتَٰبٗا مَّوۡقُوتٗا"
NISO_103_UZ = "Albatta namoz mo'minlarga vaqtida ado etish farz qilingandir"

_cached_times: list[str] = []
_cached_date: str = ""


async def get_prayer_times() -> list[str]:
    """namozvaqti.uz dan bugungi vaqtlarni olish (kunlik kesh)."""
    global _cached_times, _cached_date
    now = datetime.utcnow() + timedelta(hours=5)
    today = now.strftime("%Y-%m-%d")

    if _cached_date == today and _cached_times:
        return _cached_times

    try:
        headers = {"User-Agent": "MudarrisAI/1.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(NAMOZVAQTI_URL, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.error("namozvaqti.uz xato: status %d", resp.status)
                    return _cached_times
                html = await resp.text()

        # const times = ['04:31', '05:53', '12:29', '17:02', '18:58', '20:16', '04:30']
        match = re.search(r"const\s+times\s*=\s*\[([^\]]+)\]", html)
        if not match:
            log.error("namozvaqti.uz da times[] topilmadi")
            return _cached_times

        raw = match.group(1)
        times = re.findall(r"'(\d{2}:\d{2})'", raw)
        if len(times) >= 6:
            _cached_times = times
            _cached_date = today
            log.info("Namoz vaqtlari yangilandi (namozvaqti.uz): %s",
                     {n["uz"]: times[n["idx"]] for n in NAMOZ_LIST})
        return times

    except Exception as e:
        log.error("Namoz vaqtlari olishda xato: %s", e)
        return _cached_times


def format_reminder(namoz: dict, vaqt: str) -> str:
    """Chiroyli formatlangan namoz eslatmasi."""
    return (
        f"{namoz['emoji']} <b>{namoz['uz']} namozi vaqti kirdi!</b>\n\n"
        f"🕐 <b>{vaqt}</b> (Toshkent vaqti)\n\n"
        f"<blockquote>{NISO_103}</blockquote>\n"
        f"<i>{NISO_103_UZ}</i>\n"
        f"<i>(Niso surasi, 103-oyat)</i>"
    )


async def namoz_scheduler(bot, chat_ids: list[int]):
    """Namoz vaqtlarini kuzatib, vaqti kelganda eslatma yuboradi."""
    log.info("Namoz scheduler ishga tushdi: %s", chat_ids)
    sent_today: set[str] = set()
    last_date = ""

    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=5)  # Toshkent UTC+5
            current_date = now.strftime("%Y-%m-%d")

            # Yangi kun — sent ni tozalash
            if last_date != current_date:
                sent_today.clear()
                last_date = current_date

            times = await get_prayer_times()
            if not times:
                await asyncio.sleep(60)
                continue

            current_time = now.strftime("%H:%M")

            for namoz in NAMOZ_LIST:
                if namoz["idx"] >= len(times):
                    continue
                namoz_hm = times[namoz["idx"]]
                reminder_key = f"{current_date}_{namoz['key']}"

                if reminder_key in sent_today:
                    continue

                # Vaqt kelganmi? (aniq yoki 2 daqiqa ichida)
                if current_time >= namoz_hm:
                    try:
                        diff = datetime.strptime(current_time, "%H:%M") - datetime.strptime(namoz_hm, "%H:%M")
                        if diff <= timedelta(minutes=2):
                            msg = format_reminder(namoz, namoz_hm)
                            for chat_id in chat_ids:
                                try:
                                    await bot.send_message(chat_id, msg, parse_mode="HTML")
                                    log.info("Namoz eslatma: %s %s -> %d", namoz["uz"], namoz_hm, chat_id)
                                except Exception as e:
                                    log.error("Namoz eslatma xatosi: %s -> %d: %s", namoz["uz"], chat_id, e)
                            sent_today.add(reminder_key)
                    except ValueError:
                        pass

        except Exception as e:
            log.error("Namoz scheduler xatosi: %s", e)

        await asyncio.sleep(5)
