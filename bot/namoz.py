"""
Namoz vaqtlari eslatma tizimi — Toshkent, Aladhan API.
Har bir namoz vaqtida guruh va kanalga chiroyli eslatma yuboradi.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp

log = logging.getLogger(__name__)

ALADHAN_URL = "https://api.aladhan.com/v1/timingsByCity"
CITY = "Tashkent"
COUNTRY = "Uzbekistan"
METHOD = 2  # ISNA

# Namoz nomlari va emoji
NAMOZ_INFO = {
    "Fajr":    {"uz": "Bomdod", "emoji": "🌅", "icon": "☪️"},
    "Dhuhr":   {"uz": "Peshin", "emoji": "☀️", "icon": "☪️"},
    "Asr":     {"uz": "Asr",    "emoji": "🌤", "icon": "☪️"},
    "Maghrib": {"uz": "Shom",   "emoji": "🌅", "icon": "☪️"},
    "Isha":    {"uz": "Xufton", "emoji": "🌙", "icon": "☪️"},
}

# Niso 103 oyati
NISO_103 = (
    "إِنَّ ٱلصَّلَوٰةَ كَانَتۡ عَلَى ٱلۡمُؤۡمِنِينَ كِتَٰبٗا مَّوۡقُوتٗا"
)
NISO_103_UZ = "Albatta namoz mo'minlarga vaqtida ado etish farz qilingandir"


async def get_prayer_times() -> dict | None:
    """Bugungi Toshkent namoz vaqtlarini olish."""
    try:
        params = {"city": CITY, "country": COUNTRY, "method": METHOD}
        async with aiohttp.ClientSession() as session:
            async with session.get(ALADHAN_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("data", {}).get("timings", {})
    except Exception as e:
        log.error("Namoz vaqtlari olishda xato: %s", e)
        return None


def format_reminder(namoz_key: str, vaqt: str, hijri_date: str = "") -> str:
    """Chiroyli formatlangan namoz eslatmasi."""
    info = NAMOZ_INFO.get(namoz_key, {})
    uz_name = info.get("uz", namoz_key)
    emoji = info.get("emoji", "🕌")

    text = (
        f"{emoji} <b>{uz_name} namozi vaqti kirdi!</b>\n\n"
        f"🕐 <b>{vaqt}</b> (Toshkent vaqti)\n\n"
        f"<blockquote>{NISO_103}</blockquote>\n"
        f"<i>{NISO_103_UZ}</i>\n"
        f"<i>(Niso surasi, 103-oyat)</i>"
    )
    return text


async def namoz_scheduler(bot, chat_ids: list[int]):
    """Namoz vaqtlarini kuzatib, vaqti kelganda eslatma yuboradi."""
    log.info("Namoz scheduler ishga tushdi: %s", chat_ids)
    sent_today = set()  # Bugun qaysi namozlar yuborilgan

    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=5)  # Toshkent UTC+5
            current_date = now.strftime("%Y-%m-%d")

            # Yangi kun — sent_today ni tozalash
            if hasattr(namoz_scheduler, '_last_date') and namoz_scheduler._last_date != current_date:
                sent_today.clear()
            namoz_scheduler._last_date = current_date

            timings = await get_prayer_times()
            if not timings:
                await asyncio.sleep(300)  # 5 daqiqa kutib qayta urinish
                continue

            current_time = now.strftime("%H:%M")

            for namoz_key in NAMOZ_INFO:
                namoz_time = timings.get(namoz_key, "")
                if not namoz_time:
                    continue

                # Vaqtni solishtirish (HH:MM formatda)
                namoz_hm = namoz_time[:5]  # "04:35 (UTC+5)" -> "04:35"
                reminder_key = f"{current_date}_{namoz_key}"

                if reminder_key in sent_today:
                    continue

                if current_time == namoz_hm or (
                    # 1 daqiqa ichida bo'lsa ham yuborish (scheduler 30s da tekshiradi)
                    current_time > namoz_hm and
                    datetime.strptime(current_time, "%H:%M") - datetime.strptime(namoz_hm, "%H:%M") < timedelta(minutes=2)
                ):
                    msg = format_reminder(namoz_key, namoz_hm)
                    for chat_id in chat_ids:
                        try:
                            await bot.send_message(chat_id, msg, parse_mode="HTML")
                            log.info("Namoz eslatma yuborildi: %s -> %d", namoz_key, chat_id)
                        except Exception as e:
                            log.error("Namoz eslatma xatosi: %s -> %d: %s", namoz_key, chat_id, e)
                    sent_today.add(reminder_key)

        except Exception as e:
            log.error("Namoz scheduler xatosi: %s", e)

        await asyncio.sleep(30)  # Har 30 soniyada tekshirish
