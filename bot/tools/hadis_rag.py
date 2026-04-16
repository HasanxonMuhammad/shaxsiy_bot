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
        "ya": "я", "yu": "ю", "ye": "е", "yo": "ё",
        "ts": "ц", "yo'": "йў",
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
                        SELECT h.kitob_nomi, h.kitob_id, h.bob_id, h.sarlavha,
                               h.arabcha, h.uzbekcha, h.hadis_raqam,
                               bm25(hadis_fts) AS score
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

            # FTS5 topilmasa — mavzular ustunidan qidirish
            if not rows:
                cyrillic_q = self._to_cyrillic(query)
                for like_q in [query.lower(), cyrillic_q]:
                    if rows:
                        break
                    words = [w for w in like_q.split() if len(w) >= 3]
                    for word in words:
                        if rows:
                            break
                        cur.execute(
                            """SELECT kitob_nomi, kitob_id, bob_id, sarlavha,
                                      arabcha, uzbekcha, hadis_raqam
                               FROM hadislar WHERE mavzular LIKE ? LIMIT ?""",
                            (f"%{word}%", limit),
                        )
                        rows = cur.fetchall()

            # Mavzu ham topilmasa — LIKE fallback (faqat uzbekcha, tez)
            if not rows:
                cyrillic_q = self._to_cyrillic(query)
                for like_q in [cyrillic_q, query]:
                    if rows:
                        break
                    cur.execute(
                        """SELECT kitob_nomi, kitob_id, bob_id, sarlavha,
                                  arabcha, uzbekcha, hadis_raqam
                           FROM hadislar WHERE uzbekcha LIKE ? LIMIT ?""",
                        (f"%{like_q}%", limit),
                    )
                    rows = cur.fetchall()

            if not rows:
                conn.close()
                return ""

            result = self._format_rows(rows, conn)
            conn.close()
            return result

        except Exception as e:
            log.error("HadisRAG qidirish xatosi: %s", e)
            return f"Qidiruvda xato: {e}"

    def _format_rows(self, rows, conn=None) -> str:
        parts = []
        for r in rows:
            lines = []
            lines.append(f"KITOB: {r['kitob_nomi']}")
            if r["hadis_raqam"]:
                lines.append(f"RAQAM: {r['hadis_raqam']}")
            # Bob nomini hadis_boblar dan olish
            if conn:
                try:
                    kitob_id = r["kitob_id"] if "kitob_id" in r.keys() else None
                    bob_id = r["bob_id"] if "bob_id" in r.keys() else None
                    if kitob_id and bob_id:
                        bc = conn.cursor()
                        bc.execute(
                            "SELECT nomi_uz, nomi_ar FROM hadis_boblar "
                            "WHERE kitob_id=? AND bob_id=?",
                            (kitob_id, bob_id)
                        )
                        brow = bc.fetchone()
                        if brow and brow["nomi_uz"]:
                            lines.append(f"BOB: {brow['nomi_uz']}")
                        if brow and brow["nomi_ar"]:
                            lines.append(f"BOB (AR): {brow['nomi_ar']}")
                except Exception:
                    pass
            if r["sarlavha"]:
                lines.append(f"SARLAVHA: {r['sarlavha']}")
            if r["arabcha"]:
                lines.append(f"ARABCHA: {r['arabcha']}")
            if r["uzbekcha"]:
                lines.append(f"UZBEKCHA: {r['uzbekcha']}")
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
            lines = []
            lines.append(f"KITOB: {row['kitob_nomi']}")
            if row["hadis_raqam"]:
                lines.append(f"RAQAM: {row['hadis_raqam']}")
            if row["sarlavha"]:
                lines.append(f"SARLAVHA: {row['sarlavha']}")
            if row["arabcha"]:
                lines.append(f"ARABCHA: {row['arabcha']}")
            if row["uzbekcha"]:
                lines.append(f"UZBEKCHA: {row['uzbekcha']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Xato: {e}"
