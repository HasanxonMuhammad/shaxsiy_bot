#!/usr/bin/env python3
"""
hadis.islom.uz saytidan barcha hadislarni yuklab olish va SQLite FTS5 ga indekslash.

Ishlatish:
    python scrape_hadis.py           # yuklab olish + indekslash
    python scrape_hadis.py --force   # qayta yuklab olish

Sayt tuzilishi:
    /kitob/{id}       — boblar ro'yxati
    /kitob/{id}/{bob} — bobdagi barcha hadislar (inline, <div id="hadisN">)
    /kitob/{id}/{bob}/{num} — bitta hadis (ba'zi kitoblarda)

Hadis HTML bloki:
    <div id="hadisN">
      <h2>SARLAVHA</h2>
      <p class="text-gray-600">O'zbekcha matn</p>
      <div class="text-right">Arabcha matn</div>
    </div>
"""
import json
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from urllib.request import urlopen, Request

BASE_URL = "https://hadis.islom.uz"
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "hadislar.db"
JSON_CACHE = BASE_DIR / "data" / "hadislar_raw.json"

# Kitob ID lari (asosiy sahifadan)
KITOBLAR = {
    17: "Ал-Азкор",
    22: "101 ҳадис",
    23: "2002 ҳадис",
    24: "Арбаъин",
    25: "Шамоилул-Муҳаммадия",
    26: "Саҳиҳул Бухорийнинг мухтасари",
    27: "Имомлари иттифоқ қилган ҳадислар",
    28: "Риёзус солиҳийн",
    29: "Риёзус солиҳийн (болалар учун)",
    30: "Забидий",
    32: "Риёзус солиҳийн шарҳи",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def fetch(url: str) -> str:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def clean_html(text: str) -> str:
    """HTML taglarni olib tashlash va entity decode."""
    # Script va style bloklarini tozalash
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = unescape(text)
    # JavaScript qoldiqlarini tozalash
    text = re.sub(r'function\s+\w+\s*\([^)]*\)\s*\{[^}]*\}', '', text, flags=re.DOTALL)
    text = re.sub(r'(?:var|let|const|if|for|while|document|window)\b[^;]*;', '', text)
    text = re.sub(r'\bclassList\b.*?;', '', text)
    # Keraksiz bo'sh belgilarni tozalash
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # BOM va boshqa maxsus belgilarni olib tashlash
    text = text.replace('\ufeff', '')
    # "Nusxa olish" va shunga o'xshash UI matnlarini tozalash
    text = re.sub(r'Нусха олиш|Юклаш|Улашиш', '', text)
    return text.strip()


def extract_arabic(html_block: str) -> str:
    """text-right div dan arabcha matnni olish."""
    m = re.search(r'class="[^"]*text-right[^"]*"[^>]*>(.*?)</div>', html_block, re.DOTALL)
    if m:
        return clean_html(m.group(1))
    # Fallback — Unicode Arabic range orqali
    arabic = re.findall(r'[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s,.:;()]{20,}', html_block)
    return max(arabic, key=len).strip() if arabic else ""


def extract_uzbek(html_block: str) -> str:
    """p taglardan o'zbekcha matnni olish (arabcha qismdan tashqari)."""
    # text-right ni olib tashlash
    block = re.sub(r'<div[^>]*class="[^"]*text-right[^"]*"[^>]*>.*?</div>', '', html_block, flags=re.DOTALL)
    # h2 sarlavhani olib tashlash (alohida olinadi)
    block = re.sub(r'<h2[^>]*>.*?</h2>', '', block, flags=re.DOTALL)
    # Tugmalar va linklar tozalash
    block = re.sub(r'<(?:button|a)[^>]*>.*?</(?:button|a)>', '', block, flags=re.DOTALL)
    block = re.sub(r'<div[^>]*class="[^"]*flex[^"]*"[^>]*>.*?</div>', '', block, flags=re.DOTALL)
    return clean_html(block)


def extract_title(html_block: str) -> str:
    """h2 tagdan sarlavhani olish."""
    m = re.search(r'<h2[^>]*>(.*?)</h2>', html_block, re.DOTALL)
    return clean_html(m.group(1)) if m else ""


def parse_chapter_page(html: str, kitob_id: int, kitob_nomi: str, bob_id: int) -> list[dict]:
    """Bob sahifasidagi hadislarni parse qilish."""
    hadiths = []

    # <div id="hadisN"> bloklarini topish
    # Har bir blok keyingi hadis blokigacha yoki sahifa oxirigacha
    pattern = r'<div[^>]*id="hadis(\d+)"[^>]*>'
    positions = [(m.start(), int(m.group(1))) for m in re.finditer(pattern, html)]

    if positions:
        for i, (start, num) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(html)
            block = html[start:end]

            title = extract_title(block)
            arabic = extract_arabic(block)
            uzbek = extract_uzbek(block)

            if uzbek or arabic:
                hadiths.append({
                    "kitob_id": kitob_id,
                    "kitob_nomi": kitob_nomi,
                    "bob_id": bob_id,
                    "hadis_raqam": num,
                    "sarlavha": title,
                    "arabcha": arabic,
                    "uzbekcha": uzbek,
                })
        return hadiths

    # Fallback: individual hadis linklar mavjud bo'lsa
    hadith_links = re.findall(rf'/kitob/{kitob_id}/{bob_id}/(\d+)', html)
    hadith_nums = sorted(set(int(n) for n in hadith_links))

    if hadith_nums:
        print(f"    → {len(hadith_nums)} ta alohida hadis sahifasi", end="", flush=True)
        for num in hadith_nums:
            h = scrape_single_hadith(kitob_id, kitob_nomi, bob_id, num)
            if h:
                hadiths.append(h)
            time.sleep(0.3)
        return hadiths

    # Hech narsa topilmadi — butun sahifa matnini olish
    text = clean_html(html)
    if len(text) > 50:
        hadiths.append({
            "kitob_id": kitob_id,
            "kitob_nomi": kitob_nomi,
            "bob_id": bob_id,
            "hadis_raqam": 0,
            "sarlavha": "",
            "arabcha": "",
            "uzbekcha": text[:5000],
        })

    return hadiths


def scrape_single_hadith(kitob_id: int, kitob_nomi: str, bob_id: int, num: int) -> dict | None:
    """Bitta hadisni alohida sahifasidan yuklab olish."""
    url = f"{BASE_URL}/kitob/{kitob_id}/{bob_id}/{num}"
    try:
        html = fetch(url)
        # Script/style/nav tozalash
        clean = html
        for tag in ('script', 'style', 'nav', 'header', 'footer'):
            clean = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', clean, flags=re.DOTALL)

        arabic = extract_arabic(clean)
        title = extract_title(clean)

        # O'zbekcha matnni olish
        # main content qismini topish
        uzbek_parts = re.findall(r'<p[^>]*>(.*?)</p>', clean, re.DOTALL)
        uzbek_texts = []
        for p in uzbek_parts:
            t = clean_html(p)
            # Arabcha matnni o'tkazib yuborish
            if t and not re.match(r'^[\u0600-\u06FF\s]+$', t):
                uzbek_texts.append(t)
        uzbek = "\n".join(uzbek_texts)

        if uzbek or arabic:
            return {
                "kitob_id": kitob_id,
                "kitob_nomi": kitob_nomi,
                "bob_id": bob_id,
                "hadis_raqam": num,
                "sarlavha": title,
                "arabcha": arabic,
                "uzbekcha": uzbek,
            }
    except Exception as e:
        print(f".", end="", flush=True)
    return None


def get_chapters(kitob_id: int) -> list[int]:
    """Kitobdagi boblar ro'yxatini olish."""
    try:
        html = fetch(f"{BASE_URL}/kitob/{kitob_id}")
        chapters = re.findall(rf'/kitob/{kitob_id}/(\d+)', html)
        result = sorted(set(int(c) for c in chapters))
        return result if result else [1]
    except Exception as e:
        print(f"  [!] Boblar yuklanmadi: {e}")
        return [1]


def scrape_all(force: bool = False) -> list[dict]:
    """Barcha kitoblardan hadislarni yuklab olish."""
    if JSON_CACHE.exists() and not force:
        print(f"Kesh topildi: {JSON_CACHE}")
        print("Qayta yuklash uchun: python scrape_hadis.py --force\n")
        with open(JSON_CACHE, encoding="utf-8") as f:
            return json.load(f)

    all_hadiths = []

    for kitob_id, kitob_nomi in KITOBLAR.items():
        print(f"\n{'='*60}")
        print(f"  {kitob_nomi} (ID: {kitob_id})")
        print(f"{'='*60}")

        chapters = get_chapters(kitob_id)
        print(f"  Boblar: {len(chapters)}")

        kitob_count = 0
        for bob_id in chapters:
            print(f"  Bob {bob_id}...", end=" ", flush=True)
            try:
                html = fetch(f"{BASE_URL}/kitob/{kitob_id}/{bob_id}")
                hadiths = parse_chapter_page(html, kitob_id, kitob_nomi, bob_id)
                all_hadiths.extend(hadiths)
                kitob_count += len(hadiths)
                print(f"{len(hadiths)} hadis")
                time.sleep(0.5)
            except Exception as e:
                print(f"[!] Xato: {e}")
                continue

        print(f"  Jami: {kitob_count} hadis")

    # Keshga saqlash
    JSON_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(JSON_CACHE, "w", encoding="utf-8") as f:
        json.dump(all_hadiths, f, ensure_ascii=False, indent=2)
    print(f"\n{'='*60}")
    print(f"JAMI: {len(all_hadiths)} hadis yuklab olindi -> {JSON_CACHE}")

    return all_hadiths


# ── SQLite FTS5 indekslash ──────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS hadis_kitoblar (
            id          INTEGER PRIMARY KEY,
            nomi        TEXT NOT NULL,
            hadis_soni  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS hadislar (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kitob_id    INTEGER NOT NULL,
            bob_id      INTEGER DEFAULT 0,
            hadis_raqam INTEGER DEFAULT 0,
            kitob_nomi  TEXT NOT NULL,
            sarlavha    TEXT DEFAULT '',
            arabcha     TEXT DEFAULT '',
            uzbekcha    TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS hadis_fts
            USING fts5(
                kitob_nomi,
                sarlavha,
                arabcha,
                uzbekcha,
                content='hadislar',
                content_rowid='id',
                tokenize='unicode61'
            );
    """)
    conn.commit()


def index_hadiths(hadiths: list[dict]):
    """Hadislarni SQLite FTS5 ga indekslash."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    # Eski ma'lumotlarni tozalash
    conn.executescript("""
        DELETE FROM hadis_fts;
        DELETE FROM hadislar;
        DELETE FROM hadis_kitoblar;
    """)

    cur = conn.cursor()

    # Kitoblarni qo'shish
    for kitob_id, kitob_nomi in KITOBLAR.items():
        count = sum(1 for h in hadiths if h.get("kitob_id") == kitob_id)
        cur.execute(
            "INSERT OR REPLACE INTO hadis_kitoblar (id, nomi, hadis_soni) VALUES (?, ?, ?)",
            (kitob_id, kitob_nomi, count)
        )

    # Hadislarni qo'shish
    inserted = 0
    for h in hadiths:
        uzbekcha = h.get("uzbekcha", "").strip()
        arabcha = h.get("arabcha", "").strip()
        if not uzbekcha and not arabcha:
            continue
        if len(uzbekcha) < 10 and len(arabcha) < 10:
            continue

        cur.execute(
            """INSERT INTO hadislar
               (kitob_id, bob_id, hadis_raqam, kitob_nomi, sarlavha, arabcha, uzbekcha)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (h["kitob_id"], h.get("bob_id", 0), h.get("hadis_raqam", 0),
             h.get("kitob_nomi", ""), h.get("sarlavha", ""), arabcha, uzbekcha)
        )
        rowid = cur.lastrowid
        cur.execute(
            "INSERT INTO hadis_fts (rowid, kitob_nomi, sarlavha, arabcha, uzbekcha) VALUES (?, ?, ?, ?, ?)",
            (rowid, h.get("kitob_nomi", ""), h.get("sarlavha", ""), arabcha, uzbekcha)
        )
        inserted += 1

    # FTS rebuild
    conn.execute("INSERT INTO hadis_fts(hadis_fts) VALUES('rebuild')")
    conn.commit()

    # Statistika
    cur.execute("SELECT nomi, hadis_soni FROM hadis_kitoblar ORDER BY id")
    print("\nKitoblar:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]} hadis")

    conn.close()
    print(f"\nJami: {inserted} hadis indekslandi -> {DB_PATH}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    print("hadis.islom.uz — O'zbekcha hadislar yuklovchi")
    print("=" * 60)

    hadiths = scrape_all(force=force)
    if hadiths:
        index_hadiths(hadiths)
    else:
        print("Hadislar topilmadi!")
