import logging
import re

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-lite:generateContent?key={key}"

CLASSIFY_PROMPT = """Quyidagi xabar spam yoki yo'qligini aniqlang.
Spam belgilari: reklama, pul ishlash va'dasi, begona link/kanal, 18+ kontent, crypto/bitcoin.
Javob FAQAT bitta so'z: SPAM yoki SAFE

Xabar: {text}"""


class SpamFilter:
    SPAM_PATTERNS = [
        re.compile(r"(?i)(crypto|bitcoin|earn\s+\$|free\s+money|investment\s+opportunity)"),
        re.compile(r"(?i)(click\s+here|join\s+now|limited\s+offer|act\s+fast)"),
        re.compile(r"(?i)(t\.me/\S+bot|@\S+bot)\s+(earn|free|money)"),
        re.compile(r"(?i)(onlyfans|adult|18\+|xxx)"),
        re.compile(r"(?i)(hamkorlik|reklama|pul\s+ishlash|daromad)\s.*(link|havola|kanal)"),
    ]

    SAFE_PATTERNS = [
        re.compile(r"(?i)(salom|assalomu|rahmat|yaxshi|qanday)"),
        re.compile(r"(?i)(savol|yordam|maslahat|fikr)"),
    ]

    def __init__(self):
        self._http: aiohttp.ClientSession | None = None

    def check(self, text: str) -> bool | None:
        """Regex prefilter: True=spam, False=safe, None=noaniq (AI kerak)"""
        for p in self.SAFE_PATTERNS:
            if p.search(text):
                return False
        for p in self.SPAM_PATTERNS:
            if p.search(text):
                return True
        return None

    async def classify_with_ai(self, text: str, api_key: str) -> bool:
        """Gemini Flash Lite bilan noaniq xabarlarni tekshirish. True=spam."""
        try:
            if not self._http or self._http.closed:
                self._http = aiohttp.ClientSession()

            url = API_URL.format(key=api_key)
            body = {
                "contents": [{"parts": [{"text": CLASSIFY_PROMPT.format(text=text[:500])}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 10},
            }

            async with self._http.post(url, json=body) as resp:
                data = await resp.json()

            if "candidates" in data:
                result = data["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
                is_spam = "SPAM" in result
                log.info("AI spam classifier: '%s...' → %s", text[:30], result)
                return is_spam

        except Exception as e:
            log.error("AI spam classifier xatosi: %s", e)

        return False  # Xatolikda spam emas deb qabul qilinadi
