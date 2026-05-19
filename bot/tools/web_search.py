"""Web search tool — DuckDuckGo HTML endpoint orqali.

Bot o'z bilim va lokal tool'laridan javob topa olmaganda chaqiriladi.
API kalit kerak emas, bepul. Internet jihat sifatida ishlatish uchun.
"""
import asyncio
import html
import logging
import re
from typing import List, Dict
from urllib.parse import unquote, urlparse, parse_qs

import aiohttp

log = logging.getLogger(__name__)

_DDG_HTML = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,uz;q=0.8,ru;q=0.7,ar;q=0.6",
}

# DDG natija blok shabloni — har bir natija uchun title/url/snippet ajratamiz
_RESULT_RE = re.compile(
    r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a\s+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _strip_html(s: str) -> str:
    """HTML teglar va entity'larni tozalash."""
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _resolve_ddg_url(url: str) -> str:
    """DDG /l/?uddg=... redirect URL ni asl URL ga aylantirish."""
    if not url:
        return url
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if parsed.path == "/l/" or parsed.netloc.endswith("duckduckgo.com"):
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
    except Exception:
        pass
    return url


def _format_results(query: str, results: List[Dict[str, str]]) -> str:
    """Natijalarni AI uchun o'qiladigan formatda chiqarish."""
    if not results:
        return f"Qidiruv: {query!r}\nNatija topilmadi."
    lines = [f"Qidiruv: {query!r}", f"Natijalar ({len(results)}):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r['title']}")
        lines.append(f"URL: {r['url']}")
        if r.get("snippet"):
            lines.append(f"Snippet: {r['snippet']}")
    return "\n".join(lines)


async def web_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safe: str = "moderate",
    timeout: float = 15.0,
) -> str:
    """DuckDuckGo orqali web qidirish.

    Returns:
        Formatlangan string — har natija title+URL+snippet bilan.
        Xato bo'lsa "Web search xato: ..." matni.
    """
    q = (query or "").strip()
    if not q:
        return "Web search xato: query bo'sh"

    safe_map = {"off": "-2", "moderate": "-1", "strict": "1"}
    data = {
        "q": q,
        "kl": region,
        "kp": safe_map.get(safe, "-1"),
        "b": "",
        "df": "",
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=_HEADERS,
        ) as session:
            async with session.post(_DDG_HTML, data=data) as resp:
                if resp.status != 200:
                    log.warning("web_search: DDG status=%s", resp.status)
                    return f"Web search xato: qidiruv xizmati javob bermadi (HTTP {resp.status})"
                text = await resp.text()
    except asyncio.TimeoutError:
        return "Web search xato: qidiruv timeout"
    except Exception as e:
        log.error("web_search xato: %s", e)
        return f"Web search xato: {e}"

    results: List[Dict[str, str]] = []
    for m in _RESULT_RE.finditer(text):
        raw_url, raw_title, raw_snippet = m.group(1), m.group(2), m.group(3)
        url = _resolve_ddg_url(raw_url)
        title = _strip_html(raw_title)
        snippet = _strip_html(raw_snippet)
        if not url or not title:
            continue
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    if not results:
        if "No results" in text or "no-results" in text:
            return f"Qidiruv: {q!r}\nNatija topilmadi."
        log.warning("web_search: natija parse qilolmadi (sahifa o'lcham %d)", len(text))
        return "Web search xato: natijani o'qib bo'lmadi (DDG sahifasi o'zgargan bo'lishi mumkin)"

    return _format_results(q, results)
