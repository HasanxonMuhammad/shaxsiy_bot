import asyncio
import logging

import aiohttp
import google.generativeai as genai

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"


class GeminiEngine:
    def __init__(self, api_keys: list[str], model: str = "gemini-2.5-flash"):
        self._keys = api_keys
        self._model_name = model
        self._current_key = 0
        self._http: aiohttp.ClientSession | None = None

    def _rotate_key(self):
        old = self._current_key
        self._current_key = (self._current_key + 1) % len(self._keys)
        log.info("Kalit: #%d → #%d", old + 1, self._current_key + 1)

    async def _get_http(self):
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def chat(self, system_prompt: str, messages: list[dict], use_search: bool = False) -> str:
        has_media = any(m.get("media") for m in messages)

        # Media bor yoki search kerak — mos usulni tanlaymiz
        if use_search:
            return await self._chat_rest(system_prompt, messages, use_search=True)
        elif has_media:
            return await self._chat_sdk(system_prompt, messages)
        else:
            return await self._chat_rest(system_prompt, messages, use_search=False)

    async def _chat_rest(self, system_prompt: str, messages: list[dict], use_search: bool = False) -> str:
        """REST API orqali — google_search qo'llab-quvvatlaydi."""
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

        for attempt in range(total_keys * 2):
            url = API_URL.format(model=self._model_name, key=self._keys[self._current_key])
            try:
                log.info("Gemini REST so'rov (kalit #%d, search=%s)...", self._current_key + 1, use_search)
                async with http.post(url, json=body) as resp:
                    data = await resp.json()

                if "candidates" in data:
                    parts = []
                    for p in data["candidates"][0]["content"]["parts"]:
                        if "text" in p:
                            parts.append(p["text"])
                    text = "\n".join(parts)
                    log.info("Gemini javob olindi: %d belgi", len(text))
                    return text

                error_msg = data.get("error", {}).get("message", "")
                if "429" in str(data.get("error", {}).get("code", "")) or "quota" in error_msg.lower():
                    log.warning("Rate limit — keyingi kalit")
                    self._rotate_key()
                    if (attempt + 1) % total_keys == 0:
                        await asyncio.sleep(30)
                else:
                    log.error("Gemini REST xatosi: %s", error_msg[:200])
                    return ""

            except Exception as e:
                log.error("Gemini REST xatosi: %s", str(e)[:200])
                return ""

        return ""

    async def _chat_sdk(self, system_prompt: str, messages: list[dict]) -> str:
        """SDK orqali — media (rasm/audio) qo'llab-quvvatlaydi."""
        contents = []
        for msg in messages:
            parts = []
            if msg.get("text"):
                parts.append(msg["text"])
            for media in msg.get("media", []):
                parts.append({
                    "mime_type": media["mime"],
                    "data": media["data"],
                })
                log.info("Media qo'shildi: %s, %d bayt", media["mime"], len(media["data"]))
            contents.append({"role": msg["role"], "parts": parts})

        total_keys = len(self._keys)

        for attempt in range(total_keys * 2):
            try:
                genai.configure(api_key=self._keys[self._current_key])
                model = genai.GenerativeModel(
                    model_name=self._model_name,
                    system_instruction=system_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=0.9,
                        max_output_tokens=4096,
                    ),
                )
                log.info("Gemini SDK so'rov (kalit #%d)...", self._current_key + 1)
                response = await model.generate_content_async(contents=contents)

                if response and response.candidates:
                    result = []
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "text") and part.text:
                            result.append(part.text)
                    text = "\n".join(result)
                    log.info("Gemini javob olindi: %d belgi", len(text))
                    return text
                else:
                    return ""

            except Exception as e:
                error_str = str(e)
                log.error("Gemini SDK xatosi (kalit #%d): %s", self._current_key + 1, error_str[:200])
                if "429" in error_str or "quota" in error_str.lower():
                    self._rotate_key()
                    if (attempt + 1) % total_keys == 0:
                        await asyncio.sleep(30)
                else:
                    return ""

        return ""
