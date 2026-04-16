"""
SheerRAG — SQLite FTS5 orqali arabcha she'rlar/baytlardan qidirish.
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


class SheerRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Sheer DB topilmadi: %s", db_path)

    def search(self, query: str, shoir: str = "", mavzu: str = "", limit: int = 5) -> str:
        """She'r qidirish — FTS5 + lotin→krill. Natija: formatlangan matn."""
        if not self._ok:
            return ""
        if not query.strip() and not shoir.strip() and not mavzu.strip():
            return "Qidiruv so'zi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            rows = []

            # FTS5 qidirish
            if query.strip():
                queries = [query]
                cyr = _to_cyrillic(query)
                if cyr != query.lower():
                    queries.append(cyr)

                for q in queries:
                    if rows:
                        break
                    try:
                        fts_query = q
                        params = [fts_query, limit]
                        sql = """SELECT a.shoir, a.davr, a.vazn, a.mavzu, a.bayt,
                                        bm25(ashaar_fts) AS score
                                 FROM ashaar_fts
                                 JOIN ashaar a ON ashaar_fts.rowid = a.id
                                 WHERE ashaar_fts MATCH ?
                                 ORDER BY score LIMIT ?"""
                        cur.execute(sql, params)
                        rows = cur.fetchall()
                    except Exception:
                        pass

            # LIKE fallback yoki shoir/mavzu filter
            if not rows:
                conditions = []
                params = []

                if query.strip():
                    cyr = _to_cyrillic(query)
                    conditions.append("(bayt LIKE ? OR bayt LIKE ?)")
                    params.extend([f"%{query}%", f"%{cyr}%"])
                if shoir.strip():
                    conditions.append("shoir LIKE ?")
                    params.append(f"%{shoir}%")
                if mavzu.strip():
                    conditions.append("mavzu LIKE ?")
                    params.append(f"%{mavzu}%")

                if conditions:
                    where = " AND ".join(conditions)
                    params.append(limit)
                    cur.execute(
                        f"SELECT shoir, davr, vazn, mavzu, bayt FROM ashaar WHERE {where} LIMIT ?",
                        params
                    )
                    rows = cur.fetchall()

            conn.close()
            if not rows:
                return ""

            parts = []
            seen = set()
            for r in rows:
                bayt = r["bayt"]
                if bayt in seen:
                    continue
                seen.add(bayt)
                lines = [f"BAYT: {bayt}"]
                if r["shoir"]:
                    lines.append(f"SHOIR: {r['shoir']}")
                if r["davr"]:
                    lines.append(f"DAVR: {r['davr']}")
                if r["mavzu"]:
                    lines.append(f"MAVZU: {r['mavzu']}")
                parts.append("\n".join(lines))

            return "\n\n---\n\n".join(parts)

        except Exception as e:
            log.error("SheerRAG xato: %s", e)
            return f"Qidiruvda xato: {e}"

    def get_random(self, mavzu: str = "") -> str:
        """Tasodifiy bayt."""
        if not self._ok:
            return ""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            if mavzu.strip():
                cur.execute(
                    "SELECT shoir, davr, mavzu, bayt FROM ashaar WHERE mavzu LIKE ? ORDER BY RANDOM() LIMIT 1",
                    (f"%{mavzu}%",)
                )
            else:
                cur.execute("SELECT shoir, davr, mavzu, bayt FROM ashaar ORDER BY RANDOM() LIMIT 1")

            r = cur.fetchone()
            conn.close()
            if not r:
                return ""
            lines = [f"BAYT: {r['bayt']}"]
            if r["shoir"]:
                lines.append(f"SHOIR: {r['shoir']}")
            if r["davr"]:
                lines.append(f"DAVR: {r['davr']}")
            if r["mavzu"]:
                lines.append(f"MAVZU: {r['mavzu']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"
