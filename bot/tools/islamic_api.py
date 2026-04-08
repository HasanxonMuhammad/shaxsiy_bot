"""
Islomiy API integratsiya — HadeethEnc + QuranEnc.
Hadis va Qur'on oyatlarini o'zbekcha/arabcha olish.
"""
import logging
import aiohttp

log = logging.getLogger(__name__)

HADEETH_BASE = "https://hadeethenc.com/api/v1"
QURAN_BASE = "https://quranenc.com/api/v1"
TIMEOUT = aiohttp.ClientTimeout(total=15)


class IslamicAPI:
    """HadeethEnc va QuranEnc API bilan ishlash."""

    # ── Hadis ────────────────────────────────────────────────────

    async def search_hadith(self, query: str, lang: str = "uz", limit: int = 3) -> str:
        """Kategoriyalar bo'yicha hadis qidirish."""
        try:
            # Avval barcha kategoriyalarni olish
            cats = await self._get_categories(lang)
            if not cats:
                return "Hadis API ga ulanib bo'lmadi"

            # Kerakli kategoriyalarni topish — sarlavhada so'z bor
            matching_cats = []
            q_lower = query.lower()
            for cat in cats:
                if q_lower in cat["title"].lower():
                    matching_cats.append(cat)

            # Agar kategoriya topilmasa — barcha kategoriyalardan hadislarni qidirish
            if not matching_cats:
                # Barcha hadislardan umumiy qidirish
                return await self._search_all_hadith(query, lang, limit)

            # Topilgan kategoriyalardan hadislar olish
            results = []
            for cat in matching_cats[:3]:
                hadeeths = await self._get_hadeeths_by_category(cat["id"], lang, limit)
                results.extend(hadeeths)
                if len(results) >= limit:
                    break

            if not results:
                return await self._search_all_hadith(query, lang, limit)

            return self._format_hadeeths(results[:limit])
        except Exception as e:
            log.error("Hadis qidirish xatosi: %s", e)
            return f"Hadis qidirishda xato: {e}"

    async def get_hadith_by_id(self, hadith_id: str, lang: str = "uz") -> str:
        """Bitta hadisni ID bo'yicha olish."""
        try:
            url = f"{HADEETH_BASE}/hadeeths/one/?language={lang}&id={hadith_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return f"Hadis #{hadith_id} topilmadi"
                    data = await resp.json()

            return self._format_one_hadith(data)
        except Exception as e:
            return f"Xato: {e}"

    async def _get_categories(self, lang: str) -> list:
        """Barcha kategoriyalar (ildiz + bolalar)."""
        try:
            url = f"{HADEETH_BASE}/categories/list?language={lang}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    return await resp.json()
        except Exception:
            return []

    async def _get_hadeeths_by_category(self, cat_id, lang: str, limit: int) -> list:
        """Kategoriya bo'yicha hadislar ro'yxati."""
        try:
            url = f"{HADEETH_BASE}/hadeeths/list?language={lang}&category_id={cat_id}&per_page={limit}&page=1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    items = data.get("data", [])

            # Har bir hadisning to'liq ma'lumotini olish
            full = []
            for item in items[:limit]:
                h = await self._fetch_one(item["id"], lang)
                if h:
                    full.append(h)
            return full
        except Exception:
            return []

    async def _search_all_hadith(self, query: str, lang: str, limit: int) -> str:
        """Barcha kategoriyalardan hadis qidirish — barcha hadis sarlavhalarini tekshirish."""
        try:
            cats = await self._get_categories(lang)
            results = []
            q_lower = query.lower()

            for cat in cats:
                if len(results) >= limit:
                    break
                url = f"{HADEETH_BASE}/hadeeths/list?language={lang}&category_id={cat['id']}&per_page=50&page=1"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=TIMEOUT) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()

                for item in data.get("data", []):
                    if q_lower in item.get("title", "").lower():
                        h = await self._fetch_one(item["id"], lang)
                        if h:
                            results.append(h)
                        if len(results) >= limit:
                            break

            if not results:
                return "Bu mavzu bo'yicha hadis topilmadi"
            return self._format_hadeeths(results[:limit])
        except Exception as e:
            return f"Hadis qidirishda xato: {e}"

    async def _fetch_one(self, hid: str, lang: str) -> dict | None:
        try:
            url = f"{HADEETH_BASE}/hadeeths/one/?language={lang}&id={hid}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None

    def _format_one_hadith(self, h: dict) -> str:
        parts = []
        # Arabcha matn
        if h.get("hadeeth_ar"):
            parts.append(h["hadeeth_ar"])
        # O'zbekcha tarjima
        if h.get("hadeeth"):
            parts.append(h["hadeeth"])
        # Manba va daraja
        info = []
        if h.get("attribution"):
            info.append(f"Manba: {h['attribution']}")
        if h.get("grade"):
            info.append(f"Daraja: {h['grade']}")
        if info:
            parts.append(" | ".join(info))
        # Sharh
        if h.get("explanation"):
            parts.append(f"Sharh: {h['explanation'][:500]}")
        return "\n\n".join(parts)

    def _format_hadeeths(self, hadeeths: list) -> str:
        blocks = []
        for h in hadeeths:
            blocks.append(self._format_one_hadith(h))
        return "\n\n---\n\n".join(blocks)

    # ── Qur'on ───────────────────────────────────────────────────

    async def get_ayah(self, sura: int, ayah: int = 0, lang: str = "uzbek_sadiq") -> str:
        """Sura yoki oyat olish. ayah=0 bo'lsa butun surani qaytaradi."""
        try:
            url = f"{QURAN_BASE}/translation/sura/{lang}/{sura}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status != 200:
                        return f"Sura {sura} topilmadi"
                    data = await resp.json()

            results = data.get("result", [])
            if not results:
                return f"Sura {sura} bo'sh"

            if ayah > 0:
                # Bitta oyat
                for r in results:
                    if int(r.get("aya", 0)) == ayah:
                        return self._format_ayah(r)
                return f"Oyat {sura}:{ayah} topilmadi"
            else:
                # Butun sura (birinchi 10 oyat)
                lines = []
                for r in results[:10]:
                    lines.append(self._format_ayah(r))
                result = "\n\n".join(lines)
                if len(results) > 10:
                    result += f"\n\n... (jami {len(results)} oyat)"
                return result
        except Exception as e:
            return f"Qur'on API xatosi: {e}"

    def _format_ayah(self, r: dict) -> str:
        parts = []
        parts.append(r.get("arabic_text", ""))
        translation = r.get("translation", "")
        # Izohlarni olib tashlash (asosiy tarjima uchun)
        parts.append(translation)
        footnotes = r.get("footnotes", "")
        if footnotes:
            parts.append(f"Izoh: {footnotes[:300]}")
        return f"[{r.get('sura', '?')}:{r.get('aya', '?')}]\n" + "\n".join(parts)
