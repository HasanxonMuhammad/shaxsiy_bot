import asyncio
import logging
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatAction

from bot.config import Config
from bot.db import Database
from bot.ai import GeminiEngine
from bot.memory import MemoryStore
from bot.tools import ToolHandler
from bot.tools.handler import parse_response
from bot.telegram.spam import SpamFilter

log = logging.getLogger(__name__)

# ── Reaksiya emojilar ─────────────────────────────────────────
POSITIVE_REACTIONS = ["👍", "❤", "🔥", "👏", "🤲", "💯", "⭐"]
QURAN_REACTIONS = ["❤", "🤲", "🔥", "����", "💯"]
GREETING_REACTIONS = ["👋", "❤", "🤲"]


def build_system_prompt() -> str:
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    uz_time = datetime.now(timezone(timedelta(hours=5)))
    time_str = uz_time.strftime("%H:%M")
    hour = uz_time.hour

    if 5 <= hour < 12:
        vaqt = "ertalab"
    elif 12 <= hour < 17:
        vaqt = "tushdan keyin"
    elif 17 <= hour < 21:
        vaqt = "kechqurun"
    else:
        vaqt = "kechasi"

    # Prompt fayldan o'qish
    prompt_file = Config.SYSTEM_PROMPT_FILE
    if prompt_file:
        path = Path(prompt_file)
        if path.exists():
            template = path.read_text(encoding="utf-8")
            return (
                template
                .replace("{bot_name}", Config.BOT_NAME)
                .replace("{owner_id}", str(Config.OWNER_ID))
                .replace("{time}", time_str)
                .replace("{vaqt}", vaqt)
            )

    # Default prompt
    return f"""Sen "{Config.BOT_NAME}". Quvnoq, hazilkash yordamchi.

Hozir soat {time_str} ({vaqt}). Vaqt haqida har safar gapirma.

- QISQA javob — 1-3 jumla
- Hazilkash, samimiy
- Har safar salom berma
- HAR BIR xabarga javob ber. [NO_ACTION] dema.
- Reaksiya: [REACT:emoji]
- Tool: [TOOL:name]{{"param": "value"}}
- Owner ID: {Config.OWNER_ID}

Toollar: search_messages, create_memory, set_reminder

Javob formati: oddiy matn | [TOOL:name]{{params}} | [REACT:emoji]"""


class MessageBuffer:
    """Claudir-style debouncer."""

    def __init__(self):
        self._buffers: dict[int, list[dict]] = {}
        self._timers: dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.on_flush = None

    async def add(self, chat_id: int, msg: dict):
        async with self._lock:
            self._buffers.setdefault(chat_id, []).append(msg)
            if chat_id in self._timers:
                self._timers[chat_id].cancel()
            self._timers[chat_id] = asyncio.create_task(self._debounce(chat_id))

    async def _debounce(self, chat_id: int):
        await asyncio.sleep(Config.DEBOUNCE_SEC)
        async with self._lock:
            messages = self._buffers.pop(chat_id, [])
            self._timers.pop(chat_id, None)
        if messages and self.on_flush:
            await self.on_flush(chat_id, messages)


# ── Global state ──────────────────────────────────────────────
db: Database = None
ai: GeminiEngine = None
memory: MemoryStore = None
tools: ToolHandler = None
spam_filter = SpamFilter()
buffer = MessageBuffer()
dp = Dispatcher()


