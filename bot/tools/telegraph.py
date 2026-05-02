"""Telegraph API — longread maqola yaratish va rasm yuklash."""
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)

TELEGRAPH_UPLOAD = "https://telegra.ph/upload"
TELEGRAPH_API = "https://api.telegra.ph"
TOKEN_FILE = Path("data/telegraph_token.txt")


async def get_or_create_token(author_name: str = "MudarrisAI",
                               author_url: str = "https://t.me/mudarrisblog") -> str:
    """Token bir marta yaratiladi, faylda saqlanadi."""
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{TELEGRAPH_API}/createAccount", json={
            "short_name": author_name[:32],
            "author_name": author_name,
            "author_url": author_url,
        }) as resp:
            data = await resp.json()

    if data.get("ok"):
        token = data["result"]["access_token"]
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token)
        log.info("Telegraph token yaratildi")
        return token

    raise Exception(f"Telegraph token yaratib bo'lmadi: {data}")


async def upload_image(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    """Rasmni telegra.ph/upload ga yuklash. /file/xxx.jpg qaytaradi."""
    ext = mime.split("/")[-1].replace("jpeg", "jpg")
    form = aiohttp.FormData()
    form.add_field("file", image_bytes, filename=f"image.{ext}", content_type=mime)

    async with aiohttp.ClientSession() as session:
        async with session.post(TELEGRAPH_UPLOAD, data=form,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()

    if isinstance(data, list) and data:
        src = data[0].get("src", "")
        log.info("Rasm yuklandi: %s", src)
        return src

    raise Exception(f"Rasm yuklashda xato: {data}")


# Telegraph qabul qiladigan teglar.
# Inline (matn ichida): b, i, em, strong, u, s, code, a, br
# Block: p, h3, h4, blockquote, ul, ol, li, pre, figure, img, hr, aside
_TG_BLOCK = {"p", "h3", "h4", "blockquote", "ul", "ol", "li", "pre", "figure", "aside"}
_TG_INLINE = {"b", "strong", "i", "em", "u", "s", "a", "code", "br"}
# Mapping: HTML teglar → Telegraph teglar
_TAG_MAP = {
    "h1": "h3", "h2": "h3",          # Telegraph faqat h3/h4 qabul qiladi
    "strong": "b", "em": "i",         # standartlashtirish
    "del": "s", "strike": "s",
}


class _TGHTMLParser(HTMLParser):
    """HTMLParser → Telegraph node tree.
    Block teglar nestedlash (h3/blockquote/p ichida bold), tartibni saqlaydi.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list = []
        self.stack: list[dict] = []  # ochiq elementlar stack'i

    # --- helpers ---

    def _push_text(self, text: str) -> None:
        if not text:
            return
        if self.stack:
            self.stack[-1].setdefault("children", []).append(text)
        else:
            # Top-level matn — paragraf ichiga oramiz
            self.nodes.append({"tag": "p", "children": [text]})

    def _open_node(self, tag: str, attrs: list) -> None:
        node: dict = {"tag": tag}
        # <a href="...">, <img src="..."> uchun atributlar
        if tag in ("a", "img", "iframe"):
            keep = {}
            for k, v in attrs:
                if v is None:
                    continue
                if tag == "a" and k == "href":
                    keep[k] = v
                elif tag == "img" and k == "src":
                    keep[k] = v
                elif tag == "iframe" and k == "src":
                    keep[k] = v
            if keep:
                node["attrs"] = keep
        self.stack.append(node)

    def _close_top(self) -> dict | None:
        if not self.stack:
            return None
        node = self.stack.pop()
        # Bo'sh children'ni olib tashlash (Telegraph "br" kabi)
        if not node.get("children") and node["tag"] not in ("br", "img", "hr"):
            return None
        if self.stack:
            self.stack[-1].setdefault("children", []).append(node)
        else:
            self.nodes.append(node)
        return node

    # --- HTMLParser hooks ---

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = _TAG_MAP.get(tag, tag)
        if tag == "br":
            # void teg — to'g'ridan-to'g'ri qo'yamiz
            br = {"tag": "br"}
            if self.stack:
                self.stack[-1].setdefault("children", []).append(br)
            else:
                self.nodes.append(br)
            return
        if tag == "img":
            node = {"tag": "img"}
            for k, v in attrs:
                if k == "src" and v:
                    node["attrs"] = {"src": v}
                    break
            if self.stack:
                self.stack[-1].setdefault("children", []).append(node)
            else:
                self.nodes.append(node)
            return
        if tag in _TG_BLOCK or tag in _TG_INLINE:
            self._open_node(tag, attrs)
        # qabul qilinmagan teg (div, span, table, ...) — e'tibor bermaymiz,
        # lekin uning ichidagi matn yaqin element ichiga tushadi

    def handle_endtag(self, tag: str) -> None:
        tag = _TAG_MAP.get(tag, tag)
        # Stack'da shu teg bormi tekshiramiz
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                # Stack'dagi yuqoridagi keng-keng tartibsiz teglarni avval yopib tashlaymiz
                while len(self.stack) - 1 > i:
                    self._close_top()
                self._close_top()
                return
        # Topilmasa — e'tibor bermaymiz

    def handle_data(self, data: str) -> None:
        # Telegraph node tree'da bo'sh joyni qoldirish kerak emas, ammo
        # paragraflar orasidagi yagona bo'sh qatorlarni ham saqlamaymiz.
        if data is None:
            return
        if not data.strip() and self.stack:
            # Mavjud node ichida bo'lsak — bo'sh joyni saqlaymiz (so'zlar orasi)
            self.stack[-1].setdefault("children", []).append(data)
            return
        if data.strip():
            self._push_text(data)


def html_to_nodes(html: str) -> list:
    """HTML matnni Telegraph node formatiga aylantirish.
    Block teglarni to'g'ri ajratadi, inline formatlash (b/i/code) saqlanadi.
    """
    if not html:
        return [{"tag": "p", "children": [""]}]
    parser = _TGHTMLParser()
    try:
        parser.feed(html)
        # Qolgan ochiq elementlarni yopib qo'yish
        while parser.stack:
            parser._close_top()
    except Exception as e:
        log.warning("html_to_nodes parse xatosi (%s) — plain matn fallback", e)
        return [{"tag": "p", "children": [_strip_tags(html[:8000])]}]

    # Bo'sh natija bo'lsa — fallback
    if not parser.nodes:
        return [{"tag": "p", "children": [_strip_tags(html[:8000])]}]
    return parser.nodes


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


async def create_page(title: str, content_html: str,
                      image_src: str | None = None,
                      author_name: str = "MudarrisAI",
                      author_url: str = "https://t.me/mudarrisblog") -> str:
    """Telegraph maqola yaratish. URL qaytaradi."""
    token = await get_or_create_token(author_name, author_url)

    nodes = []
    if image_src:
        nodes.append({"tag": "img", "attrs": {"src": image_src}})

    nodes.extend(html_to_nodes(content_html))

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{TELEGRAPH_API}/createPage", json={
            "access_token": token,
            "title": title[:256],
            "author_name": author_name,
            "author_url": author_url,
            "content": nodes,
            "return_content": False,
        }, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()

    if data.get("ok"):
        url = data["result"]["url"]
        log.info("Telegraph maqola yaratildi: %s", url)
        return url

    raise Exception(f"Telegraph maqola yaratishda xato: {data}")
