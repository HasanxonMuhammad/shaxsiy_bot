import asyncio
import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatAction

from bot.config import Config
from bot.db import Database
from bot.ai import GeminiEngine
from bot.memory import MemoryStore
from bot.tools import ToolHandler
from bot.tools.handler import strip_tool_blocks, isolate_arabic, force_rtl_blockquote, expand_long_blockquotes, markdown_to_html
from bot.telegram.spam import SpamFilter

log = logging.getLogger(__name__)

import time as _time_module
_bot_start_time = _time_module.time()

# ── Reaksiya emojilar ─────────────────────────────────────────
POSITIVE_REACTIONS = ["👍", "❤", "🔥", "👏", "🤲", "💯", "⭐"]
QURAN_REACTIONS = ["❤", "🤲", "🔥", "����", "💯"]
GREETING_REACTIONS = ["👋", "❤", "🤲"]


def _current_time_label() -> tuple[str, str, str]:
    """Toshkent vaqti, kun bo'limi va to'liq sana qaytaradi."""
    from datetime import datetime, timezone, timedelta
    uz_time = datetime.now(timezone(timedelta(hours=5)))
    time_str = uz_time.strftime("%H:%M")
    date_str = uz_time.strftime("%Y-%m-%d (%A)")
    hour = uz_time.hour
    if 5 <= hour < 12:
        vaqt = "ertalab"
    elif 12 <= hour < 17:
        vaqt = "tushdan keyin"
    elif 17 <= hour < 21:
        vaqt = "kechqurun"
    else:
        vaqt = "kechasi"
    return time_str, vaqt, date_str


def build_system_prompt() -> str:
    """Static system prompt — har xabarda bir xil baytlar (cache prefix uchun).

    {time}/{vaqt} dynamic — promptga emas, dispatcher tomonidan user message
    contextiga inject qilinadi. Shu sababli prompt baytlari barqaror va
    Vertex Context Caching bilan kesh qilinishi mumkin.
    """
    from pathlib import Path

    prompt_file = Config.SYSTEM_PROMPT_FILE
    if prompt_file:
        path = Path(prompt_file)
        if path.exists():
            template = path.read_text(encoding="utf-8")
            return (
                template
                .replace("{bot_name}", Config.BOT_NAME)
                .replace("{owner_id}", str(Config.OWNER_ID))
            )

    # Default prompt (static)
    return f"""Sen "{Config.BOT_NAME}". Quvnoq, hazilkash yordamchi.

(Hozirgi sana va vaqt har xabarda foydalanuvchi xabari oldidan beriladi.)

- QISQA javob — 1-3 jumla
- Hazilkash, samimiy
- Har safar salom berma
- HAR BIR xabarga javob ber. [NO_ACTION] dema.
- Reaksiya: [REACT:emoji]
- Tool: [TOOL:name]{{"param": "value"}}
- Owner ID: {Config.OWNER_ID}

Toollar: search_messages, create_memory, set_reminder

Javob formati: oddiy matn | [TOOL:name]{{params}} | [REACT:emoji]"""


def build_time_prefix() -> str:
    """User xabari oldiga qo'yiladigan vaqt context — har xabarda yangilanadi."""
    time_str, vaqt, date_str = _current_time_label()
    return f"[Sana: {date_str}, soat {time_str} ({vaqt})]\n"


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


# ── Guest Mode debug logger (vaqtincha) ──────────────────────────
# Telegram 2026-05-07 da Guest Mode joriy qildi. Bot API 9.6 da hali documented emas
# va aiogram 3.27 da model yo'q. BotFather'da yoqilgach, kelayotgan Update'larda
# noma'lum field'larni payqaymiz va xom JSON'ni logga yozamiz — keyin handler yozamiz.
_KNOWN_UPDATE_CONTENT_FIELDS = {
    "message", "edited_message", "channel_post", "edited_channel_post",
    "business_connection", "business_message", "edited_business_message",
    "deleted_business_messages",
    "message_reaction", "message_reaction_count",
    "inline_query", "chosen_inline_result", "callback_query",
    "shipping_query", "pre_checkout_query", "purchased_paid_media",
    "poll", "poll_answer",
    "my_chat_member", "chat_member", "chat_join_request",
    "chat_boost", "removed_chat_boost",
}


@dp.update.outer_middleware()
async def _guest_update_logger(handler, event, data):
    try:
        dump = event.model_dump(exclude_none=True)
        extras = getattr(event, "model_extra", None) or {}
        present = set(dump.keys()) - {"update_id"}
        unknown_present = present - _KNOWN_UPDATE_CONTENT_FIELDS
        if extras or unknown_present or not present:
            raw = event.model_dump_json(exclude_none=True)
            log.warning(
                "GUEST_CANDIDATE update_id=%s known=%s unknown=%s extras=%s raw=%s",
                dump.get("update_id"),
                sorted(present & _KNOWN_UPDATE_CONTENT_FIELDS),
                sorted(unknown_present),
                list(extras.keys()),
                raw[:2500],
            )
    except Exception as e:
        log.debug("Guest logger error: %s", e)
    return await handler(event, data)


