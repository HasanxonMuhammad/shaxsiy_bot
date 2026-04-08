"""
Hadis RAG — SQLite FTS5 orqali hadis.islom.uz hadislaridan qidirish.
scrape_hadis.py skripti DB ni to'ldiradi, bu sinf faqat qidiradi.
"""
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)


class HadisRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Hadis DB topilmadi: %s", db_path)

    # Lotin → krill transliteratsiya jadvali (o'zbekcha)
    _LATIN_TO_CYRILLIC = {
        "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф",
        "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
        "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
        "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
        "v": "в", "x": "х", "y": "й", "z": "з", "w": "в",
        "sh": "ш", "ch": "ч", "ng": "нг", "o'": "ў", "g'": "ғ",
    }

    def _to_cyrillic(self, text: str) -> str:
        """Oddiy lotin→krill konvertatsiya (qidirish uchun)."""
        result = text.lower()
        # Ikki harflilarni avval almashtirish
        for lat, cyr in sorted(self._LATIN_TO_CYRILLIC.items(),
                               key=lambda x: -len(x[0])):
            result = result.replace(lat, cyr)
        return result

    def search(self, query: str, limit: int = 5) -> str:
        """Hadis qidirish — FTS5 (krill + lotin). Natija: formatlangan matn."""
        if not self._ok:
            return ""
        if not query.strip():
            return "Qidiruv so'zi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            rows = []
            # Qidirish variantlari: asl so'z + krill versiyasi
            queries_to_try = [query]
            cyrillic_q = self._to_cyrillic(query)
            if cyrillic_q != query.lower():
                queries_to_try.append(cyrillic_q)

            # FTS5 MATCH qidirish (har bir variant)
            for q in queries_to_try:
                if rows:
                    break
                try:
                    cur.execute(
                        """
                        SELECT h.kitob_nomi, h.sarlavha, h.arabcha, h.uzbekcha,
                               h.hadis_raqam, bm25(hadis_fts) AS score
                        FROM hadis_fts
                        JOIN hadislar h ON hadis_fts.rowid = h.id
                        WHERE hadis_fts MATCH ?
                        ORDER BY score
                        LIMIT ?
                        """,
                        (q, limit),
                    )
                    rows = cur.fetchall()
                except Exception:
                    pass

            conn.close()

            if not rows:
                return ""

            return self._format_rows(rows)

        except Exception as e:
            log.error("HadisRAG qidirish xatosi: %s", e)
            return f"Qidiruvda xato: {e}"

    def _format_rows(self, rows) -> str:
        parts = []
        for r in rows:
            lines = [f"📖 <b>{r['kitob_nomi']}</b>"]
            if r["sarlavha"]:
                lines.append(f"<b>{r['sarlavha']}</b>")
            if r["hadis_raqam"]:
                lines.append(f"#{r['hadis_raqam']}")
            if r["arabcha"]:
                lines.append(r["arabcha"][:500])
            if r["uzbekcha"]:
                lines.append(r["uzbekcha"][:800])
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)

    def list_books(self) -> str:
        """Barcha hadis kitoblari ro'yxati."""
        if not self._ok:
            return "Hadis bazasi mavjud emas"
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT nomi, hadis_soni FROM hadis_kitoblar ORDER BY id")
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Hali hadis indekslanmagan"
            lines = [f"• {r['nomi']} — {r['hadis_soni']} hadis" for r in rows]
            total = sum(r['hadis_soni'] for r in rows)
            lines.append(f"\nJami: {total} hadis")
            return "Hadis kitoblari:\n" + "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"

    def get_random(self, kitob_id: int = 0) -> str:
        """Tasodifiy hadis olish."""
        if not self._ok:
            return ""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if kitob_id:
                cur.execute(
                    "SELECT * FROM hadislar WHERE kitob_id = ? ORDER BY RANDOM() LIMIT 1",
                    (kitob_id,)
                )
            else:
                cur.execute("SELECT * FROM hadislar ORDER BY RANDOM() LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if not row:
                return "Hadis topilmadi"
            lines = [f"📖 <b>{row['kitob_nomi']}</b>"]
            if row["sarlavha"]:
                lines.append(f"<b>{row['sarlavha']}</b>")
            if row["arabcha"]:
                lines.append(row["arabcha"][:500])
            if row["uzbekcha"]:
                lines.append(row["uzbekcha"][:800])
            return "\n\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"
