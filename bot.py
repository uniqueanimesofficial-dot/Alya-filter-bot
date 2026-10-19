import asyncio, hashlib, html, logging, os, random, re, time
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
asyncio.set_event_loop(asyncio.new_event_loop())  # Pyrogram needs a loop at import time (Python 3.12+)
from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatAction, ChatMembersFilter, ChatMemberStatus as S, ChatType as T, MessageEntityType as E, ParseMode
from pymongo import ReturnDocument
from pyrogram.types import ChatPermissions, InlineKeyboardButton as B, InlineKeyboardMarkup as M, InputMediaPhoto

load_dotenv(); env = os.environ.get
API_ID, API_HASH, TOKEN, MONGO = int(env("API_ID", 0)), env("API_HASH"), env("BOT_TOKEN"), env("MONGO_URI")
OWNER = int(env("OWNER_ID", 0))
assert API_ID and API_HASH and TOKEN and MONGO and OWNER, "set API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_ID"
logging.basicConfig(level=logging.INFO); log = logging.getLogger("fb")

db = AsyncIOMotorClient(MONGO)[env("DB_NAME", "FilterBot")]
fl, cn, ch, ad, st, wc, wn = db.filters, db.connections, db.chats, db.admins, db.settings, db.welcome, db.warns
app = Client("fb", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN, parse_mode=ParseMode.HTML, in_memory=True)

GRP = (T.GROUP, T.SUPERGROUP)
MEDIA = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")
KW, ADMINS, SEEN = {}, set(), set()
BTN, TAG = re.compile(r"\[(.+?)\]\(buttonurl:(.+?)((?::(?:same|red|green|blue))*)\)"), re.compile(r"<[^>]+>")
STY = {"red": "danger", "green": "success", "blue": "primary"}
plain = lambda s: html.unescape(TAG.sub("", s)).strip()

# ---------------------------------------------------- start screen branding
BRAND = "ALYA"                       # decorative letter buttons (one letter per button)
MAINTAINER = "Pʀɨʍɛ ƈօʀɛ"
MAINTAINER_URL = "https://t.me/PrimeCoreHQ"
START_IMGS = ["https://files.catbox.moe/sla8rd.jpg" "https://files.catbox.moe/9tqcsy.jpg" "https://files.catbox.moe/t3c8bc.jpg" "https://files.catbox.moe/xh6fdc.jpg"
    # ⚠️ USE DIRECT IMAGE URLs HERE FOR "PREVIEW ONLY" MODE (e.g. "https://i.imgur.com/xyz.jpg")
    # If you use Telegram file_ids (AgACAgIA...), it will send as a normal downloadable photo.
]
LOAD_EMOJIS = ["⚙️", "🔄", "⏳", "✨"]

SC = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ꜰ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ",
      "m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}
def smallcaps(s): return "".join(SC.get(ch, ch) for ch in (s or "").lower())
def is_url(img): return img.startswith("http")

def greet():
    h = datetime.now().hour
    if 5 <= h < 12: return "GOOD MORNING"
    if 12 <= h < 17: return "GOOD AFTERNOON"
    if 17 <= h < 22: return "GOOD EVENING"
    return "GOOD NIGHT"

def start_txt(u):
    return (f"HEY {smallcaps(u.first_name or 'FRIEND')} ツ, {greet()} 👋\n\n"
            "I AM THE MOST POWERFUL AUTO FILTER BOT WITH PREMIUM FEATURES, "
            "JUST ADD ME TO YOUR GROUP AND ENJOY!\n\n"
            f'<blockquote>▶ <b>MAINTAINED BY :</b> <a href="{MAINTAINER_URL}">{MAINTAINER}</a> ❞</blockquote>')

def start_kb(idx, uname):
    return M([[B(ch, callback_data=f"sb:img:{idx}") for ch in smallcaps(BRAND)],
              [B("ADD ME TO YOUR GROUP ➕", url=f"https://t.me/{uname}?startgroup=true")],
              [B("• COMMANDS •", callback_data="sb:help"), B("• SETTINGS •", callback_data="sb:set")],
              [B("• CONNECTIONS •", callback_data="sb:conn"), B("• ABOUT •", callback_data="sb:about")]])

# ------------------------------------------------------- loading animations
LD = {}  # (chat_id, user_id) -> active loader message

async def _ttl(msg, ttl):
    await asyncio.sleep(ttl)
    try: await msg.delete()
    except Exception: pass

async def think(c, m, base="ᴡᴀɪᴛ  sᴇᴄ", emojis=None, ticks=3, delay=0.4, ttl=10):
    """Send an animated 'base. .. ...' loader message (Bold + Italic + Small Caps)."""
    if not m.from_user: return None
    msg = None
    for i in range(1, ticks + 1):
        body = base + "." * i
        styled_body = f"<b><i>{body}</i></b>"
        if emojis: styled_body = f"{emojis[(i - 1) % len(emojis)]} {styled_body}"
        if msg is None: msg = await c.send_message(m.chat.id, styled_body)
        else:
            try: await msg.edit_text(styled_body)
            except Exception: pass
        await asyncio.sleep(delay)
    LD[(m.chat.id, m.from_user.id)] = msg
    asyncio.create_task(_ttl(msg, ttl))
    return msg

async def clear_ld(c, chat, uid):
    msg = LD.pop((chat, uid), None)
    if msg:
        try: await msg.delete()
        except Exception: pass

async def ship(c, m, text=None, photo=None, **kw):
    """Delete the loader (if any) and send the real reply."""
    if m.from_user: await clear_ld(c, m.chat.id, m.from_user.id)
    if photo is not None: return await m.reply_photo(photo, caption=text, **kw)
    return await m.reply_text(text, **kw)

async def qanim(q, base="ᴡᴀɪᴛ  sᴇᴄ", ticks=3, delay=0.3):
    """Animate the clicked message itself (Bold + Italic + Small Caps)."""
    for i in range(1, ticks + 1):
        body = base + "." * i
        styled_body = f"<b><i>{body}</i></b>"
        try:
            if q.message.media: await q.message.edit_caption(styled_body)
            else: await q.message.edit_text(styled_body)
        except Exception: pass
        await asyncio.sleep(delay)

# Helper to handle URL previews vs File IDs safely
async def send_start(c, chat, img, caption, kb):
    if is_url(img):
        # Send as link preview (hides URL using zero-width space \u200b)
        await c.send_message(chat, f'<a href="{img}">\u200b</a>\n{caption}', disable_web_page_preview=False, reply_markup=kb)
    else:
        await c.send_photo(chat, img, caption=caption, reply_markup=kb)

async def update_start(q, img, caption, kb):
    if is_url(img):
        try: await q.message.delete()
        except Exception: pass
        await q.message.chat.send_message(f'<a href="{img}">\u200b</a>\n{caption}', disable_web_page_preview=False, reply_markup=kb)
    else:
        if q.message.photo:
            await q.message.edit_media(InputMediaPhoto(img, caption=caption, parse_mode=ParseMode.HTML), reply_markup=kb)
        else:
            try: await q.message.delete()
            except Exception: pass
            await q.message.chat.send_photo(img, caption=caption, reply_markup=kb)

