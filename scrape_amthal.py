"""
Arabcha amthallarni scrape qilish va SQLite FTS5 ga indekslash.
Manba: simple.wikiquote.org/wiki/Arabic_proverbs

Ishlatish:
    python scrape_amthal.py
"""
import re
import sqlite3
import sys
import time
import urllib.request
from html import unescape
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path("data/amthal.db")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def is_arabic(text: str) -> bool:
    """Matnda arabcha harflar bormi."""
    return bool(re.search(r"[\u0600-\u06FF]", text))


def parse_simple_wikiquote(html: str) -> list[dict]:
    """simple.wikiquote.org/wiki/Arabic_proverbs dan amthal olish."""
    results = []

    # Asosiy kontent qismini olish
    m = re.search(r'<div[^>]*class="mw-parser-output"[^>]*>(.*?)</div>\s*</div>',
                  html, re.DOTALL)
    content = m.group(1) if m else html

    # Paragraf va ul/li bloklarni juftlashtirish
    # Pattern: <p>ARABIC</p><ul><li>ENGLISH</li>...
    blocks = re.split(r'(?=<p[^>]*>)', content)

    for block in blocks:
        # p tegidin arabcha matn
        p_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        if not p_match:
            continue
        arabic = clean(p_match.group(1))
        if not arabic or not is_arabic(arabic):
            continue

        # Birinchi li dan tarjima
        li_matches = re.findall(r'<li[^>]*>(.*?)</li>', block, re.DOTALL)
        tarjima = ""
        izoh = ""
        for li in li_matches:
            text = clean(li)
            if not text:
                continue
            # "Simple:" bilan boshlanuvchi — izoh
            if re.match(r"Simple[\s:]*", text, re.IGNORECASE):
                izoh = re.sub(r"^Simple[\s:]*", "", text, flags=re.IGNORECASE).strip()
            elif not tarjima:
                tarjima = text

        if arabic:
            results.append({
                "arabcha": arabic,
                "tarjima": tarjima,
                "izoh": izoh,
                "manba": "wikiquote",
            })

    return results


def parse_en_wikiquote(html: str) -> list[dict]:
    """en.wikiquote.org/wiki/Arabic_proverbs dan amthal olish."""
    results = []
    # li teglarda arabcha + tarjima
    items = re.findall(r'<li[^>]*>(.*?)</li>', html, re.DOTALL)
    for item in items:
        text = clean(item)
        if not text or len(text) < 5:
            continue
        # Arabcha qism bormi
        if is_arabic(text):
            # Arabcha va inglizchani ajratish
            parts = re.split(r'\s*[–—-]\s*', text, maxsplit=1)
            if len(parts) == 2:
                arabic = parts[0].strip()
                tarjima = parts[1].strip()
                if is_arabic(arabic) and len(arabic) > 3:
                    results.append({
                        "arabcha": arabic,
                        "tarjima": tarjima,
                        "izoh": "",
                        "manba": "wikiquote_en",
                    })
    return results


def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS amthal (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            arabcha  TEXT NOT NULL,
            tarjima  TEXT DEFAULT '',
            izoh     TEXT DEFAULT '',
            manba    TEXT DEFAULT ''
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS amthal_fts
            USING fts5(
                arabcha,
                tarjima,
                izoh,
                content='amthal',
                content_rowid='id',
                tokenize='unicode61'
            );
    """)
    conn.commit()


def index_amthal(records: list[dict]):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # Eski ma'lumotlarni tozalash
    conn.executescript("DELETE FROM amthal_fts; DELETE FROM amthal;")
    conn.commit()

    cur = conn.cursor()
    seen = set()
    inserted = 0
    for r in records:
        key = r["arabcha"][:50]
        if key in seen:
            continue
        seen.add(key)
        cur.execute(
            "INSERT INTO amthal (arabcha, tarjima, izoh, manba) VALUES (?,?,?,?)",
            (r["arabcha"], r["tarjima"], r["izoh"], r["manba"])
        )
        inserted += 1

    # FTS rebuild
    conn.execute("INSERT INTO amthal_fts(amthal_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print(f"Jami {inserted} ta amthal indekslandi -> {DB_PATH}")


def main():
    all_records = []

    # 1. simple.wikiquote.org
    print("1. simple.wikiquote.org dan yuklab olinmoqda...")
    try:
        html = fetch("https://simple.wikiquote.org/wiki/Arabic_proverbs")
        records = parse_simple_wikiquote(html)
        print(f"   {len(records)} ta amthal topildi")
        all_records.extend(records)
    except Exception as e:
        print(f"   Xato: {e}")

    time.sleep(1)

    # 2. en.wikiquote.org
    print("2. en.wikiquote.org dan yuklab olinmoqda...")
    try:
        html = fetch("https://en.wikiquote.org/wiki/Arabic_proverbs")
        records = parse_en_wikiquote(html)
        print(f"   {len(records)} ta amthal topildi")
        all_records.extend(records)
    except Exception as e:
        print(f"   Xato: {e}")

    print(f"\nJami: {len(all_records)} ta amthal (dublikatlar olib tashlanadi)")
    index_amthal(all_records)
    print("Tayyor!")


if __name__ == "__main__":
    main()
