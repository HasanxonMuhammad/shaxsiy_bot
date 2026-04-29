"""Tabir va Dalil DB lardagi bo'sh mavzu_uz larni Gemini bilan to'ldirish.

Tabir kitobida ko'pincha mavzu nomi faqat arabcha — qidiruv yomonlashadi.
Bu skript bo'sh mavzu_uz larni topib batchda tarjima qiladi.

Ishga tushirish (DB lar yig'ilgandan keyin):
    python scripts/ingest_books/05_enrich_topics.py
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ingest_books.common import call_gemini, load_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("enrich")

SYSTEM_PROMPT = """Sen arab-o'zbek tarjimonisan. Faqat arabcha mavzu sarlavhasini
qisqa, jonli o'zbekchaga tarjima qil. Sof o'zbek so'zlar (uchun=ga, va=va).
Maks. 6 so'z. "Iboralar/ifodalar" so'zini ortiqcha takrorlama, agar mavzu
"...iboralari" bo'lsa — qisqartiramiz: "تَعَابِيرُ السَّعَادَةِ" → "Baxt iboralari".
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "mavzu_uz": {"type": "string"},
                },
                "required": ["id", "mavzu_uz"],
            },
        }
    },
    "required": ["items"],
}


def enrich(db_path: Path, table: str, batch_size: int = 20) -> None:
    if not db_path.exists():
        log.warning("DB topilmadi: %s — o'tkazib yuborildi", db_path)
        return
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        f"SELECT id, mavzu_ar, mavzu_uz FROM {table} "
        "WHERE (mavzu_uz IS NULL OR mavzu_uz = '' OR mavzu_uz = 'Boshqa') "
        "AND mavzu_ar != ''"
    )
    rows = cur.fetchall()
    log.info("%s: %d ta mavzu tarjima kerak", table, len(rows))
    if not rows:
        conn.close()
        return

    keys = load_keys()
    updated = 0
    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        payload = {
            "items": [{"id": r["id"], "mavzu_ar": r["mavzu_ar"]} for r in batch]
        }
        prompt = (
            "Quyidagi arabcha mavzu sarlavhalarini o'zbekchaga tarjima qil. "
            "id ni aynan saqla.\n\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
        t0 = time.time()
        result = call_gemini(
            keys=keys,
            model="gemini-2.5-flash",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=SCHEMA,
        )
        if not isinstance(result, dict):
            log.warning("Batch %d: javob yo'q", batch_start)
            continue
        for it in result.get("items", []):
            tid = it.get("id")
            uz = (it.get("mavzu_uz") or "").strip()
            if not tid or not uz:
                continue
            conn.execute(f"UPDATE {table} SET mavzu_uz=? WHERE id=?", (uz, tid))
            updated += 1
        conn.commit()
        log.info("Batch %d-%d: %d yangilandi (%.1fs)",
                 batch_start, batch_start + len(batch), len(result.get("items", [])),
                 time.time() - t0)

    log.info("%s: jami %d ta mavzu yangilandi", table, updated)
    conn.close()


def rebuild_fts(db_path: Path, scheme: str) -> None:
    """FTS jadvalini yangilash uchun qayta to'ldirish."""
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    if scheme == "tabir":
        conn.execute("DELETE FROM tabir_fts")
        conn.execute(
            """INSERT INTO tabir_fts(mavzu_uz, head_ar, hint_uz, arabcha, tarjima_uz, expr_id, topic_id)
               SELECT t.mavzu_uz, e.head_ar, e.hint_uz, ex.arabcha, ex.tarjima_uz, e.id, t.id
               FROM tabir_examples ex
               JOIN tabir_expressions e ON e.id = ex.expression_id
               JOIN tabir_topics t ON t.id = e.topic_id"""
        )
    elif scheme == "dalil":
        conn.execute("DELETE FROM dalil_fts")
        conn.execute(
            """INSERT INTO dalil_fts(mavzu_uz, savol_ar, savol_uz, level, q_id, topic_id)
               SELECT t.mavzu_uz, q.savol_ar, q.savol_uz, q.level, q.id, t.id
               FROM dalil_questions q
               JOIN dalil_topics t ON t.id = q.topic_id"""
        )
    conn.commit()
    conn.close()
    log.info("%s FTS qayta yig'ildi: %s", scheme, db_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    enrich(args.data_dir / "tabir.db", "tabir_topics")
    rebuild_fts(args.data_dir / "tabir.db", "tabir")

    enrich(args.data_dir / "dalil.db", "dalil_topics")
    rebuild_fts(args.data_dir / "dalil.db", "dalil")


if __name__ == "__main__":
    main()
