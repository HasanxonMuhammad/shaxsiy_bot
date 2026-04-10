"""Gemini AI Engine — SDK pattern asosida retry, error handling, context management."""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import aiohttp
import google.generativeai as genai

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={key}"

# SDK patterndan: retry konstantalari
MAX_RETRIES = 3
BASE_DELAY_MS = 500

# Xato turlari (SDK patterndan — markazlashtirilgan)
RATE_LIMIT_MARKERS = ["429", "quota", "rate limit", "resource_exhausted"]
AUTH_ERROR_MARKERS = ["api_key_invalid", "api key expired", "permission_denied"]
TRANSIENT_MARKERS = ["503", "502", "timeout", "connection"]


def _classify_error(error_str: str) -> str:
    """Xato turini aniqlash (SDK errorUtils patterndan)."""
    lower = error_str.lower()
    for marker in RATE_LIMIT_MARKERS:
        if marker in lower:
            return "rate_limit"
    for marker in AUTH_ERROR_MARKERS:
        if marker in lower:
            return "auth_error"
    for marker in TRANSIENT_MARKERS:
        if marker in lower:
            return "transient"
    return "unknown"


@dataclass
class EngineStats:
    """So'rov statistikasi."""
    total_requests: int = 0
    successful: int = 0
    rate_limited: int = 0
    errors: int = 0
    total_tokens_approx: int = 0
    last_request_time: float = 0
    avg_response_ms: float = 0
    _response_times: list = field(default_factory=list)

    def record(self, duration_ms: float, success: bool, tokens: int = 0):
        self.total_requests += 1
        if success:
            self.successful += 1
            self.total_tokens_approx += tokens
        self._response_times.append(duration_ms)
        if len(self._response_times) > 100:
            self._response_times = self._response_times[-50:]
        self.avg_response_ms = sum(self._response_times) / len(self._response_times)
        self.last_request_time = time.time()


FALLBACK_MODEL = "gemini-2.5-flash"


