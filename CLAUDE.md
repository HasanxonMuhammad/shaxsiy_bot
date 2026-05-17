# CLAUDE.md — Loyiha kontekst (handoff)

Bu fayl Claude Code uchun. Yangi sessiya boshlanganda avtomatik kontekstga yuklanadi.

## 1. Loyiha nima

`d:\hasanxon\shaxsiy_bot` — bitta kod bazasi, **3 ta mustaqil Telegram bot**:

| Bot | Token Env | Username | Persona |
|-----|-----------|----------|---------|
| **Mudarris** | `.env` | @qamusaibot | Arab tili o'qituvchisi, hadis, Qur'on |
| **Olima** | `.env.aziza` | @olimambot | Ayollik+oilaviy yordamchi, Aziza rahbar |
| **Super Boshliq** | `.env.superboshliq` | @tsuosbossbot | TSUOS universitet leader |

Owner: Hasanxon Muhammad (ID `6350373395`, @hasanxon_muhammad). U bot egasi, asosiy suhbatdosh.

Aziza (Hasanxonning xotini, ID `5792080114`, @nosirjonovaazizaxon) — **Olima'ning yagona rahbari**. Mudarris/Super Boshliq'da VIP user.

## 2. Texnik stack

- **AI:** Gemini 3.1 Pro via Vertex AI REST (32 API kalit, kontekst caching yoqilgan)
- **Framework:** aiogram 3.27 (Python 3.13)
- **DB:** aiosqlite + FTS5 (hadis, kitob, lug'at, amthal, sheer, tabir, dalil RAG'lari)
- **Hosting:** Azure VM `shaxsiy-bot` (Linux), systemd service `shaxsiy-bot`
- **Repo:** `github.com/HasanxonMuhammad/shaxsiy_bot`

Mudarris radio bot alohida: `/home/hasanxon/radio_bot/` systemd `mudarris-radio` — userbot Telethon orqali voice chatlarda radio quyadi.

## 3. Deploy workflow

Loyiha Windows mahalliy + Azure Linux server. Deploy oqimi:

```bash
# 1. Mahalliy edit
# 2. Sintaksis tekshirish
python -c "import ast; ast.parse(open('bot/telegram/dispatcher.py', encoding='utf-8').read()); print('OK')"

# 3. Commit + push
git add ... && git commit -m "..." && git push origin main

# 4. Azure'da deploy (run_in_background=true bilan)
az vm run-command invoke -g SHAXSIY-BOT_GROUP -n shaxsiy-bot --command-id RunShellScript --scripts "cd /home/hasanxon/shaxsiy_bot && sudo -u hasanxon git pull origin main 2>&1 | tail -3 && sudo systemctl restart shaxsiy-bot && sleep 5 && ps -ef | grep 'main.py --env' | grep -v grep | wc -l" --query 'value[0].message' -o tsv
```

Tekshirish: `journalctl -u shaxsiy-bot --since '2 minutes ago' | grep -E 'Olima|Mudarris|Super Boshliq|ishga tushdi'`

## 4. Asosiy fayllar

- **`bot/telegram/dispatcher.py`** — asosiy oqim: handler, buffer, group filterlari, response yuborish
- **`bot/ai/gemini.py`** — Vertex REST, context caching, media support (inline base64)
- **`bot/tools/handler.py`** — barcha toollar (hadis, quron, lugat, send_poll, gen_image, telegraf_post, va h.k.)
- **`bot/db.py`** — chat, user, message, reminder, memory, student tables
- **`prompts/mudarris.md`** / **`prompts/aziza.md`** / **`prompts/shaxsiybot.md`** — bot personalari + tool ko'rsatmalari
- **`main.py`** — entrypoint, `--env` flag bilan qaysi botni ishga tushirishni belgilaydi
- **`start_all.py`** — 3 ta botni birga ishga tushiradi (systemd shu skriptni chaqiradi)

## 5. Muhim guruh ID'lar

