"""
Amthal tarjimalarini inglizchadan o'zbekchaga Gemini orqali o'girish.
DB ga tarjima_uz ustunini qo'shadi.

Ishlatish:
    python translate_amthal.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = Path("data/amthal.db")
API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEYS", "").split(",")[0].strip()
MODEL = "gemini-2.5-flash"
BATCH = 10


def gemini_translate(items: list[dict], api_key: str) -> list[str]:
    """
    items = [{"arabcha": ..., "tarjima": ..., "izoh": ...}, ...]
    Qaytaradi: [uz_tarjima, ...] — xuddi shu tartibda
    """
    prompt_lines = []
    for i, it in enumerate(items):
        line = f"{i+1}. AR: {it['arabcha']}"
        if it["tarjima"]:
            line += f" | EN: {it['tarjima']}"
        if it["izoh"]:
            line += f" | IZOH: {it['izoh']}"
        prompt_lines.append(line)

    prompt = f"""Quyidagi arabcha maqollarning (amthal) o'zbek tilidagi tarjimasini yoz.
Har bir maqol uchun faqat o'zbekcha tarjima yaz — qisqa, aniq, adabiy.
Arabcha matn va inglizcha tarjimadan foydalanib, o'zbek madaniyatiga mos o'girish qil.
Agar inglizcha bo'lmasa, arabcha matndan o'zgir.
JSON massivi sifatida qaytargin: ["tarjima1", "tarjima2", ...]
Faqat JSON, boshqa hech narsa yozma.

{chr(10).join(prompt_lines)}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    # JSON extract
    start = text.find("[")
    end = text.rfind("]") + 1
    if start == -1:
        raise ValueError(f"JSON topilmadi: {text[:200]}")
    return json.loads(text[start:end])


def main():
    if not API_KEY:
        print("GEMINI_API_KEY yoki GEMINI_API_KEYS env o'zgaruvchisi kerak")
        return

    if not DB_PATH.exists():
        print(f"DB topilmadi: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # tarjima_uz ustunini qo'shish (agar yo'q bo'lsa)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(amthal)").fetchall()]
    if "tarjima_uz" not in cols:
        conn.execute("ALTER TABLE amthal ADD COLUMN tarjima_uz TEXT DEFAULT ''")
        conn.commit()
        print("tarjima_uz ustuni qo'shildi")

    # Tarjimasiz yozuvlarni olish
    rows = conn.execute(
        "SELECT id, arabcha, tarjima, izoh FROM amthal WHERE tarjima_uz IS NULL OR tarjima_uz = ''"
    ).fetchall()

    if not rows:
        print("Barcha amthallar allaqachon o'zbekchaga tarjima qilingan!")
        conn.close()
        return

    print(f"Tarjima qilinadigan amthallar: {len(rows)}")
    cur = conn.cursor()
    done = 0

    for i in range(0, len(rows), BATCH):
        batch = [dict(r) for r in rows[i:i+BATCH]]
        try:
            translations = gemini_translate(batch, API_KEY)
            if len(translations) != len(batch):
                print(f"  OGOHLANTIRISH: {len(batch)} so'radim, {len(translations)} oldi")
            for j, row in enumerate(batch):
                uz = translations[j] if j < len(translations) else ""
                cur.execute("UPDATE amthal SET tarjima_uz = ? WHERE id = ?", (uz, row["id"]))
            conn.commit()
            done += len(batch)
            print(f"  {done}/{len(rows)} ta tarjima qilindi...")
            time.sleep(1)
        except Exception as e:
            print(f"  Xato (batch {i//BATCH + 1}): {e}")
            time.sleep(5)

    # FTS rebuild — yangi ustun qo'shildi, lekin FTS da yo'q
    # amthal_fts ga tarjima_uz qo'shish uchun FTS ni qayta yaratish kerak
    print("\nFTS jadvalini yangilash...")
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
    conn.close()

    print(f"\nTayyor! {done} ta amthal o'zbekchaga tarjima qilindi va FTS yangilandi.")
    print("Endi o'zbekcha so'zlar bilan ham qidiruv ishlaydi.")


if __name__ == "__main__":
    main()
