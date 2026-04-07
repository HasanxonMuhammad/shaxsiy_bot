import asyncio
import google.generativeai as genai
import logging

log = logging.getLogger(__name__)


class GeminiEngine:
    def __init__(self, api_keys: list[str], model: str = "gemini-2.5-flash"):
        self._keys = api_keys
        self._model_name = model
        self._current_key = 0

    def _rotate_key(self):
        old = self._current_key
        self._current_key = (self._current_key + 1) % len(self._keys)
        log.info("Kalit: #%d → #%d", old + 1, self._current_key + 1)

    async def chat(self, system_prompt: str, messages: list[dict], use_search: bool = False) -> str:
        """
        messages: [{"role": "user", "text": "...", "media": [{"data": bytes, "mime": "image/jpeg"}]}]
        use_search: True bo'lsa Google Search tool yoqiladi
        """
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

        # Google Search tool
        tools = None
        if use_search:
            tools = [{"google_search": {}}]

        total_keys = len(self._keys)

        for attempt in range(total_keys * 2):
            try:
                genai.configure(api_key=self._keys[self._current_key])

                model_kwargs = {
                    "model_name": self._model_name,
                    "system_instruction": system_prompt,
                    "generation_config": genai.GenerationConfig(
                        temperature=0.9,
                        max_output_tokens=4096,
                    ),
                }
                if tools:
                    model_kwargs["tools"] = tools

                model = genai.GenerativeModel(**model_kwargs)
                log.info("Gemini so'rov yuborilmoqda (kalit #%d, search=%s)...", self._current_key + 1, use_search)
                response = await model.generate_content_async(contents=contents)

                if response and response.candidates:
                    # Barcha text partlarni yig'ish
                    result_parts = []
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            result_parts.append(part.text)
                    text = "\n".join(result_parts) if result_parts else ""
                    log.info("Gemini javob olindi: %d belgi", len(text))
                    return text
                else:
                    log.warning("Gemini bo'sh javob qaytardi")
                    return ""

            except Exception as e:
                error_str = str(e)
                log.error("Gemini xatosi (kalit #%d): %s", self._current_key + 1, error_str[:200])
                if "429" in error_str or "quota" in error_str.lower():
                    self._rotate_key()
                    if (attempt + 1) % total_keys == 0:
                        log.warning("Barcha kalitlar limit — 30s kutish")
                        await asyncio.sleep(30)
                else:
                    return ""

        log.error("Barcha urinishlar muvaffaqiyatsiz")
        return ""