```
-1003436904722  Botlar choyxonasi (3 bot + Aziza)
-1002401618185  Xonai Saodat (Mudarris+Olima kanal materiali)
-1003280067467  Arab tili o'rganuvchilar (Olima smart filter + RTL, 200+ a'zo)
-1003520828779  Arab tili eski guruh
-1003831509848  Arab ana tili (Mudarris teng suhbatdosh, fus'ha/ammiya)
-1003910902823  Mudarris muhokama
-1003942449794  Mudarris kanal (@mudarrisblog)
-1003969668190  Olima kanali
-1003910902823  Mudarris Radio target
```

## 6. So'nggi muhim qarorlar (2026-05-15)

**Xarajat optimizatsiya** ($220/oy → ~$40-60/oy kutilmoqda):
- `USE_SEARCH=false` har 3 botda — Vertex caching ishlay boshladi (search bilan cache mumkin emas)
- Promptlar -26% qisqartirildi (mudarris 53→34KB, aziza 25→23KB, shaxsiybot 19→15KB)
- Backup: `prompts/_backup_2026-05-15/`
- Inner voice loop'lar **o'chirildi** — bot guruhda o'z-o'zidan yozmaydi
- **Bularni qaytarib yoqma** ruxsatsiz — egalari ataylab o'chirgan

**Format/UX:**
- Markdown `**bold**` post-processor → HTML `<b>` (handler.py'da `markdown_to_html`)
- HTML parse fail bo'lsa: sanitatsiya → fallback plain (dispatcher.py'da `_safe_send` / `_sanitize_html`)
- Photo+caption: matn >1024 belgi bo'lsa rasm + alohida xabar (rasmga reply)
- Video, GIF, document/video, animation media qo'llab-quvvatlanadi

**RTL:**
- Olima'da Arab guruh (`-1003280067467`) — har doim RTL (U+200F har qator boshida)
- Mudarris'da: agar matn ≥80% arabcha bo'lsa guruhda RTL, aralash bo'lsa odatiy

**Smart filter (Olima + Mudarris uchun "arab tili" guruhlarida):**
- Faqat: @mention, reply-to-bot, owner/Aziza, savol pattern (?, ؟, "qanday", "ما", arab murojaat `يا عالمة` va h.k.)
- 2 user reply zanjiriga ARALASHMA
- Kod: `dispatcher.py` da `ARAB_LEARNER_GROUP`, `ARABIC_ADDRESS_RE` qismi

## 7. Promptlar haqida

Har bir bot promptida quyidagi blok ATOMIC va load-bearing — **kesma**:
- Persona (kim, qaysi rolda)
- Munosabatlar (Aziza-rahbar, Hasanxon-owner, juftlar)
- Til qoidalari
- Tool ta'riflari (kesilsa AI tool ishlatishni unutadi)
- Diniy fatvo qoidasi (xato fatvo bermaslik)
- Smart filter qoidalari guruh ID bilan
- TO'QIMA QOIDA (hadis/oyat/lug'at tool'siz to'qima)

**Kesilishi mumkin** (sifatga ta'sirsiz):
- Emoji kategoriyalari (AI semantik biladi)
- Tool ishlatish to'liq misollar (qisqa Misol: bilan almashtirsa bo'ladi)
- Takrorlangan qoidalar

## 8. Foydalanuvchi (Hasanxon) bilan ishlash uslubi

