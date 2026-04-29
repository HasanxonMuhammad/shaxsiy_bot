"""Dalil RAG — arabcha savollar bazasidan qidirish, mavzu va daraja bo'yicha filterlash.

DB: data/dalil.db — `04_build_dbs.py` skripti to'ldiradi.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}

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


class DalilRAG:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ok = db_path.exists()
        if not self._ok:
            log.warning("Dalil DB topilmadi: %s", db_path)

    def search(
        self,
        mavzu: str = "",
        level: str = "",
        limit: int = 5,
    ) -> str:
        """Mavzu va/yoki daraja bo'yicha savollar qaytaradi.
        Tasodifiy tartiblanadi — har gal turli savollar.
        """
        if not self._ok:
            return ""

        level = (level or "").strip().upper()
        if level and level not in VALID_LEVELS:
            return f"Noto'g'ri daraja: {level}. Ruxsat: {sorted(VALID_LEVELS)}"

        mavzu = (mavzu or "").strip()
        cyr = _to_cyrillic(mavzu) if mavzu else ""

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            q_ids: list[int] = []

            # FTS yo'li (mavzu yoki kalit so'z bo'yicha)
            if mavzu:
                for q in [mavzu, cyr] if cyr and cyr != mavzu.lower() else [mavzu]:
                    if q_ids:
                        break
                    try:
                        if level:
                            cur.execute(
                                """SELECT DISTINCT q_id FROM dalil_fts
                                   WHERE dalil_fts MATCH ? AND level = ?
                                   LIMIT ?""",
                                (q, level, limit * 6),
                            )
                        else:
                            cur.execute(
                                """SELECT DISTINCT q_id FROM dalil_fts
                                   WHERE dalil_fts MATCH ? LIMIT ?""",
                                (q, limit * 6),
                            )
                        q_ids = [r[0] for r in cur.fetchall()]
                    except Exception:
                        pass

                # LIKE fallback
                if not q_ids:
                    if level:
                        cur.execute(
                            """SELECT DISTINCT q.id FROM dalil_questions q
                               JOIN dalil_topics t ON t.id = q.topic_id
                               WHERE q.level = ?
                                 AND (t.mavzu_uz LIKE ? OR t.mavzu_ar LIKE ?
                                      OR q.savol_uz LIKE ? OR q.izoh LIKE ?)
                               LIMIT ?""",
                            (level, f"%{mavzu}%", f"%{mavzu}%",
                             f"%{mavzu}%", f"%{mavzu}%", limit * 4),
                        )
                    else:
                        cur.execute(
                            """SELECT DISTINCT q.id FROM dalil_questions q
                               JOIN dalil_topics t ON t.id = q.topic_id
                               WHERE t.mavzu_uz LIKE ? OR t.mavzu_ar LIKE ?
                                  OR q.savol_uz LIKE ? OR q.izoh LIKE ?
                               LIMIT ?""",
                            (f"%{mavzu}%", f"%{mavzu}%",
                             f"%{mavzu}%", f"%{mavzu}%", limit * 4),
                        )
                    q_ids = [r[0] for r in cur.fetchall()]
            else:
                if level:
                    cur.execute(
                        "SELECT id FROM dalil_questions WHERE level=? "
                        "ORDER BY RANDOM() LIMIT ?",
                        (level, limit * 2),
                    )
                else:
                    cur.execute(
                        "SELECT id FROM dalil_questions ORDER BY RANDOM() LIMIT ?",
                        (limit * 2,),
                    )
                q_ids = [r[0] for r in cur.fetchall()]

            if not q_ids:
                conn.close()
                return ""

            # Tasodifiy aralashtirish va kesib qo'yish
            import random
            random.shuffle(q_ids)
            q_ids = q_ids[:limit]

            placeholders = ",".join("?" * len(q_ids))
            cur.execute(
                f"""SELECT q.savol_ar, q.savol_uz, q.level, q.izoh,
                           t.mavzu_uz, t.mavzu_ar
                    FROM dalil_questions q
                    LEFT JOIN dalil_topics t ON t.id = q.topic_id
                    WHERE q.id IN ({placeholders})""",
                q_ids,
            )
            rows = cur.fetchall()
            conn.close()

            if not rows:
                return ""
            return self._format(rows)
        except Exception as e:
            log.error("DalilRAG xato: %s", e)
            return f"Qidiruvda xato: {e}"

    def list_topics(self) -> str:
        if not self._ok:
            return "Dalil bazasi mavjud emas"
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """SELECT t.mavzu_uz, t.mavzu_ar, COUNT(q.id)
                   FROM dalil_topics t LEFT JOIN dalil_questions q ON q.topic_id = t.id
                   GROUP BY t.id ORDER BY COUNT(q.id) DESC"""
            )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return "Mavzular bo'sh"
            lines = []
            for uz, ar, n in rows:
                label = uz or ar or "?"
                lines.append(f"• {label} ({n} savol)")
            return "Dalil mavzulari:\n" + "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"

    def stats(self) -> str:
        if not self._ok:
            return "Dalil bazasi mavjud emas"
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT level, COUNT(*) FROM dalil_questions "
                        "GROUP BY level ORDER BY level")
            level_rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM dalil_questions")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dalil_topics")
            n_topics = cur.fetchone()[0]
            conn.close()
            lines = [f"Jami: {total} savol, {n_topics} mavzu"]
            for level, n in level_rows:
                lines.append(f"  {level}: {n}")
            return "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"

    def _format(self, rows) -> str:
        parts: list[str] = []
        for r in rows:
            savol_ar, savol_uz, level, izoh, mavzu_uz, mavzu_ar = r
            lines = []
            mavzu = mavzu_uz or mavzu_ar
            if mavzu:
                lines.append(f"MAVZU: {mavzu}")
            if level:
                lines.append(f"DARAJA: {level}")
            lines.append(f"SAVOL: {savol_ar}")
            if savol_uz:
                lines.append(f"TARJIMA: {savol_uz}")
            if izoh:
                lines.append(f"IZOH: {izoh}")
            parts.append("\n".join(lines))
        return "\n\n---\n\n".join(parts)
