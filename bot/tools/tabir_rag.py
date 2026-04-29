"""Tabir RAG — arabcha gap yasash iboralari (taʻbir) bazasidan qidirish.

DB: data/tabir.db — `04_build_dbs.py` skripti to'ldiradi.
"""
from __future__ import annotations

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


class TabirRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Tabir DB topilmadi: %s", db_path)

    def search(self, query: str, limit: int = 3) -> str:
        """Mavzu yoki kalit ibora bo'yicha qidirish.
        Bitta natija = 1 ibora + uning misol gaplari (book ground truth).
        """
        if not self._ok:
            return ""
        if not query.strip():
            return "Qidiruv soʻzi kerak"

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # FTS5 — mavzu/head/hint/arabcha/tarjima bo'yicha
            queries = [query]
            cyr = _to_cyrillic(query)
            if cyr != query.lower():
                queries.append(cyr)

            expr_ids: list[int] = []
            for q in queries:
                if expr_ids:
                    break
                try:
                    cur.execute(
                        """SELECT DISTINCT expr_id FROM tabir_fts
                           WHERE tabir_fts MATCH ? LIMIT ?""",
                        (q, limit * 4),
                    )
                    expr_ids = [r[0] for r in cur.fetchall()]
                except Exception:
                    pass

            # LIKE fallback
            if not expr_ids:
                cur.execute(
                    """SELECT DISTINCT e.id FROM tabir_expressions e
                       LEFT JOIN tabir_topics t ON t.id = e.topic_id
                       WHERE t.mavzu_uz LIKE ? OR t.mavzu_ar LIKE ?
                          OR e.head_ar LIKE ? OR e.hint_uz LIKE ?
                       LIMIT ?""",
                    (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit * 2),
                )
                expr_ids = [r[0] for r in cur.fetchall()]

            if not expr_ids:
                conn.close()
                return ""

            # Unique ekspressiyalar — limit
            seen: list[int] = []
            for eid in expr_ids:
                if eid not in seen:
                    seen.append(eid)
                if len(seen) >= limit:
                    break

            result = self._format(conn, seen)
            conn.close()
            return result
        except Exception as e:
            log.error("TabirRAG xato: %s", e)
            return f"Qidiruvda xato: {e}"

    def by_topic(self, mavzu: str, limit: int = 3) -> str:
        """Mavzu nomi bo'yicha aniq qidirish."""
        return self.search(mavzu, limit)

    def get_random(self, mavzu: str = "") -> str:
        if not self._ok:
            return ""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            if mavzu.strip():
                cur.execute(
                    """SELECT e.id FROM tabir_expressions e
                       JOIN tabir_topics t ON t.id = e.topic_id
                       WHERE t.mavzu_uz LIKE ? OR t.mavzu_ar LIKE ?
                       ORDER BY RANDOM() LIMIT 1""",
                    (f"%{mavzu}%", f"%{mavzu}%"),
                )
            else:
                cur.execute(
                    "SELECT id FROM tabir_expressions ORDER BY RANDOM() LIMIT 1"
                )
            row = cur.fetchone()
            if not row:
                conn.close()
                return ""
            res = self._format(conn, [row[0]])
            conn.close()
            return res
        except Exception as e:
            return f"Xato: {e}"

    def list_topics(self) -> str:
        if not self._ok:
            return "Tabir bazasi mavjud emas"
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """SELECT t.mavzu_uz, t.mavzu_ar, COUNT(e.id)
                   FROM tabir_topics t LEFT JOIN tabir_expressions e ON e.topic_id = t.id
                   GROUP BY t.id ORDER BY t.id"""
            )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Tabir mavzulari bo'sh"
            lines = []
            for uz, ar, n in rows:
                label = uz or ar or "?"
                lines.append(f"• {label} ({n} ibora)")
            return "Tabir mavzulari:\n" + "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"

    def _format(self, conn: sqlite3.Connection, expr_ids: list[int]) -> str:
        parts: list[str] = []
        for eid in expr_ids:
            cur = conn.execute(
                """SELECT e.head_ar, e.hint_uz, e.hint_tr, e.izoh,
                          t.mavzu_uz, t.mavzu_ar
                   FROM tabir_expressions e
                   LEFT JOIN tabir_topics t ON t.id = e.topic_id
                   WHERE e.id = ?""",
                (eid,),
            )
            row = cur.fetchone()
            if not row:
                continue
            head_ar, hint_uz, hint_tr, izoh, mavzu_uz, mavzu_ar = row

            cur = conn.execute(
                "SELECT arabcha, tarjima_uz FROM tabir_examples "
                "WHERE expression_id=? ORDER BY id LIMIT 5",
                (eid,),
            )
            examples = cur.fetchall()

            lines = []
            mavzu = mavzu_uz or mavzu_ar
            if mavzu:
                lines.append(f"MAVZU: {mavzu}")
            lines.append(f"IBORA: {head_ar}")
            if hint_uz:
                lines.append(f"MA'NO (UZ): {hint_uz}")
            elif hint_tr:
                lines.append(f"MA'NO (TR): {hint_tr}")
            if izoh:
                lines.append(f"IZOH: {izoh}")
            if examples:
                lines.append("MISOLLAR:")
                for ar, uz in examples:
                    if uz:
                        lines.append(f"  {ar}  —  {uz}")
                    else:
                        lines.append(f"  {ar}")
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)
