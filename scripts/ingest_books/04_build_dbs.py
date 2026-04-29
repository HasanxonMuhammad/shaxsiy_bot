"""tabir_translated.jsonl va dalil.jsonl ni SQLite + FTS5 bazasiga aylantirish.

Chiqish:
  - data/tabir.db
  - data/dalil.db

Ishga tushirish:
    python scripts/ingest_books/04_build_dbs.py \
        --tabir scripts/ingest_books/out/tabir_translated.jsonl \
        --dalil scripts/ingest_books/out/dalil.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build")

TABIR_SCHEMA = ROOT / "bot" / "db" / "tabir_schema.sql"
DALIL_SCHEMA = ROOT / "bot" / "db" / "dalil_schema.sql"


def init_db(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    return conn


def get_or_create_topic(
    conn: sqlite3.Connection, mavzu_ar: str, mavzu_uz: str
) -> int:
    mavzu_ar = (mavzu_ar or "").strip()
    mavzu_uz = (mavzu_uz or "").strip()
    if not mavzu_ar and not mavzu_uz:
        mavzu_uz = "Boshqa"
    cur = conn.execute(
        "SELECT id FROM tabir_topics WHERE mavzu_ar=? AND mavzu_uz=?",
        (mavzu_ar, mavzu_uz),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO tabir_topics(mavzu_ar, mavzu_uz) VALUES(?, ?)",
        (mavzu_ar, mavzu_uz),
    )
    return cur.lastrowid


def get_or_create_dalil_topic(
    conn: sqlite3.Connection, mavzu_ar: str, mavzu_uz: str
) -> int:
    mavzu_ar = (mavzu_ar or "").strip()
    mavzu_uz = (mavzu_uz or "").strip()
    if not mavzu_ar and not mavzu_uz:
        mavzu_uz = "Boshqa"
    cur = conn.execute(
        "SELECT id FROM dalil_topics WHERE mavzu_ar=? AND mavzu_uz=?",
        (mavzu_ar, mavzu_uz),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO dalil_topics(mavzu_ar, mavzu_uz) VALUES(?, ?)",
        (mavzu_ar, mavzu_uz),
    )
    return cur.lastrowid


def build_tabir(jsonl: Path, db_path: Path) -> None:
    conn = init_db(db_path, TABIR_SCHEMA)
    n_topics = n_expr = n_ex = 0
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        page = row.get("page", 0)
        for e in row.get("expressions", []):
            head = (e.get("head_ar") or "").strip()
            if not head:
                continue
            topic_id = get_or_create_topic(
                conn, e.get("mavzu_ar", ""), e.get("mavzu_uz", "")
            )
            cur = conn.execute(
                "INSERT INTO tabir_expressions(topic_id, head_ar, hint_uz, hint_tr, izoh, pdf_page) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    topic_id,
                    head,
                    e.get("hint_uz", ""),
                    e.get("hint_tr", ""),
                    e.get("izoh", ""),
                    page,
                ),
            )
            expr_id = cur.lastrowid
            n_expr += 1

            mavzu_uz_for_fts = ""
            tcur = conn.execute(
                "SELECT mavzu_uz FROM tabir_topics WHERE id=?", (topic_id,)
            )
            trow = tcur.fetchone()
            if trow:
                mavzu_uz_for_fts = trow[0]

            for ex in e.get("examples", []):
                ar = (ex.get("arabcha") or "").strip()
                if not ar:
                    continue
                tr = (ex.get("tarjima_uz") or "").strip()
                conn.execute(
                    "INSERT INTO tabir_examples(expression_id, arabcha, tarjima_uz, pdf_page) "
                    "VALUES(?, ?, ?, ?)",
                    (expr_id, ar, tr, page),
                )
                conn.execute(
                    "INSERT INTO tabir_fts(mavzu_uz, head_ar, hint_uz, arabcha, tarjima_uz, expr_id, topic_id) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (mavzu_uz_for_fts, head, e.get("hint_uz", ""), ar, tr, expr_id, topic_id),
                )
                n_ex += 1

    conn.commit()
    cur = conn.execute("SELECT COUNT(*) FROM tabir_topics")
    n_topics = cur.fetchone()[0]
    log.info("Tabir DB: %d mavzu, %d ibora, %d misol → %s",
             n_topics, n_expr, n_ex, db_path)
    conn.close()


def build_dalil(jsonl: Path, db_path: Path) -> None:
    conn = init_db(db_path, DALIL_SCHEMA)
    n_topics = n_q = 0
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        page = row.get("page", 0)
        for q in row.get("questions", []):
            ar = (q.get("savol_ar") or "").strip()
            if not ar:
                continue
            level = (q.get("level") or "").strip().upper()
            if level not in {"A1", "A2", "B1", "B2", "C1", "C2"}:
                level = "B1"  # default
            topic_id = get_or_create_dalil_topic(
                conn, q.get("mavzu_ar", ""), q.get("mavzu_uz", "")
            )
            cur = conn.execute(
                "INSERT INTO dalil_questions(topic_id, savol_ar, savol_uz, level, izoh, pdf_page) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (
                    topic_id,
                    ar,
                    q.get("savol_uz", ""),
                    level,
                    q.get("izoh", ""),
                    page,
                ),
            )
            q_id = cur.lastrowid
            n_q += 1

            mavzu_uz_for_fts = ""
            tcur = conn.execute(
                "SELECT mavzu_uz FROM dalil_topics WHERE id=?", (topic_id,)
            )
            trow = tcur.fetchone()
            if trow:
                mavzu_uz_for_fts = trow[0]

            conn.execute(
                "INSERT INTO dalil_fts(mavzu_uz, savol_ar, savol_uz, level, q_id, topic_id) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (mavzu_uz_for_fts, ar, q.get("savol_uz", ""), level, q_id, topic_id),
            )

    conn.commit()
    cur = conn.execute("SELECT COUNT(*) FROM dalil_topics")
    n_topics = cur.fetchone()[0]
    log.info("Dalil DB: %d mavzu, %d savol → %s", n_topics, n_q, db_path)
    cur = conn.execute(
        "SELECT level, COUNT(*) FROM dalil_questions GROUP BY level ORDER BY level"
    )
    for level, count in cur.fetchall():
        log.info("  %s: %d", level, count)
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabir", type=Path,
                    default=ROOT / "scripts/ingest_books/out/tabir_translated.jsonl")
    ap.add_argument("--dalil", type=Path,
                    default=ROOT / "scripts/ingest_books/out/dalil.jsonl")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    if args.tabir.exists():
        build_tabir(args.tabir, args.data_dir / "tabir.db")
    else:
        log.warning("Tabir JSONL topilmadi: %s — o'tkazib yuborildi", args.tabir)

    if args.dalil.exists():
        build_dalil(args.dalil, args.data_dir / "dalil.db")
    else:
        log.warning("Dalil JSONL topilmadi: %s — o'tkazib yuborildi", args.dalil)


if __name__ == "__main__":
    main()
