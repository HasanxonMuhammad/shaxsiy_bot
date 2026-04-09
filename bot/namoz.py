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

MUSLIM_UZ_URL = "https://muslim.uz/oz"

# Namoz nomlari — muslim.uz dagi krillcha nomlarga mos
NAMOZ_LIST = [
    {"key": "bomdod", "uz": "Bomdod", "cyr": "Бомдод", "emoji": "🌅"},
    {"key": "peshin", "uz": "Peshin", "cyr": "Пешин",  "emoji": "☀️"},
    {"key": "asr",    "uz": "Asr",    "cyr": "Аср",    "emoji": "🌤"},
    {"key": "shom",   "uz": "Shom",   "cyr": "Шом",    "emoji": "🌅"},
    {"key": "xufton", "uz": "Xufton", "cyr": "Хуфтон", "emoji": "🌙"},
]

# Niso 103 oyati
NISO_103 = "إِنَّ ٱلصَّلَوٰةَ كَانَتۡ عَلَى ٱلۡمُؤۡمِنِينَ كِتَٰبٗا مَّوۡقُوتٗا"
NISO_103_UZ = "Albatta namoz mo'minlarga vaqtida ado etish farz qilingandir"

_cached_times: dict[str, str] = {}
_cached_date: str = ""


async def get_prayer_times() -> dict[str, str]:
    """namozvaqti.uz dan bugungi vaqtlarni olish (kunlik kesh)."""
    global _cached_times, _cached_date
    now = datetime.utcnow() + timedelta(hours=5)
    today = now.strftime("%Y-%m-%d")

    if _cached_date == today and _cached_times:
        return _cached_times

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(MUSLIM_UZ_URL, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10),
                                   allow_redirects=True) as resp:
                if resp.status != 200:
                    log.error("muslim.uz xato: status %d", resp.status)
                    return _cached_times
                html = await resp.text()

        # <div>Бомдод</div> <div>04:32</div> formatdan parse
        times = {}
        for namoz in NAMOZ_LIST:
            pattern = rf"{namoz['cyr']}</div>\s*<div>(\d{{2}}:\d{{2}})</div>"
            match = re.search(pattern, html)
            if match:
                times[namoz["key"]] = match.group(1)

        if times:
            _cached_times = times
            _cached_date = today
            log.info("Namoz vaqtlari yangilandi (muslim.uz): %s", times)
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
                namoz_hm = times.get(namoz["key"], "")
                if not namoz_hm:
                    continue
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
