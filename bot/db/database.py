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

    async def update_student(self, user_id: int, **fields):
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [user_id]
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