# ── Guest Mode handler ───────────────────────────────────────────
# Bot API 10.0 (2026-05-08) — aiogram 3.27 da `Update.guest_message`
# field hali yo'q, shuning uchun aiogram'ni chetlab o'tib qo'lda parse qilamiz.
async def _answer_guest_query(token: str, guest_query_id: str, text: str,
                              parse_mode: str | None = None) -> dict:
    import aiohttp
    url = f"https://api.telegram.org/bot{token}/answerGuestQuery"
    payload: dict = {"guest_query_id": guest_query_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()


async def _handle_guest_message(bot: Bot, guest_msg: dict):
    """Guest mode mention'iga javob: Gemini -> answerGuestQuery."""
    from bot.tools.handler import strip_tool_blocks
    try:
        guest_query_id = guest_msg.get("guest_query_id")
        text = (guest_msg.get("text") or guest_msg.get("caption") or "").strip()
        from_user = guest_msg.get("from") or {}
        chat = guest_msg.get("chat") or {}

        if not guest_query_id:
            log.warning("Guest message guest_query_id'siz: %s", guest_msg)
            return

        # Boshidagi @bot mention'ni olib tashlash
        question = text
        for e in guest_msg.get("entities") or []:
            if e.get("type") == "mention" and e.get("offset") == 0:
                question = text[e.get("length", 0):].strip()
                break

        if not question:
            await _answer_guest_query(
                bot.token, guest_query_id,
                "Bismillah, savolingizni yozing — yordam beraman."
            )
            return

        log.info(
            "GUEST query from %s (%s) chat=%s: %s",
            from_user.get("username") or from_user.get("first_name"),
            from_user.get("id"),
            chat.get("title") or chat.get("id"),
            question[:120],
        )

        # Spam regex pre-filter (AI tekshiruvisiz, tezroq)
        if spam_filter.check(question) is True:
            log.warning("Guest spam: %s", question[:80])
            return

        time_str, vaqt, date_str = _current_time_label()
        ctx = (
            f'<guest_mode time="{time_str}" vaqt="{vaqt}" date="{date_str}">\n'
            f'  Bu — Guest Mode chaqirig\'i. Chat tarixi yo\'q, bir martalik javob.\n'
            f'  Foydalanuvchi: {from_user.get("first_name", "")} '
            f'(@{from_user.get("username", "noma\'lum")})\n'
            f'  Chat: "{chat.get("title", "shaxsiy")}" ({chat.get("type", "")})\n'
            f'  Qoidalar:\n'
            f'  - Qisqa, aniq javob (1-3 jumla, kerak bo\'lsa ko\'proq)\n'
            f'  - Tool ishlatma — bu yerda kontekst yo\'q, faqat matn\n'
            f'  - Botning shaxsiyatiga sodiq qol\n'
            f'</guest_mode>\n\n'
            f'{question}'
        )

        conversation = [{"role": "user", "content": ctx}]
        response = await ai.chat(
            build_system_prompt(),
            conversation,
            use_search=Config.USE_SEARCH,
        )

        if not response:
            return

        # Tool block'larni olib tashla (guest mode'da bajarib bo'lmaydi)
        clean_text = strip_tool_blocks(response)
        clean_text = re.sub(r"\[REACT:[^\]]+\]", "", clean_text).strip()
        if not clean_text or clean_text == "[NO_ACTION]":
            return

        # Telegram message limiti
        if len(clean_text) > 4000:
            clean_text = clean_text[:3950].rstrip() + "..."

        result = await _answer_guest_query(
            bot.token, guest_query_id, clean_text, parse_mode="HTML"
        )
        if not result.get("ok"):
            log.warning("answerGuestQuery HTML xato: %s, plain text bilan qaytadan", result)
            result = await _answer_guest_query(bot.token, guest_query_id, clean_text)
            if not result.get("ok"):
                log.error("answerGuestQuery final xato: %s", result)
                return

        log.info("GUEST javob: %d belgi, ok=%s", len(clean_text), result.get("ok"))

    except Exception as e:
        log.error("Guest handler xato: %s", e, exc_info=True)


# Aiogram 3.27 `update.guest_message` ni bilmaydi → biz feed_update'ni
# o'rab olib, guest_message bo'lsa qo'lda boshqaramiz, qolganini originalga uzatamiz.
def _install_guest_hook():
    _orig = dp.feed_update

    async def _patched(target_bot, update, **kwargs):
        try:
            guest_msg = getattr(update, "guest_message", None)
            if guest_msg is None:
                extra = getattr(update, "model_extra", None) or {}
                guest_msg = extra.get("guest_message")
            if guest_msg:
                asyncio.create_task(_handle_guest_message(target_bot, guest_msg))
                return None
        except Exception as e:
            log.error("Guest hook detect xatosi: %s", e)
        return await _orig(target_bot, update, **kwargs)

    dp.feed_update = _patched


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

    history = await db.get_recent_messages(chat_id, 15)

    # Joriy kontekst — bot tool chaqirganida shu chat_id ni ishlatishi kerak.
    # Vaqt context ham shu yerga kiradi (system prompt'dan olingan, cache prefix barqaror bo'lishi uchun).
    is_private = chat_id > 0
    ctx = (
        build_time_prefix()
        + f'<current_context chat_id="{chat_id}" is_private="{str(is_private).lower()}"/>\n'
        + f'<chat_history>\n'
    )
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
    session_history = await db.get_session_history(chat_id, limit=8)
    # Agar session juda uzaygan bo'lsa — eskisini tozalash
    full_history = await db.get_session_history(chat_id, limit=50)
    if len(full_history) > 20:
        await db.clear_session(chat_id)
        # Faqat oxirgi 10 ta turnni saqlash
        for turn in full_history[-10:]:
            await db.save_session_turn(chat_id, turn["role"], turn["text"])
        session_history = await db.get_session_history(chat_id, limit=8)
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
                # Telegram caption limiti 1024 belgi. Matn uzunroq bo'lsa:
                # 1) Rasmni qisqa caption bilan jo'natamiz (yoki captionsiz)
                # 2) To'liq matnni alohida xabar qilib jo'natamiz
                CAPTION_LIMIT = 1024
                clean_reply = reply_text or ""
                if not clean_reply:
                    await bot.send_photo(chat_id, photo,
                                         reply_to_message_id=last_msg_id)
                elif len(clean_reply) <= CAPTION_LIMIT:
                    await bot.send_photo(chat_id, photo,
                                         reply_to_message_id=last_msg_id,
                                         caption=clean_reply,
                                         parse_mode="HTML")
                else:
                    # Rasm + to'liq matn — alohida xabar
                    photo_msg = await bot.send_photo(chat_id, photo,
                                                     reply_to_message_id=last_msg_id)
                    # Matnni rasm xabariga reply qilib jo'natamiz
                    full_text = markdown_to_html(clean_reply)
                    full_text = isolate_arabic(full_text)
                    full_text = force_rtl_blockquote(full_text)
                    full_text = expand_long_blockquotes(full_text)
                    for chunk in _split(full_text, 4000):
                        await _safe_send(bot, chat_id, chunk,
                                         reply_to=photo_msg.message_id)
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
            # Tool natijasidan keyin LLM ga qaytib javobni shakllantirishni so'raymiz.
            # Yakuniy harakat tool'lari (post yuborish, ban va h.k.) qaytadan
            # chain'lanmaydi — bir marta bajarilgan, tugagan.
            FINAL_ACTION_TOOLS = {
                "telegraf_post", "kanalga_post", "guruhga_yoz",
                "send_poll", "send_voice", "gen_image",
                "ban_user", "kick_user", "mute_user", "unban_user",
                "delete_message", "set_reminder", "save_lesson",
                "sv_restart", "sv_deploy", "sv_edit",
            }
            history = [
                {"role": "user", "text": messages[-1].get("text", "")},
                {"role": "model", "text": response},
                {"role": "user", "text": f"Tool natijasi: {result[:1500]}. Shu natijaga qarab foydalanuvchiga javob ber. Yana tool CHAQIRMA — bu yakuniy javob bo'lsin."},
            ]
            tool_response = await ai.chat(build_system_prompt(), history)

            # Chain'ni faqat 1 ta marta ruxsat etamiz va yakuniy harakat tool'larini chain'lamaymiz
            # (telegraf_post → telegraf_post — bekor takror, qimmat va xato).
            if (tool_call["name"] not in FINAL_ACTION_TOOLS) and tool_response:
                _, chain_tool = parse_response(tool_response)
                if chain_tool and chain_tool["name"] not in FINAL_ACTION_TOOLS \
                        and chain_tool["name"] != tool_call["name"]:
                    chain_result = await tools.execute(chain_tool)
                    log.info("Chained tool %s: %s", chain_tool["name"], chain_result[:100])
                    history.append({"role": "model", "text": tool_response})
                    history.append({
                        "role": "user",
                        "text": f"Tool natijasi: {chain_result[:1500]}. Endi YAKUNIY javob ber, yana tool chaqirma.",
                    })
                    tool_response = await ai.chat(build_system_prompt(), history)

            # Tool natijasidan keyin yaratilgan javob — eng yaxshi (formatlangan, kontekstli).
            # reply_text ko'pincha intro ("xo'p qilaman", "qaray-chi") — uni tashlaymiz.
            final_text = tool_response or reply_text or result
            if final_text and "[NO_ACTION]" not in final_text:
                final_text = strip_tool_blocks(final_text)
                final_text = re.sub(r"\[REACT:[^\]]+\]", "", final_text).strip()
                # Markdown qoldiqlarini HTML'ga aylantirish (**bold** → <b>bold</b>)
                final_text = markdown_to_html(final_text)
                # Bidi izolyatsiya: aralash arabcha+lotin matn Telegram'da to'g'ri ko'rinadi
                final_text = isolate_arabic(final_text)
                # Blockquote ichida arabcha bo'lsa boshiga RLM qo'shamiz — RTL base direction
                final_text = force_rtl_blockquote(final_text)
                # Uzun (>250 belgi) blockquote'larni 'expandable' qilamiz —
                # foydalanuvchi yig'ilgan ko'rinishda ko'radi, kerak bo'lsa kengaytadi
                final_text = expand_long_blockquotes(final_text)
                # RTL majburlash:
                # 1) Arab tili o'rganuvchilar guruhi — har doim RTL
                # 2) Mudarris guruhda sof arabcha xabar yozsa — RTL.
                #    Aralash (o'zbek+arab) bo'lsa — odatdagi qoida.
                _is_mudarris = "mudarris" in Config.BOT_NAME.lower()
                _force_rtl = (
                    chat_id == -1003280067467
                    or (_is_mudarris and chat_id < 0 and _is_mostly_arabic(final_text))
                )
                if _force_rtl:
                    final_text = "\u200F" + final_text.replace("\n", "\n\u200F")
                for chunk in _split(final_text, 4000):
                    await _safe_send(bot, chat_id, chunk,
                                     reply_to=messages[-1]["message_id"])
    elif reply_text:
        last_msg_id = messages[-1]["message_id"]
        reply_text = markdown_to_html(reply_text)
        reply_text = isolate_arabic(reply_text)
        reply_text = force_rtl_blockquote(reply_text)
        reply_text = expand_long_blockquotes(reply_text)
        _is_mudarris = "mudarris" in Config.BOT_NAME.lower()
        _force_rtl_reply = (
            chat_id == -1003280067467
            or (_is_mudarris and chat_id < 0 and _is_mostly_arabic(reply_text))
        )
        if _force_rtl_reply:
            reply_text = "‏" + reply_text.replace("\n", "\n‏")
        for chunk in _split(reply_text, 4000):
            await _safe_send(bot, chat_id, chunk, reply_to=last_msg_id)


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


# Telegram HTML rejimida ruxsat etilgan teglar (regex'da tekshiriladigan nomlar)
_TELEGRAM_ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "blockquote", "tg-spoiler", "tg-emoji", "span",
    "br",
}
_HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9\-]*)\b[^<>]*>")


