"""
Amthal RAG — SQLite FTS5 orqali arabcha maqollardan qidirish.
"""
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

_LATIN_TO_CYRILLIC = {
    "sh": "ш", "ch": "ч", "ng": "нг",
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф",
    "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "x": "х", "y": "й", "z": "з",
}


def _to_cyrillic(text: str) -> str:
    result = text.lower()
    for lat, cyr in sorted(_LATIN_TO_CYRILLIC.items(), key=lambda x: -len(x[0])):
        result = result.replace(lat, cyr)
    return result


class AmthalRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Amthal DB topilmadi: %s", db_path)

    def search(self, query: str, limit: int = 5) -> str:
        """Amthal qidirish — FTS5 + lotin→krill. Natija: formatlangan matn."""
        if not self._ok:
            return ""
        if not query.strip():
            return "Qidiruv so'zi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            rows = []
            queries = [query]
            cyr = _to_cyrillic(query)
            if cyr != query.lower():
                queries.append(cyr)

            for q in queries:
                if rows:
                    break
                try:
                    cur.execute(
                        """SELECT a.arabcha, a.tarjima, a.tarjima_uz, a.izoh, bm25(amthal_fts) AS score
                           FROM amthal_fts
                           JOIN amthal a ON amthal_fts.rowid = a.id
                           WHERE amthal_fts MATCH ?
                           ORDER BY score LIMIT ?""",
                        (q, limit)
                    )
                    rows = cur.fetchall()
                except Exception:
                    pass

            # LIKE fallback — tarjima_uz va tags_uz ham qidirish
            if not rows:
                for like_q in [query, cyr]:
                    if rows:
                        break
                    cur.execute(
                        """SELECT arabcha, tarjima, tarjima_uz, izoh FROM amthal
                           WHERE arabcha LIKE ? OR tarjima_uz LIKE ? OR tags_uz LIKE ? LIMIT ?""",
                        (f"%{like_q}%", f"%{like_q}%", f"%{like_q}%", limit)
                    )
                    rows = cur.fetchall()

            conn.close()
            if not rows:
                return ""

            parts = []
            for r in rows:
                lines = [f"MATHAL: {r['arabcha']}"]
                # O'zbek tarjima ustuvor
                uz = r["tarjima_uz"] if "tarjima_uz" in r.keys() and r["tarjima_uz"] else None
                en = r["tarjima"] if r["tarjima"] else None
                if uz:
                    lines.append(f"TARJIMA: {uz}")
                elif en:
                    lines.append(f"TARJIMA (EN): {en}")
                if r["izoh"]:
                    lines.append(f"IZOH: {r['izoh']}")
                parts.append("\n".join(lines))
            return "\n\n---\n\n".join(parts)

        except Exception as e:
            log.error("AmthalRAG xato: %s", e)
            return f"Qidiruvda xato: {e}"

    def get_random(self) -> str:
        """Tasodifiy amthal."""
        if not self._ok:
            return ""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT arabcha, tarjima, tarjima_uz, izoh FROM amthal ORDER BY RANDOM() LIMIT 1")
            r = cur.fetchone()
            conn.close()
            if not r:
                return ""
            lines = [f"MATHAL: {r['arabcha']}"]
            uz = r["tarjima_uz"] if "tarjima_uz" in r.keys() and r["tarjima_uz"] else None
            en = r["tarjima"] if r["tarjima"] else None
            if uz:
                lines.append(f"TARJIMA: {uz}")
            elif en:
                lines.append(f"TARJIMA (EN): {en}")
            if r["izoh"]:
                lines.append(f"IZOH: {r['izoh']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"
