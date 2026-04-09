import base64
import json
import logging
import re

import aiohttp

from bot.db import Database
from bot.memory import MemoryStore
from bot.tools.lugat import Lugat
from bot.tools.kitob import KitobRAG
from bot.tools.hadis_rag import HadisRAG
from bot.tools.islamic_api import IslamicAPI

log = logging.getLogger(__name__)


def parse_response(text: str) -> tuple[str, dict | None]:
    """
    Bot javobini parse qiladi.
    [TOOL:name]{"param": "value"} → tool call
    [NO_ACTION] → hech narsa
    oddiy matn → reply
    """
    m = re.search(r"\[TOOL:(\w+)\](\{.*\})", text, re.DOTALL)
    if m:
        tool_name = m.group(1)
        try:
            params = json.loads(m.group(2))
            # Tool dan keyingi matnni ham qaytarish
            remaining = text[:m.start()].strip() + text[m.end():].strip()
            remaining = re.sub(r"\[REACT:[^\]]+\]", "", remaining).strip()
            return remaining, {"name": tool_name, "params": params}
        except json.JSONDecodeError:
            pass

    if text.strip() == "[NO_ACTION]":
        return "", None

    return text.strip(), None


class ToolHandler:
    def __init__(self, db: Database, memory: MemoryStore):
        self.db = db
        self.memory = memory
        self._stats: dict[str, dict] = {}  # tool_name → {calls, errors, avg_ms}
        # Lug'at bazasi
        from bot.config import Config
        lugat_path = Config.DATA_DIR / "universal_lugat.db"
        self.lugat = Lugat(lugat_path) if lugat_path.exists() else None
        kitob_path = Config.DATA_DIR / "kitoblar.db"
        self.kitob = KitobRAG(kitob_path)
        hadis_path = Config.DATA_DIR / "hadislar.db"
        self.hadis_rag = HadisRAG(hadis_path)
        self.islamic = IslamicAPI()

    async def execute(self, tool: dict) -> str:
        """SDK pattern: pre-validation → execute → post-logging."""
        import time
        name = tool["name"]
        params = tool.get("params", {})

        # Pre-hook: parametr validatsiya
        if not isinstance(params, dict):
            return f"Xato: params dict bo'lishi kerak, {type(params).__name__} berildi"

        start = time.time()
        log.info("Tool chaqirildi: %s(%s)", name, str(params)[:100])
        try:
            result = await self._dispatch(name, params)
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            log.error("Tool %s xatosi (%.0fms): %s", name, duration_ms, e)
            self._record_stat(name, duration_ms, error=True)
            return f"Xato: {e}"

        # Post-hook: statistika
        duration_ms = (time.time() - start) * 1000
        log.info("Tool %s bajarildi: %.0fms", name, duration_ms)
        self._record_stat(name, duration_ms)
        return result

    async def _dispatch(self, name: str, params: dict) -> str:
        match name:
            case "search_messages":
                return await self._search_messages(params)
            case "create_memory":
                return self._create_memory(params)
            case "read_memory":
                return self._read_memory(params)
            case "list_memories":
                return self._list_memories()
            case "search_memories":
                return self._search_memories(params)
            case "set_reminder":
                return await self._set_reminder(params)
            case "get_chat_history":
                return await self._get_history(params)
            case "get_student":
                return await self._get_student(params)
            case "save_lesson":
                return await self._save_lesson(params)
            case "list_students":
                return await self._list_students()
            case "student_history":
                return await self._student_history(params)
            case "add_note":
                return await self._add_note(params)
            case "get_notes":
                return await self._get_notes(params)
            case "update_student":
                return await self._update_student(params)
            case "gen_image":
                return await self._gen_image(params)
            case "mute_chat":
                return await self._mute_chat(params)
            case "unmute_chat":
                return await self._unmute_chat(params)
            case "send_voice":
                return await self._send_voice(params)
            case "lugat":
                return self._lugat_search(params)
            case "kitob_qidirish":
                return self._kitob_search(params)
            case "list_kitoblar":
                return self._list_kitoblar()
            case "guruhga_yoz":
                return await self._guruhga_yoz(params)
            case "hadis":
                return await self._hadis(params)
            case "hadis_kitoblar":
                return self.hadis_rag.list_books()
            case "tasodifiy_hadis":
                return self.hadis_rag.get_random()
            case "quron":
                return await self._quron(params)
            case "query":
                return await self._query(params)
            case "send_poll":
                return await self._send_poll(params)
            case "ban_user":
                return await self._ban_user(params)
            case "mute_user":
                return await self._mute_user(params)
            case "kick_user":
                return await self._kick_user(params)
            case "unban_user":
                return await self._unban_user(params)
            case "delete_message":
                return await self._delete_message(params)
            case "get_chat_admins":
                return await self._get_chat_admins(params)
            case _:
                return f"Noma'lum tool: {name}"

    def _record_stat(self, name: str, duration_ms: float, error: bool = False):
        if name not in self._stats:
            self._stats[name] = {"calls": 0, "errors": 0, "total_ms": 0}
        self._stats[name]["calls"] += 1
        self._stats[name]["total_ms"] += duration_ms
        if error:
            self._stats[name]["errors"] += 1

    # ── Xabar toollar ─────────────────────────────────────────
    async def _search_messages(self, p: dict) -> str:
        msgs = await self.db.search_messages(
            p.get("chat_id", 0), p.get("query", ""), p.get("limit", 20)
        )
        if not msgs:
            return "Natija topilmadi"
        lines = [f"[{m['timestamp']}] {m['username']}: {m['text']}" for m in msgs]
        return "\n".join(lines)

    def _create_memory(self, p: dict) -> str:
        self.memory.create(p.get("name", "unnamed.md"), p.get("content", ""))
        return f"Xotira saqlandi: {p.get('name')}"

    def _read_memory(self, p: dict) -> str:
        return self.memory.read(p.get("name", ""))

    def _list_memories(self) -> str:
        files = self.memory.list_all()
        return "\n".join(files) if files else "Xotiralar bo'sh"

    def _search_memories(self, p: dict) -> str:
        results = self.memory.search(p.get("query", ""))
        if not results:
            return "Natija topilmadi"
        return "\n---\n".join(f"{name}\n{preview}" for name, preview in results)

    async def _set_reminder(self, p: dict) -> str:
        rid = await self.db.save_reminder(
            p.get("chat_id", 0), p.get("user_id", 0),
            p.get("text", ""), p.get("trigger_at", ""),
        )
        return f"Eslatma #{rid} saqlandi: {p.get('trigger_at')} da"

    async def _get_history(self, p: dict) -> str:
        msgs = await self.db.get_recent_messages(
            p.get("chat_id", 0), p.get("limit", 50)
        )
        lines = [
            f'<msg id="{m["message_id"]}" user="{m["username"]}" time="{m["timestamp"]}">'
            f'{m["text"]}</msg>'
            for m in msgs
        ]
        return "\n".join(lines)

    # ── O'quvchi toollar ──────────────────────────────────────
    async def _get_student(self, p: dict) -> str:
        user_id = p.get("user_id", 0)
        student = await self.db.get_student(user_id)
        if not student:
            return f"O'quvchi topilmadi (ID: {user_id})"
        return (
            f"📖 {student['first_name']}\n"
            f"📊 Daraja: {student['level']}\n"
            f"📕 Hozirgi sura: {student['current_sura'] or 'belgilanmagan'}\n"
            f"✅ Jami darslar: {student['total_lessons']}\n"
            f"⭐ O'rtacha baho: {student['avg_score']}/10\n"
            f"🕐 Oxirgi dars: {student['last_lesson_date'] or 'hali topshirmagan'}\n"
            f"📝 Eslatma: {student['notes'] or 'yo''q'}"
        )

    async def _save_lesson(self, p: dict) -> str:
        user_id = p.get("user_id", 0)
        # Avval o'quvchini yaratish/olish
        await self.db.get_or_create_student(
            user_id, p.get("first_name", ""), p.get("username", "")
        )
        lid = await self.db.save_lesson(
            user_id=user_id,
            chat_id=p.get("chat_id", 0),
            sura=p.get("sura", ""),
            ayah_range=p.get("ayah_range", ""),
            score=p.get("score", 0),
            feedback=p.get("feedback", ""),
        )
        return f"✅ Dars #{lid} saqlandi! Baho: {p.get('score', 0)}/10"

    async def _list_students(self) -> str:
        students = await self.db.list_students()
        if not students:
            return "Hali o'quvchilar yo'q"
        lines = []
        for s in students:
            lines.append(
                f"👤 {s['first_name']} (@{s['username'] or '?'}) — "
                f"{s['level']}, {s['total_lessons']} dars, "
                f"⭐{s['avg_score']}/10"
            )
        return "\n".join(lines)

    async def _student_history(self, p: dict) -> str:
        user_id = p.get("user_id", 0)
        lessons = await self.db.get_student_lessons(user_id, p.get("limit", 10))
        if not lessons:
            return "Bu o'quvchining darslari topilmadi"
        lines = []
        for l in lessons:
            lines.append(
                f"📕 {l['sura']} ({l['ayah_range']}) — "
                f"⭐{l['score']}/10 — {l['submitted_at']}\n"
                f"   💬 {l['feedback']}"
            )
        return "\n".join(lines)

    async def _add_note(self, p: dict) -> str:
        user_id = p.get("user_id", 0)
        note = p.get("note", "")
        await self.db.add_student_note(user_id, note)
        return f"📝 Eslatma saqlandi"

    async def _get_notes(self, p: dict) -> str:
        user_id = p.get("user_id", 0)
        notes = await self.db.get_student_notes(user_id)
        if not notes:
            return "Bu o'quvchi haqida eslatmalar yo'q"
        lines = [f"[{n['created_at']}] {n['note']}" for n in notes]
        return "\n".join(lines)

    async def _update_student(self, p: dict) -> str:
        user_id = p.pop("user_id", 0)
        if not user_id:
            return "user_id kerak"
        allowed = {"level", "current_sura", "notes", "completed_suras"}
        fields = {k: v for k, v in p.items() if k in allowed}
        if not fields:
            return "Yangilanadigan maydon yo'q"
        await self.db.update_student(user_id, **fields)
        return f"✅ O'quvchi yangilandi: {', '.join(fields.keys())}"

    async def _gen_image(self, p: dict) -> str:
        """Gemini Imagen orqali rasm yaratish. Natija: base64 rasm."""
        prompt = p.get("prompt", "")
        if not prompt:
            return "Rasm uchun tavsif kerak"

        from bot.config import Config
        if not Config.GEMINI_API_KEYS:
            return "API kalit yo'q"

        # Gemini image generation — generateContent bilan
        model = "gemini-2.5-flash-image"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.GEMINI_API_KEYS[0]}"
        body = {
            "contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    data = await resp.json()

            log.info("gen_image API javob: %s", str(data)[:500])

            if "candidates" in data:
                candidate = data["candidates"][0]
                # Xavfsizlik filtri tekshiruvi
                if candidate.get("finishReason") == "IMAGE_SAFETY":
                    return "Rasm yaratib bo'lmadi — xavfsizlik filtri"
                parts = candidate.get("content", {}).get("parts", [])
                for part in parts:
                    if "inlineData" in part:
                        img_b64 = part["inlineData"]["data"]
                        mime = part["inlineData"].get("mimeType", "image/png")
                        return f"IMAGE:{mime}:{img_b64}"
                # Rasm yo'q — faqat matn qaytgan
                text_parts = [p.get("text", "") for p in parts if "text" in p]
                if text_parts:
                    return f"Rasm yaratilmadi. AI javobi: {' '.join(text_parts)[:200]}"

            error = data.get("error", {}).get("message", "")
            log.error("gen_image xato: %s | to'liq: %s", error, str(data)[:300])
            return f"Rasm yaratishda xato: {error or 'API javob bermadi'}"
        except Exception as e:
            log.error("gen_image xatosi: %s", e)
            return f"Rasm yaratishda xato: {e}"

    def _lugat_search(self, p: dict) -> str:
        """Arab-O'zbek lug'atdan qidirish."""
        if not self.lugat:
            return "Lug'at bazasi mavjud emas"
        query = p.get("query", "")
        if not query:
            return "Qidiruv so'zi kerak"
        return self.lugat.search(query, limit=p.get("limit", 5))

    def _kitob_search(self, p: dict) -> str:
        """Kitob bazasidan qidirish (RAG)."""
        query = p.get("query", "")
        if not query:
            return "Qidiruv so'zi kerak"
        return self.kitob.search(query, limit=p.get("limit", 5))

    def _list_kitoblar(self) -> str:
        """Barcha indekslangan kitoblar ro'yxati."""
        return self.kitob.list_books()

    async def _hadis(self, p: dict) -> str:
        """Hadis qidirish — avval lokal RAG, keyin API."""
        query = p.get("query", "")
        hid = p.get("id")

        if hid:
            return await self.islamic.get_hadith_by_id(str(hid))

        if not query:
            # Tasodifiy hadis
            random_h = self.hadis_rag.get_random()
            if random_h:
                return random_h
            return "Hadis qidirish uchun 'query' yoki 'id' kerak"

        # Avval lokal bazadan qidirish (hadis.islom.uz)
        local = self.hadis_rag.search(query, limit=p.get("limit", 3))
        if local:
            return local

        # Lokal topilmasa — API dan qidirish
        return await self.islamic.search_hadith(query, limit=p.get("limit", 3))

    async def _quron(self, p: dict) -> str:
        """Qur'on oyati olish."""
        sura = p.get("sura", 0)
        if not sura:
            return "Sura raqami kerak"
        ayah = p.get("ayah", 0)
        return await self.islamic.get_ayah(sura, ayah)

    async def _mute_chat(self, p: dict) -> str:
        chat_id = p.get("chat_id", 0)
        duration_min = p.get("duration_min", 60)
        from datetime import datetime, timedelta
        until = (datetime.utcnow() + timedelta(minutes=duration_min)).strftime("%Y-%m-%d %H:%M:%S")
        await self.db.mute_chat(chat_id, until, p.get("reason", ""))
        return f"🔇 Chat {duration_min} daqiqaga o'chirildi"

    async def _guruhga_yoz(self, p: dict) -> str:
        """Guruhga xabar yuborish (owner buyrug'i)."""
        chat_id = p.get("chat_id", 0)
        text = p.get("text", "")
        if not chat_id or not text:
            return "chat_id va text kerak"
        # _bot ni dispatcher o'rnatadi
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        try:
            from aiogram.enums import ParseMode
            await self._bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            return f"Guruhga yuborildi"
        except Exception as e:
            return f"Yuborishda xato: {e}"

    async def _unmute_chat(self, p: dict) -> str:
        chat_id = p.get("chat_id", 0)
        await self.db.unmute_chat(chat_id)
        return f"🔊 Chat qayta yoqildi"

    async def _send_voice(self, p: dict) -> str:
        """Gemini TTS modeli orqali ovozli xabar yaratish."""
        text = p.get("text", "")
        lang = p.get("lang", "uz")
        if not text:
            return "Matn kerak"

        from bot.config import Config
        if not Config.GEMINI_API_KEYS:
            return "API kalit yo'q"

        # Til kodini to'liq nomga aylantirish
        lang_map = {
            "uz": "Uzbek", "ar": "Arabic", "en": "English",
            "tr": "Turkish", "fa": "Persian", "ja": "Japanese",
            "ko": "Korean", "zh": "Chinese", "ru": "Russian",
        }
        lang_name = lang_map.get(lang, lang)

        try:
            # Gemini TTS — maxsus TTS modeli
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={Config.GEMINI_API_KEYS[0]}"
            body = {
                "contents": [{"parts": [{"text": f"{text[:500]}"}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Kore"
                            }
                        }
                    }
                },
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    data = await resp.json()

            log.info("TTS API javob: %s", str(data)[:300])

            if "candidates" in data:
                for part in data["candidates"][0]["content"]["parts"]:
                    if "inlineData" in part:
                        raw_b64 = part["inlineData"]["data"]
                        pcm_data = base64.b64decode(raw_b64)
                        # PCM → WAV konvertatsiya (Telegram uchun)
                        import struct, io
                        wav_buf = io.BytesIO()
                        # WAV header yozish
                        num_channels = 1
                        sample_rate = 24000
                        bits_per_sample = 16
                        data_size = len(pcm_data)
                        wav_buf.write(b'RIFF')
                        wav_buf.write(struct.pack('<I', 36 + data_size))
                        wav_buf.write(b'WAVE')
                        wav_buf.write(b'fmt ')
                        wav_buf.write(struct.pack('<IHHIIHH', 16, 1, num_channels, sample_rate,
                                                  sample_rate * num_channels * bits_per_sample // 8,
                                                  num_channels * bits_per_sample // 8, bits_per_sample))
                        wav_buf.write(b'data')
                        wav_buf.write(struct.pack('<I', data_size))
                        wav_buf.write(pcm_data)
                        wav_bytes = wav_buf.getvalue()
                        return f"VOICE:{base64.b64encode(wav_bytes).decode()}"

            # Fallback — Google Translate TTS
            log.warning("Gemini TTS ishlamadi, Google Translate fallback")
            from urllib.parse import quote
            encoded = quote(text[:200])
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://translate.google.com/",
            }
            tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={lang}&client=tw-ob&ttsspeed=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(tts_url, headers=headers) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        return f"VOICE:{base64.b64encode(audio_data).decode()}"

            error = data.get("error", {}).get("message", "Noma'lum xato")
            return f"TTS xatosi: {error}"
        except Exception as e:
            log.error("TTS xatosi: %s", e)
            return f"TTS xatosi: {e}"

    # ── Yangi toollar (Claudir pattern) ──────────────────────────

    def _is_owner(self, user_id: int) -> bool:
        from bot.config import Config
        return Config.is_owner(user_id)

    async def _query(self, p: dict) -> str:
        """Owner uchun read-only SQL so'rov."""
        sql = p.get("sql", "").strip()
        if not sql:
            return "SQL so'rov kerak"
        # Faqat SELECT ruxsat
        if not sql.upper().startswith("SELECT"):
            return "Faqat SELECT so'rovlar ruxsat berilgan"
        try:
            cursor = await self.db._db.execute(sql)
            rows = await cursor.fetchall()
            if not rows:
                return "Natija bo'sh"
            cols = [d[0] for d in cursor.description]
            lines = [" | ".join(cols)]
            lines.append("-" * len(lines[0]))
            for row in rows[:20]:  # Max 20 qator
                lines.append(" | ".join(str(v) for v in row))
            if len(rows) > 20:
                lines.append(f"... (jami {len(rows)} qator)")
            return "\n".join(lines)
        except Exception as e:
            return f"SQL xato: {e}"

    async def _send_poll(self, p: dict) -> str:
        """Guruhga so'rovnoma yuborish."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        question = p.get("question", "")
        options = p.get("options", [])
        if not chat_id or not question or len(options) < 2:
            return "chat_id, question va kamida 2 ta option kerak"
        try:
            from aiogram.types import InputPollOption
            poll_options = [InputPollOption(text=o) for o in options[:10]]
            await self._bot.send_poll(
                chat_id, question=question, options=poll_options,
                is_anonymous=p.get("anonymous", True),
            )
            return "So'rovnoma yuborildi"
        except Exception as e:
            return f"So'rovnoma xatosi: {e}"

    async def _ban_user(self, p: dict) -> str:
        """Foydalanuvchini guruhdan ban qilish."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        user_id = p.get("user_id", 0)
        if not chat_id or not user_id:
            return "chat_id va user_id kerak"
        # Owner himoyasi
        if self._is_owner(user_id):
            return "Owner ni ban qilib bo'lmaydi"
        try:
            await self._bot.ban_chat_member(chat_id, user_id)
            return f"Foydalanuvchi {user_id} ban qilindi"
        except Exception as e:
            return f"Ban xatosi: {e}"

    async def _mute_user(self, p: dict) -> str:
        """Foydalanuvchini guruhda ovozini o'chirish."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        user_id = p.get("user_id", 0)
        duration = p.get("duration_minutes", 60)
        if not chat_id or not user_id:
            return "chat_id va user_id kerak"
        if self._is_owner(user_id):
            return "Owner ni mute qilib bo'lmaydi"
        try:
            from datetime import datetime, timedelta
            from aiogram.types import ChatPermissions
            until = datetime.utcnow() + timedelta(minutes=duration)
            await self._bot.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            return f"Foydalanuvchi {user_id} {duration} daqiqaga mute qilindi"
        except Exception as e:
            return f"Mute xatosi: {e}"

    async def _kick_user(self, p: dict) -> str:
        """Foydalanuvchini guruhdan chiqarish (ban emas)."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        user_id = p.get("user_id", 0)
        if not chat_id or not user_id:
            return "chat_id va user_id kerak"
        if self._is_owner(user_id):
            return "Owner ni chiqarib bo'lmaydi"
        try:
            await self._bot.ban_chat_member(chat_id, user_id)
            await self._bot.unban_chat_member(chat_id, user_id)
            return f"Foydalanuvchi {user_id} chiqarildi"
        except Exception as e:
            return f"Kick xatosi: {e}"

    async def _unban_user(self, p: dict) -> str:
        """Ban olib tashlash."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        user_id = p.get("user_id", 0)
        if not chat_id or not user_id:
            return "chat_id va user_id kerak"
        try:
            await self._bot.unban_chat_member(chat_id, user_id)
            return f"Foydalanuvchi {user_id} ban olib tashlandi"
        except Exception as e:
            return f"Unban xatosi: {e}"

    async def _delete_message(self, p: dict) -> str:
        """Xabarni o'chirish."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        message_id = p.get("message_id", 0)
        if not chat_id or not message_id:
            return "chat_id va message_id kerak"
        try:
            await self._bot.delete_message(chat_id, message_id)
            return "Xabar o'chirildi"
        except Exception as e:
            return f"O'chirish xatosi: {e}"

    async def _get_chat_admins(self, p: dict) -> str:
        """Guruh adminlarini ko'rish."""
        if not hasattr(self, '_bot') or not self._bot:
            return "Bot ulanmagan"
        chat_id = p.get("chat_id", 0)
        if not chat_id:
            return "chat_id kerak"
        try:
            admins = await self._bot.get_chat_administrators(chat_id)
            lines = []
            for a in admins:
                u = a.user
                role = "owner" if a.status == "creator" else "admin"
                lines.append(f"{u.first_name} (@{u.username or '?'}) — {role} [ID: {u.id}]")
            return "\n".join(lines) if lines else "Admin topilmadi"
        except Exception as e:
            return f"Xato: {e}"