def _sanitize_html(text: str) -> str:
    """Telegram tomonidan qo'llab-quvvatlanmaydigan teg'larni escape qilish.

    Format saqlanadi (<b>, <i>, <blockquote> va h.k.) — lekin `<h3>`, `<p>`,
    `<div>`, `<table>` kabi taqiqlangan teglar `&lt;tag&gt;` ga aylantiriladi.
    Bu bilan parse_mode=HTML xato bermay, valid format saqlanadi.
    """
    if not text:
        return text

    def _replace(m):
        tag = m.group(2).lower()
        if tag in _TELEGRAM_ALLOWED_TAGS:
            return m.group(0)  # asl tegni saqla
        # taqiqlangan teg — escape
        return m.group(0).replace("<", "&lt;").replace(">", "&gt;")

    return _HTML_TAG_RE.sub(_replace, text)


_HTML_STRIP_RE = re.compile(r"<[^>]+>")
# Sof arabcha tekshiruvi uchun: arabcha harflar oralig'i (Olima'da bor isolate_arabic ga o'xshash)
_ARABIC_LETTER_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_LATIN_CYR_LETTER_RE = re.compile(r"[A-Za-zЀ-ӿԀ-ԯ]")


def _is_mostly_arabic(text: str, threshold: float = 0.8) -> bool:
    """Matn asosan arabcha bo'lsa True qaytaradi (rtl majburlash uchun).

    HTML teglar, emoji, raqamlar va tinish belgilarini hisoblamaymiz —
    faqat alfavit harflarini. Agar arabcha harflar ulushi threshold dan oshsa
    matnni RTL ga majburlaymiz.
    """
    if not text:
        return False
    # HTML teglarni olib tashlaymiz
    plain = _HTML_STRIP_RE.sub("", text)
    arabic_count = len(_ARABIC_LETTER_RE.findall(plain))
    latin_count = len(_LATIN_CYR_LETTER_RE.findall(plain))
    total = arabic_count + latin_count
    if total < 5:  # juda qisqa matn — qoidaga moslashtirmaymiz
        return False
    return (arabic_count / total) >= threshold


