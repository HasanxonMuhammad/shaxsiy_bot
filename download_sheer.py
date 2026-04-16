"""
Qafiyah she'rlarini HuggingFace Parquet dan yuklab SQLite FTS5 ga indekslash.
Dataset: qafiyah/classical-arabic-poetry (84K she'r, 944K+ bayt)

Ishlatish:
    python download_sheer.py
"""
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path("data/sheer.db")
PARQUET_URL = (
    "https://huggingface.co/datasets/qafiyah/classical-arabic-poetry"
    "/resolve/main/data/train-00000-of-00001.parquet"
)
# Fallback (API URL)
PARQUET_API_URL = (
    "https://huggingface.co/api/datasets/qafiyah/classical-arabic-poetry"
    "/parquet/default/train/0.parquet"
)


def download_parquet(url: str, dest: Path) -> bool:
    """Parquet faylni yuklab olish."""
    print(f"Yuklab olinmoqda: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/octet-stream",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 1024 * 1024  # 1 MB
            with open(dest, "wb") as f:
                while True:
                    data = resp.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if total:
                        pct = downloaded / total * 100
                        print(f"\r  {downloaded/1024/1024:.1f} MB / {total/1024/1024:.1f} MB ({pct:.0f}%)", end="", flush=True)
            print()
        return True
    except Exception as e:
        print(f"\n  Xato: {e}")
        return False


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ashaar (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            shoir       TEXT DEFAULT '',
            davr        TEXT DEFAULT '',
            vazn        TEXT DEFAULT '',
            mavzu       TEXT DEFAULT '',
            qofiya      TEXT DEFAULT '',
            sheer_nomi  TEXT DEFAULT '',
            bayt        TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS ashaar_fts
            USING fts5(
                shoir,
                davr,
                vazn,
                mavzu,
                bayt,
                content='ashaar',
                content_rowid='id',
                tokenize='unicode61'
            );
    """)
    conn.commit()


def load_and_index(parquet_path: Path):
    """Parquet fayldan o'qib SQLite ga indekslash."""
    import pandas as pd

    print("Parquet o'qilmoqda...")
    df = pd.read_parquet(parquet_path)
    print(f"Yuklandi: {len(df)} ta she'r")
    print(f"Ustunlar: {list(df.columns)}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # Eski ma'lumotlarni tozalash
    conn.executescript("DELETE FROM ashaar_fts; DELETE FROM ashaar;")
    conn.commit()

    cur = conn.cursor()
    total_bayt = 0
    batch = []
    BATCH_SIZE = 5000

    for i, row in df.iterrows():
        shoir = str(row.get("poet_name", "") or "")
        davr = str(row.get("era_name", "") or "")
        vazn = str(row.get("meter_name", "") or "")
        mavzu = str(row.get("theme_name", "") or "")
        qofiya = str(row.get("rhyme_pattern", "") or "")
        sheer_nomi = str(row.get("title", "") or "")

        # Baytlarni alohida satrlar sifatida olish
        raw_verses = row.get("verses", None)
        try:
            verses = list(raw_verses) if raw_verses is not None else []
        except Exception:
            verses = []
        if not verses:
            content = row.get("content", "")
            if content and str(content).strip():
                verses = [v.strip() for v in str(content).split("\n") if v.strip()]

        for bayt in verses:
            bayt = str(bayt).strip()
            if not bayt or len(bayt) < 5:
                continue
            batch.append((shoir, davr, vazn, mavzu, qofiya, sheer_nomi, bayt))
            total_bayt += 1

        # Batch commit
        if len(batch) >= BATCH_SIZE:
            cur.executemany(
                "INSERT INTO ashaar (shoir, davr, vazn, mavzu, qofiya, sheer_nomi, bayt) VALUES (?,?,?,?,?,?,?)",
                batch
            )
            conn.commit()
            batch = []
            print(f"\r  {total_bayt:,} bayt indekslandi...", end="", flush=True)

    # Qolgan batch
    if batch:
        cur.executemany(
            "INSERT INTO ashaar (shoir, davr, vazn, mavzu, qofiya, sheer_nomi, bayt) VALUES (?,?,?,?,?,?,?)",
            batch
        )
        conn.commit()

    print(f"\nFTS rebuild boshlandi ({total_bayt:,} bayt)...")
    conn.execute("INSERT INTO ashaar_fts(ashaar_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    print(f"Tayyor! {total_bayt:,} bayt indekslandi -> {DB_PATH}")


def main():
    parquet_cache = Path("data/qafiyah_cache.parquet")

    MIN_SIZE = 50 * 1024 * 1024  # 50 MB — to'liq bo'lmagan faylni qayta yuklab olish
    if parquet_cache.exists() and parquet_cache.stat().st_size < MIN_SIZE:
        print(f"Kesh fayl to'liq emas ({parquet_cache.stat().st_size // 1024 // 1024} MB), qayta yuklanmoqda...")
        parquet_cache.unlink()

    if not parquet_cache.exists():
        print("Parquet fayl yuklab olinmoqda (190 MB)...")
        ok = download_parquet(PARQUET_URL, parquet_cache)
        if not ok:
            print("Birinchi URL ishlamadi, API URL sinab ko'rilmoqda...")
            ok = download_parquet(PARQUET_API_URL, parquet_cache)
        if not ok:
            print("Yuklab bo'lmadi!")
            return
    else:
        print(f"Kesh topildi: {parquet_cache} ({parquet_cache.stat().st_size // 1024 // 1024} MB)")

    load_and_index(parquet_cache)


if __name__ == "__main__":
    main()
