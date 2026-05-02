"""Gemini AI Engine — SDK pattern asosida retry, error handling, context management.

Ikki rejimda ishlaydi:
  1. AI Studio API key (default) — generativelanguage.googleapis.com, ?key=...
  2. Vertex AI service account — *-aiplatform.googleapis.com, Bearer token
     Vertex rejimida $300 Free Trial krediti ishlaydi (AI Studio'da yo'q).
"""
import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field

import aiohttp
import google.generativeai as genai

log = logging.getLogger(__name__)

# AI Studio (API key) — default
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={key}"

# Vertex AI (service account). "global" region URL'da hostname'siz prefix oladi:
#   global  -> https://aiplatform.googleapis.com/...
#   us-*    -> https://us-central1-aiplatform.googleapis.com/...
def _vertex_host(region: str) -> str:
    return "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"

VERTEX_URL_TPL = (
    "https://{host}/v1/projects/{project}"
    "/locations/{region}/publishers/google/models/{model}:generateContent"
)
VERTEX_STREAM_URL_TPL = (
    "https://{host}/v1/projects/{project}"
    "/locations/{region}/publishers/google/models/{model}:streamGenerateContent?alt=sse"
)

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



class GeminiEngine:
    def __init__(
        self,
        api_keys: list[str],
        model: str = "gemini-2.5-flash",
        fallback_model: str | None = None,
        vertex_project: str | None = None,
        vertex_region: str = "us-central1",
        vertex_key_path: str | None = None,
    ):
        self._keys = api_keys
        self._model_name = model
        self._fallback_model = fallback_model
        self._current_key = 0
        self._http: aiohttp.ClientSession | None = None
        self.stats = EngineStats()
        # Vertex AI rejimi (agar konfiguratsiya berilgan bo'lsa)
        self._vertex_project = vertex_project
        self._vertex_region = vertex_region
        self._vertex_key_path = vertex_key_path
        self._use_vertex = bool(vertex_project and vertex_key_path)
        self._vertex_creds = None
        self._vertex_token: str | None = None
        self._vertex_token_expires: float = 0
        if self._use_vertex:
            log.info("Vertex AI rejimi: project=%s region=%s",
                     vertex_project, vertex_region)
        else:
            log.info("AI Studio rejimi: %d kalit", len(api_keys))

    def _rotate_key(self):
        if not self._keys:
            return
        old = self._current_key
        self._current_key = (self._current_key + 1) % len(self._keys)
        log.info("Kalit: #%d → #%d", old + 1, self._current_key + 1)

    def _get_vertex_token(self) -> str:
        """Service account JSON kaliti bilan OAuth access token olish (cache'lanadi).
        Token ~1 soat amal qiladi; muddati tugashidan 60 soniya oldin yangilanadi.
        """
        if self._vertex_token and time.time() < self._vertex_token_expires - 60:
            return self._vertex_token
        from google.oauth2 import service_account
        import google.auth.transport.requests
        creds = service_account.Credentials.from_service_account_file(
            self._vertex_key_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        creds.refresh(google.auth.transport.requests.Request())
        self._vertex_creds = creds
        self._vertex_token = creds.token
        self._vertex_token_expires = (
            creds.expiry.timestamp() if creds.expiry else time.time() + 3500
        )
        return self._vertex_token

    def _build_request(self, model: str, stream: bool = False) -> tuple[str, dict]:
        """Joriy rejimga qarab URL va headers qaytaradi.
        Vertex bo'lsa: Bearer token, Vertex URL.
        AI Studio bo'lsa: ?key= URL, oddiy headers.
        """
        headers: dict = {"Content-Type": "application/json"}
        if self._use_vertex:
            tpl = VERTEX_STREAM_URL_TPL if stream else VERTEX_URL_TPL
            url = tpl.format(
                host=_vertex_host(self._vertex_region),
                project=self._vertex_project,
                region=self._vertex_region,
                model=model,
            )
            headers["Authorization"] = f"Bearer {self._get_vertex_token()}"
        else:
            url = (STREAM_URL if stream else API_URL).format(
                model=model,
                key=self._keys[self._current_key],
            )
        return url, headers

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
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 16384},
        }

        http = await self._get_http()
        url, headers = self._build_request(self._model_name, stream=True)
        full_text = ""

        try:
            async with http.post(url, json=body, headers=headers) as resp:
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
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 16384},
        }
        if use_search:
            # Vertex AI da kalit nomi farqli: "googleSearch" (camelCase)
            body["tools"] = [
                {"googleSearch": {}} if self._use_vertex else {"google_search": {}}
            ]

        # Retry hisobi: Vertex'da bitta token, AI Studio'da har bir kalit uchun urinish
        total_attempts = MAX_RETRIES if self._use_vertex else max(len(self._keys), 1) * MAX_RETRIES
        http = await self._get_http()

        for attempt in range(total_attempts):
            url, headers = self._build_request(self._model_name, stream=False)
            start = time.time()
            try:
                if self._use_vertex:
                    log.info("Vertex REST (search=%s, urinish %d)...", use_search, attempt + 1)
                else:
                    log.info("Gemini REST (kalit #%d, search=%s, urinish %d)...",
                             self._current_key + 1, use_search, attempt + 1)
                async with http.post(url, json=body, headers=headers) as resp:
                    data = await resp.json()

                duration_ms = (time.time() - start) * 1000

                if "candidates" in data:
                    candidate = data["candidates"][0]
                    content = candidate.get("content", {})
                    parts = [p["text"] for p in content.get("parts", []) if "text" in p]
                    text = "\n".join(parts)
                    if text:
                        self.stats.record(duration_ms, True, len(text))
                        log.info("Gemini javob: %d belgi, %.0fms", len(text), duration_ms)
                        return text
                    # Bo'sh javob — google_search olib retry
                    finish_reason = candidate.get("finishReason", "UNKNOWN")
                    log.warning("Gemini bo'sh javob (finishReason=%s, urinish %d) — search o'chirib qayta", finish_reason, attempt + 1)
                    self.stats.record(duration_ms, False)
                    body.pop("tools", None)  # google_search chalkashtiryapti
                    await asyncio.sleep(1)
                    continue

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
                    # error_msg bo'sh bo'lsa — to'liq javobni ko'rsatamiz (debug)
                    if not error_msg:
                        import json as _json
                        log.error("Gemini noma'lum javob (HTTP %d): %s",
                                  resp.status, _json.dumps(data, ensure_ascii=False)[:500])
                    else:
                        log.error("Gemini xatosi: %s", error_msg[:200])
                    self.stats.errors += 1
                    return ""

            except asyncio.TimeoutError:
                log.warning("Gemini timeout — qayta urinish")
                await asyncio.sleep(2)
            except Exception as e:
                log.error("Gemini exception (%s): %s",
                          type(e).__name__, str(e)[:200] or repr(e)[:200])
                self.stats.errors += 1
                return ""

        # Barcha kalit/urinishlar tugadi — fallback modelga o'tish
        if self._fallback_model and self._model_name != self._fallback_model:
            log.warning("Barcha urinishlar tugadi — fallback: %s → %s",
                        self._model_name, self._fallback_model)
            original_model = self._model_name
            self._model_name = self._fallback_model
            self._current_key = 0
            result = await self._chat_rest(system_prompt, messages, use_search=use_search)
            self._model_name = original_model  # keyingi so'rov uchun qaytarish
            return result

        log.error("Barcha urinishlar muvaffaqiyatsiz (%d)", total_attempts)
        return ""

    async def _chat_vertex_media(self, system_prompt: str, messages: list[dict]) -> str:
        """Vertex AI REST orqali rasm/audio yuborish (inline base64)."""
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append({"text": msg["text"]})
            for media in msg.get("media", []):
                # media["data"] bytes — base64 ga aylantiramiz
                raw = media["data"]
                if isinstance(raw, bytes):
                    encoded = base64.b64encode(raw).decode("ascii")
                else:
                    encoded = raw  # allaqachon base64 bo'lsa
                parts.append({
                    "inlineData": {
                        "mimeType": media["mime"],
                        "data": encoded,
                    }
                })
                log.info("Media (Vertex): %s, %d bayt",
                         media["mime"], len(raw) if isinstance(raw, bytes) else len(encoded))
            contents.append({"role": msg["role"], "parts": parts})

        body = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"temperature": 0.9, "maxOutputTokens": 16384},
        }
        http = await self._get_http()

        for attempt in range(MAX_RETRIES):
            url, headers = self._build_request(self._model_name, stream=False)
            start = time.time()
            try:
                log.info("Vertex media REST (urinish %d)...", attempt + 1)
                async with http.post(url, json=body, headers=headers) as resp:
                    data = await resp.json()
                duration_ms = (time.time() - start) * 1000

                if "candidates" in data and data["candidates"]:
                    parts = data["candidates"][0].get("content", {}).get("parts", [])
                    text = "\n".join(p["text"] for p in parts if "text" in p)
                    if text:
                        self.stats.record(duration_ms, True, len(text))
                        log.info("Vertex media javob: %d belgi, %.0fms", len(text), duration_ms)
                        return text

                err = data.get("error", {}).get("message", "")
                log.error("Vertex media xato: %s | data: %s", err[:200], str(data)[:300])
                if "429" in str(data) or "quota" in err.lower():
                    delay = (BASE_DELAY_MS / 1000) * (2 ** attempt)
                    await asyncio.sleep(min(delay, 30))
                else:
                    self.stats.errors += 1
                    return ""
            except Exception as e:
                log.error("Vertex media exception: %s", str(e)[:200] or repr(e)[:200])
                await asyncio.sleep(2)
        return ""

    async def _chat_sdk(self, system_prompt: str, messages: list[dict]) -> str:
        """Media (rasm/audio) bilan suhbat. Vertex rejimida REST + inline base64,
        AI Studio rejimida google.generativeai SDK ishlatiladi.
        """
        # Vertex rejimi — REST orqali inline base64 yuborish
        if self._use_vertex:
            return await self._chat_vertex_media(system_prompt, messages)

        # AI Studio SDK yo'li (eski)
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append(msg["text"])
            for media in msg.get("media", []):
                parts.append({"mime_type": media["mime"], "data": media["data"]})
                log.info("Media: %s, %d bayt", media["mime"], len(media["data"]))
            contents.append({"role": msg["role"], "parts": parts})

        total_keys = max(len(self._keys), 1)

        for attempt in range(total_keys * MAX_RETRIES):
            start = time.time()
            try:
                genai.configure(api_key=self._keys[self._current_key])
                model = genai.GenerativeModel(
                    model_name=self._model_name,
                    system_instruction=system_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=0.9, max_output_tokens=16384,
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