HELP = """<b>🤖 Filter Bot</b>

<blockquote>
<b>👥 Admins</b> (group, or PM after /connect)
• <code>/filter</code> word - reply to a msg/media to save it
• <code>/filter</code> "two words" text - text filter
• <code>/filters</code> - list with 🗑 delete buttons
• <code>/settings</code> - protection panel (anti-link, flood, etc.)
• <code>/allowlink</code> domain.com - links that anti-link ignores
• <code>/warn</code> (reply) reason | <code>/unwarn</code> | <code>/warns</code>
• <code>/setwel</code> - welcome message editor (PM only)
</blockquote>

<blockquote>
<b>⚙️ Filter modes &amp; Buttons</b>
• <code>regex:^hi</code> or <code>contains:price</code> (default = whole word)
• Reply to a post (media + buttons) with <code>/filter any words</code>
• Buttons: <code>[Name](buttonurl:https://x.com)</code>
  Add <code>:same</code> (same row), <code>:red</code> <code>:green</code> <code>:blue</code>
• <code>/color</code> word red green - set button colours
</blockquote>

<blockquote>
<b>👑 Bot Owner / Admins</b>
• <code>/broadcast</code> (reply) - send to all groups &amp; channels
• <code>/stats</code> - view bot statistics
• <code>/addadmin</code> id | <code>/rmadmin</code> id
• <code>/info</code> - view user info
</blockquote>
"""

def parse(t):
    rows = []
    for m in BTN.finditer(t or ""):
        lb, url = plain(m[1]), plain(m[2])
        if url.lower().startswith("t.me/"): url = "https://" + url
        if not lb or not url.lower().startswith(("http://", "https://", "tg://")): continue
        o = m[3].split(":")
        b = {"text": lb, "url": url}
        for x in o:
            if x in STY: b["s"] = STY[x]
        if "same" in o and rows: rows[-1].append(b)
        else: rows.append([b])
    return BTN.sub("", t or "").strip(), rows

def kmode(s):
    s = (s or "").strip()
    for pre, mt in (("regex:", "regex"), ("re:", "regex"), ("contains:", "contains"), ("has:", "contains")):
        if s.lower().startswith(pre): return mt, s[len(pre):].strip()
    return "word", s

def kwsplit(s, low=True):
    s = (s or "").strip()
    m = re.match(r'^(?:"|&quot;|“)(.+?)(?:"|&quot;|”)\s*(.*)$', s, re.S)
    if m: k, b = m[1], m[2]
    else:
        p = s.split(None, 1)
        k, b = (p[0], p[1] if len(p) > 1 else "") if p else ("", "")
    return (plain(k).lower() if low else plain(k)), b.strip()

def sudo(u): return u == OWNER or u in ADMINS

async def is_adm(c, cid, uid, owner=False, trust=False):
    if sudo(uid): return True
    try: m = await c.get_chat_member(cid, uid)
    except Exception: return trust
    return m.status in ((S.OWNER,) if owner else (S.OWNER, S.ADMINISTRATOR))

async def target(c, m, owner=False):
    if not m.from_user: return None
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        g = (await cn.find_one({"_id": u}) or {}).get("active")
        if not g:
            await clear_ld(c, m.chat.id, u)
            await m.reply_text("<b>❌ NOT CONNECTED</b>\n\n<blockquote>Send /connect in your group, or /connect -100id here.</blockquote>")
            return None
    if await is_adm(c, g, u, owner, trust=(m.chat.type not in GRP and not owner)): return g
    await clear_ld(c, m.chat.id, u)
    await m.reply_text("<b>❌ PERMISSION DENIED</b>\n\n<blockquote>Only the group owner can do this.</blockquote>" if owner else "<b>❌ PERMISSION DENIED</b>\n\n<blockquote>Admins only.</blockquote>")
    return None

async def title(c, g):
    try: return (await c.get_chat(g)).title or str(g)
    except Exception: return str(g)

async def conn_set(u, g, add=True):
    d = await cn.find_one({"_id": u}) or {}
    gs = [x for x in d.get("groups", []) if x != g]
    if add: gs.append(g)
    act = g if add else (d.get("active") if d.get("active") != g else (gs[-1] if gs else None))
    await cn.update_one({"_id": u}, {"$set": {"groups": gs, "active": act}}, upsert=True)

async def api_send(chat, reply_id, d):
    ft = d.get("ft")
    kb = [[{k: v for k, v in (("text", b["text"]), ("url", b["url"]), ("style", b.get("s"))) if v} for b in r] for r in d["b"]]
    pl = {"chat_id": chat, "reply_parameters": {"message_id": reply_id, "allow_sending_without_reply": True},
          "reply_markup": {"inline_keyboard": kb}}
    if ft:
        pl[ft] = d["fid"]
        if ft not in ("sticker", "video_note") and d.get("t"): pl["caption"], pl["parse_mode"] = d["t"], "HTML"
        method = "send" + "".join(w.title() for w in ft.split("_"))
    else:
        pl.update(text=d.get("t") or ".", parse_mode="HTML", link_preview_options={"is_disabled": True})
        method = "sendMessage"
    async with ClientSession(timeout=ClientTimeout(total=30)) as s:
        async with s.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=pl) as r:
            j = await r.json()
    if not j.get("ok"): raise RuntimeError(j.get("description"))
    return j["result"]["message_id"]

async def send(c, m, d):
    if any(b.get("s") for r in d.get("b") or [] for b in r):
        try: return await api_send(m.chat.id, m.id, d)
        except Exception as e: log.warning("coloured send failed (%s) - falling back to plain buttons", e)
    mk = M([[B(b["text"], url=b["url"]) for b in r] for r in d["b"]]) if d.get("b") else None
    ft, kw = d.get("ft"), dict(chat_id=m.chat.id, reply_to_message_id=m.id, reply_markup=mk)
    for _ in range(2):
        try:
            if ft:
                extra = {} if ft in ("sticker", "video_note") else {"caption": d["t"]}
                return await getattr(c, "send_" + ft)(**{ft: d["fid"]}, **extra, **kw)
            return await c.send_message(text=d["t"] or ".", disable_web_page_preview=True, **kw)
        except FloodWait as e: await asyncio.sleep(e.value)
        except Exception as e: return log.warning("send failed %s: %s", m.chat.id, e)

@app.on_message(group=-1)
async def debug_log(c, m):
    log.info("update: chat=%s user=%s text=%r", m.chat.id, m.from_user and m.from_user.id, (m.text or m.caption or "")[:40])

# ------------------------------------------------------------------ /start
@app.on_message(filters.command(["start", "help"]))
async def start(c, m):
    if m.chat.type == T.PRIVATE and m.command[1:2] == ["setwel"]: return await setwel(c, m)
    if m.chat.type == T.PRIVATE and m.command[0] == "start":
        only = len(m.command) == 1
        await think(c, m, "sᴛᴀʀᴛɪɴɢ", emojis=LOAD_EMOJIS if only else None, ttl=15)
        await clear_ld(c, m.chat.id, m.from_user.id)
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            await send_start(c, m.chat.id, START_IMGS[idx], start_txt(m.from_user), start_kb(idx, c.me.username))
        else:
            await m.reply_text(start_txt(m.from_user), reply_markup=start_kb(0, c.me.username))
        return
    await think(c, m)
    await ship(c, m, HELP)

async def sb_text(c, q, txt, kb):
    chat = q.message.chat.id
    if q.message.photo or q.message.media:
        try: await q.message.delete()
        except Exception: pass
        await c.send_message(chat, txt, reply_markup=kb, disable_web_page_preview=True)
    else:
        try: await q.message.edit_text(txt, reply_markup=kb)
        except Exception: await c.send_message(chat, txt, reply_markup=kb, disable_web_page_preview=True)

