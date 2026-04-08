"""
Kitob RAG — SQLite FTS5 orqali arabcha/o'zbekcha kitoblardan qidirish.
index_books.py skripti DB ni to'ldiradi, bu sinf faqat qidiradi.
"""
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Bir parcha maksimal uzunligi (belgi)
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


class KitobRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Kitob DB topilmadi: %s", db_path)

    def search(self, query: str, limit: int = 5) -> str:
        """FTS5 bilan qidirish. Natija: formatlangan matn."""
        if not self._ok:
            return "Kitob bazasi mavjud emas"
        if not query.strip():
            return "Qidiruv so'zi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # FTS5 MATCH qidirish
            cur.execute(
                """
                SELECT k.title, k.lang, c.chunk_text,
                       bm25(kitob_fts) AS score
                FROM kitob_fts
                JOIN kitob_chunks c ON kitob_fts.rowid = c.id
                JOIN kitoblar k ON c.kitob_id = k.id
                WHERE kitob_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, limit),
            )
            rows = cur.fetchall()
            conn.close()

            if not rows:
                return "Ushbu mavzu bo'yicha kitoblarda hech narsa topilmadi"

            parts = []
            seen = set()
            for r in rows:
                chunk = r["chunk_text"].strip()
                # Takroriy parchalami o'tkazib yubor
                key = chunk[:80]
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"📖 <b>{r['title']}</b>\n{chunk}")

            return "\n\n---\n\n".join(parts)

        except Exception as e:
            log.error("KitobRAG qidirish xatosi: %s", e)
            return f"Qidiruvda xato: {e}"

    def list_books(self) -> str:
        """Barcha indekslangan kitoblar ro'yxati."""
        if not self._ok:
            return "Kitob bazasi mavjud emas"
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT title, lang, chunk_count FROM kitoblar ORDER BY lang, title")
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Hali kitob indekslanmagan"
            lines = [f"• {r['title']} ({r['lang']}) — {r['chunk_count']} parcha" for r in rows]
            return "Kitoblar:\n" + "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"
