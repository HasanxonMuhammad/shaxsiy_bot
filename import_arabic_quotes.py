"""
Arabic Quotes Dataset (5900+) ni yuklab, o'zbekchaga tarjima qilib amthal.db ga qo'shish.
Manba: github.com/BoulahiaAhmed/Arabic-Quotes-Dataset

Ishlatish:
    python import_arabic_quotes.py

Faqat 1 ta Gemini kalit ishlatiladi — tejamkorlik uchun.
"""
import csv
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)

DB_PATH = Path("data/amthal.db")
CSV_CACHE = Path("data/arabic_quotes_cache.csv")
CSV_URL = (
    "https://raw.githubusercontent.com/BoulahiaAhmed/Arabic-Quotes-Dataset"
    "/main/Arabic_Quotes.csv"
)
MODEL = "gemini-2.5-flash"
BATCH = 40  # 1 so'rovda 40 ta — tejamkor


def get_api_key() -> str:
    """Faqat 1 ta yaxshi kalit olish."""
    keys_line = ""
    for env_file in [".env", ".env.aziza", ".env.superboshliq"]:
        if Path(env_file).exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEYS="):
                        keys_line = line.strip().split("=", 1)[1]
                        break
        if keys_line:
            break

    key = os.getenv("GEMINI_API_KEY", "")
    if key:
        return key

    blocked = ["fYB3jY", "Sxu4"]
    keys = [k.strip() for k in keys_line.split(",") if k.strip()]
    good = [k for k in keys if not any(k.endswith(b) for b in blocked)]
    if not good:
        raise ValueError("Hech qanday ishlaydigan Gemini kalit topilmadi")
    return good[0]  # FAQAT BITTA


def download_csv():
    if CSV_CACHE.exists():
        print(f"CSV kesh topildi: {CSV_CACHE}")
        return
    print(f"CSV yuklanmoqda...")
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    CSV_CACHE.write_bytes(data)
    print(f"Yuklandi: {len(data) // 1024} KB")


def load_csv() -> list[dict]:
    rows = []
    with open(CSV_CACHE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            quote = (row.get("quote") or row.get("Quote") or "").strip()
            tags = (row.get("tags") or row.get("Tags") or "").strip()
            if quote:
                rows.append({"arabcha": quote, "tags": tags})
    return rows


def gemini_translate(items: list[dict], api_key: str) -> list[str]:
    """40 ta arabcha iqtibosni o'zbekchaga o'girish."""
    lines = []
    for i, it in enumerate(items):
        lines.append(f"{i+1}. {it['arabcha']}")

    prompt = f"""Quyidagi arabcha iqtiboslarni (quotes) o'zbek tiliga tarjima qil.
Har bir iqtibos uchun faqat o'zbekcha tarjima yaz — qisqa, adabiy, aniq.
JSON massivi sifatida qaytargin: ["tarjima1", "tarjima2", ...]
Faqat JSON, boshqa hech narsa yozma.

{chr(10).join(lines)}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1:
        raise ValueError(f"JSON topilmadi: {text[:200]}")
    return json.loads(text[start:end])


def init_db(conn: sqlite3.Connection):
    """amthal jadvalini tekshirish, tarjima_uz ustunini qo'shish."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(amthal)").fetchall()]
    if "tarjima_uz" not in cols:
        conn.execute("ALTER TABLE amthal ADD COLUMN tarjima_uz TEXT DEFAULT ''")
    if "tags" not in cols:
        conn.execute("ALTER TABLE amthal ADD COLUMN tags TEXT DEFAULT ''")
    conn.commit()


def rebuild_fts(conn: sqlite3.Connection):
    print("FTS qayta qurilmoqda...")
    conn.executescript("""
        DROP TABLE IF EXISTS amthal_fts;
        CREATE VIRTUAL TABLE amthal_fts
            USING fts5(
                arabcha,
                tarjima,
                tarjima_uz,
                izoh,
                content='amthal',
                content_rowid='id',
                tokenize='unicode61'
            );
        INSERT INTO amthal_fts(amthal_fts) VALUES('rebuild');
    """)
    conn.commit()


def main():
    api_key = get_api_key()
    print(f"API kalit: ...{api_key[-8:]} (faqat 1 ta ishlatiladi)")

    download_csv()
    all_quotes = load_csv()
    print(f"CSV da jami: {len(all_quotes)} ta iqtibos")

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # Allaqachon bazada borlarini filtr
    existing = set(
        r[0] for r in conn.execute("SELECT arabcha FROM amthal").fetchall()
    )
    new_quotes = [q for q in all_quotes if q["arabcha"] not in existing]
    print(f"Yangi (bazada yo'q): {len(new_quotes)} ta")

    if not new_quotes:
        print("Barcha iqtiboslar allaqachon bazada bor!")
        rebuild_fts(conn)
        conn.close()
        return

    cur = conn.cursor()
    done = 0
    errors = 0

    for i in range(0, len(new_quotes), BATCH):
        batch = new_quotes[i:i + BATCH]
        try:
            translations = gemini_translate(batch, api_key)

            for j, item in enumerate(batch):
                uz = translations[j].strip() if j < len(translations) else ""
                cur.execute(
                    "INSERT INTO amthal (arabcha, tarjima, tarjima_uz, izoh, manba, tags) "
                    "VALUES (?, '', ?, '', 'arabic_quotes', ?)",
                    (item["arabcha"], uz, item.get("tags", ""))
                )
            conn.commit()
            done += len(batch)
            pct = done / len(new_quotes) * 100
            print(f"  {done}/{len(new_quotes)} ({pct:.0f}%) ...")

            # Rate limit — 1 kalit bilan ehtiyotkorlik
            time.sleep(2)

        except Exception as e:
            errors += 1
            print(f"  Xato (batch {i // BATCH + 1}): {e}")
            if errors >= 5:
                print("Ko'p xato — to'xtatildi. Qayta ishga tushiring.")
                break
            time.sleep(10)

    rebuild_fts(conn)
    conn.close()

    total = conn_count()
    print(f"\nTayyor! {done} ta yangi iqtibos qo'shildi.")
    print(f"Amthal bazasida jami: {total} ta")


def conn_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM amthal").fetchone()[0]
    conn.close()
    return n


if __name__ == "__main__":
    main()