@app.on_callback_query(filters.regex(r"^sb:"))
async def sb_cb(c, q):
    p = q.data.split(":"); a = p[1]; chat = q.message.chat.id
    if a == "img":  # ALYA letter buttons -> NO animation, just swap image instantly
        if not START_IMGS: return await q.answer("No images added yet.", show_alert=True)
        cur = int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else -1
        idx = random.choice([i for i in range(len(START_IMGS)) if i != cur] or [0])
        await q.answer()
        await update_start(q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        return
    if a == "back":
        await qanim(q)
        await q.answer()
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            try: await q.message.delete()
            except Exception: pass
            await send_start(c, chat, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        else:
            await sb_text(c, q, start_txt(q.from_user), start_kb(0, c.me.username))
        return
    if a == "help":
        await qanim(q); await q.answer()
        return await sb_text(c, q, HELP, M([[B("◀ BACK", callback_data="sb:back")]]))
    if a == "about":
        await qanim(q); await q.answer()
        txt = (f"<b>❓ ABOUT {BRAND}</b>\n\n"
               "<blockquote>I AM AN AUTO FILTER BOT WITH PREMIUM FEATURES.\n"
               f"MAINTAINED BY : <a href=\"{MAINTAINER_URL}\">{MAINTAINER}</a></blockquote>")
        return await sb_text(c, q, txt, M([[B("◀ BACK", callback_data="sb:back")]]))
    if a == "set":
        g = (await cn.find_one({"_id": q.from_user.id}) or {}).get("active")
        if not g: return await q.answer("Connect a group first: send /connect in your group.", show_alert=True)
        if not await is_adm(c, g, q.from_user.id, trust=True): return await q.answer("Admins only.", show_alert=True)
        await qanim(q); await q.answer()
        cf = await cfg(g)
        kb = st_kb(g, cf); kb.inline_keyboard.append([B("◀ BACK", callback_data="sb:back")])
        return await sb_text(c, q, st_txt(cf), kb)
    if a == "conn":
        await qanim(q); await q.answer()
        kb = await conn_kb(c, q.from_user.id)
        if kb: kb.inline_keyboard.append([B("◀ BACK", callback_data="sb:back")])
        return await sb_text(c, q, "<b>🔗 YOUR GROUPS</b>\n\n<blockquote>Tap to select, 🗑 to remove.</blockquote>" if kb
                             else "<b>📭 NO CONNECTIONS</b>\n\n<blockquote>Send /connect in your group first.</blockquote>",
                             kb or M([[B("◀ BACK", callback_data="sb:back")]]))

@app.on_message(filters.command("info"))
async def info_cmd(c, m):
    await think(c, m)
    u = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else m.from_user
    if not u:
        return await ship(c, m, "<b>❌ NO USER</b>\n\n<blockquote>Reply to a user or send in a group to see info.</blockquote>")
    text = "<b>👤 USER INFO</b>\n\n<blockquote>"
    text += f"<b>🆔 ID:</b> <code>{u.id}</code>\n"
    text += f"<b>👤 FIRST NAME:</b> {html.escape(u.first_name or 'None')}\n"
    if u.last_name: text += f"<b>👤 LAST NAME:</b> {html.escape(u.last_name)}\n"
    if u.username: text += f"<b>🔗 USERNAME:</b> @{u.username}\n"
    text += f"<b>👑 PREMIUM:</b> {'✅ Yes' if u.is_premium else '❌ No'}\n"
    text += f"<b>🤖 BOT:</b> {'✅ Yes' if u.is_bot else '❌ No'}\n"
    text += f"<b>🔗 PROFILE:</b> <a href='tg://user?id={u.id}'>Click Here</a>"
    text += "</blockquote>"
    await ship(c, m, text, disable_web_page_preview=True)

@app.on_message(filters.command(["filter", "add"]))
async def add(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    p = (m.text.html if m.text else "").split(None, 1)
    mt, arg = kmode(p[1] if len(p) > 1 else "")
    low = mt != "regex"
    k, rest = kwsplit(arg, low)
    r, ft, fid = m.reply_to_message, None, None
    if r and not arg.lstrip().startswith(('"', "&quot;", "“")):
        k, rest = (plain(arg).lower() if low else plain(arg)), ""
    if not k or len(k) > 100:
        return await ship(c, m,
            "<b>❌ USAGE ERROR</b>\n\n"
            "<blockquote>"
            "Reply to a message with <code>/filter keyword</code>\n"
            "Or <code>/filter word text</code>\n\n"
            "<b>Modes:</b>\n"
            "• <code>contains:price</code>\n"
            "• <code>regex:^hi(llo)?$</code>"
            "</blockquote>")
    if mt == "regex":
        try: re.compile(k)
        except re.error as e: return await ship(c, m, f"<b>❌ INVALID REGEX</b>\n\n<blockquote>{html.escape(str(e))}</blockquote>")
    t, b = parse(rest)
    if r:
        for x in MEDIA:
            if getattr(r, x, None): ft, fid = x, getattr(r, x).file_id; break
        body = r.text or r.caption
        bt, bb = parse(body.html if body else "")
        t, b = bt or t, bb + b
        kb = getattr(r.reply_markup, "inline_keyboard", None)
        if kb:
            rows = [[{"text": x.text, "url": x.url} for x in row if getattr(x, "url", None)] for row in kb]
            b = [row for row in rows if row] + b
    if not t and not fid: return await ship(c, m, "<b>❌ EMPTY FILTER</b>\n\n<blockquote>Reply to a message/media or add some text.</blockquote>")
    if fid and len(t) > 1024: return await ship(c, m, "<b>❌ CAPTION TOO LONG</b>\n\n<blockquote>Caption max is 1024 characters.</blockquote>")
    await fl.update_one({"g": g, "k": k}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "mt": mt}}, upsert=True)
    KW.pop(g, None)
    await ship(c, m, f"<b>✅ FILTER SAVED</b>\n\n<blockquote>Keyword: <code>{html.escape(k)}</code>" + ("" if mt == "word" else f" ({mt} match)") + "</blockquote>")

def kh(k): return hashlib.md5(k.encode()).hexdigest()[:10]

async def fl_kb(g, page=0):
    ds = sorted([(d["k"], d.get("mt", "word")) async for d in fl.find({"g": g}, {"k": 1, "mt": 1})])
    ks, pre = len(ds), {"regex": "re: ", "contains": "~ "}
    pages = max(1, -(-ks // 8)); page = max(0, min(page, pages - 1))
    rows = [[B((pre.get(mt, "") + k)[:32], callback_data=f"fv:{g}:{kh(k)}:{page}"), B("🗑", callback_data=f"fd:{g}:{kh(k)}:{page}")] for k, mt in ds[page * 8:page * 8 + 8]]
    if pages > 1:
        rows.append([B("◀", callback_data=f"fp:{g}:{page - 1}"), B(f"{page + 1}/{pages}", callback_data=f"fp:{g}:{page}"), B("▶", callback_data=f"fp:{g}:{page + 1}")])
    return (M(rows) if rows else None), ks

async def panel(c, m, g):
    kb, n = await fl_kb(g)
    if not kb: return await ship(c, m, "<b>📭 NO FILTERS</b>\n\n<blockquote>No filters yet. Reply to a post with <code>/filter keyword</code> to add one.</blockquote>")
    await ship(c, m, f"<b>📋 FILTERS IN {html.escape(await title(c, g))}</b> ({n})\n\n<blockquote>Tap 🗑 to delete a filter.</blockquote>", reply_markup=kb)

@app.on_message(filters.command("filters"))
async def lst(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    await panel(c, m, g)

@app.on_callback_query(filters.regex(r"^f[dpv]:"))
async def fl_cb(c, q):
    p = q.data.split(":"); a, g = p[0], int(p[1])
    cd = await cn.find_one({"_id": q.from_user.id}) or {}
    trust = q.message.chat.type == T.PRIVATE and g in cd.get("groups", [])
    if not await is_adm(c, g, q.from_user.id, trust=trust): return await q.answer("Admins only.", show_alert=True)
    page = int(p[2]) if a == "fp" else int(p[3])
    if a == "fv":
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        return await q.answer(k or "Already deleted", show_alert=True)
    await qanim(q)
    if a == "fp": await q.answer()
    else:
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        if k: await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
        await q.answer("Deleted 🗑" if k else "Already deleted")
    kb, n = await fl_kb(g, page)
    try: await q.message.edit_text(f"<b>📋 FILTERS IN {html.escape(await title(c, g))}</b> ({n})\n\n<blockquote>Tap 🗑 to delete a filter.</blockquote>" if kb else "<b>📭 NO FILTERS</b>\n\n<blockquote>No filters left.</blockquote>", reply_markup=kb)
    except Exception: pass

@app.on_message(filters.command(["del", "stop"]))
async def rm(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    p = (m.text.html or "").split(None, 1)
    arg = p[1] if len(p) > 1 else ""
    if not arg.strip(): return await panel(c, m, g)
    mt, arg = kmode(arg)
    k, _ = kwsplit(arg, mt != "regex")
    if not arg.lstrip().startswith(('"', "&quot;", "“")): k = plain(arg) if mt == "regex" else plain(arg).lower()
    r = await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
    await ship(c, m, f"<b>✅ DELETED</b>\n\n<blockquote>Filter <code>{k}</code> deleted.</blockquote>" if r.deleted_count else "<b>❌ NOT FOUND</b>\n\n<blockquote>No such filter. Send /filters and tap 🗑 instead.</blockquote>")

@app.on_message(filters.command("delall"))
async def delall(c, m):
    g = await target(c, m, owner=True)
    if g is None: return
    await think(c, m)
    await ship(c, m, "<b>⚠️ WARNING</b>\n\n<blockquote>Delete <b>all</b> filters?</blockquote>", reply_markup=M([[B("Yes", callback_data=f"da:y:{g}"), B("Cancel", callback_data="da:n:0")]]))

@app.on_callback_query(filters.regex(r"^da:"))
async def delall_cb(c, q):
    _, a, g = q.data.split(":"); g = int(g)
    if a != "y":
        await qanim(q)
        return await q.message.edit_text("<b>❌ CANCELLED</b>")
    if not await is_adm(c, g, q.from_user.id, True): return await q.answer("Owner only.", show_alert=True)
    await qanim(q)
    r = await fl.delete_many({"g": g}); KW.pop(g, None)
    await q.message.edit_text(f"<b>✅ SUCCESS</b>\n\n<blockquote>Deleted {r.deleted_count} filter(s).</blockquote>")

@app.on_message(filters.command("color"))
async def color(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    p = (m.text.html or "").split(None, 1)
    k, rest = kwsplit(p[1] if len(p) > 1 else "")
    cols = rest.lower().replace(",", " ").split()
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if not k or not cols: return await ship(c, m, '<b>❌ USAGE ERROR</b>\n\n<blockquote>Usage: /color keyword red green blue none\n(one colour per button, in order)</blockquote>')
    if not d or not d.get("b"): return await ship(c, m, "<b>❌ NO BUTTONS</b>\n\n<blockquote>No such filter, or it has no buttons.</blockquote>")
    i = 0
    for row in d["b"]:
        for b in row:
            col = cols[i] if i < len(cols) else None
            i += 1
            if col in STY: b["s"] = STY[col]
            else: b.pop("s", None)
    await fl.update_one({"g": g, "k": k}, {"$set": {"b": d["b"]}})
    await ship(c, m, f"<b>✅ UPDATED</b>\n\n<blockquote>Button colours updated for <code>{k}</code>.</blockquote>")

@app.on_message(filters.command("connect"))
async def connect(c, m):
    if not m.from_user: return
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        if len(m.command) < 2:
            return await m.reply_text("<b>❌ HOW TO CONNECT</b>\n\n<blockquote>Best way: send /connect <b>inside your group</b>.\nOr here: <code>/connect -100xxxxxxxxxx</code></blockquote>")
        try: g = int(m.command[1])
        except ValueError: return await m.reply_text("<b>❌ INVALID ID</b>\n\n<blockquote>The group id must be a number like <code>-1001234567890</code>.</blockquote>")
        try: await c.get_chat(g)
        except Exception as e:
            return await m.reply_text(f"<b>❌ UNREACHABLE</b>\n\n<blockquote>I can't reach that group ({type(e).__name__}).\nMake sure I'm added to it, then send /connect <b>inside the group</b> instead.</blockquote>")
    if not await is_adm(c, g, u): return await m.reply_text("<b>❌ NOT ADMIN</b>\n\n<blockquote>You must be an admin of that group to connect it.</blockquote>")
    await think(c, m)
    await conn_set(u, g)
    await ship(c, m, f"<b>✅ CONNECTED</b>\n\n<blockquote>Connected to <b>{html.escape(await title(c, g))}</b>. Manage filters from my PM.</blockquote>")

@app.on_message(filters.command("disconnect"))
async def disconnect(c, m):
    if not m.from_user: return
    await think(c, m)
    g = m.chat.id if m.chat.type in GRP else (await cn.find_one({"_id": m.from_user.id}) or {}).get("active")
    if g: await conn_set(m.from_user.id, g, False)
    await ship(c, m, "<b>✅ DISCONNECTED</b>" if g else "<b>❌ NOT CONNECTED</b>")

async def conn_kb(c, u):
    d = await cn.find_one({"_id": u}) or {}
    rows = [[B(("✅ " if g == d.get("active") else "") + await title(c, g), callback_data=f"cn:s:{g}"), B("🗑", callback_data=f"cn:d:{g}")] for g in d.get("groups", [])]
    return M(rows) if rows else None

@app.on_message(filters.command("connections") & filters.private)
async def conns(c, m):
    await think(c, m)
    kb = await conn_kb(c, m.from_user.id)
    await ship(c, m, "<b>🔗 YOUR GROUPS</b>\n\n<blockquote>Tap to select, 🗑 to remove.</blockquote>" if kb else "<b>📭 NO CONNECTIONS</b>", reply_markup=kb)

@app.on_callback_query(filters.regex(r"^cn:"))
async def conn_cb(c, q):
    _, a, g = q.data.split(":"); g, u = int(g), q.from_user.id
    await qanim(q)
    if a == "s": await cn.update_one({"_id": u}, {"$set": {"active": g}})
    else: await conn_set(u, g, False)
    await q.answer("Done")
    kb = await conn_kb(c, u)
    await (q.message.edit_reply_markup(kb) if kb else q.message.edit_text("<b>📭 NO CONNECTIONS</b>"))

CFG, PEND, FLOOD, ADC, OWN = {}, {}, {}, {}, {}

async def cfg(g):
    if g not in CFG: CFG[g] = await st.find_one({"_id": g}) or {}
    return CFG[g]

async def cfg_set(g, **kw):
    (await cfg(g)).update(kw)
    await st.update_one({"_id": g}, {"$set": kw}, upsert=True)

async def adm_c(c, g, u):
    t = ADC.get((g, u))
    if t and time.time() - t[1] < 300: return t[0]
    v = await is_adm(c, g, u); ADC[(g, u)] = (v, time.time()); return v

async def can(c, q, g):
    cd = await cn.find_one({"_id": q.from_user.id}) or {}
    return await is_adm(c, g, q.from_user.id, trust=(q.message.chat.type == T.PRIVATE and g in cd.get("groups", [])))

def st_txt(cf):
    return ("<b>⚙️ GROUP PROTECTION</b>\n\n"
            "<blockquote>"
            "Admins &amp; the owner are always allowed.\n"
            "Turn on 🧪 <b>Test mode</b> to test with your own account.\n\n"
            "• <b>Anti-link</b>: deletes links (except allowed) and PMs the owner.\n"
            "• <b>Anti-forward</b> / <b>Anti-mention</b>: delete forwarded messages / @mentions.\n"
            f"• <b>Flood</b>: {cf.get('fn', 6)} messages in {cf.get('ft', 5)}s = 10 min mute.\n"
            "• <b>Auto-warn</b>: each deleted message adds a warning; at the limit the user is punished."
            "</blockquote>"
            "\n<i>Bot needs admin rights: Delete messages + Restrict members.</i>")

def st_kb(g, cf):
    b = lambda lb, k: B(f"{lb} {'✅' if cf.get(k) else '⛔'}", callback_data=f"st:{g}:{k}")
    return M([[b("🔗 Anti-link", "al"), b("↪️ Anti-forward", "af")],
              [b("@ Anti-mention", "am"), b("🌊 Flood", "fd")],
              [b("⚠️ Auto-warn", "aw"), b("🧪 Test mode", "tm")],
              [B("➖", callback_data=f"st:{g}:w-"), B(f"Warn limit: {cf.get('wl', 3)}", callback_data=f"st:{g}:w0"), B("➕", callback_data=f"st:{g}:w+")],
              [B(f"Warn action: {cf.get('wa', 'mute')}", callback_data=f"st:{g}:wa")]])

@app.on_message(filters.command(["settings", "antilink"]))
async def settings_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    if m.command[0] == "antilink" and m.command[1:2] in (["on"], ["off"]): await cfg_set(g, al=m.command[1] == "on")
    await think(c, m)
    cf = await cfg(g)
    await ship(c, m, st_txt(cf), reply_markup=st_kb(g, cf))

@app.on_callback_query(filters.regex(r"^st:"))
async def st_cb(c, q):
    _, g, k = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("Admins only.", show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if k in ("al", "af", "am", "fd", "aw", "tm"): await cfg_set(g, **{k: not cf.get(k)})
    elif k in ("w+", "w-"): await cfg_set(g, wl=max(2, min(10, cf.get("wl", 3) + (1 if k == "w+" else -1))))
    elif k == "wa": await cfg_set(g, wa={"mute": "kick", "kick": "ban"}.get(cf.get("wa", "mute"), "mute"))
    await q.answer()
    try: await q.message.edit_text(st_txt(cf), reply_markup=st_kb(g, cf))
    except Exception: pass

@app.on_message(filters.command("setflood"))
async def setflood(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    try:
        n, t = int(m.command[1]), int(m.command[2]); assert 2 <= n <= 30 and 1 <= t <= 60
    except Exception: return await ship(c, m, "<b>❌ USAGE ERROR</b>\n\n<blockquote>Usage: /setflood messages seconds\n(e.g. <code>/setflood 6 5</code>)</blockquote>")
    await cfg_set(g, fn=n, ft=t)
    await ship(c, m, f"<b>✅ UPDATED</b>\n\n<blockquote>Flood limit: {n} messages in {t} seconds.</blockquote>")

async def tmp(c, g, text, secs=60, kb=None):
    try:
        w = await c.send_message(g, text, reply_markup=kb, disable_web_page_preview=True)
        await asyncio.sleep(secs); await w.delete()
    except Exception: pass

async def add_warn(c, g, u, why):
    cf = await cfg(g); lim = cf.get("wl", 3)
    r = await wn.find_one_and_update({"g": g, "u": u.id}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    n = r["n"]
    if n < lim:
        return f"<b>⚠️ WARNING ADDED</b>\n\n<blockquote>{u.mention} warned <b>({n}/{lim})</b>\nReason: {html.escape(why)}</blockquote>", M([[B("🗑 Remove warn", callback_data=f"uw:{g}:{u.id}")]])
    await wn.delete_one({"g": g, "u": u.id})
    act = cf.get("wa", "mute")
    try:
        if act == "mute": await c.restrict_chat_member(g, u.id, ChatPermissions())
        else:
            await c.ban_chat_member(g, u.id)
            if act == "kick": await c.unban_chat_member(g, u.id)
        done = {"mute": "muted", "kick": "kicked", "ban": "banned"}[act]
    except Exception as e:
        log.warning("warn action failed in %s: %s", g, e); done = "NOT punished (I need the 'Restrict members' admin right)"
    return f"<b>🚫 LIMIT REACHED</b>\n\n<blockquote>{u.mention} reached {lim} warnings and was {done}.\nReason: {html.escape(why)}</blockquote>", M([[B("↩️ Undo", callback_data=f"ub:{g}:{u.id}")]])

async def tgt_user(c, m):
    if m.chat.type not in GRP or not m.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id if m.from_user else 0)
        await m.reply_text("<b>❌ GROUP ONLY</b>\n\n<blockquote>Use this in a group, as a reply to the user's message.</blockquote>"); return None, None
    if not await is_adm(c, m.chat.id, m.from_user.id):
        await clear_ld(c, m.chat.id, m.from_user.id)
        await m.reply_text("<b>❌ ADMINS ONLY</b>"); return None, None
    r = m.reply_to_message
    if not r or not r.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id)
        await m.reply_text("<b>❌ REPLY MISSING</b>\n\n<blockquote>Reply to the user's message.</blockquote>"); return None, None
    return m.chat.id, r.from_user

@app.on_message(filters.command("warn"))
async def warn_cmd(c, m):
    g, u = await tgt_user(c, m)
    if not g: return
    if await is_adm(c, g, u.id): return await ship(c, m, "<b>❌ CANNOT WARN</b>\n\n<blockquote>I can't warn admins.</blockquote>")
    await think(c, m)
    txt, kb = await add_warn(c, g, u, " ".join(m.command[1:]) or "no reason given")
    await ship(c, m, txt, reply_markup=kb)

@app.on_message(filters.command(["unwarn", "resetwarns"]))
async def unwarn_cmd(c, m):
    g, u = await tgt_user(c, m)
    if not g: return
    await think(c, m)
    if m.command[0] == "resetwarns": await wn.delete_one({"g": g, "u": u.id}); left = 0
    else:
        r = await wn.find_one_and_update({"g": g, "u": u.id, "n": {"$gt": 0}}, {"$inc": {"n": -1}}, return_document=ReturnDocument.AFTER)
        left = r["n"] if r else 0
    await ship(c, m, f"<b>✅ UPDATED</b>\n\n<blockquote>{u.mention} now has {left} warning(s).</blockquote>")

@app.on_message(filters.command("warns") & filters.group)
async def warns_cmd(c, m):
    await think(c, m)
    r = m.reply_to_message
    u = r.from_user if r and r.from_user else m.from_user
    if not u: return
    d = await wn.find_one({"g": m.chat.id, "u": u.id})
    await ship(c, m, f"<b>⚠️ WARNINGS</b>\n\n<blockquote>{u.mention}: <b>{(d or {}).get('n', 0)}/{(await cfg(m.chat.id)).get('wl', 3)}</b> warnings.</blockquote>")

@app.on_message(filters.command(["unmute", "unban"]) & filters.group)
async def unrestrict(c, m):
    if not m.from_user or not await is_adm(c, m.chat.id, m.from_user.id): return
    await think(c, m)
    r = m.reply_to_message
    try: uid = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await ship(c, m, "<b>❌ USAGE ERROR</b>\n\n<blockquote>Reply to the user, or: <code>/unmute user_id</code></blockquote>")
    try: await c.unban_chat_member(m.chat.id, uid); await ship(c, m, "<b>✅ DONE</b>\n\n<blockquote>Restrictions removed.</blockquote>")
    except Exception as e: await ship(c, m, f"<b>❌ FAILED</b>\n\n<blockquote>{type(e).__name__} (I need the 'Restrict members' right)</blockquote>")

@app.on_callback_query(filters.regex(r"^u[wb]:"))
async def undo_cb(c, q):
    a, g, u = q.data.split(":"); g, u = int(g), int(u)
    if not await is_adm(c, g, q.from_user.id): return await q.answer("Admins only.", show_alert=True)
    await qanim(q)
    if a == "uw":
        r = await wn.find_one_and_update({"g": g, "u": u, "n": {"$gt": 0}}, {"$inc": {"n": -1}}, return_document=ReturnDocument.AFTER)
        msg = f"<b>✅ REMOVED</b>\n\n<blockquote>One warning removed ({r['n'] if r else 0} left).</blockquote>"
    else:
        try: await c.unban_chat_member(g, u)
        except Exception: pass
        msg = "<b>↩️ UNDONE</b>\n\n<blockquote>Punishment undone.</blockquote>"
    await q.answer("Done")
    try: await q.message.edit_text(msg)
    except Exception: pass

def norm(u): return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.strip().lower()).rstrip("/")

def allowed(link, wl):
    n = norm(link); host = re.split(r"[/?#]", n)[0]
    return any(n == a or n.startswith(a + "/") or ("/" not in a and (host == a or host.endswith("." + a))) for a in wl)

def dom_view(g, cf):
    ds = cf.get("dom", [])
    if not ds:
        return ("<b>🔗 ALLOWED LINKS</b>\n\n"
                "<blockquote>"
                "No allowed links yet.\n"
                "Add: <code>/allowlink domain.com</code> or <code>/allowlink t.me/yourchannel</code>"
                "</blockquote>"), None
    return ("<b>🔗 ALLOWED LINKS</b>\n\n"
            "<blockquote>"
            "Anti-link ignores these domains.\n"
            "Add more: <code>/allowlink domain.com</code>"
            "</blockquote>",
            M([[B(d[:40], callback_data=f"dn:{g}:{kh(d)}"), B("🗑", callback_data=f"dd:{g}:{kh(d)}")] for d in ds]))

@app.on_message(filters.command(["allowlink", "allowlinks"]))
async def allowlink(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    new = [norm(x) for x in m.command[1:] if "." in x]
    cf = await cfg(g)
    if new: await cfg_set(g, dom=list(dict.fromkeys(cf.get("dom", []) + new)))
    txt, kb = dom_view(g, cf)
    await ship(c, m, txt, reply_markup=kb)

@app.on_callback_query(filters.regex(r"^d[dn]:"))
async def dom_cb(c, q):
    a, g, h = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("Admins only.", show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if a == "dd": await cfg_set(g, dom=[d for d in cf.get("dom", []) if kh(d) != h])
    await q.answer("Removed" if a == "dd" else None)
    txt, kb = dom_view(g, cf)
    try: await q.message.edit_text(txt, reply_markup=kb)
    except Exception: pass

async def gc_owner(c, g):
    if g in OWN: return OWN[g]
    try:
        async for mem in c.get_chat_members(g, filter=ChatMembersFilter.ADMINISTRATORS):
            if mem.status == S.OWNER: OWN[g] = mem.user.id; return OWN[g]
    except Exception as e: log.warning("cannot find the owner of %s: %s", g, e)

def links_of(m):
    txt, out = (m.text or m.caption or ""), []
    for e in (m.entities or []) + (m.caption_entities or []):
        try:
            if e.type == E.TEXT_LINK and e.url: out.append(e.url)
            elif e.type == E.URL: out.append(txt[e.offset:e.offset + e.length])
        except Exception: pass
    return list(dict.fromkeys(out)) or re.findall(r"(?:https?://|t\.me/|www\.)\S+", txt)

async def report(c, g, m, found):
    u = m.from_user
    ls = "\n".join(f"• <code>{html.escape(x)}</code>" for x in found) or "-"
    text = (f"🚨 <b>LINK DELETED</b>\n<b>Group:</b> {html.escape(await title(c, g))} (<code>{g}</code>)\n"
            f"<b>User:</b> {u.mention}\n<b>ID:</b> <code>{u.id}</code>\n"
            f"<b>Username:</b> {'@' + u.username if u.username else 'none'}\n<b>Link(s):</b>\n{ls}")
    owner = await gc_owner(c, g)
    if owner:
        try: return await c.send_message(owner, text, disable_web_page_preview=True)
        except Exception as e: log.info("cannot PM owner %s of %s (%s) - owner must /start the bot", owner, g, e)
    for uid in [d["_id"] async for d in cn.find({"groups": g})]:
        try: await c.send_message(uid, text, disable_web_page_preview=True)
        except Exception: pass

async def punish(c, g, m, why):
    cf = await cfg(g)
    try: await m.delete()
    except Exception as e:
        log.warning("guard: cannot delete in %s (%s) - make the bot admin with 'Delete messages'", g, e); return False
    note, kb = f"<b>⚠️ DELETED</b>\n\n<blockquote>{m.from_user.mention}, {why}. Your message was deleted.</blockquote>", None
    if cf.get("aw"): note, kb = await add_warn(c, g, m.from_user, why)
    asyncio.create_task(tmp(c, g, note, 60, kb))
    return True

@app.on_message(filters.group & ~filters.service, group=5)
async def guard(c, m):
    g, u = m.chat.id, m.from_user
    if not u or u.is_self or u.id == 777000: return
    cf = await cfg(g)
    if not (cf.get("al") or cf.get("af") or cf.get("am") or cf.get("fd")): return
    if not cf.get("tm") and await adm_c(c, g, u.id): return
    ents = (m.entities or []) + (m.caption_entities or [])

    if cf.get("fd"):
        dq, now = FLOOD.setdefault((g, u.id), deque(maxlen=30)), time.time()
        dq.append((now, m.id))
        while dq and now - dq[0][0] > cf.get("ft", 5): dq.popleft()
        if len(dq) >= cf.get("fn", 6):
            ids = [i for _, i in dq]; dq.clear()
            try: await c.restrict_chat_member(g, u.id, ChatPermissions(), until_date=datetime.now() + timedelta(minutes=10))
            except Exception as e: log.warning("flood: cannot mute in %s (%s) - give the bot 'Restrict members'", g, e)
            try: await c.delete_messages(g, ids)
            except Exception: pass
            asyncio.create_task(tmp(c, g, f"<b>🌊 FLOOD MUTE</b>\n\n<blockquote>{u.mention} was muted for 10 minutes for flooding.</blockquote>", 60, M([[B("↩️ Undo", callback_data=f"ub:{g}:{u.id}")]])))
            if cf.get("aw"):
                wt, wk = await add_warn(c, g, u, "flooding"); asyncio.create_task(tmp(c, g, wt, 60, wk))
            m.stop_propagation()

    fwd = getattr(m, "forward_date", None) or getattr(m, "forward_from_chat", None) or getattr(m, "forward_sender_name", None)
    if cf.get("af") and fwd and await punish(c, g, m, "forwarded messages are not allowed here"): m.stop_propagation()

    if cf.get("am") and any(e.type == E.MENTION for e in ents) and await punish(c, g, m, "@mentions are not allowed here"): m.stop_propagation()

    if cf.get("al") and any(e.type in (E.URL, E.TEXT_LINK) for e in ents):
        bad = [x for x in links_of(m) if not allowed(x, cf.get("dom", []))]
        if bad and await punish(c, g, m, "links are not allowed here"):
            asyncio.create_task(report(c, g, m, bad)); m.stop_propagation()

@app.on_message(filters.group & (filters.new_chat_members | filters.left_chat_member), group=10)
async def del_service(c, m):
    try: await m.delete()
    except Exception: pass

WEL_VARS = "{first} {last} {fullname} {mention} {username} {id} {group} {count}"
WS = [0, 60, 300, 600]

def fmt_s(s): return "off" if not s else f"{s // 60}m"

async def wel_text(c, t, users, chat):
    if not t: return t
    u = users[0]
    r = {"{first}": html.escape(u.first_name or ""), "{last}": html.escape(u.last_name or ""),
         "{fullname}": html.escape(f"{u.first_name or ''} {u.last_name or ''}".strip()),
         "{mention}": ", ".join(x.mention for x in users),
         "{username}": f"@{u.username}" if u.username else html.escape(u.first_name or ""),
         "{id}": str(u.id), "{group}": html.escape(chat.title or "")}
    if "{count}" in t:
        try: r["{count}"] = str(await c.get_chat_members_count(chat.id))
        except Exception: r["{count}"] = ""
    for k, v in r.items(): t = t.replace(k, v)
    return t

async def wel_view(c, g):
    w = await wc.find_one({"_id": g}) or {}
    has = bool(w.get("t") or w.get("fid"))
    t = "<b>👋 WELCOME MESSAGE</b>\n\n"
    if has:
        t += f"<blockquote>Status: {'✅ ON' if w.get('on') else '⛔ OFF'} | Type: {w.get('ft') or 'text'}\n"
        t += f"Buttons: {sum(len(r) for r in w.get('b') or [])} | Auto-delete: {fmt_s(w.get('ad', 0))}</blockquote>\n"
    else:
        t += "<blockquote>No welcome message set yet.</blockquote>\n"
    t += ("\n<blockquote>"
          "Tap <b>✏️ Set / change</b>, then send me the welcome.\n"
          f"Variables: <code>{WEL_VARS}</code>\n"
          "Buttons: <code>[Join](buttonurl:https://t.me/x)</code>"
          "</blockquote>")
    rows = [[B("✏️ Set / change", callback_data=f"wl:{g}:set")]]
    if has:
        rows += [[B("👁 Preview", callback_data=f"wl:{g}:prev"), B("🔛 ON / OFF", callback_data=f"wl:{g}:tog")],
                 [B(f"⏱ Auto-delete: {fmt_s(w.get('ad', 0))}", callback_data=f"wl:{g}:ad"), B("🗑 Remove", callback_data=f"wl:{g}:rm")]]
    return t, M(rows)

@app.on_message(filters.command("setwel"))
async def setwel(c, m):
    if m.chat.type in GRP:
        await think(c, m)
        return await ship(c, m, "<b>👋 WELCOME EDITOR</b>\n\n<blockquote>Set the welcome message in my PM 👇</blockquote>", reply_markup=M([[B("Open PM", url=f"https://t.me/{c.me.username}?start=setwel")]]))
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    txt, kb = await wel_view(c, g)
    await ship(c, m, txt, reply_markup=kb)

@app.on_callback_query(filters.regex(r"^wl:"))
async def wel_cb(c, q):
    _, g, a = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("Admins only.", show_alert=True)
    w = await wc.find_one({"_id": g}) or {}
    if a == "set":
        await qanim(q)
        PEND[q.from_user.id] = g
        await q.answer()
        await q.message.reply_text("<b>✏️ EDITING</b>\n\n<blockquote>Send me the new welcome message now - text, or a photo/video/GIF with a caption.\n"
                                   "Buttons: <code>[Name](buttonurl:https://...)</code>\nSend /cancel to stop.</blockquote>")
        txt, kb = await wel_view(c, g)
        try: await q.message.edit_text(txt, reply_markup=kb)
        except Exception: pass
        return
    if a == "prev":
        await qanim(q)
        d = {"t": await wel_text(c, w.get("t"), [q.from_user], SimpleNamespace(title=await title(c, g), id=g)),
             "ft": w.get("ft"), "fid": w.get("fid"), "b": w.get("b")}
        await q.answer()
        await send(c, q.message, d)
        txt, kb = await wel_view(c, g)
        try: await q.message.edit_text(txt, reply_markup=kb)
        except Exception: pass
        return
    await qanim(q)
    if a == "tog": await wc.update_one({"_id": g}, {"$set": {"on": not w.get("on")}}, upsert=True)
    elif a == "rm": await wc.delete_one({"_id": g})
    elif a == "ad":
        cur = w.get("ad", 0)
        await wc.update_one({"_id": g}, {"$set": {"ad": WS[(WS.index(cur) + 1) % len(WS)] if cur in WS else 0}}, upsert=True)
    await q.answer()
    txt, kb = await wel_view(c, g)
    try: await q.message.edit_text(txt, reply_markup=kb)
    except Exception: pass

async def _pend(_, __, m): return bool(m.from_user) and m.from_user.id in PEND
pend = filters.create(_pend)

@app.on_message(filters.command("cancel") & filters.private)
async def cancel(c, m):
    await think(c, m, ticks=2, delay=0.3)
    PEND.pop(m.from_user.id, None)
    await ship(c, m, "<b>❌ CANCELLED</b>")

@app.on_message(filters.private & pend & ~filters.regex(r"^/"), group=2)
async def wel_capture(c, m):
    uid = m.from_user.id; g = PEND.pop(uid)
    ft = fid = None
    for x in MEDIA:
        if getattr(m, x, None): ft, fid = x, getattr(m, x).file_id; break
    body = m.text or m.caption
    t, b = parse(body.html if body else "")
    kb = getattr(m.reply_markup, "inline_keyboard", None)
    if kb: b = [r for r in ([{"text": x.text, "url": x.url} for x in row if getattr(x, "url", None)] for row in kb) if r] + b
    if not t and not fid: PEND[uid] = g; return await m.reply_text("<b>❌ INVALID INPUT</b>\n\n<blockquote>Send text, or media with a caption.</blockquote>")
    if fid and ft not in ("sticker", "video_note") and len(t) > 1024: PEND[uid] = g; return await m.reply_text("<b>❌ TOO LONG</b>\n\n<blockquote>Caption max is 1024 characters - send a shorter one.</blockquote>")
    await wc.update_one({"_id": g}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "on": True}}, upsert=True)
    txt, kb2 = await wel_view(c, g)
    await m.reply_text("<b>✅ SAVED</b>\n\n<blockquote>Welcome message saved!</blockquote>\n\n" + txt, reply_markup=kb2)
    m.stop_propagation()

async def later(c, g, mid, secs):
    await asyncio.sleep(secs)
    try: await c.delete_messages(g, mid)
    except Exception: pass

@app.on_message(filters.new_chat_members & filters.group)
async def welcome(c, m):
    g = m.chat.id
    users = [u for u in m.new_chat_members if not u.is_bot]
    if not users: return
    w = await wc.find_one({"_id": g})
    if not w or not w.get("on"): return
    await c.send_chat_action(g, ChatAction.TYPING)
    d = {"t": await wel_text(c, w.get("t"), users, m.chat), "ft": w.get("ft"), "fid": w.get("fid"), "b": w.get("b")}
    r = await send(c, m, d)
    if r and w.get("ad"): asyncio.create_task(later(c, g, getattr(r, "id", r), w["ad"]))

async def kws(g):
    if g not in KW:
        KW[g] = sorted([(d["k"], d.get("mt", "word")) async for d in fl.find({"g": g}, {"k": 1, "mt": 1})], key=lambda x: len(x[0]), reverse=True)
    return KW[g]

def hit(k, mt, txt):
    try:
        if mt == "contains": return k in txt.lower()
        if mt == "regex": return re.search(k, txt, re.I) is not None
        return re.search(rf"(?<!\w){re.escape(k)}(?!\w)", txt, re.I) is not None
    except re.error: return False

@app.on_message(filters.group & filters.text & ~filters.regex(r"^[/!]"), group=10)
async def listen(c, m):
    g = m.chat.id
    if g not in SEEN:
        SEEN.add(g); await ch.update_one({"_id": g}, {"$set": {"type": "group"}}, upsert=True)
    for k, mt in await kws(g):
        if hit(k, mt, m.text):
            d = await fl.find_one({"g": g, "k": k})
            if d:
                await c.send_chat_action(g, ChatAction.TYPING)
                # Show "finding..." animation before sending the filter
                loader = await c.send_message(g, "<b><i>ꜰɪɴᴅɪɴɢ...</i></b>", reply_to_message_id=m.id)
                await asyncio.sleep(0.8)
                try: await loader.delete()
                except Exception: pass
                await send(c, m, d)
            return

@app.on_chat_member_updated()
async def tracker(c, u):
    n, o = u.new_chat_member, u.old_chat_member
    x = n or o
    if u.chat.type == T.PRIVATE or not x or not x.user.is_self: return
    if n and n.status not in (S.LEFT, S.BANNED):
        await ch.update_one({"_id": u.chat.id}, {"$set": {"type": "channel" if u.chat.type == T.CHANNEL else "group"}}, upsert=True)
    else:
        await ch.delete_one({"_id": u.chat.id}); SEEN.discard(u.chat.id)

async def bsend(msg, cid):
    for _ in range(2):
        try: await msg.copy(cid); return True
        except FloodWait as e: await asyncio.sleep(e.value + 1)
        except (ChannelPrivate, ChatWriteForbidden, UserNotParticipant): await ch.delete_one({"_id": cid}); return False
        except Exception: return False
    return False

@app.on_message(filters.command("broadcast"))
async def broadcast(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    if not m.reply_to_message: return await m.reply_text("<b>❌ REPLY MISSING</b>\n\n<blockquote>Reply to the message you want to broadcast with /broadcast</blockquote>")
    s = await think(c, m, "ʙʀᴏᴀᴄᴀsᴛɴɢ", ttl=3600)
    ids = [d["_id"] async for d in ch.find()]
    ok = 0
    for i in ids:
        ok += await bsend(m.reply_to_message, i)
        await asyncio.sleep(0.3)
    LD.pop((m.chat.id, m.from_user.id), None)
    if s:
        try: await s.edit_text(f"<b>✅ DONE</b>\n\n<blockquote>Broadcast done: ✅ {ok} sent, ❌ {len(ids) - ok} failed.</blockquote>")
        except Exception: pass

@app.on_message(filters.command("stats"))
async def stats(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    g, h = await ch.count_documents({"type": "group"}), await ch.count_documents({"type": "channel"})
    await ship(c, m, f"<b>📊 BOT STATISTICS</b>\n\n<blockquote>Groups: {g}\nChannels: {h}\nFilters: {await fl.count_documents({})}\nAdmins: {len(ADMINS)}</blockquote>")

@app.on_message(filters.command(["addadmin", "rmadmin"]))
async def admin_cmd(c, m):
    if not m.from_user or m.from_user.id != OWNER: return
    r = m.reply_to_message
    try: u = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await m.reply_text("<b>❌ USAGE ERROR</b>\n\n<blockquote>Usage: /addadmin user_id (or reply to a user)</blockquote>")
    await think(c, m, ticks=2, delay=0.3)
    add = m.command[0] == "addadmin"
    if add: await ad.update_one({"_id": u}, {"$set": {"a": 1}}, upsert=True); ADMINS.add(u)
    else: await ad.delete_one({"_id": u}); ADMINS.discard(u)
    await ship(c, m, f"<b>✅ UPDATED</b>\n\n<blockquote>{'Added' if add else 'Removed'} admin <code>{u}</code></blockquote>")

@app.on_message(filters.command("admins"))
async def admins(c, m):
    if m.from_user and sudo(m.from_user.id):
        await think(c, m, ticks=2, delay=0.3)
        await ship(c, m, f"<b>👑 ADMIN LIST</b>\n\n<blockquote>Owner: <code>{OWNER}</code>\nAdmins: " + (", ".join(f"<code>{a}</code>" for a in ADMINS) or "none") + "</blockquote>")

async def web_start():
    a = web.Application()
    for p in ("/", "/health"): a.router.add_get(p, lambda r: web.Response(text="ok"))
    r = web.AppRunner(a); await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(env("PORT", 10000))).start()

async def ping():
    u = env("RENDER_EXTERNAL_URL")
    if not u: return
    async with ClientSession(timeout=ClientTimeout(total=20)) as s:
        while True:
            await asyncio.sleep(600)
            try:
                async with s.get(u): pass
            except Exception: pass

async def main():
    await web_start(); asyncio.create_task(ping())
    log.info("connecting to Telegram...")
    await app.start(); log.info("STARTED as @%s - send /start to it", (await app.get_me()).username)
    try:
        await fl.create_index([("g", 1), ("k", 1)], unique=True)
        ADMINS.update([d["_id"] async for d in ad.find()])
        log.info("MongoDB OK")
    except Exception as e:
        log.error("MongoDB problem (filters will not work): %s", e)
    await idle(); await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())