class GeminiEngine:
    def __init__(self, api_keys: list[str], model: str = "gemini-2.5-flash"):
        self._keys = api_keys
        self._model_name = model
        self._current_key = 0
        self._http: aiohttp.ClientSession | None = None
        self.stats = EngineStats()

    def _rotate_key(self):
        old = self._current_key
        self._current_key = (self._current_key + 1) % len(self._keys)
        log.info("Kalit: #%d → #%d", old + 1, self._current_key + 1)

    async def _get_http(self):
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )
        return self._http

    async def chat(self, system_prompt: str, messages: list[dict], use_search: bool = False) -> str:
        has_media = any(m.get("media") for m in messages)

        if has_media:
            # Media bor — SDK ishlatish (rasm/audio tahlili)
            return await self._chat_sdk(system_prompt, messages)
        elif use_search:
            return await self._chat_rest(system_prompt, messages, use_search=True)
        else:
            return await self._chat_rest(system_prompt, messages, use_search=False)

    async def chat_stream(self, system_prompt: str, messages: list[dict]):
        """Gemini SSE stream — har bir chunk kelganda yield qiladi.
        Yields: (chunk_text, full_text_so_far)
        """
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append({"text": msg["text"]})
            contents.append({"role": msg["role"], "parts": parts})

        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 4096},
        }

        http = await self._get_http()
        url = STREAM_URL.format(model=self._model_name, key=self._keys[self._current_key])
        full_text = ""

        try:
            async with http.post(url, json=body) as resp:
                if resp.status != 200:
                    # Stream ishlamasa oddiy chat ga fallback
                    return

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data: "):
                        continue
                    import json
                    try:
                        data = json.loads(line[6:])
                    except (json.JSONDecodeError, ValueError):
                        continue

                    candidates = data.get("candidates", [])
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if "text" in part:
                            chunk = part["text"]
                            full_text += chunk
                            yield chunk, full_text

        except Exception as e:
            log.error("Gemini stream xatosi: %s", e)

    async def _chat_rest(self, system_prompt: str, messages: list[dict], use_search: bool = False) -> str:
        """REST API — google_search + retry logic."""
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append({"text": msg["text"]})
            contents.append({"role": msg["role"], "parts": parts})

        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 4096},
        }
        if use_search:
            body["tools"] = [{"google_search": {}}]

        total_keys = len(self._keys)
        http = await self._get_http()

        for attempt in range(total_keys * MAX_RETRIES):
            url = API_URL.format(model=self._model_name, key=self._keys[self._current_key])
            start = time.time()
            try:
                log.info("Gemini REST (kalit #%d, search=%s, urinish %d)...",
                         self._current_key + 1, use_search, attempt + 1)
                async with http.post(url, json=body) as resp:
                    data = await resp.json()

                duration_ms = (time.time() - start) * 1000

                if "candidates" in data:
                    parts = [p["text"] for p in data["candidates"][0]["content"]["parts"] if "text" in p]
                    text = "\n".join(parts)
                    self.stats.record(duration_ms, True, len(text))
                    log.info("Gemini javob: %d belgi, %.0fms", len(text), duration_ms)
                    return text

                error_msg = data.get("error", {}).get("message", "")
                error_type = _classify_error(error_msg)
                self.stats.record(duration_ms, False)

                if error_type == "rate_limit":
                    self.stats.rate_limited += 1
                    self._rotate_key()
                    # SDK pattern: exponential backoff
                    delay = (BASE_DELAY_MS / 1000) * (2 ** (attempt % MAX_RETRIES))
                    log.warning("Rate limit — %.1fs kutish", delay)
                    await asyncio.sleep(delay)
                elif error_type == "auth_error":
                    log.error("Auth xatosi — kalit yaroqsiz: %s", error_msg[:100])
                    self._rotate_key()
                elif error_type == "transient":
                    delay = (BASE_DELAY_MS / 1000) * (2 ** attempt)
                    log.warning("Vaqtinchalik xato — %.1fs kutish", delay)
                    await asyncio.sleep(min(delay, 30))
                else:
                    log.error("Gemini xatosi: %s", error_msg[:200])
                    self.stats.errors += 1
                    return ""

            except asyncio.TimeoutError:
                log.warning("Gemini timeout — qayta urinish")
                await asyncio.sleep(2)
            except Exception as e:
                log.error("Gemini xatosi: %s", str(e)[:200])
                self.stats.errors += 1
                return ""

        # Fallback — asosiy model ishlamasa, gemini-2.5-flash bilan urinish
        if self._model_name != FALLBACK_MODEL:
            log.warning("Asosiy model (%s) ishlamadi — %s ga fallback", self._model_name, FALLBACK_MODEL)
            fallback_url = API_URL.format(model=FALLBACK_MODEL, key=self._keys[self._current_key])
            try:
                async with http.post(fallback_url, json=body) as resp:
                    data = await resp.json()
                if "candidates" in data:
                    candidate = data["candidates"][0]
                    content = candidate.get("content", {})
                    parts = content.get("parts", [])
                    text_parts = [p["text"] for p in parts if "text" in p]
                    if text_parts:
                        text = "\n".join(text_parts)
                        self.stats.record(0, True, len(text))
                        log.info("Fallback (%s) javob: %d belgi", FALLBACK_MODEL, len(text))
                        return text
                    log.warning("Fallback javob bo'sh (parts yo'q)")
            except Exception as e:
                log.error("Fallback ham ishlamadi: %s", e)

        log.error("Barcha urinishlar muvaffaqiyatsiz (%d)", total_keys * MAX_RETRIES)
        return ""

    async def _chat_sdk(self, system_prompt: str, messages: list[dict]) -> str:
        """SDK — media (rasm/audio) + retry."""
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append(msg["text"])
            for media in msg.get("media", []):
                parts.append({"mime_type": media["mime"], "data": media["data"]})
                log.info("Media: %s, %d bayt", media["mime"], len(media["data"]))
            contents.append({"role": msg["role"], "parts": parts})

        total_keys = len(self._keys)

        for attempt in range(total_keys * MAX_RETRIES):
            start = time.time()
            try:
                genai.configure(api_key=self._keys[self._current_key])
                model = genai.GenerativeModel(
                    model_name=self._model_name,
                    system_instruction=system_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=0.9, max_output_tokens=4096,
                    ),
                )
                log.info("Gemini SDK (kalit #%d, urinish %d)...", self._current_key + 1, attempt + 1)
                response = await model.generate_content_async(contents=contents)
                duration_ms = (time.time() - start) * 1000

                if response and response.candidates:
                    result = [p.text for p in response.candidates[0].content.parts if hasattr(p, "text") and p.text]
                    text = "\n".join(result)
                    self.stats.record(duration_ms, True, len(text))
                    log.info("Gemini javob: %d belgi, %.0fms", len(text), duration_ms)
                    return text
                return ""

            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                error_str = str(e)
                error_type = _classify_error(error_str)
                self.stats.record(duration_ms, False)
                log.error("Gemini SDK xatosi: %s → %s", error_type, error_str[:150])

                if error_type == "rate_limit":
                    self.stats.rate_limited += 1
                    self._rotate_key()
                    delay = (BASE_DELAY_MS / 1000) * (2 ** (attempt % MAX_RETRIES))
                    await asyncio.sleep(delay)
                elif error_type == "transient":
                    await asyncio.sleep(2)
                else:
                    self.stats.errors += 1
                    return ""

        return ""
