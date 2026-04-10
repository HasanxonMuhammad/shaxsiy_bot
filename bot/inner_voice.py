"""
Inner Voice — bot o'zi xohlagan paytda o'zi mavzu boshlaydi.
Inson kabi — schedule emas, tasodifiy va tabiiy.
"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# Mavzular — bot birini tasodifiy tanlaydi, Gemini o'zi yozadi
TOPICS = [
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


async def inner_voice_loop(bot, ai, chat_id: int, bot_name: str):
    """Bot o'zi xohlagan paytda yozadi — tasodifiy, tabiiy."""
    log.info("Inner voice ishga tushdi: %s (chat %d)", bot_name, chat_id)

    while True:
        try:
            # Tasodifiy kutish — 1 dan 4 soatgacha
            wait_hours = random.uniform(1.5, 4.0)
            wait_seconds = int(wait_hours * 3600)
            log.info("Inner voice: keyingi xabar %d daqiqadan keyin", wait_seconds // 60)
            await asyncio.sleep(wait_seconds)

            # Kechasi yozmasin (00:00 - 06:00 Toshkent)
            now = datetime.utcnow() + timedelta(hours=5)
            if now.hour < 6 or now.hour > 23:
                continue

            # 50% ehtimollik — har doim yozmasin, ba'zan jim tursin
            if random.random() < 0.4:
                log.info("Inner voice: bu safar jim turaman")
                continue

            # Tasodifiy mavzu tanlash
            topic = random.choice(TOPICS)

            # Gemini dan javob olish
            from bot.telegram.dispatcher import build_system_prompt
            response = await ai.chat(
                build_system_prompt(),
                [{"role": "user", "text": f"Sen hozir o'zing yozyapsan, hech kim so'ramagan. Shunchaki o'zing xohlading. Mavzu: {topic}\n\nQisqa, tabiiy, 2-5 jumla. Guruhga yozyapsan. Savol berma, shunchaki fikr bildir yoki ma'lumot ber."}],
            )

            if response and "[NO_ACTION]" not in response and len(response) > 10:
                # Tool call larni tozalash
                import re
                clean = re.sub(r"\[TOOL:\w+\]\{[^}]*\}", "", response).strip()
                clean = re.sub(r"\[REACT:[^\]]+\]", "", clean).strip()
                clean = re.sub(r"\[NO_ACTION\]", "", clean).strip()

                if clean and len(clean) > 10:
                    try:
                        await bot.send_message(chat_id, clean, parse_mode="HTML")
                        log.info("Inner voice yozdi: %s", clean[:80])
                    except Exception as e:
                        # HTML parse xatosi bo'lsa — formatsiz yuborish
                        try:
                            await bot.send_message(chat_id, clean)
                        except Exception:
                            log.error("Inner voice yuborish xatosi: %s", e)

        except Exception as e:
            log.error("Inner voice xatosi: %s", e)
            await asyncio.sleep(600)
