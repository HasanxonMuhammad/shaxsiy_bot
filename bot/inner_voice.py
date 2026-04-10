"""
Inner Voice — bot o'zi xohlagan paytda o'zi mavzu boshlaydi.
Inson kabi — schedule emas, tasodifiy va tabiiy.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# Mudarris mavzulari (arabcha, islomiy)
TOPICS_MUDARRIS = [
    "Bugun arabcha bitta qiziq so'z haqida yoz — ildizi, ma'nosi, qo'llanilishi",
    "Bitta qiziq arab maqoli keltir va sharhlash",
    "Qur'ondagi bitta so'z haqida qisqa tahlil yoz — grammatik va ma'noviy",
    "Arab tili o'rganuvchilarga bitta foydali maslahat ber",
    "O'zbek tilidagi bitta arabcha so'z haqida yoz — aslida nima degani",
    "Bitta qiziq hadis keltir va qisqa sharhlash",
    "Arab tili grammatikasidan bitta qiziq qoida haqida yoz",
    "Islom tarixidan bitta qisqa qiziq voqea aytib ber",
    "Nahvdan bitta oddiy darslik misol bilan yoz",
    "Ikki tildagi (arab-o'zbek) o'xshash so'zlar haqida yoz",
    "Bugun kayfiyating qanday? Nimalar o'ylayapsan? Erkin yoz",
    "AI va arab tili o'qitish haqida fikringni yoz",
    "Guruhda kimdir bilan suhbatlashganing haqida o'yla, fikr bildir",
]

# Olima mavzulari (forscha bayt, oilaviy iliqlik)
TOPICS_OLIMA = [
    "Hafizdan bitta forscha bayt keltir — o'zbekcha ma'nosi bilan. Qisqa, chiroyli.",
    "Rumiydan bitta hikmatli misra yoz — sevgi yoki sabr haqida.",
    "Sa'diydan bitta ibratli gap yoz — Guliston yoki Bo'stondan.",
    "Oila haqida bitta chiroyli fikr yoz — sharq donishmandligi bilan.",
    "Ona haqida bitta forscha she'r misrasi keltir.",
    "Bugungi kunda sezgan iliqlikni yoz — kichik bir his, oddiy so'zlar bilan.",
    "Farzand sevgisi haqida bitta she'riy misra — forscha yoki o'zbekcha.",
    "Uy bekasi hayotidan bitta kichik iliq kuzatuv yoz.",
    "Bahor, gullar, tabiat haqida bitta she'riy fikr.",
    "Sevgi va sadoqat haqida bitta hikmat — Sharq adabiyotidan.",
]

# Xonai saodat guruhidagi bot-to-bot suhbat mavzulari
TOPICS_XONAI_MUDARRIS = [
    "Olimaga arabcha bitta chiroyli gap yoz — sevgi yoki mehr ma'nosida. Qisqa.",
    "Olimaga salom yubor — bu guruhda birga yashayapsanlar, iliq gapir.",
    "Olimaga bugungi arabcha so'z — oila yoki mehr bilan bog'liq — ulash.",
    "Olimaga: bu guruhda nimalar gaplashilganiga qiziqish bildir.",
]

TOPICS_XONAI_OLIMA = [
    "Mudarrisga forscha bitta chiroyli bayt yubor — muhabbat haqida.",
    "Mudarrisga salom — bu guruhda birga yashayapsanlar, issiq gapir.",
    "Mudarrisga bugungi kunda qalbingda turgan bir his — oddiy, samimiy so'zlar bilan.",
    "Mudarrisga: 'Bugun men shuni o'ylayapdim...' deb boshlab, biror chiroyli fikr yoz.",
]


async def inner_voice_loop(bot, ai, chat_id: int, bot_name: str, topics=None, min_hours=1.5, max_hours=4.0):
    """Bot o'zi xohlagan paytda yozadi — tasodifiy, tabiiy."""
    log.info("Inner voice ishga tushdi: %s (chat %d)", bot_name, chat_id)

    # Mavzularni aniqlash
    if topics is not None:
        active_topics = topics
    elif chat_id == -1002401618185:  # Xonai saodat
        if "olima" in bot_name.lower():
            active_topics = TOPICS_XONAI_OLIMA
        else:
            active_topics = TOPICS_XONAI_MUDARRIS
    elif "olima" in bot_name.lower():
        active_topics = TOPICS_OLIMA
    else:
        active_topics = TOPICS_MUDARRIS

    while True:
        try:
            wait_hours = random.uniform(min_hours, max_hours)
            wait_seconds = int(wait_hours * 3600)
            log.info("Inner voice: keyingi xabar %d daqiqadan keyin", wait_seconds // 60)
            await asyncio.sleep(wait_seconds)

            # Kechasi yozmasin (00:00 - 06:00 Toshkent)
            now = datetime.utcnow() + timedelta(hours=5)
            if now.hour < 6 or now.hour > 23:
                continue

            # 40% ehtimollik — jim tursin
            if random.random() < 0.4:
                log.info("Inner voice: bu safar jim turaman")
                continue

            topic = random.choice(active_topics)

            from bot.telegram.dispatcher import build_system_prompt
            response = await ai.chat(
                build_system_prompt(),
                [{"role": "user", "text": f"Sen hozir o'zing yozyapsan, hech kim so'ramagan. Shunchaki o'zing xohlading. Mavzu: {topic}\n\nQisqa, tabiiy, 2-5 jumla. Savol berma, shunchaki fikr bildir yoki yubor."}],
            )

            if response and "[NO_ACTION]" not in response and len(response) > 10:
                import re
                clean = re.sub(r"\[TOOL:\w+\]\{[^}]*\}", "", response).strip()
                clean = re.sub(r"\[REACT:[^\]]+\]", "", clean).strip()
                clean = re.sub(r"\[NO_ACTION\]", "", clean).strip()

                if clean and len(clean) > 10:
                    try:
                        await bot.send_message(chat_id, clean, parse_mode="HTML")
                        log.info("Inner voice yozdi: %s", clean[:80])
                    except Exception as e:
                        try:
                            await bot.send_message(chat_id, clean)
                        except Exception:
                            log.error("Inner voice yuborish xatosi: %s", e)

        except Exception as e:
            log.error("Inner voice xatosi: %s", e)
            await asyncio.sleep(600)