- **O'zbek tilida yozadi.** Javob ham o'zbekcha.
- **Texnik bilimi yaxshi** (bot quradi, Telegram API biladi). Texnik tushuntirish kerak bo'lsa to'g'ridan-to'g'ri ber.
- **Tezlik qadrlaydi:** uzun rejasiz darrov amalga oshir. Lekin xavfli operatsiyalarda (force push, fayl o'chirish) avval so'ra.
- **Xato bo'lsa ochiq tan ol:** "halol gapirib" deydi — go'yo to'g'ri ekan kabi gapirma.
- **Sifatga e'tiborli:** prompt qisqartishda ham "sifatga ta'sir qilmasa" deb urg'u beradi.
- **Loglar ko'rsatma:** muammoni hal qilishda log tekshirish darrov birinchi qadam.

## 9. Tez-tez chiqadigan operatsiyalar

```bash
# Loglarda muayyan bot xatosini ko'rish
az vm run-command invoke -g SHAXSIY-BOT_GROUP -n shaxsiy-bot --command-id RunShellScript --scripts "journalctl -u shaxsiy-bot --since '10 minutes ago' | grep -iE 'error|xato|olima' | tail -20" --query 'value[0].message' -o tsv

# Cache hit rate (faollik tekshiruvi)
az vm run-command invoke -g SHAXSIY-BOT_GROUP -n shaxsiy-bot --command-id RunShellScript --scripts "journalctl -u shaxsiy-bot --since '1 hour ago' | grep -E 'Cache yaratildi|cachedContent' | tail -20" --query 'value[0].message' -o tsv

# Env o'zgartirish + restart
az vm run-command invoke -g SHAXSIY-BOT_GROUP -n shaxsiy-bot --command-id RunShellScript --scripts "cd /home/hasanxon/shaxsiy_bot && sudo -u hasanxon sed -i 's/^KEY=.*/KEY=value/' .env && sudo systemctl restart shaxsiy-bot && sleep 5 && ps -ef | grep 'main.py --env' | grep -v grep | wc -l"

# Guruhga ALLOWED_GROUPS ga yangi ID qo'shish
sed -i 's|^ALLOWED_GROUPS=.*|&,-1003NEW_ID|' .env
```

## 10. Pitfalls — diqqat

1. **Telegram bot pollda ovoz BERA OLMAYDI.** Faqat foydalanuvchi/userbot vote qila oladi. Bot quizga "ishtirokchi" sifatida qatnashishi mumkin emas — buni so'rasalar tushuntir.
2. **Caption limit 1024, message limit 4096.** Photo+uzun matn → photo+alohida xabar.
3. **Bot privacy mode:** agar guruhda bot poll xabarini ko'rmasa — bot admin emasligida yoki privacy on bo'lganida. BotFather'da o'chirilishi kerak.
4. **`/main.py --env .env.foo`** — multiprocessing orqali 3 process, har bir bot alohida config. `Config.BOT_NAME` orqali bot identifikatsiya qilinadi.
5. **HTML parser strict:** Telegram'da faqat `b/strong/i/em/u/s/code/pre/a/blockquote/tg-spoiler/tg-emoji/span/br` allowed. Boshqa teglar `_sanitize_html` orqali escape qilinadi.
6. **RLM (U+200F) RTL uchun:** har qator boshiga qo'shiladi (`\n` o'rniga `\n\u200F`).
7. **`isolate_arabic` (FSI/PDI U+2068/U+2069)** — har bir arabcha bo'lakni alohida bidi izolyatsiya qiladi. Aralash matn uchun.
8. **`force_rtl_blockquote`** — blockquote ichida arabcha bo'lsa RLM qo'shadi (Telegram blockquote'ni LTR deb deteksiya qilishi mumkin).

## 11. Endi xato qilma

- Hech qachon `--no-verify`, `--force` ishlatma (owner so'ramagunich).
- `git reset --hard` faqat aniq so'ralganda.
- `prompts/_backup_*` papkasini o'chirma.
- `.env` fayllarda `OWNER_ID` va `VIP_IDS` ni qaytarma yoki ishonchsiz qiymatga almashtirma.
- Olima/Mudarris/Super Boshliq personalarini o'zgartirma — egasi mukammal mato bilan ishlab chiqgan.

---

**Yakuniy maslahat:** har yangi vazifa boshlashdan oldin oxirgi 5-10 ta commitni ko'rib chiq (`git log --oneline -20`). Loyiha tez o'zgaradi, eskirgan ma'lumotni qo'llama. Foydalanuvchi yangi tushuntirish bersa — uni MEMORY ga ham yoz, keyingi sessiyada qayta tushuntirilmasin.
