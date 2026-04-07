import aiosqlite
from pathlib import Path
from datetime import datetime


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        schema = (Path(__file__).parent / "schema.sql").read_text()
        await self._db.executescript(schema)

    async def close(self):
        if self._db:
            await self._db.close()

    # ── Messages ──────────────────────────────────────────────
    async def save_message(
        self,
        chat_id: int,
        message_id: int,
        user_id: int | None,
        username: str | None,
        first_name: str | None,
        text: str | None,
        reply_to: int | None,
        timestamp: str,
    ):
        await self._db.execute(
            """INSERT OR REPLACE INTO messages
               (chat_id, message_id, user_id, username, first_name, text, reply_to_message_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, message_id, user_id, username, first_name, text, reply_to, timestamp),
        )
        await self._db.commit()

    async def get_recent_messages(self, chat_id: int, limit: int = 30) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT message_id, user_id, username, first_name, text,
                      reply_to_message_id, timestamp
               FROM messages WHERE chat_id = ?
               ORDER BY message_id DESC LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            dict(
                message_id=r[0], user_id=r[1], username=r[2], first_name=r[3],
                text=r[4], reply_to=r[5], timestamp=r[6],
            )
            for r in reversed(rows)
        ]

    async def search_messages(self, chat_id: int, query: str, limit: int = 20) -> list[dict]:
        # Avval FTS5 bilan sinash, ishlamasa LIKE ga qaytish
        try:
            cursor = await self._db.execute(
                """SELECT m.message_id, m.user_id, m.username, m.first_name, m.text,
                          m.reply_to_message_id, m.timestamp
                   FROM messages m
                   JOIN messages_fts f ON m.rowid = f.rowid
                   WHERE f.messages_fts MATCH ? AND m.chat_id = ?
                   ORDER BY m.message_id DESC LIMIT ?""",
                (query, chat_id, limit),
            )
        except Exception:
            # FTS ishlamasa oddiy LIKE
            cursor = await self._db.execute(
                """SELECT message_id, user_id, username, first_name, text,
                          reply_to_message_id, timestamp
                   FROM messages WHERE chat_id = ? AND text LIKE ?
                   ORDER BY message_id DESC LIMIT ?""",
                (chat_id, f"%{query}%", limit),
            )
        rows = await cursor.fetchall()
        return [
            dict(
                message_id=r[0], user_id=r[1], username=r[2], first_name=r[3],
                text=r[4], reply_to=r[5], timestamp=r[6],
            )
            for r in reversed(rows)
        ]

    # ── Users ─────────────────────────────────────────────────
    async def upsert_user(
        self, chat_id: int, user_id: int, username: str | None, first_name: str
    ):
        await self._db.execute(
            """INSERT INTO users (chat_id, user_id, username, first_name, last_message_date, message_count)
               VALUES (?, ?, ?, ?, datetime('now'), 1)
               ON CONFLICT(chat_id, user_id) DO UPDATE SET
                  username = COALESCE(?, username),
                  first_name = ?,
                  last_message_date = datetime('now'),
                  message_count = message_count + 1""",
            (chat_id, user_id, username, first_name, username, first_name),
        )
        await self._db.commit()

    # ── Strikes ───────────────────────────────────────────────
    async def add_strike(self, user_id: int) -> int:
        await self._db.execute(
            """INSERT INTO strikes (user_id, count, last_strike)
               VALUES (?, 1, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                  count = count + 1, last_strike = datetime('now')""",
            (user_id,),
        )
        await self._db.commit()
        cursor = await self._db.execute(
            "SELECT count FROM strikes WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ── Reminders ─────────────────────────────────────────────
    async def save_reminder(
        self, chat_id: int, user_id: int, text: str, trigger_at: str
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO reminders (chat_id, user_id, text, trigger_at, completed)
               VALUES (?, ?, ?, ?, 0)""",
            (chat_id, user_id, text, trigger_at),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_due_reminders(self) -> list[dict]:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor = await self._db.execute(
            """SELECT id, chat_id, user_id, text, trigger_at
               FROM reminders WHERE completed = 0 AND trigger_at <= ?""",
            (now,),
        )
        rows = await cursor.fetchall()
        return [
            dict(id=r[0], chat_id=r[1], user_id=r[2], text=r[3], trigger_at=r[4])
            for r in rows
        ]

    async def complete_reminder(self, reminder_id: int):
        await self._db.execute(
            "UPDATE reminders SET completed = 1 WHERE id = ?", (reminder_id,)
        )
        await self._db.commit()

    # ── Students (O'quvchilar) ────────────────────────────────
    async def get_or_create_student(
        self, user_id: int, first_name: str, username: str | None = None
    ) -> dict:
        cursor = await self._db.execute(
            "SELECT * FROM students WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))

        await self._db.execute(
            """INSERT INTO students (user_id, first_name, username)
               VALUES (?, ?, ?)""",
            (user_id, first_name, username),
        )
        await self._db.commit()
        return {
            "user_id": user_id, "first_name": first_name, "username": username,
            "level": "boshlang'ich", "current_sura": None,
            "completed_suras": "[]", "total_lessons": 0,
            "last_lesson_date": None, "avg_score": 0, "notes": "",
        }

    async def get_student(self, user_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM students WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        if row:
            cols = [d[0] for d in cursor.description]
            return dict(zip(cols, row))
        return None

    # ── Chat Sessions (AI suhbat tarixi) ───────────────────
    async def save_session_turn(self, chat_id: int, role: str, text: str):
        """AI suhbat tarixiga qo'shish."""
        await self._db.execute(
            "INSERT INTO chat_sessions (chat_id, role, text) VALUES (?, ?, ?)",
            (chat_id, role, text[:5000]),  # 5000 belgidan ko'p saqlamaslik
        )
        await self._db.commit()
        # Eski tarixni tozalash — har chat uchun max 50 ta turn
        await self._db.execute(
            """DELETE FROM chat_sessions WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM chat_sessions WHERE chat_id = ? ORDER BY created_at DESC LIMIT 50
            )""",
            (chat_id, chat_id),
        )
        await self._db.commit()

    async def get_session_history(self, chat_id: int, limit: int = 20) -> list[dict]:
        """Oxirgi N ta suhbat turnini olish."""
        cursor = await self._db.execute(
            """SELECT role, text, created_at FROM chat_sessions
               WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?""",
            (chat_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(role=r[0], text=r[1], created_at=r[2]) for r in reversed(rows)]

    async def clear_session(self, chat_id: int):
        """Chat sessiyasini tozalash."""
        await self._db.execute("DELETE FROM chat_sessions WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    # ── Focus Mode ─────────────────────────────────────────
    async def get_focus(self) -> int | None:
        cursor = await self._db.execute("SELECT chat_id FROM focus_state WHERE id = 1")
        row = await cursor.fetchone()
        return row[0] if row else None

    async def set_focus(self, chat_id: int | None):
        await self._db.execute(
            "INSERT INTO focus_state (id, chat_id) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET chat_id = ?",
            (chat_id, chat_id),
        )
        await self._db.commit()

    async def set_chat_alias(self, alias: str, chat_id: int):
        await self._db.execute(
            "INSERT OR REPLACE INTO chat_aliases (alias, chat_id) VALUES (?, ?)",
            (alias, chat_id),
        )
        await self._db.commit()

    async def get_chat_by_alias(self, alias: str) -> int | None:
        cursor = await self._db.execute(
            "SELECT chat_id FROM chat_aliases WHERE alias = ?", (alias,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def mute_chat(self, chat_id: int, until: str | None = None, reason: str = ""):
        await self._db.execute(
            "INSERT OR REPLACE INTO muted_chats (chat_id, muted_until, reason) VALUES (?, ?, ?)",
            (chat_id, until, reason),
        )
        await self._db.commit()

    async def unmute_chat(self, chat_id: int):
        await self._db.execute("DELETE FROM muted_chats WHERE chat_id = ?", (chat_id,))
        await self._db.commit()

    async def is_muted(self, chat_id: int) -> bool:
        cursor = await self._db.execute(
            "SELECT muted_until FROM muted_chats WHERE chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        if row[0]:
            from datetime import datetime
            try:
                until = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() > until:
                    await self.unmute_chat(chat_id)
                    return False
            except ValueError:
                pass
        return True

    # ── Bot-to-bot xabarlar ─────────────────────────────────
    async def send_bot_message(self, from_bot: str, to_bot: str | None, message: str):
        await self._db.execute(
            "INSERT INTO bot_messages (from_bot, to_bot, message) VALUES (?, ?, ?)",
            (from_bot, to_bot, message),
        )
        await self._db.commit()

    async def poll_bot_messages(self, bot_name: str) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT id, from_bot, message, created_at FROM bot_messages
               WHERE (to_bot = ? OR to_bot IS NULL) AND from_bot != ? AND read = 0
               ORDER BY created_at""",
            (bot_name, bot_name),
        )
        rows = await cursor.fetchall()
        if rows:
            ids = [r[0] for r in rows]
            placeholders = ",".join("?" * len(ids))
            await self._db.execute(
                f"UPDATE bot_messages SET read = 1 WHERE id IN ({placeholders})", ids
            )
            await self._db.commit()
        return [dict(id=r[0], from_bot=r[1], message=r[2], created_at=r[3]) for r in rows]

    STUDENT_ALLOWED_FIELDS = {"level", "current_sura", "notes", "completed_suras", "first_name", "username"}

    async def update_student(self, user_id: int, **fields):
        # SQL injection himoya: faqat ruxsat berilgan maydonlar
        safe_fields = {k: v for k, v in fields.items() if k in self.STUDENT_ALLOWED_FIELDS}
        if not safe_fields:
            return
        sets = ", ".join(f"{k} = ?" for k in safe_fields)
        vals = list(safe_fields.values()) + [user_id]
        await self._db.execute(
            f"UPDATE students SET {sets} WHERE user_id = ?", vals
        )
        await self._db.commit()

    async def list_students(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT user_id, first_name, username, level, current_sura, total_lessons, avg_score, last_lesson_date FROM students ORDER BY first_name"
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in rows]

    # ── Lessons (Darslar) ─────────────────────────────────────
    async def save_lesson(
        self, user_id: int, chat_id: int, sura: str,
        ayah_range: str = "", score: int = 0, feedback: str = ""
    ) -> int:
        cursor = await self._db.execute(
            """INSERT INTO lessons (user_id, chat_id, sura, ayah_range, score, feedback)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, sura, ayah_range, score, feedback),
        )
        # O'quvchi statistikasini yangilash
        await self._db.execute(
            """UPDATE students SET
                  total_lessons = total_lessons + 1,
                  last_lesson_date = datetime('now'),
                  avg_score = (
                      SELECT ROUND(AVG(score), 1) FROM lessons WHERE user_id = ?
                  )
               WHERE user_id = ?""",
            (user_id, user_id),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_student_lessons(self, user_id: int, limit: int = 10) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT sura, ayah_range, score, feedback, submitted_at
               FROM lessons WHERE user_id = ?
               ORDER BY submitted_at DESC LIMIT ?""",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, r)) for r in rows]

    # ── Student Notes (Eslatmalar) ────────────────────────────
    async def add_student_note(self, user_id: int, note: str):
        await self._db.execute(
            "INSERT INTO student_notes (user_id, note) VALUES (?, ?)",
            (user_id, note),
        )
        await self._db.commit()

    async def get_student_notes(self, user_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT note, created_at FROM student_notes WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(note=r[0], created_at=r[1]) for r in rows]
