"""Arab-O'zbek va O'zbek-Arab lug'at qidiruvi."""
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


class Lugat:
    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def search_arab(self, query: str, limit: int = 5) -> list[dict]:
        """Arabcha so'z bo'yicha qidirish."""
        conn = self._get_conn()
        q = query.strip()
        # Aniq moslik
        rows = conn.execute(
            "SELECT word, data_json FROM dictionary WHERE arabsearch = ? LIMIT ?",
            (q, limit),
        ).fetchall()

        # Topilmasa — LIKE bilan
        if not rows:
            rows = conn.execute(
                "SELECT word, data_json FROM dictionary WHERE arabsearch LIKE ? LIMIT ?",
                (f"%{q}%", limit),
            ).fetchall()

        results = []
        for r in rows:
            data = json.loads(r["data_json"]) if r["data_json"] else {}
            content = data.get("contentSearch", "")
            results.append({
                "word": r["word"],
                "meaning": content[:300],
            })
        return results

    def search_uzbek(self, query: str, limit: int = 5) -> list[dict]:
        """O'zbekcha so'z bo'yicha arabcha tarjima qidirish."""
        conn = self._get_conn()
        # Aniq moslik
        rows = conn.execute(
            "SELECT uzbek, arab FROM uzbarab WHERE uzbek = ? COLLATE NOCASE LIMIT ?",
            (query.strip(), limit),
        ).fetchall()

        # Topilmasa — LIKE bilan
        if not rows:
            rows = conn.execute(
                "SELECT uzbek, arab FROM uzbarab WHERE uzbek LIKE ? COLLATE NOCASE LIMIT ?",
                (f"%{query.strip()}%", limit),
            ).fetchall()

        return [{"uzbek": r["uzbek"], "arab": r["arab"]} for r in rows]

    def search(self, query: str, limit: int = 5) -> str:
        """Ikkala yo'nalishda qidirish — natijani matn sifatida qaytarish."""
        query = query.strip()
        if not query:
            return "So'z kiritilmadi"

        # Arabcha harflar bormi tekshirish
        has_arabic = any('\u0600' <= c <= '\u06FF' for c in query)

        if has_arabic:
            results = self.search_arab(query, limit)
            if not results:
                return f"'{query}' lug'atda topilmadi"
            lines = []
            for r in results:
                lines.append(f"📖 {r['word']} — {r['meaning']}")
            return "\n".join(lines)
        else:
            results = self.search_uzbek(query, limit)
            if not results:
                return f"'{query}' lug'atda topilmadi"
            lines = []
            for r in results:
                lines.append(f"📖 {r['uzbek']} — {r['arab']}")
            return "\n".join(lines)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
