"""
Hadislarni mavzu boyicha teglash — Gemini 2.5 Flash.
Faqat 2 ta API kalit ishlatiladi.
Progress saqlanadi — tuxtatsa davom ettirish mumkin.

Ishlatish:
    python tag_hadis.py
"""
import json
import sqlite3
import sys
import time
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

DB_PATH = "data/hadislar.db"

API_KEYS = [
    "AIzaSyAeFiTID1Wmkuk0HENfNMDZ0bXKgfYB3jY",
    "AIzaSyDKIq4kE_RexN3Pg7p7ko9r3h0QSRXSxu4",
]
MODEL = "gemini-2.5-flash"
BATCH = 20        # 20 ta — MAX_TOKENS xatosidan saqlanish uchun
WORKERS = 4
SLEEP = 0.3
RETRY_SLEEP = 30

MAVZULAR = [
    "sabr", "shukr", "taqvo", "imon", "tavba", "duo", "zikr", "quron",
    "ilm", "aql", "hayo", "kibr", "hasad", "giyba", "yolgon",
    "namoz", "roza", "zakot", "haj", "tahorat", "janoza", "masjid",
    "sadaqa", "halol", "haram", "tijorat", "rizq", "mol-dunyo",
    "oila", "nikoh", "bola", "ota-ona", "qoshni", "mehr", "adolat",
    "qiyomat", "jannat", "jahannam", "olim", "gunoh", "rahmat",
    "paygambar", "sahoba", "sunnat", "bidat", "jihod", "zulm",
    "dostlik", "mehnat", "soglik", "qadar",
]

MAVZU_STR = ", ".join(MAVZULAR)

_key_lock = threading.Lock()
_key_idx = [0]


def next_key():
    with _key_lock:
        k = API_KEYS[_key_idx[0] % len(API_KEYS)]
        _key_idx[0] += 1
        return k


def gemini_tag(hadith_batch):
    items = [f"{i+1}. {h['uzbekcha'][:300]}" for i, h in enumerate(hadith_batch)]
    prompt = (
        f"Quyidagi {len(hadith_batch)} ta hadis uchun mavzular bering.\n"
        f"FAQAT shu so'zlardan ishlating: {MAVZU_STR}\n"
        "Har hadis uchun 1-5 ta mavzu, vergul bilan.\n"
        "Javob: FAQAT JSON massiv, boshqa hech narsa yozma:\n"
        '["mavzu1,mavzu2", "mavzu1", "mavzu1,mavzu2,mavzu3"]\n\n'
        + "\n\n".join(items)
    )

    for attempt in range(4):
        key = next_key()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{MODEL}:generateContent?key={key}"
        )
        body = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 1024,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }).encode()

        try:
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                tags = json.loads(raw[start:end])
                if isinstance(tags, list):
                    while len(tags) < len(hadith_batch):
                        tags.append("")
                    return [str(t).strip() for t in tags[:len(hadith_batch)]]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                sys.stderr.write(f"Rate limit — {RETRY_SLEEP}s kutish...\n")
                sys.stderr.flush()
                time.sleep(RETRY_SLEEP)
                continue
            sys.stderr.write(f"HTTP {e.code}\n")
            sys.stderr.flush()
        except Exception as ex:
            sys.stderr.write(f"Xato (urinish {attempt+1}): {ex}\n")
            sys.stderr.flush()
            time.sleep(5)

    return [""] * len(hadith_batch)


def setup_column(conn):
    cur = conn.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(hadislar)")]
    if "mavzular" not in cols:
        cur.execute("ALTER TABLE hadislar ADD COLUMN mavzular TEXT DEFAULT ''")
        conn.commit()


def main():
    # stdout ni fayl rejimida ham ishlaydigan qilib sozlash
    out = open(sys.stdout.fileno(), mode="w", encoding="utf-8",
               errors="replace", buffering=1, closefd=False)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    setup_column(conn)

    cur = conn.cursor()
    cur.execute(
        "SELECT id, uzbekcha FROM hadislar "
        "WHERE mavzular IS NULL OR mavzular = '' "
        "ORDER BY id"
    )
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    out.write(f"Teglanmagan: {total} ta | Batch:{BATCH} Workers:{WORKERS}\n")
    out.flush()

    if total == 0:
        out.write("Hammasi teglangan!\n")
        return

    batches = [
        [{"id": r["id"], "uzbekcha": r["uzbekcha"]} for r in rows[i:i+BATCH]]
        for i in range(0, total, BATCH)
    ]

    done = [0]
    lock = threading.Lock()

    def worker(batch_dicts):
        tags = gemini_tag(batch_dicts)
        time.sleep(SLEEP)

        wconn = sqlite3.connect(DB_PATH, timeout=10)
        wcur = wconn.cursor()
        for row, tag in zip(batch_dicts, tags):
            wcur.execute(
                "UPDATE hadislar SET mavzular=? WHERE id=?",
                (tag, row["id"])
            )
        wconn.commit()
        wconn.close()

        with lock:
            done[0] += len(batch_dicts)
            pct = done[0] / total * 100
            sample = (tags[0] if tags else "")[:40]
            out.write(f"[{done[0]}/{total}] {pct:.1f}% | {sample}\n")
            out.flush()

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        ex.map(worker, batches)

    out.write("\nHammasi teglandi!\n")
    out.flush()


if __name__ == "__main__":
    main()