def _strip_html_tags(text: str) -> str:
    """Oxirgi chora — barcha HTML teglarni olib tashlab plain text qaytaradi."""
    if not text:
        return text
    text = _HTML_STRIP_RE.sub("", text)
    return (text.replace("&lt;", "<").replace("&gt;", ">")
                .replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'"))


async def _safe_send(bot: Bot, chat_id: int, text: str,
                     reply_to: int | None = None) -> None:
    """HTML bilan yuborishga urinish — fail bo'lsa: 1) sanitatsiya, 2) plain text.

    Format imkon qadar saqlanadi. Faqat sanitatsiyadan keyin ham xato bo'lsa
    teglar olib tashlanadi va loglarga yoziladi.
    """
    try:
        await bot.send_message(chat_id, text,
                               reply_to_message_id=reply_to,
                               parse_mode="HTML")
        return
    except Exception as e:
        log.warning("HTML parse xato (%s) — sanitatsiya qilib qayta urinish", e)

    # 1-urinish: faqat noma'lum teglarni escape qilib qaytadan yuborish
    sanitized = _sanitize_html(text)
    if sanitized != text:
        try:
            await bot.send_message(chat_id, sanitized,
                                   reply_to_message_id=reply_to,
                                   parse_mode="HTML")
            return
        except Exception as e:
            log.warning("HTML hatto sanitatsiyadan keyin xato (%s) — plain text", e)

    # Oxirgi chora — plain text
    plain = _strip_html_tags(text)
    try:
        await bot.send_message(chat_id, plain, reply_to_message_id=reply_to)
    except Exception:
        await bot.send_message(chat_id, plain)


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

def _format_poll_as_text(poll: types.Poll) -> str:
    """Quiz/so'rovnomani matn ko'rinishida ifodalash."""
    kind = "Quiz" if poll.type == "quiz" else "So'rovnoma"
    lines = [f"[{kind}: {poll.question}]"]
    for i, opt in enumerate(poll.options):
        letter = chr(ord('A') + i) if i < 26 else str(i + 1)
        lines.append(f"{letter}) {opt.text}")
    if poll.is_closed and poll.correct_option_id is not None:
        ci = poll.correct_option_id
        if 0 <= ci < len(poll.options):
            correct_letter = chr(ord('A') + ci) if ci < 26 else str(ci + 1)
            lines.append(f"(To'g'ri javob: {correct_letter})")
    return "\n".join(lines)


@dp.message()
async def on_message(message: types.Message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id if user else 0
    username = (user.username or "") if user else ""
    first_name = (user.first_name or "Unknown") if user else "Unknown"
    text = message.text or message.caption or ""
    if not text and message.poll:
        text = _format_poll_as_text(message.poll)

    # Tashlandiq filter — text/media/poll bo'lmagan service xabarlarni o'tkazib yuborish
    if (not text and not message.photo and not message.voice
            and not message.audio and not message.video_note and not message.video
            and not message.animation and not message.document and not message.poll):
        log.debug("Service message skipped: chat=%s type=%s", chat_id, message.content_type)
        return

    # ── Olima pause/resume kodi (faqat Arab tili o'rganuvchilar guruhida) ──
    # Hasanxon (6350373395) yoki Aziza (5792080114) maxsus kodlarni yozsa,
    # Olima u guruhda butunlay jim turadi yoki qaytadan ishlay boshlaydi.
    # Boshqa guruhlar va DM tegmaydi.
    # Eslatma: Olima'da OWNER_ID=Aziza, VIP_IDS=Hasanxon — shu sababli is_vip ishlatamiz.
    PAUSE_GROUP = -1003280067467
    PAUSE_CODE = "20010212"
    RESUME_CODE = "12022001"
    PAUSE_AUTHORIZED = {6350373395, 5792080114}  # Hasanxon, Aziza
    if (chat_id == PAUSE_GROUP
            and "olima" in Config.BOT_NAME.lower()
            and user_id in PAUSE_AUTHORIZED):
        stripped = text.strip()
        if stripped == PAUSE_CODE:
            await db.mute_chat(chat_id, reason="manual pause")
            log.info("Olima Arab guruhda JIM (pause kodi)")
            try:
                await message.reply("🤫")
            except Exception:
                pass
            return
        if stripped == RESUME_CODE:
            await db.unmute_chat(chat_id)
            log.info("Olima Arab guruhda QAYTA ISHLAYDI (resume kodi)")
            try:
                await message.reply("🌸")
            except Exception:
                pass
            return

    # Agar Arab guruh muted bo'lsa — darrov chiqamiz (token, bandwidth tejaymiz).
    if (chat_id == PAUSE_GROUP
            and "olima" in Config.BOT_NAME.lower()
            and await db.is_muted(chat_id)):
        return

    if message.poll:
        log.info(
            "POLL ARRIVED: chat=%s type=%s question=%r options=%d closed=%s correct=%s",
            chat_id, message.poll.type, message.poll.question,
            len(message.poll.options), message.poll.is_closed, message.poll.correct_option_id,
        )

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

    # To'liq video fayl (uploaded MP4 yoki o'xshash). Gemini inline base64 chegarasi
    # ~20MB. Kattaroq fayllarni ham urinib ko'ramiz — Gemini File API bo'lsa AI engine
    # o'zi switch qiladi. Aks holda warning.
    if message.video:
        try:
            v = message.video
            size_mb = (v.file_size or 0) / (1024 * 1024) if v.file_size else 0
            if size_mb and size_mb > 20:
                log.warning("Video katta (%.1fMB) — inline yuklash uchun limit ~20MB; baribir urinaman", size_mb)
            data = await download_file(tg_bot, v.file_id)
            mime = v.mime_type or "video/mp4"
            media_list.append({"data": data, "mime": mime})
            if not text:
                text = "[Video yuborildi]"
        except Exception as e:
            log.error("Video yuklab olishda xato: %s", e)

    # Animatsiya (GIF) — Gemini ham ko'ra oladi
    if message.animation:
        try:
            data = await download_file(tg_bot, message.animation.file_id)
            mime = message.animation.mime_type or "video/mp4"
            media_list.append({"data": data, "mime": mime})
            if not text:
                text = "[GIF yuborildi]"
        except Exception as e:
            log.error("Animatsiya yuklab olishda xato: %s", e)

    # Document sifatida yuborilgan video/audio
    if message.document:
        d = message.document
        mime = d.mime_type or ""
        if mime.startswith(("video/", "audio/", "image/")):
            try:
                size_mb = (d.file_size or 0) / (1024 * 1024) if d.file_size else 0
                if size_mb > 20:
                    log.warning("Document media katta (%.1fMB) — urinaman", size_mb)
                data = await download_file(tg_bot, d.file_id)
                media_list.append({"data": data, "mime": mime})
                if not text:
                    text = f"[{mime} fayl yuborildi]"
            except Exception as e:
                log.error("Document media yuklab olishda xato: %s", e)

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
        if text == "/status" or text == "/stats":
            import time as _time
            s = ai.stats
            students = await db.list_students()
            # Uptime hisoblash
            uptime_sec = int(_time.time() - _bot_start_time)
            uptime_h = uptime_sec // 3600
            uptime_m = (uptime_sec % 3600) // 60
            # Model nomi
            model = Config.GEMINI_MODEL
            # Xabarlar soni
            try:
                cursor = await db._db.execute("SELECT COUNT(*) FROM messages")
                total_msgs = (await cursor.fetchone())[0]
            except Exception:
                total_msgs = "?"
            # Narx taxminiy (gemini-3.1-pro: ~$1.25/M input, ~$5/M output)
            tokens = s.total_tokens_approx
            cost_approx = tokens * 0.000003  # o'rtacha $3/M token
            await message.reply(
                f"<b>📊 {Config.BOT_NAME} — Status</b>\n\n"
                f"<b>🤖 Model:</b> <code>{model}</code>\n"
                f"<b>⏱ Uptime:</b> {uptime_h}s {uptime_m}d\n\n"
                f"<b>📈 So'rovlar:</b>\n"
                f"  ✅ Muvaffaqiyat: {s.successful}\n"
                f"  ❌ Xato: {s.errors}\n"
                f"  ⏳ Rate limit: {s.rate_limited}\n"
                f"  📊 Jami: {s.total_requests}\n\n"
                f"<b>⚡ Tezlik:</b> o'rtacha {s.avg_response_ms:.0f}ms\n"
                f"<b>📝 Tokenlar:</b> ~{tokens:,}\n"
                f"<b>💰 Taxminiy sarfj:</b> ~${cost_approx:.2f}\n\n"
                f"<b>👥 O'quvchilar:</b> {len(students)}\n"
                f"<b>💬 Jami xabarlar:</b> {total_msgs}\n\n"
                f"<b>🗄 Bazalar:</b>\n"
                f"  📖 Hadis: 9,059 ta\n"
                f"  📚 Kitoblar: 12 ta (8,464 parcha)\n"
                f"  📕 Lug'at: 97,000+ so'z",
                parse_mode="HTML",
            )
            return
        if text == "/reset":
            await db.clear_session(chat_id)
            await message.reply("🔄 Suhbat tarixi tozalandi. Yangi suhbat boshlanadi.")
            return
        if text == "/chatid" or text == "id sini ayt":
            info = f"Chat ID: <code>{chat_id}</code>"
            if message.reply_to_message:
                rm = message.reply_to_message
                if rm.forward_from_chat:
                    info += f"\nForward kanal: <code>{rm.forward_from_chat.id}</code> ({rm.forward_from_chat.title})"
                if rm.from_user:
                    info += f"\nUser ID: <code>{rm.from_user.id}</code> ({rm.from_user.first_name})"
            await message.reply(info, parse_mode="HTML")
            return

    # Botlar choyxonasi + free chat guruhlar — hamma xabarga javob beradi
    FREE_CHAT_GROUPS = {-1003436904722, -1003648834056, -1002401618185}  # choyxona + nodira + xonai saodat
    # Bot lab guruhlar — faqat botlar yozadi, loop limit yo'q
    BOT_LAB_GROUPS = {-1003648834056}  # bot_xona: faqat botlar, loop himoya kerak emas
    is_bot = user.is_bot if user else False
    bot_me = await tg_bot.me()

    if is_bot:
        bot_username = bot_me.username or ""
        mentioned = f"@{bot_username}".lower() in text.lower() if bot_username else False
        replied_to_me = False
        if message.reply_to_message and message.reply_to_message.from_user:
            replied_to_me = message.reply_to_message.from_user.id == bot_me.id

        if chat_id in BOT_LAB_GROUPS:
            # Bot xona: 2 bot o'zaro suhbat qilayotgan bo'lsa — aralashma
            # Oxirgi 4 xabar aynan 2 xil botdan galma-gal kelayotgan bo'lsa → chiqib ket
            recent = await db.get_recent_messages(chat_id, 4)
            if len(recent) >= 3:
                recent_senders = [m.get("username", "") for m in recent if m.get("username", "").endswith("bot")]
                unique_bots = set(recent_senders)
                if len(unique_bots) == 2 and len(recent_senders) >= 3 and not mentioned and not replied_to_me:
                    log.info("Bot lab: 2 bot suhbati (%s), aralashilmadi", unique_bots)
                    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    await db.save_message(chat_id, message.message_id, user_id, username, first_name, text, None, ts)
                    return
            # Aks holda: AIga uzatiladi, AI prompt orqali qo'shilish/qo'shilmaslikni hal qiladi
        elif chat_id not in FREE_CHAT_GROUPS:
            # Tashqi guruhlarda bot-to-bot loop himoyasi
            if not mentioned and not replied_to_me:
                log.info("Bot xabar (loop himoya): %s dan, o'tkazildi", first_name)
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                await db.save_message(chat_id, message.message_id, user_id, username, first_name, text, None, ts)
                return
        else:
            # Choyxona/xonai saodat: loop limiti — 12 xabardan 10 tasi bot bo'lsagina to'xtatiladi
            recent = await db.get_recent_messages(chat_id, 12)
            bot_msgs = sum(1 for m in recent if m.get("user_id", 0) != Config.OWNER_ID and m.get("username", "").endswith("bot"))
            if bot_msgs >= 10:
                log.info("Choyxona loop limiti: %d bot xabar, to'xtatildi", bot_msgs)
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

        # Mention-only guruhlar — bot faqat @mention, ism aytilganda yoki
        # uning xabariga reply qilinganda javob beradi. Boshqa xabarlarni
        # kontekst uchun saqlab qo'yamiz, lekin AI ga uzatmaymiz.
        MENTION_ONLY_GROUPS: set[int] = set()  # -1003280067467 → Arab guruh, pastda alohida logika
        if chat_id in MENTION_ONLY_GROUPS:
            bot_username = (bot_me.username or "").lower()
            bot_name_low = Config.BOT_NAME.lower()
            text_lower = text.lower()
            mentioned = bool(bot_username) and f"@{bot_username}" in text_lower
            name_referenced = bool(bot_name_low) and re.search(
                rf"\b{re.escape(bot_name_low)}\b", text_lower
            ) is not None
            replied_to_me = bool(
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id == bot_me.id
            )
            if not (mentioned or name_referenced or replied_to_me):
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                await db.save_message(
                    chat_id, message.message_id, user_id,
                    username, first_name, text, None, ts,
                )
                return

        # Smart-filter guruhlar (faol, ko'p a'zoli):
        # — Mention/reply/ism aytilsa → AI
        # — Owner yoki Aziza yozsa → AI
        # — Savol patterni bor (?, ؟, "qanday", "ما", va h.k.) → AI
        # — Ikki user reply-zanjirida gaplashayotgan bo'lsa → JIM
        # — Aks holda kontekst uchun saqla, AI ga uzatma
        # -1003280067467 → Olima (arab tili o'rganuvchilar)
        # -1003831509848 → Mudarris (arab ana tili guruhi)
        ARAB_LEARNER_GROUP = {-1003280067467, -1003831509848}
        if chat_id in ARAB_LEARNER_GROUP:
            bot_username = (bot_me.username or "").lower()
            bot_name_low = Config.BOT_NAME.lower()
            text_lower = text.lower()
            mentioned = bool(bot_username) and f"@{bot_username}" in text_lower
            name_referenced = bool(bot_name_low) and re.search(
                rf"\b{re.escape(bot_name_low)}\b", text_lower
            ) is not None
            replied_to_me = bool(
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id == bot_me.id
            )
            is_owner_msg = Config.is_owner(user_id)
            is_aziza_msg = user_id == 5792080114

            # 2 user reply zanjiri tekshiruvi
            in_two_user_convo = False
            if (
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id not in (bot_me.id, user_id)
            ):
                replied_uid = message.reply_to_message.from_user.id
                try:
                    recent = await db.get_recent_messages(chat_id, 6)
                    non_bot_uids = {
                        m.get("user_id") for m in recent
                        if m.get("user_id") and m.get("user_id") != bot_me.id
                    }
                    non_bot_uids.add(user_id)
                    if non_bot_uids <= {user_id, replied_uid}:
                        in_two_user_convo = True
                except Exception:
                    pass

            QUESTION_RE = re.compile(
                r"(\?|؟|\bqanday\b|\bnima\b|\bqaerda\b|\bkim\b|\bqachon\b|\bnega\b|"
                r"\bqaysi\b|\bnechta\b|\bma\b|\bكيف\b|\bأين\b|\bمن\b|\bمتى\b|"
                r"\bلماذا\b|\bأي\b|\byordam\b|\btushuntir\b|\bimdod\b|"
                r"\biltimos\b|\bsharhlab\b|\bma'no\b|\btarjima\b)",
                re.IGNORECASE,
            )
            is_question = QUESTION_RE.search(text or "") is not None

            # Arabcha to'g'ridan-to'g'ri murojaat naqshlari (botga qarab)
            if chat_id == -1003280067467:
                # Olima — ayol shaklidagi murojaatlar
                ARABIC_ADDRESS_RE = re.compile(
                    r"(يا\s*عالمة|أيتها\s*العالمة|يا\s*أستاذة|يا\s*معلمة|"
                    r"يا\s*عَالِمَة|أيتها\s*المعلمة|يا\s*أم)",
                )
            else:
                # Mudarris (-1003831509848) — erkak shaklidagi murojaatlar
                ARABIC_ADDRESS_RE = re.compile(
                    r"(يا\s*أستاذ|يا\s*شيخ|يا\s*مدرس|يا\s*معلم|"
                    r"يا\s*أخ|أيها\s*الأستاذ|أيها\s*الشيخ|أيها\s*المعلم)",
                )
            arabic_addressed = ARABIC_ADDRESS_RE.search(text or "") is not None
            if arabic_addressed:
                name_referenced = True

            should_respond = (
                (mentioned or name_referenced or replied_to_me or is_owner_msg or is_aziza_msg or is_question)
                and not in_two_user_convo
            )
            log.info(
                "Arab guruh xabar (%s): mention=%s reply_me=%s name=%s aziza=%s owner=%s "
                "question=%s 2user_convo=%s → %s | text=%r",
                username or first_name, mentioned, replied_to_me, name_referenced,
                is_aziza_msg, is_owner_msg, is_question, in_two_user_convo,
                "AI" if should_respond else "SKIP", (text or "")[:80],
            )
            if not should_respond:
                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                await db.save_message(
                    chat_id, message.message_id, user_id,
                    username, first_name, text, None, ts,
                )
                return

    # Quiz/poll: guruhda avtomatik sharhlash YO'Q.
    # Faqat: bot @mention qilingan, bot xabariga reply yoki owner so'rasagina javob beradi.
    # Aks holda kontekst uchun saqlab, jim turamiz.
    if message.poll and chat_id < 0:
        bot_username_l = (bot_me.username or "").lower()
        poll_text_lower = text.lower()
        mentioned_in_poll = bool(bot_username_l) and f"@{bot_username_l}" in poll_text_lower
        replied_to_me_poll = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == bot_me.id
        )
        if not (mentioned_in_poll or replied_to_me_poll or Config.is_owner(user_id)):
            log.info("Poll/quiz guruhda (%s) — sharhlanmaydi, kontekstga saqlandi", chat_id)
            ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            await db.save_message(
                chat_id, message.message_id, user_id,
                username, first_name, text, None, ts,
            )
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
        if not reply_text and rm.poll:
            reply_text = _format_poll_as_text(rm.poll)
        reply_user = rm.from_user.first_name if rm.from_user else "Kanal"
        if rm.sender_chat:  # Kanal post
            reply_user = rm.sender_chat.title or "Kanal"
        if reply_text:
            reply_context = f"\n[Reply: {reply_user}: {reply_text[:800]}]"

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

_REPEAT_DELTAS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def _next_trigger(trigger_at: str, repeat: str) -> str | None:
    """Takror reminder uchun keyingi vaqtni hisoblaydi. Topilmasa None."""
    delta = _REPEAT_DELTAS.get(repeat.lower().strip())
    if not delta:
        return None
    try:
        dt = datetime.strptime(trigger_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    nxt = dt + delta
    # Agar kelajak vaqt bo'lguncha bir necha siklni o'tib ketgan bo'lsa, hozirgi vaqtdan oldinga olib chiqamiz
    now = datetime.utcnow()
    while nxt <= now:
        nxt += delta
    return nxt.strftime("%Y-%m-%d %H:%M:%S")


async def reminder_loop(bot: Bot):
    while True:
        await asyncio.sleep(30)
        try:
            reminders = await db.get_due_reminders()
            for r in reminders:
                try:
                    await bot.send_message(r["chat_id"], f"⏰🤲 Eslatma: {r['text']}")
                except Exception as e:
                    log.error("Reminder yuborishda xato (id=%s): %s", r["id"], e)

                repeat = r.get("repeat")
                if repeat:
                    nxt = _next_trigger(r["trigger_at"], repeat)
                    if nxt:
                        await db.reschedule_reminder(r["id"], nxt)
                        log.info("Reminder #%s takrorlandi → %s (%s)", r["id"], nxt, repeat)
                        continue
                    log.warning("Reminder #%s repeat='%s' tushunilmadi, complete qilindi", r["id"], repeat)
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

    ai = GeminiEngine(
        Config.GEMINI_API_KEYS,
        Config.GEMINI_MODEL,
        Config.FALLBACK_MODEL,
        vertex_project=Config.VERTEX_PROJECT,
        vertex_region=Config.VERTEX_REGION,
        vertex_key_path=Config.VERTEX_KEY_PATH,
    )
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

    # Namoz eslatma — faqat MudarrisAI uchun
    if "mudarris" in Config.BOT_NAME.lower() or "shaxsiy" in Config.BOT_NAME.lower():
        from bot.namoz import namoz_scheduler
        namoz_chats = [
            -1003436904722,   # Botlar choyxonasi
            -1002082269999,   # MudarrisAI guruh
            -1003742825203,   # Mudarris Blog Chat (discussion)
        ]
        asyncio.create_task(namoz_scheduler(bot, namoz_chats))
        log.info("Namoz scheduler ishga tushdi: %s", namoz_chats)

    # Inner voice o'chirildi — bot guruhlarda o'z-o'zidan xabar yubormaydi
    # (avval: choyxona va Xonai Saodat'da soatda bir o'z-o'ziga xabar yozardi)

    # Health monitor o'chirilgan — owner so'ramagan

    # Olima — ertalab Azizaxonga kunlik salom
    if "olima" in Config.BOT_NAME.lower():
        asyncio.create_task(_olima_morning_loop(bot, ai))

    # Guest Mode hook — aiogram 3.27 `update.guest_message`'ni bilmaydi,
    # biz feed_update'ni o'rab olib qo'lda handle qilamiz.
    _install_guest_hook()

    # allowed_updates'ni aniq beramiz — eski webhook config'idan qolgan ["message", "channel_post"]
    # ni overwrite qiladi. "guest_message" — Bot API 10.0 (2026-05-08) yangiligi.
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "guest_message",
        ],
    )


