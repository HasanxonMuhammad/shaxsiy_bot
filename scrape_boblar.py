"""
hadis.islom.uz — barcha kitoblar bob nomlarini scrape qilish.
hadis_boblar jadvaliga yozadi: bob_id, kitob_id, nomi_uz, nomi_ar, hadis_dan, hadis_gacha

Ishlatish:
    python scrape_boblar.py
"""
import re
import sqlite3
import sys
import time
import urllib.request
from html import unescape

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = "data/hadislar.db"
BASE_URL = "https://hadis.islom.uz"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8")


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_chapters(html: str, kitob_id: int) -> list[dict]:
    """Kitob sahifasidan bob ro'yxatini olish."""
    rows = re.findall(
        r'<tr[^>]*class="table_tr_book"[^>]*>(.*?)</tr>',
        html, re.DOTALL
    )
    results = []
    for row in rows:
        # Bob raqami
        m_num = re.search(r'table_chapter_n[^>]*>(.*?)</td>', row, re.DOTALL)
        bob_num = int(clean(m_num.group(1))) if m_num else 0

        # O'zbekcha nomi (link ichida)
        m_uz = re.search(r'table_uzbek_title[^>]*>.*?<a[^>]*>(.*?)</a>', row, re.DOTALL)
        nomi_uz = clean(m_uz.group(1)) if m_uz else ""

        # Arabcha nomi
        m_ar = re.search(r'table_arabic_title[^>]*>(.*?)</td>', row, re.DOTALL)
        nomi_ar = clean(m_ar.group(1)) if m_ar else ""

        # Hadis oralig'i (masalan "1 - 15" yoki "226 - 234")
        m_range = re.search(r'table_hadith_count[^>]*>(.*?)</td>', row, re.DOTALL)
        hadis_dan = hadis_gacha = 0
        if m_range:
            range_text = clean(m_range.group(1))
            nums = re.findall(r'\d+', range_text)
            if len(nums) >= 2:
                hadis_dan, hadis_gacha = int(nums[0]), int(nums[1])
            elif len(nums) == 1:
                hadis_dan = hadis_gacha = int(nums[0])

        if bob_num and nomi_uz:
            results.append({
                "kitob_id": kitob_id,
                "bob_id": bob_num,
                "nomi_uz": nomi_uz,
                "nomi_ar": nomi_ar,
                "hadis_dan": hadis_dan,
                "hadis_gacha": hadis_gacha,
            })

    return results


def setup_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hadis_boblar (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kitob_id    INTEGER NOT NULL,
            bob_id      INTEGER NOT NULL,
            nomi_uz     TEXT DEFAULT '',
            nomi_ar     TEXT DEFAULT '',
            hadis_dan   INTEGER DEFAULT 0,
            hadis_gacha INTEGER DEFAULT 0,
            UNIQUE(kitob_id, bob_id)
        )
    """)
    conn.commit()
    print("hadis_boblar jadvali tayyor.")


def main():
    conn = sqlite3.connect(DB_PATH)
    setup_table(conn)
    cur = conn.cursor()

    total = 0
    for kitob_id, kitob_nomi in KITOBLAR.items():
        print(f"\n{kitob_nomi} (ID:{kitob_id})...", end=" ", flush=True)
        try:
            html = fetch(f"{BASE_URL}/kitob/{kitob_id}")
            chapters = parse_chapters(html, kitob_id)

            for ch in chapters:
                cur.execute("""
                    INSERT OR REPLACE INTO hadis_boblar
                        (kitob_id, bob_id, nomi_uz, nomi_ar, hadis_dan, hadis_gacha)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (ch["kitob_id"], ch["bob_id"], ch["nomi_uz"],
                      ch["nomi_ar"], ch["hadis_dan"], ch["hadis_gacha"]))

            conn.commit()
            total += len(chapters)
            print(f"{len(chapters)} bob")
            time.sleep(0.5)

        except Exception as e:
            print(f"XATO: {e}")

    conn.close()
    print(f"\nJami {total} bob saqlandi.")

    # Natijadan namuna
    conn2 = sqlite3.connect(DB_PATH)
    conn2.row_factory = sqlite3.Row
    cur2 = conn2.cursor()
    cur2.execute("""
        SELECT kitob_id, bob_id, nomi_uz, hadis_dan, hadis_gacha
        FROM hadis_boblar LIMIT 5
    """)
    print("\nNamuna:")
    for r in cur2.fetchall():
        print(f"  [{r['kitob_id']}:{r['bob_id']}] {r['nomi_uz'][:50]} ({r['hadis_dan']}-{r['hadis_gacha']})")
    conn2.close()


if __name__ == "__main__":
    main()
