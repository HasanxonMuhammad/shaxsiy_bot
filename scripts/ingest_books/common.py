"""Tabir/Dalil ingestion uchun umumiy yordamchilar.

Bitta PDF sahifani PNG bayt sifatida olish va Gemini REST API ga structured
JSON output bilan so'rov yuborish.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
import fitz  # PyMuPDF

log = logging.getLogger(__name__)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

# Modul darajasidagi kalit indeksi — chaqiruvlar orasida saqlansin
# (leaked yoki rate-limited kalitlardan qaytadan boshlamaslik uchun).
_GLOBAL_KEY_IDX = 0


def load_keys() -> list[str]:
    """`.env` ichidagi GEMINI_API_KEYS yoki GEMINI_API_KEY ni o'qib qaytaradi.
    Loyiha ildizidagi `.env` ham ko'rib chiqiladi (ingestion vaqtinchaliq scriptlar
    serverda ishlaydi, shuning uchun .env tayyor turadi).
    """
    raw = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY") or ""
    if not raw:
        # .env ni qo'l bilan o'qish (python-dotenv majburiy bog'liqlik bo'lmasin)
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("GEMINI_API_KEYS=") or line.startswith("GEMINI_API_KEY="):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("GEMINI_API_KEY topilmadi (.env yoki env var)")
    return keys


def page_to_png(pdf: fitz.Document, page_index: int, dpi: int = 150) -> bytes:
    """PDF sahifani PNG baytga aylantirish."""
    page = pdf.load_page(page_index)
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


def call_gemini(
    keys: list[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    images_png: list[bytes] | None = None,
    schema: dict | None = None,
    max_retries: int = 4,
    timeout: int = 180,
) -> dict | list | str | None:
    """Gemini REST chaqiruvi. Structured output uchun schema beriladi.
    Qaytaradi: parsed JSON (dict/list) yoki raw matn.
    """
    parts: list[dict] = [{"text": user_prompt}]
    for img in images_png or []:
        parts.append(
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(img).decode("ascii"),
                }
            }
        )

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
        },
    }
    if schema is not None:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = schema

    global _GLOBAL_KEY_IDX
    key_idx = _GLOBAL_KEY_IDX % len(keys)
    for attempt in range(max_retries * len(keys)):
        key = keys[key_idx]
        url = GEMINI_URL.format(model=model, key=key)
        try:
            resp = requests.post(url, json=body, timeout=timeout)
        except requests.RequestException as e:
            log.warning("So'rov xatosi (kalit #%d, urinish %d): %s",
                        key_idx + 1, attempt + 1, e)
            time.sleep(2)
            continue

        if resp.status_code in (401, 403, 429) or resp.status_code >= 500:
            # 401/403 — kalit yaroqsiz/leaked, 429 — rate limit, 5xx — vaqtinchalik
            log.warning("HTTP %d (kalit #%d) — keyingi kalitga: %s",
                        resp.status_code, key_idx + 1, resp.text[:120])
            key_idx = (key_idx + 1) % len(keys)
            time.sleep(min(2 ** (attempt % 5), 10))
            continue

        if resp.status_code != 200:
            log.error("HTTP %d: %s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            log.warning("Bo'sh javob: %s", str(data)[:200])
            time.sleep(1)
            continue

        text_parts = [
            p.get("text", "")
            for p in cands[0].get("content", {}).get("parts", [])
            if "text" in p
        ]
        text = "".join(text_parts).strip()
        if not text:
            return None

        # Muvaffaqiyatli javob — keyingi chaqiruv shu kalit bilan boshlanadi
        _GLOBAL_KEY_IDX = key_idx
        if schema is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Modelimiz baribir oraliq belgilar qo'shsa — birinchi { yoki [ dan oxirgisigacha
                start = min(
                    (text.find(c) for c in "{[" if text.find(c) >= 0),
                    default=-1,
                )
                end = max(text.rfind("}"), text.rfind("]"))
                if start >= 0 and end > start:
                    try:
                        return json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        pass
                log.warning("JSON parse xatosi, raw: %s", text[:200])
                return None
        return text

    log.error("Barcha urinishlar tugadi")
    return None
