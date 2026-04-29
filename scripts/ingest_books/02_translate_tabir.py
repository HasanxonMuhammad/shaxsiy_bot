"""tabir.jsonl ichidagi misollarni o'zbekchaga tarjima qilish (batchli).

Kitobda ko'pincha turkcha hint bor, lekin o'zbekcha tarjima yo'q. Bu script
har bir misol gapga `tarjima_uz` qo'shadi.

Chiqish: out/tabir_translated.jsonl

Ishga tushirish:
    python scripts/ingest_books/02_translate_tabir.py \
        --in scripts/ingest_books/out/tabir.jsonl \
        --out scripts/ingest_books/out/tabir_translated.jsonl
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ingest_books.common import call_gemini, load_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("tabir-tr")

SYSTEM_PROMPT = """Sen arab tilidan o'zbekchaga aniq, jonli tarjima qilasan.
Sof o'zbekcha (kirill emas, lotin), jonli, tabiiy.
Tarjima — qisqa, gap ma'nosini saqlaydi, so'zma-so'z emas.
"""

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "head_ar": {"type": "string"},
                    "hint_uz": {"type": "string"},
                    "examples": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arabcha": {"type": "string"},
                                "tarjima_uz": {"type": "string"},
                            },
                            "required": ["arabcha", "tarjima_uz"],
                        },
                    },
                },
                "required": ["id", "examples"],
            },
        }
    },
    "required": ["items"],
}


def load_done(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    done: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            done[row["page"]] = row
        except Exception:
            continue
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--batch-size", type=int, default=8,
                    help="bir so'rovda qancha 'expression' yuborish")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    keys = load_keys()

    done = load_done(args.out)
    log.info("Avval tarjima qilingan sahifalar: %d", len(done))

    src_lines = args.in_path.read_text(encoding="utf-8").splitlines()
    parsed = 0
    with args.out.open("a", encoding="utf-8") as fp_out:
        for line in src_lines:
            try:
                row = json.loads(line)
            except Exception:
                continue
            page = row["page"]
            if page in done:
                continue
            exprs = row.get("expressions", [])
            if not exprs:
                fp_out.write(json.dumps(row, ensure_ascii=False) + "\n")
                fp_out.flush()
                continue

            translated = []
            for batch_start in range(0, len(exprs), args.batch_size):
                batch = exprs[batch_start : batch_start + args.batch_size]
                payload = {
                    "items": [
                        {
                            "id": batch_start + i,
                            "head_ar": e.get("head_ar", ""),
                            "hint_uz": e.get("hint_uz", ""),
                            "examples_ar": e.get("examples_ar", []),
                        }
                        for i, e in enumerate(batch)
                    ]
                }
                user_prompt = (
                    "Quyidagi arabcha iboralar va misol gaplarni o'zbekchaga "
                    "tarjima qil. Har misol uchun arabcha matn va tarjima_uz "
                    "qaytar. id ni aynan saqla.\n\n"
                    f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
                )
                t0 = time.time()
                result = call_gemini(
                    keys=keys,
                    model=args.model,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    schema=BATCH_SCHEMA,
                )
                if not isinstance(result, dict):
                    log.warning("Sahifa %d, batch %d: javob yo'q",
                                page, batch_start)
                    # Tarjimasiz qo'shamiz — keyin retry mumkin
                    for e in batch:
                        translated.append({
                            **e,
                            "examples": [
                                {"arabcha": x, "tarjima_uz": ""}
                                for x in e.get("examples_ar", [])
                            ],
                        })
                    continue

                items_by_id = {it["id"]: it for it in result.get("items", [])}
                for i, e in enumerate(batch):
                    rid = batch_start + i
                    src_examples = e.get("examples_ar", [])
                    tr_item = items_by_id.get(rid, {})
                    tr_examples = tr_item.get("examples", []) or []
                    out_examples = []
                    for j, ar in enumerate(src_examples):
                        tr_uz = ""
                        if j < len(tr_examples):
                            # Modelga ishonish — lekin arabchani src dan olamiz
                            tr_uz = tr_examples[j].get("tarjima_uz", "")
                        out_examples.append({"arabcha": ar, "tarjima_uz": tr_uz})
                    translated.append({
                        "mavzu_ar": e.get("mavzu_ar", ""),
                        "mavzu_uz": e.get("mavzu_uz", ""),
                        "head_ar": e.get("head_ar", ""),
                        "hint_uz": e.get("hint_uz", ""),
                        "hint_tr": e.get("hint_tr", ""),
                        "examples": out_examples,
                    })
                log.info("Sahifa %d batch %d (%d ibora) — %.1fs",
                         page, batch_start, len(batch), time.time() - t0)

            out_row = {"page": page, "expressions": translated}
            fp_out.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            fp_out.flush()
            parsed += 1
            log.info("Sahifa %d tugadi (jami %d ibora)", page, len(translated))

    log.info("Tugadi: %d sahifa tarjima qilindi", parsed)


if __name__ == "__main__":
    main()
