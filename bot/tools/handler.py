import base64
import json
import logging
import re

import aiohttp

from bot.db import Database
from bot.memory import MemoryStore

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

    async def execute(self, tool: dict) -> str:
        name = tool["name"]
        params = tool.get("params", {})
        try:
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
                # ── O'quvchi toollar ──────────────────────────
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
                case _:
                    return f"Noma'lum tool: {name}"
        except Exception as e:
            log.error("Tool %s xatosi: %s", name, e)
            return f"Xato: {e}"

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

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key={Config.GEMINI_API_KEYS[0]}"
        body = {
            "contents": [{"parts": [{"text": f"Generate an image: {prompt}"}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body) as resp:
                    data = await resp.json()

            if "candidates" in data:
                for part in data["candidates"][0]["content"]["parts"]:
                    if "inlineData" in part:
                        # Rasm topildi — base64 sifatida qaytarish
                        img_data = base64.b64decode(part["inlineData"]["data"])
                        mime = part["inlineData"].get("mimeType", "image/png")
                        return f"IMAGE:{mime}:{base64.b64encode(img_data).decode()}"

            error = data.get("error", {}).get("message", "Noma'lum xato")
            return f"Rasm yaratishda xato: {error}"
        except Exception as e:
            log.error("gen_image xatosi: %s", e)
            return f"Rasm yaratishda xato: {e}"

    async def _mute_chat(self, p: dict) -> str:
        chat_id = p.get("chat_id", 0)
        duration_min = p.get("duration_min", 60)
        from datetime import datetime, timedelta
        until = (datetime.utcnow() + timedelta(minutes=duration_min)).strftime("%Y-%m-%d %H:%M:%S")
        await self.db.mute_chat(chat_id, until, p.get("reason", ""))
        return f"🔇 Chat {duration_min} daqiqaga o'chirildi"

    async def _unmute_chat(self, p: dict) -> str:
        chat_id = p.get("chat_id", 0)
        await self.db.unmute_chat(chat_id)
        return f"🔊 Chat qayta yoqildi"

    async def _send_voice(self, p: dict) -> str:
        """Google TTS orqali ovozli xabar yaratish va yuborish."""
        text = p.get("text", "")
        lang = p.get("lang", "uz")  # uz, ar, en, tr, fa, ja, ko, zh
        if not text:
            return "Matn kerak"

        try:
            # Google Translate TTS (bepul, cheklanmagan)
            encoded = aiohttp.helpers.quote(text[:200], safe="")
            tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={encoded}&tl={lang}&client=tw-ob"

            async with aiohttp.ClientSession() as session:
                async with session.get(tts_url) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        return f"VOICE:{base64.b64encode(audio_data).decode()}"
                    else:
                        return f"TTS xatosi: HTTP {resp.status}"
        except Exception as e:
            log.error("TTS xatosi: %s", e)
            return f"TTS xatosi: {e}"
