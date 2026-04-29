"""dalil.pdf ni Gemini orqali strukturalangan JSONL ga aylantirish.

Har sahifani Gemini'ga yuboramiz va shu sahifadagi:
  - mavzu (sport / oila / sayohat / ...)
  - savollar (arabcha + o'zbekcha tarjima)
  - daraja (A1/A2/B1/B2/C1/C2 — savol murakkabligi va lug'at bo'yicha)
  - qisqa izoh (savol turi, kalit so'zlar)

Chiqish: out/dalil.jsonl

Ishga tushirish:
    python scripts/ingest_books/03_parse_dalil.py \
        --pdf D:/hasanxon/bot_uchun_ozuqa/dalil.pdf \
        --out scripts/ingest_books/out/dalil.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ingest_books.common import call_gemini, load_keys, page_to_png

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dalil")


SYSTEM_PROMPT = """Sen arab tili savollar kitobi sahifalarini strukturali JSON ga aylantirasan.
Kitob: turli mavzularda arabcha savollar to'plami (taʻlim qoʻllanmasi).

Har savol uchun quyidagilarni qaytar:
  - mavzu_ar: sahifa boshida koʻrinsa arabcha mavzu
  - mavzu_uz: o'zbekcha tarjima yoki ekvivalent (sport, oila, ish, sayohat va h.k.)
  - savol_ar: arabcha savol matni (harakat bilan, agar bor boʻlsa)
  - savol_uz: qisqa o'zbekcha tarjima
  - level: SAVOL DARAJASI — A1, A2, B1, B2, C1 yoki C2.
       A1 — juda oddiy, 3-5 soʻz, oddiy lugʻat, "ismingiz nima?" tipida
       A2 — kundalik, oddiy oʻtgan zamon, "qaerda yashaysiz?" tipida
       B1 — oʻrta murakkablik, fikr soʻrash, "nima uchun ...?" tipida
       B2 — yuqori oʻrta, abstrakt mavzular, taqqoslash, sabab-natija
       C1 — ilg'or, idiomatik, ilmiy/falsafiy
       C2 — eng yuqori, klassik arabcha, mukammal grammatika
  - izoh: 5-10 so'z — savol turi yoki kalit grammatik tushuncha
       (masalan: "kelajak zamoni", "shartli gap", "fikr soʻrash")

Qoidalar:
- Faqat haqiqiy savollarni yoz (savol belgisi yoki soʻroq olmoshi bilan).
- Sarlavha, izohlar, ko'rsatma matnlar — savol emas, kiritma.
- Sahifa boʻsh boʻlsa — boʻsh roʻyxat qaytar.
"""


PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mavzu_ar": {"type": "string"},
                    "mavzu_uz": {"type": "string"},
                    "savol_ar": {"type": "string"},
                    "savol_uz": {"type": "string"},
                    "level": {
                        "type": "string",
                        "enum": ["A1", "A2", "B1", "B2", "C1", "C2"],
                    },
                    "izoh": {"type": "string"},
                },
                "required": ["savol_ar", "level"],
            },
        }
    },
    "required": ["questions"],
}


def already_done_pages(out_path: Path) -> set[int]:
    if not out_path.exists():
        return set()
    done: set[int] = set()
    for line in out_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            done.add(row["page"])
        except Exception:
            continue
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=160)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys()
    log.info("API kalit: %d ta", len(keys))

    pdf = fitz.open(args.pdf)
    total = pdf.page_count
    end = args.end or total
    log.info("PDF: %s — %d sahifa, ishlanadi: %d..%d",
             args.pdf.name, total, args.start, end - 1)

    done = already_done_pages(args.out)
    log.info("Avval ishlangan: %d", len(done))

    parsed = errored = skipped = 0
    with args.out.open("a", encoding="utf-8") as fp:
        for i in range(args.start, end):
            if i in done:
                skipped += 1
                continue
            t0 = time.time()
            try:
                png = page_to_png(pdf, i, dpi=args.dpi)
                result = call_gemini(
                    keys=keys,
                    model=args.model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=(
                        f"Mana savollar kitobining {i + 1}-sahifasi. "
                        "Undagi har bir savolni JSON sxemasiga muvofiq qaytar. "
                        "Daraja (A1-C2) ni savol murakkabligi va lug'at bo'yicha aniqlа."
                    ),
                    images_png=[png],
                    schema=PAGE_SCHEMA,
                )
            except Exception as e:
                log.error("Sahifa %d: %s", i, e)
                errored += 1
                continue

            if not isinstance(result, dict):
                log.warning("Sahifa %d: javob yo'q", i)
                errored += 1
                continue

            qs = result.get("questions", [])
            row = {"page": i, "questions": qs}
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            fp.flush()
            parsed += 1
            log.info("Sahifa %d: %d savol, %.1fs", i, len(qs), time.time() - t0)

    log.info("Yakun: parsed=%d, skipped=%d, errored=%d", parsed, skipped, errored)


if __name__ == "__main__":
    main()
