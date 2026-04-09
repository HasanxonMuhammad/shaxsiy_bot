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


def _sanitize(text: str) -> str:
    """Prompt injection himoyasi — user matnida bot buyruqlarini zararsizlantirish."""
    if not text:
        return ""
    text = text.replace("[TOOL:", "[tool:").replace("[REACT:", "[react:")
    text = text.replace("[NO_ACTION]", "[no_action]")
    return text


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


async def fetch_url_content(url: str) -> str:
    """Har qanday URL dan matn kontentini olish."""
    try:
        import aiohttp as _aiohttp
        async with _aiohttp.ClientSession() as session:
            async with session.get(url, timeout=_aiohttp.ClientTimeout(total=10),
                                   headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return ""
                html = await resp.text()
                # HTML teglarni tozalash
                text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:3000]
    except Exception as e:
        log.debug("URL olishda xato: %s", e)
    return ""


async def fetch_telegram_post(url: str) -> str:
    """Telegram post linkidan kontentni olish (t.me/channel/post_id)."""
    try:
        import aiohttp as _aiohttp
        # Post ID ni ajratish
        post_match = re.search(r't\.me/([^/]+)/(\d+)', url)
        if not post_match:
            return ""
        channel = post_match.group(1)
        post_id = post_match.group(2)

        # Embed widget — aniq bitta post
        embed_url = f"https://t.me/{channel}/{post_id}?embed=1&mode=tme"

        async with _aiohttp.ClientSession() as session:
            async with session.get(embed_url, timeout=_aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return ""
                html = await resp.text()

        # HTML dan matnni ajratish
        text_match = re.search(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        if text_match:
            text = text_match.group(1)
            text = re.sub(r'<br\s*/?>', '\n', text)
            text = re.sub(r'<[^>]+>', '', text)
            text = text.strip()
            if text:
                log.info("Telegram post olindi: %s (%d belgi)", url, len(text))
                return text
    except Exception as e:
        log.debug("Telegram post olishda xato: %s", e)
    return ""


async def process_messages(chat_id: int, messages: list[dict]):
    """Debouncer flush — to'plangan xabarlarni AI ga yuboradi."""
    bot: Bot = dp["bot"]

    history = await db.get_recent_messages(chat_id, 30)

    # XML format (prompt injection himoyali)
    ctx = "<chat_history>\n"
    for m in history:
        ctx += (
            f'<msg id="{m["message_id"]}" user="{m["username"] or "unknown"}" '
            f'name="{m["first_name"] or ""}" '
            f'time="{m["timestamp"]}">{_sanitize(m["text"] or "")}</msg>\n'
        )
    ctx += "</chat_history>\n\n<new_messages>\n"
    for m in messages:
        ctx += (
            f'<msg id="{m["message_id"]}" user="{m["username"]}" '
            f'name="{m["first_name"]}">{_sanitize(m["text"])}</msg>\n'
        )
    ctx += "</new_messages>\n"

    # Xabardagi linklarni olish
    for m in messages:
        text_content = m.get("text", "")
        # Telegram post linklari
        tg_links = re.findall(r'https?://t\.me/\S+/\d+', text_content)
        for link in tg_links[:3]:
            post_text = await fetch_telegram_post(link)
            if post_text:
                ctx += f'\n<telegram_post url="{link}">{_sanitize(post_text[:2000])}</telegram_post>\n'
        # Oddiy URL lar (t.me dan tashqari)
        other_links = re.findall(r'https?://(?!t\.me)\S+', text_content)
        for link in other_links[:2]:
            page_text = await fetch_url_content(link)
            if page_text:
                ctx += f'\n<web_page url="{link}">{_sanitize(page_text[:1500])}</web_page>\n'

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

    # Session persistence — oldingi suhbat tarixini olish
    # Kontekst to'lib ketmasligi uchun max 20 turn
    session_history = await db.get_session_history(chat_id, limit=20)
    # Agar session juda uzaygan bo'lsa — eskisini tozalash
    full_history = await db.get_session_history(chat_id, limit=100)
    if len(full_history) > 40:
        await db.clear_session(chat_id)
        # Faqat oxirgi 10 ta turnni saqlash
        for turn in full_history[-10:]:
            await db.save_session_turn(chat_id, turn["role"], turn["text"])
        session_history = await db.get_session_history(chat_id, limit=20)
        log.info("Session auto-reset: %d -> 10 turn (chat %d)", len(full_history), chat_id)
    conversation = []
    for turn in session_history:
        conversation.append({"role": turn["role"], "text": turn["text"]})
    # Yangi xabarni qo'shish
    conversation.append({"role": "user", "text": ctx, "media": all_media})

    # Stream o'chirilgan — tool call ko'rinib qolish muammosi tufayli
    if False:
        response = await _stream_response(bot, ai, chat_id, messages, conversation, db, tools, ctx)
    else:
        response = await ai.chat(
            build_system_prompt(),
            conversation,
            use_search=Config.USE_SEARCH,
        )
        # Session ga saqlash
        await db.save_session_turn(chat_id, "user", ctx[:3000])
        if response:
            await db.save_session_turn(chat_id, "model", response[:3000])
        if not response:
            return
        await _handle_response(bot, ai, db, tools, chat_id, messages, response)


async def _stream_response(bot: Bot, ai: GeminiEngine, chat_id: int,
                           messages: list, conversation: list, db: Database,
                           tools: ToolHandler, ctx: str):
    """Private chatda Gemini stream + sendMessageDraft orqali real-time javob."""
    from aiogram.methods import SendMessageDraft
    import random

    draft_id = random.randint(1, 2**31 - 1)
    full_text = ""
    last_sent = ""
    last_send_time = 0
    MIN_INTERVAL = 0.4  # Har 400ms da yangilash

    had_content = False
    async for chunk, accumulated in ai.chat_stream(build_system_prompt(), conversation):
        full_text = accumulated
        had_content = True

        now = asyncio.get_event_loop().time()
        # Juda tez-tez yubormaslik (Telegram rate limit)
        if now - last_send_time < MIN_INTERVAL:
            continue
        # Matn o'zgarmagan bo'lsa yubormaslik
        if full_text == last_sent:
            continue

        # [TOOL:...] stream da ko'rsatmaslik — tool call ni yashirish
        display_text = re.sub(r"\[TOOL:\w+\]\{[^}]*\}?", "", full_text).strip()
        display_text = re.sub(r"\[REACT:[^\]]+\]", "", display_text).strip()
        display_text = re.sub(r"\[NO_ACTION\]", "", display_text).strip()
        if not display_text or display_text == last_sent:
            continue

        try:
            await bot(SendMessageDraft(
                chat_id=chat_id,
                draft_id=draft_id,
                text=display_text[:4096],
            ))
            last_sent = display_text
            last_send_time = now
        except Exception as e:
            log.debug("Draft xato: %s", e)

    # Stream tugadi — fallback: agar stream ishlamagan bo'lsa oddiy chat
    if not had_content:
        response = await ai.chat(
            build_system_prompt(), conversation,
            use_search=Config.USE_SEARCH,
        )
        await db.save_session_turn(chat_id, "user", ctx[:3000])
        if response:
            await db.save_session_turn(chat_id, "model", response[:3000])
        if not response:
            return response
        await _handle_response(bot, ai, db, tools, chat_id, messages, response)
        return response

    response = full_text

    # Session saqlash
    await db.save_session_turn(chat_id, "user", ctx[:3000])
    if response:
        await db.save_session_turn(chat_id, "model", response[:3000])

    if not response:
        return response

    await _handle_response(bot, ai, db, tools, chat_id, messages, response)
    return response


async def _handle_response(bot: Bot, ai: GeminiEngine, db: Database,
                           tools: ToolHandler, chat_id: int,
                           messages: list, response: str):
    """AI javobini parse qilib yuborish (tool, react, oddiy matn)."""
    from bot.tools.handler import parse_response

    log.info("AI javob (%d belgi): %s", len(response), response[:150])

    if "[NO_ACTION]" in response:
        log.info("AI [NO_ACTION] qaytardi, o'tkazildi")
        return

    # Reaksiya
    react_match = re.search(r"\[REACT:([^\]]+)\]", response)
    if react_match:
        emoji = react_match.group(1).strip()
        last_msg_id = messages[-1]["message_id"]
        await set_reaction(bot, chat_id, last_msg_id, emoji)
        response = re.sub(r"\[REACT:[^\]]+\]", "", response).strip()

    reply_text, tool_call = parse_response(response)
    log.info("Parse: reply_text=%d belgi, tool=%s",
             len(reply_text) if reply_text else 0,
             tool_call.get("name") if tool_call else "yo'q")

    if tool_call:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except Exception:
            pass
        result = await tools.execute(tool_call)
        log.info("Tool %s: %s", tool_call["name"], result[:100])
        import base64 as b64
        from aiogram.types import BufferedInputFile
        last_msg_id = messages[-1]["message_id"]

        if result.startswith("IMAGE:"):
            try:
                parts = result.split(":", 2)
                img_bytes = b64.b64decode(parts[2])
                photo = BufferedInputFile(img_bytes, filename="image.png")
                await bot.send_photo(chat_id, photo, reply_to_message_id=last_msg_id,
                                     caption=reply_text[:1024] if reply_text else None)
            except Exception as e:
                log.error("Rasm yuborishda xato: %s", e)
                await bot.send_message(chat_id, "Rasm yaratdim lekin yuborishda xato chiqdi 😅",
                                       reply_to_message_id=last_msg_id)
        elif result.startswith("VOICE:"):
            try:
                audio_bytes = b64.b64decode(result[6:])
                voice = BufferedInputFile(audio_bytes, filename="voice.wav")
                await bot.send_audio(chat_id, voice, reply_to_message_id=last_msg_id)
            except Exception as e:
                log.error("Ovoz yuborishda xato: %s", e)
                await bot.send_message(chat_id, "Ovozli xabar yuborishda xato chiqdi",
                                       reply_to_message_id=last_msg_id)
        else:
            tool_response = await ai.chat(
                build_system_prompt(),
                [
                    {"role": "user", "text": messages[-1].get("text", "")},
                    {"role": "model", "text": response},
                    {"role": "user", "text": f"Tool natijasi: {result[:1000]}. Shu natijaga qarab foydalanuvchiga javob ber."},
                ],
            )
            final_text = reply_text or tool_response or result
            if final_text and "[NO_ACTION]" not in final_text:
                final_text = re.sub(r"\[TOOL:\w+\]\{.*?\}", "", final_text, flags=re.DOTALL).strip()
                final_text = re.sub(r"\[REACT:[^\]]+\]", "", final_text).strip()
                for chunk in _split(final_text, 4000):
                    try:
                        await bot.send_message(chat_id, chunk,
                                               reply_to_message_id=messages[-1]["message_id"],
                                               parse_mode="HTML")
                    except Exception:
                        await bot.send_message(chat_id, chunk,
                                               reply_to_message_id=messages[-1]["message_id"])
    elif reply_text:
        last_msg_id = messages[-1]["message_id"]
        for chunk in _split(reply_text, 4000):
            try:
                await bot.send_message(chat_id, chunk,
                                       reply_to_message_id=last_msg_id,
                                       parse_mode="HTML")
            except Exception:
                try:
                    await bot.send_message(chat_id, chunk,
                                           reply_to_message_id=last_msg_id)
                except Exception:
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


# ── Auto-delete join/leave xabarlari ─────────────────────────

@dp.message(F.new_chat_members | F.left_chat_member)
async def on_join_leave(message: types.Message):
    """Guruhga kirdi/chiqdi xabarlarini o'chirish — guruhni toza tutish."""
    try:
        await message.delete()
    except Exception:
        pass  # Admin huquqi bo'lmasa o'chirolmaydi


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
            s = ai.stats
            students = await db.list_students()
            await message.reply(
                f"📊 <b>{Config.BOT_NAME}</b>\n"
                f"🤖 So'rovlar: {s.total_requests} (✅{s.successful} ❌{s.errors} ⏳{s.rate_limited})\n"
                f"⚡ O'rtacha javob: {s.avg_response_ms:.0f}ms\n"
                f"👥 O'quvchilar: {len(students)}\n"
                f"📝 Tokenlar: ~{s.total_tokens_approx:,}",
                parse_mode="HTML",
            )
            return
        if text == "/reset":
            await db.clear_session(chat_id)
            await message.reply("🔄 Suhbat tarixi tozalandi. Yangi suhbat boshlanadi.")
            return
        if text == "/chatid":
            await message.reply(f"Chat ID: <code>{chat_id}</code>", parse_mode="HTML")
            return

    # Bot-to-bot loop himoyasi
    is_bot = user.is_bot if user else False
    bot_me = await tg_bot.me()
    if is_bot:
        # Boshqa bot yozgan — faqat shu holatlarda javob ber:
        # 1) Owner buyruq bergan (reply qilib "javob ber" degan)
        # 2) Botni @mention qilgan (@qamusaibot kabi)
        bot_username = bot_me.username or ""
        mentioned = f"@{bot_username}".lower() in text.lower() if bot_username else False
        if not mentioned:
            log.info("Bot xabar (loop himoya): %s dan, o'tkazildi", first_name)
            # DB ga saqlaymiz lekin javob bermaymiz
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await db.save_message(chat_id, message.message_id, user_id, username, first_name, text, None, ts)
            return

    # Guruh tekshiruvi
    if chat_id < 0:
        log.info("Guruh xabar: chat_id=%d, user=%s", chat_id, username or first_name)
        if not Config.is_allowed_group(chat_id):
            return
        # Muted chat tekshiruvi
        if await db.is_muted(chat_id) and not Config.is_owner(user_id):
            return

    # Spam filter (guruhlar uchun)
    if chat_id < 0 and not Config.is_vip(user_id):
        spam_result = spam_filter.check(text)
        # Noaniq bo'lsa AI bilan tekshirish
        if spam_result is None and len(text) > 20 and Config.GEMINI_API_KEYS:
            spam_result = await spam_filter.classify_with_ai(text, Config.GEMINI_API_KEYS[0])
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

    # Kanal post kommentini aniqlash — reply_to_message da kanal postining matni
    reply_context = ""
    reply_to = None
    if message.reply_to_message:
        reply_to = message.reply_to_message.message_id
        rm = message.reply_to_message
        # Kanal postiga komment yoki oddiy reply
        reply_text = rm.text or rm.caption or ""
        reply_user = rm.from_user.first_name if rm.from_user else "Kanal"
        if rm.sender_chat:  # Kanal post
            reply_user = rm.sender_chat.title or "Kanal"
        if reply_text:
            reply_context = f"\n[Reply: {reply_user}: {reply_text[:500]}]"

    # DB ga saqlash
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    full_text = text + reply_context if reply_context else text

    await db.save_message(
        chat_id, message.message_id, user_id, username, first_name, full_text, reply_to, ts
    )
    await db.upsert_user(chat_id, user_id, username, first_name)

    # Debouncer ga qo'shish
    await buffer.add(
        chat_id,
        {
            "message_id": message.message_id,
            "user_id": user_id,
            "username": username or first_name,
            "first_name": first_name,
            "text": full_text,
            "media": media_list,
        },
    )


# ── Kanal monitoring ──────────────────────────────────────────

@dp.channel_post(F.text)
async def on_channel_post(message: types.Message):
    """Kanallardan kelgan postlarni kuzatish va muhimlarini guruhga ulashish."""
    if not Config.WATCH_CHANNELS or not Config.NEWS_TARGET_CHAT:
        return

    chat = message.chat
    username = chat.username or ""
    text = message.text or ""

    # Bu kanal kuzatilayaptimi?
    is_watched = any(
        ch.replace("@", "").lower() == username.lower()
        for ch in Config.WATCH_CHANNELS
    )
    if not is_watched:
        return

    log.info("Kanal post: @%s: %s", username, text[:80])

    # Regex prefilter — kalit so'zlar bilan muhimlikni tez tekshirish
    RELEVANT_KEYWORDS = re.compile(
        r'(imtihon|sessia|deadline|grant|stipendiya|konferensiya|olimpiada|'
        r'konkurs|arizalar|ro\'yxat|muddati|talaba|magistr|bakalavr|'
        r'sharqshunoslik|TSUOS|o\'quv|dars|fakultet|dekan|rektor|'
        r'ta\'lim|o\'zbekiston|toshkent|universitet|akademiya|'
        r'ish o\'rni|vakansiya|amaliyot|stajiro|sertifikat)',
        re.IGNORECASE
    )

    is_relevant = bool(RELEVANT_KEYWORDS.search(text))

    # Bazaga saqlash — faqat tegishli postlar
    if is_relevant:
        from datetime import datetime
        await db.save_message(
            chat_id=chat.id,
            message_id=message.message_id,
            user_id=None,
            username=f"@{username}",
            first_name=chat.title or username,
            text=text,
            reply_to=None,
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        )
        log.info("Kanal post saqlandi: @%s (tegishli)", username)
    else:
        log.debug("Kanal post o'tkazildi: @%s (tegishli emas)", username)
        return

    # AI bilan guruhga ulashish kerakmi tekshirish
    if not Config.GEMINI_API_KEYS:
        return

    classify_prompt = f"""Quyidagi xabar TSUOS sharqshunoslik talabalariga tegishlimi?
Tegishli = imtihon, dars, stipendiya, grant, konferensiya, olimpiada, universitet yangiliklari, ta'lim sohasi.
TEGISHLI EMAS = siyosat, sport, ko'ngilochar, reklama, chet el yangiliklari, amerikadagi voqealar.
Javob FAQAT: TEGISHLI yoki YO'Q

Kanal: @{username}
Xabar: {text[:500]}"""

    response = await ai.chat(
        "Sen klassifikator. TEGISHLI yoki YO'Q deb javob ber.",
        [{"role": "user", "text": classify_prompt}],
    )

    if response and "TEGISHLI" in response.upper():
        tg_bot: Bot = dp["bot"]
        # Guruhga ulashish — guruh muhitiga mos uslubda
        share_prompt = f"""Kanaldan talabalar uchun tegishli yangilik keldi. Buni guruhga ulash.
Kanal: @{username}
Yangilik: {text[:1000]}

QISQA yoz, 2-3 jumla. Guruh muhitiga mos, samimiy uslubda. Link qo'sh agar bor bo'lsa."""

        share_text = await ai.chat(
            "Sen guruh a'zosi. Yangilikni tabiiy tilda ulash.",
            [{"role": "user", "text": share_prompt}],
        )

        if share_text and "[NO_ACTION]" not in share_text:
            try:
                await tg_bot.send_message(
                    Config.NEWS_TARGET_CHAT,
                    share_text.strip(),
                    parse_mode="HTML",
                )
                log.info("Yangilik guruhga ulashildi: @%s", username)
            except Exception as e:
                log.error("Yangilik ulashishda xato: %s", e)


# ── Background tasks ──────────────────────────────────────────

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


async def daily_digest_loop(bot: Bot):
    """Har kuni ertalab 08:00 da (UTC+5) digest yuborish."""
    from datetime import datetime, timezone, timedelta
    uz_tz = timezone(timedelta(hours=5))

    while True:
        now = datetime.now(uz_tz)
        # Keyingi 08:00 ni hisoblash
        target = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait_sec = (target - now).total_seconds()
        log.info("Daily digest: %d soniyadan keyin (%.1f soat)", wait_sec, wait_sec / 3600)
        await asyncio.sleep(wait_sec)

        try:
            # O'quvchilar statistikasi
            students = await db.list_students()
            if not students:
                continue

            digest = f"📊 <b>Kunlik hisobot — {datetime.now(uz_tz).strftime('%d.%m.%Y')}</b>\n\n"
            digest += f"👥 Jami o'quvchilar: {len(students)}\n"

            active = [s for s in students if s.get("last_lesson_date")]
            if active:
                digest += f"📖 Dars topshirganlar: {len(active)}\n"
                top = sorted(active, key=lambda s: s.get("avg_score", 0), reverse=True)[:3]
                if top:
                    digest += "\n🏆 <b>Top o'quvchilar:</b>\n"
                    for i, s in enumerate(top, 1):
                        digest += f"  {i}. {s['first_name']} — ⭐{s['avg_score']}/10 ({s['total_lessons']} dars)\n"

            # Owner ga yuborish
            await bot.send_message(Config.OWNER_ID, digest, parse_mode="HTML")
            log.info("Daily digest yuborildi")

        except Exception as e:
            log.error("Daily digest xatosi: %s", e)


async def channel_poll_loop(bot: Bot):
    """Har soatda public kanallardan yangi postlarni o'qish va muhimlarini ulashish."""
    import aiohttp as _aiohttp

    if not Config.WATCH_CHANNELS or not Config.NEWS_TARGET_CHAT:
        return

    # Oxirgi ko'rilgan post ID larni saqlash
    last_seen: dict[str, int] = {}

    while True:
        await asyncio.sleep(3600)  # Har 1 soatda
        log.info("Kanal polling boshlandi: %s", Config.WATCH_CHANNELS)

        for channel in Config.WATCH_CHANNELS:
            channel = channel.strip().replace("@", "")
            if not channel:
                continue

            try:
                url = f"https://t.me/s/{channel}"
                async with _aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=_aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text()

                # Barcha postlarni topish
                posts = re.findall(
                    r'data-post="([^"]+)"[^>]*>.*?'
                    r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                    html, re.DOTALL
                )

                if not posts:
                    continue

                for post_id_str, raw_html in posts[-5:]:  # Oxirgi 5 ta
                    # Post ID ni olish
                    try:
                        post_num = int(post_id_str.split("/")[-1])
                    except (ValueError, IndexError):
                        continue

                    # Oldin ko'rilganmi
                    if channel in last_seen and post_num <= last_seen[channel]:
                        continue

                    # HTML tozalash
                    text = re.sub(r'<br\s*/?>', '\n', raw_html)
                    text = re.sub(r'<[^>]+>', '', text).strip()

                    if not text or len(text) < 20:
                        continue

                    # Regex prefilter
                    RELEVANT_KEYWORDS = re.compile(
                        r'(imtihon|sessia|deadline|grant|stipendiya|konferensiya|olimpiada|'
                        r'konkurs|arizalar|ro\'yxat|muddati|talaba|magistr|bakalavr|'
                        r'sharqshunoslik|TSUOS|o\'quv|dars|fakultet|dekan|rektor|'
                        r'ta\'lim|o\'zbekiston|toshkent|universitet|akademiya|'
                        r'ish o\'rni|vakansiya|amaliyot|stajiro|sertifikat)',
                        re.IGNORECASE
                    )

                    if not RELEVANT_KEYWORDS.search(text):
                        continue

                    log.info("Kanal polling: @%s/%d — tegishli post topildi", channel, post_num)

                    # Bazaga saqlash
                    from datetime import datetime
                    await db.save_message(
                        chat_id=0,
                        message_id=post_num,
                        user_id=None,
                        username=f"@{channel}",
                        first_name=channel,
                        text=text,
                        reply_to=None,
                        timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                    )

                    # AI bilan tekshirish
                    if not Config.GEMINI_API_KEYS:
                        continue

                    classify_prompt = f"""Quyidagi xabar TSUOS sharqshunoslik talabalariga tegishlimi?
Tegishli = imtihon, dars, stipendiya, grant, konferensiya, olimpiada, universitet yangiliklari.
TEGISHLI EMAS = siyosat, sport, reklama, chet el yangiliklari.
Javob FAQAT: TEGISHLI yoki YO'Q

Kanal: @{channel}
Xabar: {text[:500]}"""

                    response = await ai.chat(
                        "Sen klassifikator. TEGISHLI yoki YO'Q deb javob ber.",
                        [{"role": "user", "text": classify_prompt}],
                    )

                    if response and "TEGISHLI" in response.upper():
                        share_prompt = f"""Kanaldan talabalar uchun tegishli yangilik keldi. Buni guruhga ulash.
Kanal: @{channel}
Link: https://t.me/{channel}/{post_num}
Yangilik: {text[:1000]}

QISQA yoz, 2-3 jumla. Samimiy uslubda. Linkni qo'sh."""

                        share_text = await ai.chat(
                            "Sen guruh a'zosi. Yangilikni tabiiy tilda ulash.",
                            [{"role": "user", "text": share_prompt}],
                        )

                        if share_text and "[NO_ACTION]" not in share_text:
                            try:
                                await bot.send_message(
                                    Config.NEWS_TARGET_CHAT,
                                    share_text.strip(),
                                    parse_mode="HTML",
                                )
                                log.info("Polling: yangilik ulashildi @%s/%d", channel, post_num)
                            except Exception as e:
                                log.error("Polling ulashish xatosi: %s", e)

                # Oxirgi ko'rilgan post ni yangilash
                if posts:
                    try:
                        last_num = int(posts[-1][0].split("/")[-1])
                        last_seen[channel] = last_num
                    except (ValueError, IndexError):
                        pass

            except Exception as e:
                log.error("Kanal polling xatosi @%s: %s", channel, e)


# ── Start ─────────────────────────────────────────────────────

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
    tools._bot = bot  # guruhga_yoz tool uchun
    dp["bot"] = bot

    log.info("📖 %s ishga tushdi! Bismillah! 🤲", Config.BOT_NAME)

    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(daily_digest_loop(bot))
    asyncio.create_task(channel_poll_loop(bot))

    await dp.start_polling(bot)
