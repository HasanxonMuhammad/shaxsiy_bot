"""Kunlik token statistika — serverdagi loglardan chiqaradi.

Server'da ishlatish:
    journalctl -u shaxsiy-bot --since '24 hours ago' --no-pager | python3 token_stats.py
    journalctl -u mudarris-radio --since '24 hours ago' --no-pager | python3 token_stats.py

Yoki bir vaqtning o'zida ikkalasi:
    (journalctl -u shaxsiy-bot --since '24 hours ago' --no-pager; \
     journalctl -u mudarris-radio --since '24 hours ago' --no-pager) | python3 token_stats.py
"""
import re
import sys
from collections import defaultdict

# Gemini log: "Gemini javob: 123 belgi, 1234ms | TOKENS in=8000 (cached=7500, fresh=500) out=200 total=8200 cache_used=True"
GEMINI_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):\S* .*Gemini javob: \d+ belgi, [\d.]+ms \| TOKENS "
    r"in=(\d+) \(cached=(\d+), fresh=(\d+)\) out=(\d+) total=(\d+) cache_used=(\w+)"
)
# Userbot log: "OpenAI: 88 belgi | TOKENS in=7935 (cached=7000, fresh=935) out=32 total=7967 model=gpt-5.4-mini"
OPENAI_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):\S* .*OpenAI: \d+ belgi \| TOKENS "
    r"in=(\d+) \(cached=(\d+), fresh=(\d+)\) out=(\d+) total=(\d+) model=(\S+)"
)

# Vertex Gemini Pro narxi ($/1M token) — taxminiy
PRICE = {
    "gemini_fresh_in": 1.25,
    "gemini_cached_in": 0.31,
    "gemini_out": 5.00,
    "gpt5_fresh_in": 1.10,  # gpt-5.4-mini
    "gpt5_cached_in": 0.275,
    "gpt5_out": 4.40,
    "gpt4o_fresh_in": 2.50,
    "gpt4o_cached_in": 1.25,
    "gpt4o_out": 10.00,
}


def get_price(model: str, kind: str) -> float:
    name = model.lower()
    if "gpt-5" in name or "5.4" in name:
        return PRICE[f"gpt5_{kind}"]
    if "4o" in name:
        return PRICE[f"gpt4o_{kind}"]
    return PRICE[f"gemini_{kind}"]


def main() -> None:
    days: dict[str, dict] = defaultdict(lambda: {
        "gemini_req": 0,
        "gemini_in": 0,
        "gemini_cached": 0,
        "gemini_fresh": 0,
        "gemini_out": 0,
        "gemini_total": 0,
        "openai_req": 0,
        "openai_in": 0,
        "openai_cached": 0,
        "openai_fresh": 0,
        "openai_out": 0,
        "openai_total": 0,
        "openai_models": defaultdict(int),
        "openai_cost": 0.0,
    })

    for line in sys.stdin:
        if m := GEMINI_RE.search(line):
            day = m.group(1)
            d = days[day]
            d["gemini_req"] += 1
            d["gemini_in"] += int(m.group(3))
            d["gemini_cached"] += int(m.group(4))
            d["gemini_fresh"] += int(m.group(5))
            d["gemini_out"] += int(m.group(6))
            d["gemini_total"] += int(m.group(7))
            continue
        if m := OPENAI_RE.search(line):
            day = m.group(1)
            d = days[day]
            d["openai_req"] += 1
            in_t = int(m.group(3))
            cached = int(m.group(4))
            fresh = int(m.group(5))
            out = int(m.group(6))
            model = m.group(8)
            d["openai_in"] += in_t
            d["openai_cached"] += cached
            d["openai_fresh"] += fresh
            d["openai_out"] += out
            d["openai_total"] += int(m.group(7))
            d["openai_models"][model] += 1
            d["openai_cost"] += (
                fresh * get_price(model, "fresh_in") / 1_000_000
                + cached * get_price(model, "cached_in") / 1_000_000
                + out * get_price(model, "out") / 1_000_000
            )

    if not days:
        print("Token log topilmadi. Bot restart bo'lganiga kamida 10-15 daqiqa o'tdimi?")
        return

    total_cost_gemini = 0.0
    total_cost_openai = 0.0

    for day in sorted(days.keys()):
        d = days[day]
        gc = (
            d["gemini_fresh"] * PRICE["gemini_fresh_in"] / 1_000_000
            + d["gemini_cached"] * PRICE["gemini_cached_in"] / 1_000_000
            + d["gemini_out"] * PRICE["gemini_out"] / 1_000_000
        )
        total_cost_gemini += gc
        total_cost_openai += d["openai_cost"]

        print(f"=== {day} ===")
        if d["gemini_req"]:
            hit_rate = d["gemini_cached"] / d["gemini_in"] * 100 if d["gemini_in"] else 0
            print(f"  Gemini (Vertex): {d['gemini_req']} request")
            print(f"    in={d['gemini_in']:,} (cached={d['gemini_cached']:,} ({hit_rate:.0f}%), fresh={d['gemini_fresh']:,})")
            print(f"    out={d['gemini_out']:,}")
            print(f"    cost: ${gc:.3f}")
        if d["openai_req"]:
            models_str = ", ".join(f"{m}={c}" for m, c in d["openai_models"].items())
            hit_rate = d["openai_cached"] / d["openai_in"] * 100 if d["openai_in"] else 0
            print(f"  OpenAI (userbot): {d['openai_req']} request [{models_str}]")
            print(f"    in={d['openai_in']:,} (cached={d['openai_cached']:,} ({hit_rate:.0f}%), fresh={d['openai_fresh']:,})")
            print(f"    out={d['openai_out']:,}")
            print(f"    cost: ${d['openai_cost']:.3f}")
        print()

    print("=" * 50)
    print(f"JAMI Gemini cost: ${total_cost_gemini:.2f}")
    print(f"JAMI OpenAI cost: ${total_cost_openai:.2f}")
    print(f"JAMI hammasi:     ${total_cost_gemini + total_cost_openai:.2f}")


if __name__ == "__main__":
    main()
