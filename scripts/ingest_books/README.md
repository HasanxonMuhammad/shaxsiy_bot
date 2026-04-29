# Tabir va Dalil ingestion pipeline

Bu skriptlar `bot_uchun_ozuqa/` papkasidagi PDF kitoblarni Mudarris bot
ishlatadigan SQLite bazalariga aylantiradi.

- `tabiir.pdf` → `data/tabir.db` (arabcha gap yasash iboralari)
- `dalil.pdf`  → `data/dalil.db` (arabcha savollar A1–C2 darajalar bilan)

## Bog'liqliklar

```bash
pip install -r scripts/ingest_books/requirements.txt
```

`PyMuPDF` (fitz) — PDF dan sahifa rasm chiqarish uchun.
`requests` — Gemini REST API ga so'rov yuborish uchun.

`.env` faylda `GEMINI_API_KEYS` (vergul bilan ajratilgan ko'p kalit) yoki
`GEMINI_API_KEY` bo'lishi kerak.

## To'liq pipeline

PDF lar `D:/hasanxon/bot_uchun_ozuqa/` da deb hisoblanadi (kerak bo'lsa
yo'lni o'zgartiring).

### 1. Tabir parse (PDF → JSONL)

```bash
python scripts/ingest_books/01_parse_tabir.py \
    --pdf "D:/hasanxon/bot_uchun_ozuqa/tabiir.pdf" \
    --out scripts/ingest_books/out/tabir.jsonl
```

113 sahifa, taxminan 8-15 daqiqa (kalitlar soniga qarab). To'xtab qolsa
qayta ishga tushirsangiz qoldirgan sahifadan davom etadi (resume).

### 2. Tabir tarjima (JSONL → JSONL)

```bash
python scripts/ingest_books/02_translate_tabir.py \
    --in scripts/ingest_books/out/tabir.jsonl \
    --out scripts/ingest_books/out/tabir_translated.jsonl
```

### 3. Dalil parse + level klassifikatsiya

```bash
python scripts/ingest_books/03_parse_dalil.py \
    --pdf "D:/hasanxon/bot_uchun_ozuqa/dalil.pdf" \
    --out scripts/ingest_books/out/dalil.jsonl
```

### 4. SQLite bazalarini yig'ish

```bash
python scripts/ingest_books/04_build_dbs.py
```

Natijada `data/tabir.db` va `data/dalil.db` paydo bo'ladi.
Bot keyingi restart vaqtida ularni avtomatik topadi.

## Tekshirish

```bash
sqlite3 data/tabir.db "SELECT COUNT(*) FROM tabir_examples;"
sqlite3 data/dalil.db "SELECT level, COUNT(*) FROM dalil_questions GROUP BY level;"
```

## Daraja bo'yicha qayta klassifikatsiya

Agar Gemini bergan A1-C2 darajalar yoqmasa — `03_parse_dalil.py` natijasini
to'g'ridan-to'g'ri tahrirlash mumkin (oddiy JSONL), keyin yana
`04_build_dbs.py` chaqiring.
