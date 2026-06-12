"""Bot API 10.1 Rich Messages (sendRichMessage) qo'llab-quvvatlash.

aiogram 3.x hali Bot API 10.1 ni bilmaydi — shuning uchun raw HTTP chaqiruv.
InputRichMessage: {"html": "..."} yoki {"markdown": "..."} + is_rtl.
Limitlar: 32768 belgi, 500 blok, 16 nesting, 50 media, 20 ustun.
"""

import logging
import re

import aiohttp

log = logging.getLogger(__name__)

# Klassik parse_mode=HTML qo'llamaydigan, faqat rich rejimda ishlaydigan teglar
_RICH_TAG_RE = re.compile(
    r"<(h[1-6]|table|details|summary|ul|ol|li|aside|figure|figcaption|footer"
    r"|mark|sub|sup|tg-math-block|tg-math|tg-collage|tg-slideshow|tg-map"
    r"|tg-reference|tg-time|hr|p)\b",
    re.IGNORECASE,
)

RICH_LIMIT = 32768


def has_rich_features(text: str) -> bool:
    """Matnda rich-only teg bormi (sendRichMessage talab qiladi)."""
    return bool(text) and bool(_RICH_TAG_RE.search(text))


async def send_rich_message(
    token: str,
    chat_id: int,
    html: str,
    reply_to: int | None = None,
    is_rtl: bool = False,
) -> bool:
    """sendRichMessage raw API chaqiruv. Muvaffaqiyatda True, aks holda False
    (chaqiruvchi klassik yo'lga tushadi)."""
    url = f"https://api.telegram.org/bot{token}/sendRichMessage"
    rich: dict = {"html": html[:RICH_LIMIT]}
    if is_rtl:
        rich["is_rtl"] = True
    payload: dict = {"chat_id": chat_id, "rich_message": rich}
    if reply_to:
        payload["reply_parameters"] = {
            "message_id": reply_to,
            "allow_sending_without_reply": True,
        }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()
        if data.get("ok"):
            return True
        log.warning("sendRichMessage rad etildi: %s", data.get("description"))
        return False
    except Exception as e:
        log.warning("sendRichMessage exception: %s", e)
        return False