async def set_reaction(bot: Bot, chat_id: int, message_id: int, emoji: str):
    """Xabarga reaksiya qo'yish."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[types.ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        log.debug("Reaksiya qo'yishda xato: %s", e)


async def process_messages(chat_id: int, messages: list[dict]):
    """Debouncer flush — to'plangan xabarlarni AI ga yuboradi."""
    bot: Bot = dp["bot"]

    history = await db.get_recent_messages(chat_id, 30)

    # XML format
    ctx = "<chat_history>\n"
    for m in history:
        ctx += (
            f'<msg id="{m["message_id"]}" user="{m["username"] or "unknown"}" '
            f'name="{m["first_name"] or ""}" '
            f'time="{m["timestamp"]}">{m["text"] or ""}</msg>\n'
        )
    ctx += "</chat_history>\n\n<new_messages>\n"
    for m in messages:
        ctx += (
            f'<msg id="{m["message_id"]}" user="{m["username"]}" '
            f'name="{m["first_name"]}">{m["text"]}</msg>\n'
        )
    ctx += "</new_messages>\n"

    # O'quvchi ma'lumotlarini kontekstga qo'shish
    seen_users = set()
    for m in messages:
        uid = m.get("user_id", 0)
        if uid and uid != Config.OWNER_ID and uid not in seen_users:
            seen_users.add(uid)
            student = await db.get_student(uid)
            if student:
                ctx += (
                    f'\n<student user_id="{uid}" name="{student["first_name"]}" '
                    f'level="{student["level"]}" sura="{student["current_sura"] or "?"}" '
                    f'lessons="{student["total_lessons"]}" '
                    f'avg_score="{student["avg_score"]}"/>'
                )

    # Media ni yig'ish
    all_media = []
    for m in messages:
        all_media.extend(m.get("media", []))

    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        pass

    response = await ai.chat(
        build_system_prompt(),
        [{"role": "user", "text": ctx, "media": all_media}],
    )

    if not response:
        return

    # Reaksiya bormi tekshirish: [REACT:emoji]
    react_match = re.search(r"\[REACT:([^\]]+)\]", response)
    if react_match:
        emoji = react_match.group(1).strip()
        last_msg_id = messages[-1]["message_id"]
        await set_reaction(bot, chat_id, last_msg_id, emoji)
        response = re.sub(r"\[REACT:[^\]]+\]", "", response).strip()

    reply_text, tool_call = parse_response(response)

    if tool_call:
        result = await tools.execute(tool_call)
        log.info("Tool %s: %s", tool_call["name"], result[:100])
    elif reply_text:
        # Guruhda reply qilib javob berish
        last_msg_id = messages[-1]["message_id"]
        for chunk in _split(reply_text, 4000):
            try:
                await bot.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=last_msg_id,
                )
            except Exception:
                # Reply ishlamasa oddiy yuborish
                await bot.send_message(chat_id, chunk)


