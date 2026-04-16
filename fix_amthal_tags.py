"""
Amthal teglarini tozalash va o'zbekchaga tarjima qilish.
1. tags ustunidagi ['...', '...'] formatini tozalash
2. tags_uz ustuniga o'zbekcha teglar yozish
3. FTS rebuild

Ishlatish:
    python fix_amthal_tags.py
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)

DB_PATH = Path("data/amthal.db")
MODEL = "gemini-2.5-flash"


def get_api_key() -> str:
    blocked = ["fYB3jY", "Sxu4"]
    key = os.getenv("GEMINI_API_KEY", "")
    if key and not any(key.endswith(b) for b in blocked):
        return key
    for env_file in [".env", ".env.aziza", ".env.superboshliq"]:
        if Path(env_file).exists():
            with open(env_file) as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEYS="):
                        keys = [k.strip() for k in line.strip().split("=", 1)[1].split(",") if k.strip()]
                        good = [k for k in keys if not any(k.endswith(b) for b in blocked)]
                        if good:
                            return good[0]
    raise ValueError("API kalit topilmadi")


def clean_tag(raw: str) -> list[str]:
    """'['sabr', 'hayot']' → ['sabr', 'hayot']"""
    # Avval to'g'ri JSON bo'lsa parse qilish
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw.replace("'", '"'))
            return [t.strip() for t in parsed if t.strip()]
        except Exception:
            pass
    # Axlat belgilarni tozalash
    raw = re.sub(r"[\[\]'\"]", "", raw)
    return [t.strip() for t in raw.split(",") if t.strip()]


def gemini_translate_tags(tags: list[str], api_key: str) -> dict[str, str]:
    """Arabcha teglar ro'yxatini o'zbekchaga o'girish. {ar: uz} dict qaytaradi."""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(tags))
    prompt = f"""Quyidagi arabcha kalit so'zlarni (teglar) o'zbek tiliga tarjima qil.
Har biri uchun qisqa, 1-2 so'zli o'zbekcha ekvivalent ber.
JSON object sifatida qaytargin: {{"arabcha": "o'zbekcha", ...}}
Faqat JSON, boshqa hech narsa yozma.

{numbered}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1:
        raise ValueError(f"JSON topilmadi: {text[:200]}")
    mapping = json.loads(text[start:end])
    return {str(k): str(v) for k, v in mapping.items()}


def main():
    api_key = get_api_key()
    print(f"API kalit: ...{api_key[-8:]}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # tags_uz ustunini qo'shish
    cols = [r[1] for r in conn.execute("PRAGMA table_info(amthal)").fetchall()]
    if "tags_uz" not in cols:
        conn.execute("ALTER TABLE amthal ADD COLUMN tags_uz TEXT DEFAULT ''")
        conn.commit()
        print("tags_uz ustuni qo'shildi")

    # 1. Barcha teglarni yig'ib, tozalash
    rows = conn.execute("SELECT id, tags FROM amthal WHERE tags IS NOT NULL AND tags != ''").fetchall()
    print(f"Tegli yozuvlar: {len(rows)}")

    # Unikal arabcha teglar
    all_arabic_tags = set()
    for row in rows:
        for t in clean_tag(row["tags"]):
            if t:
                all_arabic_tags.add(t)

    print(f"Unikal arabcha teglar: {len(all_arabic_tags)}")

    # 2. Gemini bilan tarjima — 1 zarbda (374 ta yetarli)
    print("Teglar tarjima qilinmoqda...")
    tag_list = sorted(all_arabic_tags)
    mapping = {}

    # 150 tadan batch (1 so'rovda)
    BATCH = 150
    for i in range(0, len(tag_list), BATCH):
        batch = tag_list[i:i+BATCH]
        try:
            result = gemini_translate_tags(batch, api_key)
            mapping.update(result)
            print(f"  {min(i+BATCH, len(tag_list))}/{len(tag_list)} teg tarjima qilindi")
            time.sleep(1)
        except Exception as e:
            print(f"  Xato: {e}")
            time.sleep(5)

    print(f"Jami mapping: {len(mapping)} ta")

    # 3. Har bir yozuvga tags_uz yozish + tags ni tozalash
    cur = conn.cursor()
    updated = 0
    for row in rows:
        clean = clean_tag(row["tags"])
        # Arabcha teglarni tozalangan holda qayta yozish
        tags_clean = ", ".join(clean)
        # O'zbekcha teglar
        tags_uz = ", ".join(mapping.get(t, t) for t in clean if t)

        cur.execute(
            "UPDATE amthal SET tags = ?, tags_uz = ? WHERE id = ?",
            (tags_clean, tags_uz, row["id"])
        )
        updated += 1

    conn.commit()
    print(f"{updated} ta yozuv yangilandi")

    # 4. FTS rebuild — tags_uz ni qo'shish
    print("FTS qayta qurilmoqda...")
    conn.executescript("""
        DROP TABLE IF EXISTS amthal_fts;
        CREATE VIRTUAL TABLE amthal_fts
            USING fts5(
                arabcha,
                tarjima,
                tarjima_uz,
                izoh,
                tags_uz,
                content='amthal',
                content_rowid='id',
                tokenize='unicode61'
            );
        INSERT INTO amthal_fts(amthal_fts) VALUES('rebuild');
    """)
    conn.commit()
    conn.close()

    print("\nTayyor! Namunalar:")
    # Tekshirish
    conn2 = sqlite3.connect(DB_PATH)
    conn2.row_factory = sqlite3.Row
    samples = conn2.execute(
        "SELECT tags, tags_uz FROM amthal WHERE tags_uz != '' ORDER BY RANDOM() LIMIT 5"
    ).fetchall()
    for s in samples:
        print(f"  AR: {s['tags'][:50]}")
        print(f"  UZ: {s['tags_uz'][:50]}")
        print()
    conn2.close()


if __name__ == "__main__":
    main()
