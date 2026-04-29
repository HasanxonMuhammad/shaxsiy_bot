"""tabiir.pdf ni Gemini orqali strukturalangan JSONL ga aylantirish.

Har sahifa rasmga aylantiriladi va Gemini'ga yuboriladi. Gemini har sahifadagi
"ibora qutisi" larini chiqarib beradi: mavzu + arabcha kalit ibora + hint +
3-5 ta misol gap.

Chiqish: out/tabir.jsonl — har qator bir sahifa natijasi.
Hech bir sahifa qaytadan ishlanmaydi (progress fayli orqali resume).

Ishga tushirish:
    python scripts/ingest_books/01_parse_tabir.py \
        --pdf D:/hasanxon/bot_uchun_ozuqa/tabiir.pdf \
        --out scripts/ingest_books/out/tabir.jsonl
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
log = logging.getLogger("tabir")


SYSTEM_PROMPT = """Sen arab tili kitobi sahifalarini strukturali JSON ga aylantirasan.
Kitob: arabcha gap yasash iboralari (taʻbir) qoʻllanmasi.
Har sahifada bir nechta "quti" boʻladi. Har qutida:
  - mavzu sarlavhasi (sahifa boshida — agar koʻrsatilgan boʻlsa)
  - arabcha kalit ibora (bosh — qizil/qora yirik shrift)
  - turkcha va/yoki oʻzbekcha hint (kalit iboraning maʻnosi)
  - 3-5 ta misol arabcha gap (oʻq belgisi bilan)

Qoidalar:
- Arabcha matnda harakat (tashkil) bor — aynan koʻchir, oʻzgartirma.
- Misol gaplarni faqat arabcha matnini qaytar (hech narsa qoʻshma).
- Sahifa kombinatsiyasiz boʻlsa — boʻsh roʻyxat qaytar.
- Tarjima qilma — bu keyingi bosqichda boʻladi.
- "Mavzu" matnini agar sahifaning ustki qismida koʻrinsa qoʻyamiz; aks holda boʻsh.
"""


PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "expressions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mavzu_ar": {"type": "string"},
                    "mavzu_uz": {"type": "string"},
                    "head_ar": {"type": "string"},
                    "hint_uz": {"type": "string"},
                    "hint_tr": {"type": "string"},
                    "examples_ar": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["head_ar", "examples_ar"],
            },
        }
    },
    "required": ["expressions"],
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
    ap.add_argument("--start", type=int, default=0, help="boshlovchi sahifa (0-dan)")
    ap.add_argument("--end", type=int, default=0, help="tugash sahifa (0 = oxirigacha)")
    ap.add_argument("--dpi", type=int, default=160)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys()
    log.info("API kalit: %d ta", len(keys))

    pdf = fitz.open(args.pdf)
    total = pdf.page_count
    end = args.end or total
    log.info("PDF: %s — %d sahifa, ishlanadi: %d..%d", args.pdf.name, total, args.start, end - 1)

    done = already_done_pages(args.out)
    log.info("Avval ishlangan: %d sahifa", len(done))

    skipped = parsed = errored = 0
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
                        f"Mana kitobning {i + 1}-sahifasi rasmi. "
                        "Undagi har bir 'ibora qutisi'ni JSON ga aylantirib ber."
                    ),
                    images_png=[png],
                    schema=PAGE_SCHEMA,
                )
            except Exception as e:
                log.error("Sahifa %d: %s", i, e)
                errored += 1
                continue

            if not isinstance(result, dict):
                log.warning("Sahifa %d: javob noto'g'ri formatda", i)
                errored += 1
                continue

            row = {"page": i, "expressions": result.get("expressions", [])}
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            fp.flush()
            parsed += 1
            n = len(row["expressions"])
            log.info("Sahifa %d: %d ibora, %.1fs", i, n, time.time() - t0)

    log.info("Yakun: parsed=%d, skipped=%d, errored=%d", parsed, skipped, errored)


if __name__ == "__main__":
    main()