def _split(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    chunks = []
    while text:
        if len(text) <= n:
            chunks.append(text)
            break
        cut = text[:n].rfind("\n")
        if cut == -1:
            cut = text[:n].rfind(" ")
        if cut == -1:
            cut = n
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    return chunks


# ── Media yuklab olish ─────────────────────────────────────────

async def download_file(bot: Bot, file_id: str) -> bytes:
    """Telegram dan fayl yuklab olish."""
    file = await bot.get_file(file_id)
    from io import BytesIO
    buf = BytesIO()
    await bot.download_file(file.file_path, buf)
    return buf.getvalue()


# ── Handlers ──────────────────────────────────────────────────

@dp.message(F.text | F.photo | F.voice | F.audio | F.video_note)
async def on_message(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id if user else 0
    username = (user.username or "") if user else ""
    first_name = (user.first_name or "Unknown") if user else "Unknown"
    text = message.text or message.caption or ""
    is_private = chat_id > 0
    tg_bot: Bot = dp["bot"]

    # Media bor-yo'qligini tekshirish
    media_list = []
    if message.photo:
        # Eng katta rasmni olish
        photo = message.photo[-1]
        try:
            data = await download_file(tg_bot, photo.file_id)
            media_list.append({"data": data, "mime": "image/jpeg"})
            if not text:
                text = "[Rasm yuborildi]"
        except Exception as e:
            log.error("Rasm yuklab olishda xato: %s", e)

    if message.voice:
        try:
            data = await download_file(tg_bot, message.voice.file_id)
            media_list.append({"data": data, "mime": "audio/ogg"})
            if not text:
                text = "[Ovozli xabar yuborildi]"
        except Exception as e:
            log.error("Audio yuklab olishda xato: %s", e)

    if message.audio:
        try:
            data = await download_file(tg_bot, message.audio.file_id)
            mime = message.audio.mime_type or "audio/mpeg"
            media_list.append({"data": data, "mime": mime})
            if not text:
                text = "[Audio fayl yuborildi]"
        except Exception as e:
            log.error("Audio yuklab olishda xato: %s", e)

    if message.video_note:
        try:
            data = await download_file(tg_bot, message.video_note.file_id)
            media_list.append({"data": data, "mime": "video/mp4"})
            if not text:
                text = "[Video xabar yuborildi]"
        except Exception as e:
            log.error("Video yuklab olishda xato: %s", e)

    # ── SHAXSIY CHAT: faqat owner javob oladi ────────────────
    if is_private and not Config.is_vip(user_id):
        # Boshqalarga xushmuomalalik bilan rad etish
        await message.reply(
            "Assalomu alaykum! 🤲\n\n"
            "Men ustoz Hasanxonning shaxsiy yordamchisiman. "
            "Guruhda siz bilan gaplasha olaman in sha Allah! 😊\n\n"
            "Savol bo'lsa guruhga yozing, u yerda yordam beraman. 📖"
        )
        return

    # Owner buyruqlari
    if Config.is_owner(user_id) and text.startswith("/"):
        if text == "/status":
            await message.reply("✅ Bot ishlayapti! Alhamdulillah 🤲")
            return
        if text == "/stats":
            await message.reply(
                f"📊 Bot: {Config.BOT_NAME}\n"
                f"🕐 Holat: Faol\n"
                f"🤲 Bismillah!"
            )
            return

    # Guruh tekshiruvi
    if chat_id < 0:
        log.info("Guruh xabar: chat_id=%d, user=%s", chat_id, username or first_name)
        if not Config.is_allowed_group(chat_id):
            return

    # Spam filter (guruhlar uchun)
    if chat_id < 0:
        spam_result = spam_filter.check(text)
        if spam_result is True:
            log.warning("Spam: %s: %s", username, text[:50])
            strikes = await db.add_strike(user_id)
            if strikes >= 3:
                try:
                    await message.chat.ban(user_id)
                    await message.reply(f"🚫 {first_name} banlandi.")
                except Exception:
                    pass
            return

    # DB ga saqlash
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None

    await db.save_message(
        chat_id, message.message_id, user_id, username, first_name, text, reply_to, ts
    )
    await db.upsert_user(chat_id, user_id, username, first_name)

    # Guruh a'zosini avtomatik o'quvchi sifatida ro'yxatga olish
    if not is_private and user_id and not Config.is_owner(user_id):
        await db.get_or_create_student(user_id, first_name, username)

    # Debouncer ga qo'shish
    await buffer.add(
        chat_id,
        {
            "message_id": message.message_id,
            "user_id": user_id,
            "username": username or first_name,
            "first_name": first_name,
            "text": text,
            "media": media_list,
        },
    )


# ── Background tasks ─────���───────────────────────────────────

async def reminder_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            reminders = await db.get_due_reminders()
            for r in reminders:
                await bot.send_message(
                    r["chat_id"],
                    f"⏰🤲 Eslatma: {r['text']}"
                )
                await db.complete_reminder(r["id"])
        except Exception as e:
            log.error("Reminder xatosi: %s", e)


# ── Start ─��───────────────────────────��───────────────────────

async def start_bot():
    global db, ai, memory, tools

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db = Database(Config.db_path())
    await db.connect()

    ai = GeminiEngine(Config.GEMINI_API_KEYS, Config.GEMINI_MODEL)
    memory = MemoryStore(Config.memories_dir())
    tools = ToolHandler(db, memory)
    buffer.on_flush = process_messages

    bot = Bot(token=Config.TELEGRAM_TOKEN)
    dp["bot"] = bot

    log.info("📖 %s ishga tushdi! Bismillah! 🤲", Config.BOT_NAME)

    asyncio.create_task(reminder_loop(bot))

    await dp.start_polling(bot)
