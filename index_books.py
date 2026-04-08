#!/usr/bin/env python3
"""
Kitoblarni SQLite FTS5 ga indekslash skripti.

Ishlatish:
    python3 index_books.py                     # data/ papkasidan o'qiydi
    python3 index_books.py /manzil/kitoblar/   # boshqa papka
    python3 index_books.py fayl.txt            # yagona fayl

Qo'llab-quvvatlanadigan formatlar:
    .txt  — oddiy matn
    .pdf  — pdfminer.six kutubxonasi orqali (pip install pdfminer.six)
    .md   — markdown matn

Til aniqlash:
    Fayl nomi yoki papka nomida "ar", "arab" -> arabcha
    Fayl nomi yoki papka nomida "uz", "uzb"  -> o'zbekcha
    Boshqacha -> "other"
"""
import re
import sqlite3
import sys
from pathlib import Path

# Loyiha ildizi
BASE_DIR = Path(__file__).parent

CHUNK_SIZE = 800     # belgi
CHUNK_OVERLAP = 100  # belgi qayta-qayta olish

DB_PATH = BASE_DIR / "data" / "kitoblar.db"
DEFAULT_BOOK_DIR = BASE_DIR / "data" / "kitoblar"


# ── DB initsializatsiya ─────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kitoblar (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            filename    TEXT NOT NULL UNIQUE,
            lang        TEXT NOT NULL DEFAULT 'other',
            chunk_count INTEGER DEFAULT 0,
            indexed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS kitob_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kitob_id    INTEGER NOT NULL REFERENCES kitoblar(id),
            chunk_idx   INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS kitob_fts
            USING fts5(chunk_text, content='kitob_chunks', content_rowid='id',
                       tokenize='unicode61');
    """)
    conn.commit()


# ── Til aniqlash ────────────────────────────────────────────────────────────

def detect_lang(path: Path) -> str:
    name = (path.stem + str(path.parent)).lower()
    if any(x in name for x in ("ar", "arab", "nahv", "sarf", "quran", "قران")):
        return "ar"
    if any(x in name for x in ("uz", "uzb", "o'zbek", "uzbek")):
        return "uz"
    return "other"


# ── Matn o'qish ─────────────────────────────────────────────────────────────

def read_txt(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "windows-1256", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def read_pdf(path: Path) -> str:
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(path))
    except ImportError:
        print("  [!]  pdfminer.six yo'q. O'rnatish: pip install pdfminer.six")
        return ""
    except Exception as e:
        print(f"  [!]  PDF o'qish xatosi: {e}")
        return ""


def read_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return read_pdf(path)
    if ext in (".txt", ".md", ".text"):
        return read_txt(path)
    print(f"  [!]  Format qo'llab-quvvatlanmaydi: {ext}")
    return ""


# ── Parchalash ──────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Matnni CHUNK_SIZE uzunligidagi parchalarga bo'ladi."""
    # Bo'sh satrlarni tozalash
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break
        # Eng yaqin gap/satr oxirida kesish
        cut = text.rfind('\n', start, end)
        if cut <= start:
            cut = text.rfind('. ', start, end)
        if cut <= start:
            cut = end
        chunk = text[start:cut].strip()
        if chunk:
            chunks.append(chunk)
        start = cut - CHUNK_OVERLAP
        if start < 0:
            start = 0
    return chunks


# ── Indekslash ──────────────────────────────────────────────────────────────

def index_file(conn: sqlite3.Connection, path: Path, force: bool = False):
    filename = str(path.resolve())
    title = path.stem.replace("_", " ").replace("-", " ")
    lang = detect_lang(path)

    # Allaqachon indekslangan?
    cur = conn.cursor()
    cur.execute("SELECT id FROM kitoblar WHERE filename = ?", (filename,))
    existing = cur.fetchone()
    if existing and not force:
        print(f"  --  '{title}' allaqachon indekslangan, o'tkazib yuborildi")
        return

    print(f"  >> '{title}' ({lang}) o'qilmoqda...")
    text = read_file(path)
    if not text.strip():
        print(f"  [!]  Matn bo'sh, o'tkazib yuborildi")
        return

    chunks = chunk_text(text)
    print(f"     {len(text):,} belgi -> {len(chunks)} parcha")

    # Eski yozuvlarni o'chirish
    if existing:
        kitob_id = existing[0]
        conn.execute("DELETE FROM kitob_fts WHERE rowid IN (SELECT id FROM kitob_chunks WHERE kitob_id = ?)", (kitob_id,))
        conn.execute("DELETE FROM kitob_chunks WHERE kitob_id = ?", (kitob_id,))
        conn.execute("DELETE FROM kitoblar WHERE id = ?", (kitob_id,))

    # Yangi yozuv
    cur.execute(
        "INSERT INTO kitoblar (title, filename, lang, chunk_count) VALUES (?, ?, ?, ?)",
        (title, filename, lang, len(chunks))
    )
    kitob_id = cur.lastrowid

    for idx, chunk in enumerate(chunks):
        cur.execute(
            "INSERT INTO kitob_chunks (kitob_id, chunk_idx, chunk_text) VALUES (?, ?, ?)",
            (kitob_id, idx, chunk)
        )
        rowid = cur.lastrowid
        cur.execute("INSERT INTO kitob_fts (rowid, chunk_text) VALUES (?, ?)", (rowid, chunk))

    conn.commit()
    print(f"  OK '{title}' indekslandi ({len(chunks)} parcha)")


def run(targets: list[Path], force: bool = False):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    files = []
    for t in targets:
        if t.is_file():
            files.append(t)
        elif t.is_dir():
            for ext in ("*.txt", "*.pdf", "*.md"):
                files.extend(sorted(t.glob(ext)))
        else:
            print(f"[!]  Topilmadi: {t}")

    if not files:
        print("Indekslanadigan fayl topilmadi.")
        print(f"Kitoblarni shu papkaga joylashtiring: {DEFAULT_BOOK_DIR}")
        return

    print(f"\n{len(files)} ta fayl topildi:\n")
    for f in files:
        index_file(conn, f, force=force)

    # FTS rebuild
    conn.execute("INSERT INTO kitob_fts(kitob_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    # Statistika
    conn2 = sqlite3.connect(DB_PATH)
    cur = conn2.cursor()
    cur.execute("SELECT COUNT(*) FROM kitoblar")
    nb = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM kitob_chunks")
    nc = cur.fetchone()[0]
    conn2.close()
    print(f"\nOK Jami: {nb} kitob, {nc} parcha -> {DB_PATH}")


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if a != "--force"]

    if args:
        targets = [Path(a) for a in args]
    else:
        targets = [DEFAULT_BOOK_DIR]
        DEFAULT_BOOK_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Papka: {DEFAULT_BOOK_DIR}")
        print("Kitoblarni (.txt, .pdf, .md) shu papkaga joylashtiring va qayta ishga tushiring.\n")

    run(targets, force=force)
