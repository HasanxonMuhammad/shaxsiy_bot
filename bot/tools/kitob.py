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
    # Lotin → krill transliteratsiya (o'zbekcha)
    _LATIN_TO_CYRILLIC = {
        "sh": "ш", "ch": "ч", "ng": "нг", "o'": "ў", "g'": "ғ",
        "ya": "я", "yu": "ю", "ye": "е", "yo": "ё", "ts": "ц",
        "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф",
        "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
        "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
        "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
        "v": "в", "x": "х", "y": "й", "z": "з", "w": "в",
    }

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Kitob DB topilmadi: %s", db_path)

    def _to_cyrillic(self, text: str) -> str:
        result = text.lower()
        for lat, cyr in sorted(self._LATIN_TO_CYRILLIC.items(), key=lambda x: -len(x[0])):
            result = result.replace(lat, cyr)
        return result

    def search(self, query: str, limit: int = 5) -> str:
        """FTS5 bilan qidirish — lotin va krill. Natija: formatlangan matn."""
        if not self._ok:
            return "Kitob bazasi mavjud emas"
        if not query.strip():
            return "Qidiruv so'zi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Lotin + krill variant
            queries_to_try = [query]
            cyrillic_q = self._to_cyrillic(query)
            if cyrillic_q != query.lower():
                queries_to_try.append(cyrillic_q)

            rows = []
            seen_keys = set()
            for q in queries_to_try:
                try:
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
                        (q, limit),
                    )
                    for r in cur.fetchall():
                        key = r["chunk_text"][:80]
                        if key not in seen_keys:
                            seen_keys.add(key)
                            rows.append(r)
                    if rows:
                        break
                except Exception:
                    continue

            conn.close()

            if not rows:
                return "Ushbu mavzu bo'yicha kitoblarda hech narsa topilmadi"

            parts = []
            for r in rows[:limit]:
                chunk = r["chunk_text"].strip()
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