async def _olima_morning_loop(bot, ai):
    """Olima har kuni soat 6:00 da Azizaxonga iliq xabar yuboradi."""
    import random
    from datetime import datetime, timedelta, timezone

    AZIZAXON_ID = 5792080114
    TZ_UZ = timezone(timedelta(hours=5))

    # Sog'liq holati — 2-3 kun bemor (oy ko'rgan)
    health_days_left = 3  # birinchi 3 kunda sog'liq so'rash

    log.info("Olima morning scheduler ishga tushdi")

    while True:
        try:
            now = datetime.now(TZ_UZ)
            # Ertangi soat 6:00 ni hisoblash
            target = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_sec = (target - now).total_seconds()
            log.info("Olima sabah xabari: %.0f soniyadan keyin (%s)", wait_sec, target.strftime("%d.%m %H:%M"))
            await asyncio.sleep(wait_sec)

            # Sabah xabar matni
            if health_days_left > 0:
                prompt = (
                    "Sen Olima. Azizaxon hozir biroz kasal — oy ko'rgan, dam olayapti. "
                    "Unga iliq, mehribon ertalabki xabar yoz. Uning holini so'ra, dam olishni maslahat ber, "
                    "issiq choy, asal kabi uy chorasini eslatib qo'y. "
                    "Ba'zan forscha yoki o'zbekcha bitta bayt qo'sh — o'ringa qarab. "
                    "Azizaxonga 'siz' deb murojaat qil. Qisqa, iliq, samimiy. 3-5 jumla."
                )
                health_days_left -= 1
            else:
                greetings = [
                    "bugungi reja, bugungi niyat, kayfiyat haqida so'ra",
                    "tush ko'rdimi, yaxshi uxladimi so'ra",
                    "bugun nima pishirmoqchi, qanday kun o'tkazmoqchi so'ra",
                    "ertalabki tilak yubor, kuni xayrli bo'lsin de",
                    "forscha bitta bayt bilan ertalabki salom",
                    "bugun nimalar rejada, qanday his bilan uyg'ondi so'ra",
                    "ertalabki iliq fikr — tabiat, hayot, mehr haqida bitta jumla",
                ]
                theme = random.choice(greetings)
                prompt = (
                    f"Sen Olima. Azizaxonga ertalabki iliq xabar yoz. Mavzu: {theme}. "
                    "Har kuni farq qilsin — bugun boshqacha yoz. "
                    "Ba'zan forscha/o'zbekcha bayt qo'sh (har doim emas). "
                    "Azizaxonga 'siz' deb murojaat qil. Qisqa, samimiy, issiq. 2-4 jumla."
                )

            response = await ai.chat(
                build_system_prompt(),
                [{"role": "user", "text": prompt}],
            )

            if response and len(response.strip()) > 10:
                clean = strip_tool_blocks(response)
                clean = re.sub(r"\[REACT:[^\]]+\]", "", clean).strip()
                clean = re.sub(r"\[NO_ACTION\]", "", clean).strip()
                if clean:
                    try:
                        await bot.send_message(AZIZAXON_ID, clean, parse_mode="HTML")
                        log.info("Olima sabah xabari yuborildi")
                    except Exception:
                        await bot.send_message(AZIZAXON_ID, clean)

        except Exception as e:
            log.error("Olima morning loop xatosi: %s", e)
            await asyncio.sleep(3600)
