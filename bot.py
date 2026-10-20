import asyncio, hashlib, html, logging, os, random, re, time
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
asyncio.set_event_loop(asyncio.new_event_loop())
from aiohttp import ClientSession, ClientTimeout, web
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatAction, ChatMembersFilter, ChatMemberStatus as S, ChatType as T, MessageEntityType as E, ParseMode
from pymongo import ReturnDocument
from pyrogram.types import ChatPermissions, InlineKeyboardButton as B, InlineKeyboardMarkup as M

# Import all settings from config.py
from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URI, DB_NAME, OWNER_ID as OWNER,
    BOT_NAME, MAINTAINER, MAINTAINER_URL, START_IMGS, LOAD_EMOJIS,
    BTN_COLOR, DEFAULT_BTN_COLOR, PREMIUM_EMOJIS, HELP_TEXT, SC
)

logging.basicConfig(level=logging.INFO); log = logging.getLogger("fb")

db = AsyncIOMotorClient(MONGO_URI)[DB_NAME]
fl, cn, ch, ad, st, wc, wn = db.filters, db.connections, db.chats, db.admins, db.settings, db.welcome, db.warns
app = Client("fb", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=ParseMode.HTML, in_memory=True)

GRP = (T.GROUP, T.SUPERGROUP)
MEDIA = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")
KW, ADMINS, SEEN = {}, set(), set()
BTN, TAG = re.compile(r"\[(.+?)\]\(buttonurl:(.+?)((?::(?:same|red|green|blue))*)\)"), re.compile(r"<[^>]+>")
STY = {"red": "danger", "green": "success", "blue": "primary"}
plain = lambda s: html.unescape(TAG.sub("", s)).strip()

# ---------------------------------------------------- helpers
def smallcaps(s): return "".join(SC.get(ch, ch) for ch in (s or "").lower())
def is_url(img): return img.startswith("http")
def sty(w): return STY.get((w or "").lower()) or None

def pe(name):
    """Helper to easily use Premium Emojis defined in config.py"""
    data = PREMIUM_EMOJIS.get(name)
    if not data: return "✨"
    eid, fallback = data
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'

def greet():
    h = datetime.now().hour
    if 5 <= h < 12: return "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ"
    if 12 <= h < 17: return "ɢᴏᴏᴅ ᴀꜰᴛᴇʀɴᴏᴏɴ"
    if 17 <= h < 22: return "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ"
    return "ɢᴏᴏᴅ ɴɪɢʜᴛ"

def start_txt(u):
    return (f"ʜᴇʏ {smallcaps(u.first_name or 'friend')} ツ, {greet()} {pe('wave')}\n\n"
            "ɪ ᴀᴍ ᴛʜᴇ ᴍᴏsᴛ ᴘᴏᴡᴇʀꜰᴜʟ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇs, "
            f"ᴊᴜsᴛ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ᴀɴᴅ ᴇɴᴊᴏʏ! {pe('fire')}\n\n"
            f'<blockquote>▶ <b>ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ :</b> <a href="{MAINTAINER_URL}">{MAINTAINER}</a> ❞</blockquote>')

# ------------------------------------------- raw Bot API layer (blockquote + colours + previews)
def R(text, cb=None, url=None, s=None):
    b = {"text": text}
    if cb: b["callback_data"] = cb
    if url: b["url"] = url
    if sty(s): b["style"] = sty(s)
    return b

def raw_kb(rows):
    kb = []
    for r in rows or []:
        nr = []
        for b in r:
            nb = {"text": b.get("text")}
            if b.get("url"): nb["url"] = b["url"]
            if b.get("cb") or b.get("callback_data"): nb["callback_data"] = b.get("cb") or b.get("callback_data")
            sv = b.get("style") or b.get("s")
            if sv: nb["style"] = sv if sv in ("danger", "success", "primary") else sty(sv)
            nr.append({k: v for k, v in nb.items() if v})
        kb.append(nr)
    return {"inline_keyboard": kb}

async def raw_post(method, pl):
    async with ClientSession(timeout=ClientTimeout(total=30)) as s:
        async with s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=pl) as r:
            j = await r.json()
    if not j.get("ok"): raise RuntimeError(j.get("description"))
    return j["result"]

def _lpo(preview):
    o = {"is_disabled": not preview}
    if preview: o.update(show_above_link=True, prefer_large_media=True)
    return o

async def ui_send(chat, text, rows=None, reply_id=None, preview=False):
    pl = {"chat_id": chat, "text": text, "parse_mode": "HTML", "link_preview_options": _lpo(preview)}
    if reply_id: pl["reply_parameters"] = {"message_id": reply_id, "allow_sending_without_reply": True}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return (await raw_post("sendMessage", pl))["message_id"]

async def ui_edit(chat, mid, text, rows=None, preview=False):
    pl = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML", "link_preview_options": _lpo(preview)}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return await raw_post("editMessageText", pl)

async def raw_send_photo(chat, photo, caption, rows):
    pl = {"chat_id": chat, "photo": photo, "caption": caption, "parse_mode": "HTML"}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return (await raw_post("sendPhoto", pl))["message_id"]

async def raw_edit_media(chat, mid, photo, caption, rows):
    return await raw_post("editMessageMedia", {"chat_id": chat, "message_id": mid,
                          "media": {"type": "photo", "media": photo, "caption": caption, "parse_mode": "HTML"},
                          "reply_markup": raw_kb(rows)})

def start_kb(idx, uname):
    c = BTN_COLOR
    return [
        [R(ch, cb=f"sb:img:{idx}", s=c["letters"]) for ch in smallcaps(BOT_NAME)],
        [R("ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ", url=f"https://t.me/{uname}?startgroup=true", s=c["add_group"])],
        [R("ᴄᴏᴍᴍᴀɴᴅs", cb="sb:help", s=c["commands"]), R("sᴇᴛᴛɪɴɢs", cb="sb:set", s=c["settings"])],
        [R("ᴄᴏɴɴᴇᴄᴛɪᴏɴs", cb="sb:conn", s=c["connections"]), R("ᴀʙᴏᴜᴛ", cb="sb:about", s=c["about"])],
    ]

BACK = lambda: [R("◀ ʙᴀᴄᴋ", cb="sb:back", s=BTN_COLOR["back"])]

async def send_start(c, chat, img, caption, kb):
    if is_url(img): await ui_send(chat, f'<a href="{img}">\u200b</a>\n{caption}', kb, preview=True)
    else: await raw_send_photo(chat, img, caption, kb)

async def update_start(q, img, caption, kb):
    chat, mid = q.message.chat.id, q.message.id
    if is_url(img):
        if q.message.photo:
            try: await q.message.delete()
            except Exception: pass
            await ui_send(chat, f'<a href="{img}">\u200b</a>\n{caption}', kb, preview=True)
        else:
            await ui_edit(chat, mid, f'<a href="{img}">\u200b</a>\n{caption}', kb, preview=True)
    else:
        if q.message.photo: await raw_edit_media(chat, mid, img, caption, kb)
        else:
            try: await q.message.delete()
            except Exception: pass
            await raw_send_photo(chat, img, caption, kb)

# ------------------------------------------------------- loading animations
LD = {}
async def _ttl(msg, ttl):
    await asyncio.sleep(ttl)
    try: await msg.delete()
    except Exception: pass

async def think(c, m, base="ᴡᴀɪᴛ ᴀ sᴇᴄ", emojis=None, ticks=3, delay=0.4, ttl=10):
    if not m.from_user: return None
    msg = None
    for i in range(1, ticks + 1):
        body = f"<b><i>{base}{'.' * i}</i></b>"
        if emojis: body = f"{emojis[(i - 1) % len(emojis)]} {body}"
        if msg is None: msg = await c.send_message(m.chat.id, body)
        else:
            try: await msg.edit_text(body)
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

async def ship(c, m, text=None, rows=None):
    if m.from_user: await clear_ld(c, m.chat.id, m.from_user.id)
    return await ui_send(m.chat.id, text, rows, reply_id=m.id)

async def qanim(q, base="ᴡᴀɪᴛ ᴀ sᴇᴄ", ticks=3, delay=0.3):
    for i in range(1, ticks + 1):
        body = f"<b><i>{base}{'.' * i}</i></b>"
        try:
            if q.message.media: await q.message.edit_caption(body)
            else: await q.message.edit_text(body)
        except Exception: pass
        await asyncio.sleep(delay)

def apply_default(rows):
    if not DEFAULT_BTN_COLOR or not rows: return rows
    out = []
    for r in rows:
        nr = []
        for b in r:
            b = dict(b)
            if not b.get("s") and not b.get("style"): b["s"] = sty(DEFAULT_BTN_COLOR)
            nr.append(b)
        out.append(nr)
    return out

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
            await m.reply_text("<b>❌ ɴᴏᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ</b>\n\n<blockquote>sᴇɴᴅ /connect ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ, ᴏʀ /connect -100ɪᴅ ʜᴇʀᴇ.</blockquote>")
            return None
    if await is_adm(c, g, u, owner, trust=(m.chat.type not in GRP and not owner)): return g
    await clear_ld(c, m.chat.id, u)
    await m.reply_text("<b>❌ ᴘᴇʀᴍɪssɪᴏɴ ᴅᴇɴɪᴇᴅ</b>\n\n<blockquote>ᴏɴʟʏ ᴛʜᴇ ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ ᴄᴀɴ ᴅᴏ ᴛʜɪs.</blockquote>" if owner else "<b>❌ ᴘᴇʀᴍɪssɪᴏɴ ᴅᴇɴɪᴇᴅ</b>\n\n<blockquote>ᴀᴅᴍɪɴs ᴏɴʟʏ.</blockquote>")
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
    kb = raw_kb(d.get("b"))["inline_keyboard"]
    pl = {"chat_id": chat, "reply_parameters": {"message_id": reply_id, "allow_sending_without_reply": True}}
    if kb: pl["reply_markup"] = {"inline_keyboard": kb}
    if ft:
        pl[ft] = d["fid"]
        if ft not in ("sticker", "video_note") and d.get("t"): pl["caption"], pl["parse_mode"] = d["t"], "HTML"
        method = "send" + "".join(w.title() for w in ft.split("_"))
    else:
        pl.update(text=d.get("t") or ".", parse_mode="HTML", link_preview_options={"is_disabled": True})
        method = "sendMessage"
    return await raw_post(method, pl)

async def send(c, m, d):
    d = dict(d)
    if d.get("b"): d["b"] = apply_default(d["b"])
    try:
        r = await api_send(m.chat.id, m.id, d)
        return r.get("message_id") if isinstance(r, dict) else r
    except Exception as e:
        log.warning("raw send failed (%s) - falling back to pyrogram", e)
    mk = M([[B(b["text"], url=b.get("url")) for b in r] for r in d.get("b") or []]) if d.get("b") else None
    ft, kw = d.get("ft"), dict(chat_id=m.chat.id, reply_to_message_id=m.id, reply_markup=mk)
    for _ in range(2):
        try:
            if ft:
                extra = {} if ft in ("sticker", "video_note") else {"caption": d.get("t")}
                return await getattr(c, "send_" + ft)(**{ft: d["fid"]}, **extra, **kw)
            return await c.send_message(text=d.get("t") or ".", disable_web_page_preview=True, **kw)
        except Exception as e:
            if type(e).__name__ == "FloodWait": await asyncio.sleep(e.value)
            else: return log.warning("send failed %s: %s", m.chat.id, e)

@app.on_message(group=-1)
async def debug_log(c, m):
    log.info("update: chat=%s user=%s text=%r", m.chat.id, m.from_user and m.from_user.id, (m.text or m.caption or "")[:40])

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
            await ui_send(m.chat.id, start_txt(m.from_user), start_kb(0, c.me.username), reply_id=m.id)
        return
    await think(c, m)
    await ship(c, m, HELP_TEXT)

async def sb_show(c, q, txt, rows):
    chat = q.message.chat.id
    if q.message.photo or q.message.media:
        try: await q.message.delete()
        except Exception: pass
        await ui_send(chat, txt, rows)
    else:
        try: await ui_edit(chat, q.message.id, txt, rows)
        except Exception: await ui_send(chat, txt, rows)

@app.on_callback_query(filters.regex(r"^sb:"))
async def sb_cb(c, q):
    p = q.data.split(":"); a = p[1]; chat = q.message.chat.id
    if a == "img":
        if not START_IMGS: return await q.answer("ɴᴏ ɪᴍᴀɢᴇs ᴀᴅᴅᴇᴅ ʏᴇᴛ.", show_alert=True)
        cur = int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else -1
        idx = random.choice([i for i in range(len(START_IMGS)) if i != cur] or [0])
        await q.answer()
        await update_start(q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        return
    if a == "back":
        await qanim(q); await q.answer()
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            try: await q.message.delete()
            except Exception: pass
            await send_start(c, chat, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        else:
            await sb_show(c, q, start_txt(q.from_user), start_kb(0, c.me.username))
        return
    if a == "help":
        await qanim(q); await q.answer()
        return await sb_show(c, q, HELP_TEXT, BACK())
    if a == "about":
        await qanim(q); await q.answer()
        txt = (f"<b>❓ ᴀʙᴏᴜᴛ {smallcaps(BOT_NAME)}</b>\n\n<blockquote>ɪ ᴀᴍ ᴀɴ ᴀᴜᴛᴏ ꜰɪʟᴛᴇʀ ʙᴏᴛ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ ꜰᴇᴀᴛᴜʀᴇs.\n"
               f"ᴍᴀɪɴᴛᴀɪɴᴇᴅ ʙʏ : <a href=\"{MAINTAINER_URL}\">{MAINTAINER}</a></blockquote>")
        return await sb_show(c, q, txt, BACK())
    if a == "set":
        g = (await cn.find_one({"_id": q.from_user.id}) or {}).get("active")
        if not g: return await q.answer("ᴄᴏɴɴᴇᴄᴛ ᴀ ɢʀᴏᴜᴘ ꜰɪʀsᴛ: sᴇɴᴅ /connect ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ.", show_alert=True)
        if not await is_adm(c, g, q.from_user.id, trust=True): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
        await qanim(q); await q.answer()
        cf = await cfg(g)
        return await sb_show(c, q, st_txt(cf), st_kb(g, cf) + BACK())
    if a == "conn":
        await qanim(q); await q.answer()
        rows = await conn_kb(c, q.from_user.id)
        return await sb_show(c, q, conn_txt(bool(rows)), (rows or []) + BACK())

@app.on_message(filters.command("info"))
async def info_cmd(c, m):
    await think(c, m)
    u = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else m.from_user
    if not u: return await ship(c, m, "<b>❌ ɴᴏ ᴜsᴇʀ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ sᴇɴᴅ ɪɴ ᴀ ɢʀᴏᴜᴘ ᴛᴏ sᴇᴇ ɪɴꜰᴏ.</blockquote>")
    text = "<b>👤 ᴜsᴇʀ ɪɴꜰᴏ</b>\n\n<blockquote>"
    text += f"<b>🆔 ɪᴅ:</b> <code>{u.id}</code>\n<b>👤 ꜰɪʀsᴛ ɴᴀᴍᴇ:</b> {html.escape(u.first_name or 'ɴᴏɴᴇ')}\n"
    if u.last_name: text += f"<b>👤 ʟᴀsᴛ ɴᴀᴍᴇ:</b> {html.escape(u.last_name)}\n"
    if u.username: text += f"<b>🔗 ᴜsᴇʀɴᴀᴍᴇ:</b> @{u.username}\n"
    text += f"<b>👑 ᴘʀᴇᴍɪᴜᴍ:</b> {'✅ ʏᴇs' if u.is_premium else '❌ ɴᴏ'}\n<b>🤖 ʙᴏᴛ:</b> {'✅ ʏᴇs' if u.is_bot else '❌ ɴᴏ'}\n"
    text += f"<b>🔗 ᴘʀᴏꜰɪʟᴇ:</b> <a href='tg://user?id={u.id}'>ᴄʟɪᴄᴋ ʜᴇʀᴇ</a></blockquote>"
    await ship(c, m, text)

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
        return await ship(c, m, "<b>❌ ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴡɪᴛʜ <code>/filter keyword</code>\nᴏʀ <code>/filter word text</code>\n\n<b>ᴍᴏᴅᴇs:</b>\n• <code>contains:price</code>\n• <code>regex:^hi(llo)?$</code></blockquote>")
    if mt == "regex":
        try: re.compile(k)
        except re.error as e: return await ship(c, m, f"<b>❌ ɪɴᴠᴀʟɪᴅ ʀᴇɢᴇx</b>\n\n<blockquote>{html.escape(str(e))}</blockquote>")
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
    if not t and not fid: return await ship(c, m, "<b>❌ ᴇᴍᴘᴛʏ ꜰɪʟᴛᴇʀ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ/ᴍᴇᴅɪᴀ ᴏʀ ᴀᴅᴅ sᴏᴍᴇ ᴛᴇxᴛ.</blockquote>")
    if fid and len(t) > 1024: return await ship(c, m, "<b>❌ ᴄᴀᴘᴛɪᴏɴ ᴛᴏᴏ ʟᴏɴɢ</b>\n\n<blockquote>ᴄᴀᴘᴛɪᴏɴ ᴍᴀx ɪs 1024 ᴄʜᴀʀᴀᴄᴛᴇʀs.</blockquote>")
    await fl.update_one({"g": g, "k": k}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "mt": mt}}, upsert=True)
    KW.pop(g, None)
    await ship(c, m, f"<b>✅ ꜰɪʟᴛᴇʀ sᴀᴠᴇᴅ</b>\n\n<blockquote>ᴋᴇʏᴡᴏʀᴅ: <code>{html.escape(k)}</code>" + ("" if mt == "word" else f" ({smallcaps(mt)} ᴍᴀᴛᴄʜ)") + "</blockquote>")

def kh(k): return hashlib.md5(k.encode()).hexdigest()[:10]

async def fl_kb(g, page=0):
    ds = sorted([(d["k"], d.get("mt", "word")) async for d in fl.find({"g": g}, {"k": 1, "mt": 1})])
    ks, pre = len(ds), {"regex": "re: ", "contains": "~ "}
    pages = max(1, -(-ks // 8)); page = max(0, min(page, pages - 1))
    rows = [[R((pre.get(mt, "") + k)[:32], cb=f"fv:{g}:{kh(k)}:{page}"), R("🗑", cb=f"fd:{g}:{kh(k)}:{page}", s=BTN_COLOR["delete"])] for k, mt in ds[page * 8:page * 8 + 8]]
    if pages > 1:
        rows.append([R("◀", cb=f"fp:{g}:{page - 1}"), R(f"{page + 1}/{pages}", cb=f"fp:{g}:{page}"), R("▶", cb=f"fp:{g}:{page + 1}")])
    return (rows if rows else None), ks

def panel_txt(t, n, has):
    if not has: return "<b>📭 ɴᴏ ꜰɪʟᴛᴇʀs</b>\n\n<blockquote>ɴᴏ ꜰɪʟᴛᴇʀs ʏᴇᴛ. ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘᴏsᴛ ᴡɪᴛʜ <code>/filter keyword</code> ᴛᴏ ᴀᴅᴅ ᴏɴᴇ.</blockquote>"
    return f"<b>📋 ꜰɪʟᴛᴇʀs ɪɴ {html.escape(smallcaps(t))}</b> ({n})\n\n<blockquote>ᴛᴀᴘ 🗑 ᴛᴏ ᴅᴇʟᴇᴛᴇ ᴀ ꜰɪʟᴛᴇʀ.</blockquote>"

async def panel(c, m, g):
    kb, n = await fl_kb(g)
    await ship(c, m, panel_txt(await title(c, g), n, bool(kb)), kb)

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
    if not await is_adm(c, g, q.from_user.id, trust=trust): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
    page = int(p[2]) if a == "fp" else int(p[3])
    if a == "fv":
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        return await q.answer(k or "ᴀʟʀᴇᴀᴅʏ ᴅᴇʟᴇᴛᴇᴅ", show_alert=True)
    await qanim(q)
    if a == "fp": await q.answer()
    else:
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        if k: await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
        await q.answer("ᴅᴇʟᴇᴛᴇᴅ 🗑" if k else "ᴀʟʀᴇᴀᴅʏ ᴅᴇʟᴇᴛᴇᴅ")
    kb, n = await fl_kb(g, page)
    try: await ui_edit(q.message.chat.id, q.message.id, panel_txt(await title(c, g), n, bool(kb)), kb)
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
    await ship(c, m, f"<b>✅ ᴅᴇʟᴇᴛᴇᴅ</b>\n\n<blockquote>ꜰɪʟᴛᴇʀ <code>{k}</code> ᴅᴇʟᴇᴛᴇᴅ.</blockquote>" if r.deleted_count else "<b>❌ ɴᴏᴛ ꜰᴏᴜɴᴅ</b>\n\n<blockquote>ɴᴏ sᴜᴄʜ ꜰɪʟᴛᴇʀ. sᴇɴᴅ /filters ᴀɴᴅ ᴛᴀᴘ 🗑 ɪɴsᴛᴇᴀᴅ.</blockquote>")

@app.on_message(filters.command("delall"))
async def delall(c, m):
    g = await target(c, m, owner=True)
    if g is None: return
    await think(c, m)
    await ship(c, m, "<b>⚠️ ᴡᴀʀɴɪɴɢ</b>\n\n<blockquote>ᴅᴇʟᴇᴛᴇ <b>ᴀʟʟ</b> ꜰɪʟᴛᴇʀs?</blockquote>",
               [[R("ʏᴇs", cb=f"da:y:{g}", s="danger"), R("ᴄᴀɴᴄᴇʟ", cb="da:n:0")]])

@app.on_callback_query(filters.regex(r"^da:"))
async def delall_cb(c, q):
    _, a, g = q.data.split(":"); g = int(g)
    if a != "y":
        await qanim(q)
        return await ui_edit(q.message.chat.id, q.message.id, "<b>❌ ᴄᴀɴᴄᴇʟʟᴇᴅ</b>")
    if not await is_adm(c, g, q.from_user.id, True): return await q.answer("ᴏᴡɴᴇʀ ᴏɴʟʏ.", show_alert=True)
    await qanim(q)
    r = await fl.delete_many({"g": g}); KW.pop(g, None)
    await ui_edit(q.message.chat.id, q.message.id, f"<b>✅ sᴜᴄᴄᴇss</b>\n\n<blockquote>ᴅᴇʟᴇᴛᴇᴅ {r.deleted_count} ꜰɪʟᴛᴇʀ(s).</blockquote>")

@app.on_message(filters.command("color"))
async def color(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    p = (m.text.html or "").split(None, 1)
    k, rest = kwsplit(p[1] if len(p) > 1 else "")
    cols = rest.lower().replace(",", " ").split()
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if not k or not cols: return await ship(c, m, '<b>❌ ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n<blockquote>ᴜsᴀɢᴇ: /color ᴋᴇʏᴡᴏʀᴅ ʀᴇᴅ ɢʀᴇᴇɴ ʙʟᴜᴇ ɴᴏɴᴇ\n(ᴏɴᴇ ᴄᴏʟᴏᴜʀ ᴘᴇʀ ʙᴜᴛᴛᴏɴ, ɪɴ ᴏʀᴅᴇʀ)</blockquote>')
    if not d or not d.get("b"): return await ship(c, m, "<b>❌ ɴᴏ ʙᴜᴛᴛᴏɴs</b>\n\n<blockquote>ɴᴏ sᴜᴄʜ ꜰɪʟᴛᴇʀ, ᴏʀ ɪᴛ ʜᴀs ɴᴏ ʙᴜᴛᴛᴏɴs.</blockquote>")
    i = 0
    for row in d["b"]:
        for b in row:
            col = cols[i] if i < len(cols) else None
            i += 1
            if col in STY: b["s"] = STY[col]
            else: b.pop("s", None)
    await fl.update_one({"g": g, "k": k}, {"$set": {"b": d["b"]}})
    await ship(c, m, f"<b>✅ ᴜᴘᴅᴀᴛᴇᴅ</b>\n\n<blockquote>ʙᴜᴛᴛᴏɴ ᴄᴏʟᴏᴜʀs ᴜᴘᴅᴀᴛᴇᴅ ꜰᴏʀ <code>{k}</code>.</blockquote>")

@app.on_message(filters.command("connect"))
async def connect(c, m):
    if not m.from_user: return
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        if len(m.command) < 2:
            return await m.reply_text("<b>❌ ʜᴏᴡ ᴛᴏ ᴄᴏɴɴᴇᴄᴛ</b>\n\n<blockquote>ʙᴇsᴛ ᴡᴀʏ: sᴇɴᴅ /connect <b>ɪɴsɪᴅᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ</b>.\nᴏʀ ʜᴇʀᴇ: <code>/connect -100xxxxxxxxxx</code></blockquote>")
        try: g = int(m.command[1])
        except ValueError: return await m.reply_text("<b>❌ ɪɴᴠᴀʟɪᴅ ɪᴅ</b>\n\n<blockquote>ᴛʜᴇ ɢʀᴏᴜᴘ ɪᴅ ᴍᴜsᴛ ʙᴇ ᴀ ɴᴜᴍʙᴇʀ ʟɪᴋᴇ <code>-1001234567890</code>.</blockquote>")
        try: await c.get_chat(g)
        except Exception as e:
            return await m.reply_text(f"<b>❌ ᴜɴʀᴇᴀᴄʜᴀʙʟᴇ</b>\n\n<blockquote>ɪ ᴄᴀɴ'ᴛ ʀᴇᴀᴄʜ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ({type(e).__name__}).\nᴍᴀᴋᴇ sᴜʀᴇ ɪ'ᴍ ᴀᴅᴅᴇᴅ ᴛᴏ ɪᴛ, ᴛʜᴇɴ sᴇɴᴅ /connect <b>ɪɴsɪᴅᴇ ᴛʜᴇ ɢʀᴏᴜᴘ</b> ɪɴsᴛᴇᴀᴅ.</blockquote>")
    if not await is_adm(c, g, u): return await m.reply_text("<b>❌ ɴᴏᴛ ᴀᴅᴍɪɴ</b>\n\n<blockquote>ʏᴏᴜ ᴍᴜsᴛ ʙᴇ ᴀɴ ᴀᴅᴍɪɴ ᴏꜰ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ᴛᴏ ᴄᴏɴɴᴇᴄᴛ ɪᴛ.</blockquote>")
    await think(c, m)
    await conn_set(u, g)
    await ship(c, m, f"<b>✅ ᴄᴏɴɴᴇᴄᴛᴇᴅ</b>\n\n<blockquote>ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ <b>{html.escape(smallcaps(await title(c, g)))}</b>. ᴍᴀɴᴀɢᴇ ꜰɪʟᴛᴇʀs ꜰʀᴏᴍ ᴍʏ ᴘᴍ.</blockquote>")

@app.on_message(filters.command("disconnect"))
async def disconnect(c, m):
    if not m.from_user: return
    await think(c, m)
    g = m.chat.id if m.chat.type in GRP else (await cn.find_one({"_id": m.from_user.id}) or {}).get("active")
    if g: await conn_set(m.from_user.id, g, False)
    await ship(c, m, "<b>✅ ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ</b>" if g else "<b>❌ ɴᴏᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ</b>")

async def conn_kb(c, u):
    d = await cn.find_one({"_id": u}) or {}
    rows = [[R(("✅ " if g == d.get("active") else "") + smallcaps(await title(c, g)), cb=f"cn:s:{g}"), R("🗑", cb=f"cn:d:{g}", s=BTN_COLOR["delete"])] for g in d.get("groups", [])]
    return rows or None

def conn_txt(has):
    return "<b>🔗 ʏᴏᴜʀ ɢʀᴏᴜᴘs</b>\n\n<blockquote>ᴛᴀᴘ ᴛᴏ sᴇʟᴇᴄᴛ, 🗑 ᴛᴏ ʀᴇᴍᴏᴠᴇ.</blockquote>" if has else "<b>📭 ɴᴏ ᴄᴏɴɴᴇᴄᴛɪᴏɴs</b>\n\n<blockquote>sᴇɴᴅ /connect ɪɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ ꜰɪʀsᴛ.</blockquote>"

@app.on_message(filters.command("connections") & filters.private)
async def conns(c, m):
    await think(c, m)
    kb = await conn_kb(c, m.from_user.id)
    await ship(c, m, conn_txt(bool(kb)), kb)

@app.on_callback_query(filters.regex(r"^cn:"))
async def conn_cb(c, q):
    _, a, g = q.data.split(":"); g, u = int(g), q.from_user.id
    await qanim(q)
    if a == "s": await cn.update_one({"_id": u}, {"$set": {"active": g}})
    else: await conn_set(u, g, False)
    await q.answer("ᴅᴏɴᴇ")
    kb = await conn_kb(c, u)
    try: await ui_edit(q.message.chat.id, q.message.id, conn_txt(bool(kb)), kb)
    except Exception: pass

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
    return ("<b>⚙️ ɢʀᴏᴜᴘ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ</b>\n\n"
            "<blockquote>ᴀᴅᴍɪɴs &amp; ᴛʜᴇ ᴏᴡɴᴇʀ ᴀʀᴇ ᴀʟᴡᴀʏs ᴀʟʟᴏᴡᴇᴅ.\nᴛᴜʀɴ ᴏɴ 🧪 <b>ᴛᴇsᴛ ᴍᴏᴅᴇ</b> ᴛᴏ ᴛᴇsᴛ ᴡɪᴛʜ ʏᴏᴜʀ ᴏᴡɴ ᴀᴄᴄᴏᴜɴᴛ.\n\n"
            "• <b>ᴀɴᴛɪ-ʟɪɴᴋ</b>: ᴅᴇʟᴇᴛᴇs ʟɪɴᴋs (ᴇxᴄᴇᴘᴛ ᴀʟʟᴏᴡᴇᴅ) ᴀɴᴅ ᴘᴍs ᴛʜᴇ ᴏᴡɴᴇʀ.\n"
            "• <b>ᴀɴᴛɪ-ꜰᴏʀᴡᴀʀᴅ</b> / <b>ᴀɴᴛɪ-ᴍᴇɴᴛɪᴏɴ</b>: ᴅᴇʟᴇᴛᴇ ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴍᴇssᴀɢᴇs / @ᴍᴇɴᴛɪᴏɴs.\n"
            f"• <b>ꜰʟᴏᴏᴅ</b>: {cf.get('fn', 6)} ᴍᴇssᴀɢᴇs ɪɴ {cf.get('ft', 5)}s = 10 ᴍɪɴ ᴍᴜᴛᴇ.\n"
            "• <b>ᴀᴜᴛᴏ-ᴡᴀʀɴ</b>: ᴇᴀᴄʜ ᴅᴇʟᴇᴛᴇᴅ ᴍᴇssᴀɢᴇ ᴀᴅᴅs ᴀ ᴡᴀʀɴɪɴɢ; ᴀᴛ ᴛʜᴇ ʟɪᴍɪᴛ ᴛʜᴇ ᴜsᴇʀ ɪs ᴘᴜɴɪsʜᴇᴅ.</blockquote>"
            "\n<i>ʙᴏᴛ ɴᴇᴇᴅs ᴀᴅᴍɪɴ ʀɪɢʜᴛs: ᴅᴇʟᴇᴛᴇ ᴍᴇssᴀɢᴇs + ʀᴇsᴛʀɪᴄᴛ ᴍᴇᴍʙᴇʀs.</i>")

def st_kb(g, cf):
    b = lambda lb, k: R(f"{lb} {'✅' if cf.get(k) else '⛔'}", cb=f"st:{g}:{k}")
    return [[b("🔗 ᴀɴᴛɪ-ʟɪɴᴋ", "al"), b("↪️ ᴀɴᴛɪ-ꜰᴏʀᴡᴀʀᴅ", "af")],
            [b("@ ᴀɴᴛɪ-ᴍᴇɴᴛɪᴏɴ", "am"), b("🌊 ꜰʟᴏᴏᴅ", "fd")],
            [b("⚠️ ᴀᴜᴛᴏ-ᴡᴀʀɴ", "aw"), b("🧪 ᴛᴇsᴛ ᴍᴏᴅᴇ", "tm")],
            [R("➖", cb=f"st:{g}:w-"), R(f"ᴡᴀʀɴ ʟɪᴍɪᴛ: {cf.get('wl', 3)}", cb=f"st:{g}:w0"), R("➕", cb=f"st:{g}:w+")],
            [R(f"ᴡᴀʀɴ ᴀᴄᴛɪᴏɴ: {smallcaps(cf.get('wa', 'mute'))}", cb=f"st:{g}:wa")]]

@app.on_message(filters.command(["settings", "antilink"]))
async def settings_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    if m.command[0] == "antilink" and m.command[1:2] in (["on"], ["off"]): await cfg_set(g, al=m.command[1] == "on")
    await think(c, m)
    cf = await cfg(g)
    await ship(c, m, st_txt(cf), st_kb(g, cf))

@app.on_callback_query(filters.regex(r"^st:"))
async def st_cb(c, q):
    _, g, k = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if k in ("al", "af", "am", "fd", "aw", "tm"): await cfg_set(g, **{k: not cf.get(k)})
    elif k in ("w+", "w-"): await cfg_set(g, wl=max(2, min(10, cf.get("wl", 3) + (1 if k == "w+" else -1))))
    elif k == "wa": await cfg_set(g, wa={"mute": "kick", "kick": "ban"}.get(cf.get("wa", "mute"), "mute"))
    await q.answer()
    try: await ui_edit(q.message.chat.id, q.message.id, st_txt(cf), st_kb(g, cf))
    except Exception: pass

@app.on_message(filters.command("setflood"))
async def setflood(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    try:
        n, t = int(m.command[1]), int(m.command[2]); assert 2 <= n <= 30 and 1 <= t <= 60
    except Exception: return await ship(c, m, "<b>❌ ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n<blockquote>ᴜsᴀɢᴇ: /setflood ᴍᴇssᴀɢᴇs sᴇᴄᴏɴᴅs\n(ᴇ.ɢ. <code>/setflood 6 5</code>)</blockquote>")
    await cfg_set(g, fn=n, ft=t)
    await ship(c, m, f"<b>✅ ᴜᴘᴅᴀᴛᴇᴅ</b>\n\n<blockquote>ꜰʟᴏᴏᴅ ʟɪᴍɪᴛ: {n} ᴍᴇssᴀɢᴇs ɪɴ {t} sᴇᴄᴏɴᴅs.</blockquote>")

async def tmp(c, g, text, secs=60, rows=None):
    try:
        mid = await ui_send(g, text, rows)
        await asyncio.sleep(secs)
        try: await c.delete_messages(g, mid)
        except Exception: pass
    except Exception: pass

async def add_warn(c, g, u, why):
    cf = await cfg(g); lim = cf.get("wl", 3)
    r = await wn.find_one_and_update({"g": g, "u": u.id}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    n = r["n"]
    if n < lim:
        return f"<b>⚠️ ᴡᴀʀɴɪɴɢ ᴀᴅᴅᴇᴅ</b>\n\n<blockquote>{u.mention} ᴡᴀʀɴᴇᴅ <b>({n}/{lim})</b>\nʀᴇᴀsᴏɴ: {html.escape(why)}</blockquote>", [[R("🗑 ʀᴇᴍᴏᴠᴇ ᴡᴀʀɴ", cb=f"uw:{g}:{u.id}")]]
    await wn.delete_one({"g": g, "u": u.id})
    act = cf.get("wa", "mute")
    try:
        if act == "mute": await c.restrict_chat_member(g, u.id, ChatPermissions())
        else:
            await c.ban_chat_member(g, u.id)
            if act == "kick": await c.unban_chat_member(g, u.id)
        done = {"mute": "ᴍᴜᴛᴇᴅ", "kick": "ᴋɪᴄᴋᴇᴅ", "ban": "ʙᴀɴɴᴇᴅ"}[act]
    except Exception as e:
        log.warning("warn action failed in %s: %s", g, e); done = "ɴᴏᴛ ᴘᴜɴɪsʜᴇᴅ (ɪ ɴᴇᴇᴅ ᴛʜᴇ 'ʀᴇsᴛʀɪᴄᴛ ᴍᴇᴍʙᴇʀs' ᴀᴅᴍɪɴ ʀɪɢʜᴛ)"
    return f"<b>🚫 ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ</b>\n\n<blockquote>{u.mention} ʀᴇᴀᴄʜᴇᴅ {lim} ᴡᴀʀɴɪɴɢs ᴀɴᴅ ᴡᴀs {done}.\nʀᴇᴀsᴏɴ: {html.escape(why)}</blockquote>", [[R("↩️ ᴜɴᴅᴏ", cb=f"ub:{g}:{u.id}")]]

async def tgt_user(c, m):
    if m.chat.type not in GRP or not m.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id if m.from_user else 0)
        await m.reply_text("<b>❌ ɢʀᴏᴜᴘ ᴏɴʟʏ</b>\n\n<blockquote>ᴜsᴇ ᴛʜɪs ɪɴ ᴀ ɢʀᴏᴜᴘ, ᴀs ᴀ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ's ᴍᴇssᴀɢᴇ.</blockquote>"); return None, None
    if not await is_adm(c, m.chat.id, m.from_user.id):
        await clear_ld(c, m.chat.id, m.from_user.id)
        await m.reply_text("<b>❌ ᴀᴅᴍɪɴs ᴏɴʟʏ</b>"); return None, None
    r = m.reply_to_message
    if not r or not r.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id)
        await m.reply_text("<b>❌ ʀᴇᴘʟʏ ᴍɪssɪɴɢ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ's ᴍᴇssᴀɢᴇ.</blockquote>"); return None, None
    return m.chat.id, r.from_user

@app.on_message(filters.command("warn"))
async def warn_cmd(c, m):
    g, u = await tgt_user(c, m)
    if not g: return
    if await is_adm(c, g, u.id): return await ship(c, m, "<b>❌ ᴄᴀɴɴᴏᴛ ᴡᴀʀɴ</b>\n\n<blockquote>ɪ ᴄᴀɴ'ᴛ ᴡᴀʀɴ ᴀᴅᴍɪɴs.</blockquote>")
    await think(c, m)
    txt, kb = await add_warn(c, g, u, " ".join(m.command[1:]) or "ɴᴏ ʀᴇᴀsᴏɴ ɢɪᴠᴇɴ")
    await ship(c, m, txt, kb)

@app.on_message(filters.command(["unwarn", "resetwarns"]))
async def unwarn_cmd(c, m):
    g, u = await tgt_user(c, m)
    if not g: return
    await think(c, m)
    if m.command[0] == "resetwarns": await wn.delete_one({"g": g, "u": u.id}); left = 0
    else:
        r = await wn.find_one_and_update({"g": g, "u": u.id, "n": {"$gt": 0}}, {"$inc": {"n": -1}}, return_document=ReturnDocument.AFTER)
        left = r["n"] if r else 0
    await ship(c, m, f"<b>✅ ᴜᴘᴅᴀᴛᴇᴅ</b>\n\n<blockquote>{u.mention} ɴᴏᴡ ʜᴀs {left} ᴡᴀʀɴɪɴɢ(s).</blockquote>")

@app.on_message(filters.command("warns") & filters.group)
async def warns_cmd(c, m):
    await think(c, m)
    r = m.reply_to_message
    u = r.from_user if r and r.from_user else m.from_user
    if not u: return
    d = await wn.find_one({"g": m.chat.id, "u": u.id})
    await ship(c, m, f"<b>⚠️ ᴡᴀʀɴɪɴɢs</b>\n\n<blockquote>{u.mention}: <b>{(d or {}).get('n', 0)}/{(await cfg(m.chat.id)).get('wl', 3)}</b> ᴡᴀʀɴɪɴɢs.</blockquote>")

@app.on_message(filters.command(["unmute", "unban"]) & filters.group)
async def unrestrict(c, m):
    if not m.from_user or not await is_adm(c, m.chat.id, m.from_user.id): return
    await think(c, m)
    r = m.reply_to_message
    try: uid = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await ship(c, m, "<b>❌ ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴜsᴇʀ, ᴏʀ: <code>/unmute user_id</code></blockquote>")
    try: await c.unban_chat_member(m.chat.id, uid); await ship(c, m, "<b>✅ ᴅᴏɴᴇ</b>\n\n<blockquote>ʀᴇsᴛʀɪᴄᴛɪᴏɴs ʀᴇᴍᴏᴠᴇᴅ.</blockquote>")
    except Exception as e: await ship(c, m, f"<b>❌ ꜰᴀɪʟᴇᴅ</b>\n\n<blockquote>{type(e).__name__} (ɪ ɴᴇᴇᴅ ᴛʜᴇ 'ʀᴇsᴛʀɪᴄᴛ ᴍᴇᴍʙᴇʀs' ʀɪɢʜᴛ)</blockquote>")

@app.on_callback_query(filters.regex(r"^u[wb]:"))
async def undo_cb(c, q):
    a, g, u = q.data.split(":"); g, u = int(g), int(u)
    if not await is_adm(c, g, q.from_user.id): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
    await qanim(q)
    if a == "uw":
        r = await wn.find_one_and_update({"g": g, "u": u, "n": {"$gt": 0}}, {"$inc": {"n": -1}}, return_document=ReturnDocument.AFTER)
        msg = f"<b>✅ ʀᴇᴍᴏᴠᴇᴅ</b>\n\n<blockquote>ᴏɴᴇ ᴡᴀʀɴɪɴɢ ʀᴇᴍᴏᴠᴇᴅ ({r['n'] if r else 0} ʟᴇꜰᴛ).</blockquote>"
    else:
        try: await c.unban_chat_member(g, u)
        except Exception: pass
        msg = "<b>↩️ ᴜɴᴅᴏɴᴇ</b>\n\n<blockquote>ᴘᴜɴɪsʜᴍᴇɴᴛ ᴜɴᴅᴏɴᴇ.</blockquote>"
    await q.answer("ᴅᴏɴᴇ")
    try: await ui_edit(q.message.chat.id, q.message.id, msg)
    except Exception: pass

def norm(u): return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.strip().lower()).rstrip("/")

def allowed(link, wl):
    n = norm(link); host = re.split(r"[/?#]", n)[0]
    return any(n == a or n.startswith(a + "/") or ("/" not in a and (host == a or host.endswith("." + a))) for a in wl)

def dom_view(g, cf):
    ds = cf.get("dom", [])
    if not ds:
        return ("<b>🔗 ᴀʟʟᴏᴡᴇᴅ ʟɪɴᴋs</b>\n\n<blockquote>ɴᴏ ᴀʟʟᴏᴡᴇᴅ ʟɪɴᴋs ʏᴇᴛ.\nᴀᴅᴅ: <code>/allowlink domain.com</code> ᴏʀ <code>/allowlink t.me/yourchannel</code></blockquote>"), None
    return ("<b>🔗 ᴀʟʟᴏᴡᴇᴅ ʟɪɴᴋs</b>\n\n<blockquote>ᴀɴᴛɪ-ʟɪɴᴋ ɪɢɴᴏʀᴇs ᴛʜᴇsᴇ ᴅᴏᴍᴀɪɴs.\nᴀᴅᴅ ᴍᴏʀᴇ: <code>/allowlink domain.com</code></blockquote>",
            [[R(d[:40], cb=f"dn:{g}:{kh(d)}"), R("🗑", cb=f"dd:{g}:{kh(d)}", s=BTN_COLOR["delete"])] for d in ds])

@app.on_message(filters.command(["allowlink", "allowlinks"]))
async def allowlink(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    new = [norm(x) for x in m.command[1:] if "." in x]
    cf = await cfg(g)
    if new: await cfg_set(g, dom=list(dict.fromkeys(cf.get("dom", []) + new)))
    txt, kb = dom_view(g, cf)
    await ship(c, m, txt, kb)

@app.on_callback_query(filters.regex(r"^d[dn]:"))
async def dom_cb(c, q):
    a, g, h = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if a == "dd": await cfg_set(g, dom=[d for d in cf.get("dom", []) if kh(d) != h])
    await q.answer("ʀᴇᴍᴏᴠᴇᴅ" if a == "dd" else None)
    txt, kb = dom_view(g, cf)
    try: await ui_edit(q.message.chat.id, q.message.id, txt, kb)
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
    text = (f"🚨 <b>ʟɪɴᴋ ᴅᴇʟᴇᴛᴇᴅ</b>\n<b>ɢʀᴏᴜᴘ:</b> {html.escape(await title(c, g))} (<code>{g}</code>)\n"
            f"<b>ᴜsᴇʀ:</b> {u.mention}\n<b>ɪᴅ:</b> <code>{u.id}</code>\n"
            f"<b>ᴜsᴇʀɴᴀᴍᴇ:</b> {'@' + u.username if u.username else 'ɴᴏɴᴇ'}\n<b>ʟɪɴᴋ(s):</b>\n{ls}")
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
    note, kb = f"<b>⚠️ ᴅᴇʟᴇᴛᴇᴅ</b>\n\n<blockquote>{m.from_user.mention}, {why}. ʏᴏᴜʀ ᴍᴇssᴀɢᴇ ᴡᴀs ᴅᴇʟᴇᴛᴇᴅ.</blockquote>", None
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
            asyncio.create_task(tmp(c, g, f"<b>🌊 ꜰʟᴏᴏᴅ ᴍᴜᴛᴇ</b>\n\n<blockquote>{u.mention} ᴡᴀs ᴍᴜᴛᴇᴅ ꜰᴏʀ 10 ᴍɪɴᴜᴛᴇs ꜰᴏʀ ꜰʟᴏᴏᴅɪɴɢ.</blockquote>", 60, [[R("↩️ ᴜɴᴅᴏ", cb=f"ub:{g}:{u.id}")]]))
            if cf.get("aw"):
                wt, wk = await add_warn(c, g, u, "ꜰʟᴏᴏᴅɪɴɢ"); asyncio.create_task(tmp(c, g, wt, 60, wk))
            m.stop_propagation()
    fwd = getattr(m, "forward_date", None) or getattr(m, "forward_from_chat", None) or getattr(m, "forward_sender_name", None)
    if cf.get("af") and fwd and await punish(c, g, m, "ꜰᴏʀᴡᴀʀᴅᴇᴅ ᴍᴇssᴀɢᴇs ᴀʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ʜᴇʀᴇ"): m.stop_propagation()
    if cf.get("am") and any(e.type == E.MENTION for e in ents) and await punish(c, g, m, "@ᴍᴇɴᴛɪᴏɴs ᴀʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ʜᴇʀᴇ"): m.stop_propagation()
    if cf.get("al") and any(e.type in (E.URL, E.TEXT_LINK) for e in ents):
        bad = [x for x in links_of(m) if not allowed(x, cf.get("dom", []))]
        if bad and await punish(c, g, m, "ʟɪɴᴋs ᴀʀᴇ ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ ʜᴇʀᴇ"):
            asyncio.create_task(report(c, g, m, bad)); m.stop_propagation()

@app.on_message(filters.group & (filters.new_chat_members | filters.left_chat_member), group=10)
async def del_service(c, m):
    try: await m.delete()
    except Exception: pass

WEL_VARS = "{first} {last} {fullname} {mention} {username} {id} {group} {count}"
WS = [0, 60, 300, 600]
def fmt_s(s): return "ᴏꜰꜰ" if not s else f"{s // 60}ᴍ"

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
    t = "<b>👋 ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ</b>\n\n"
    if has:
        t += f"<blockquote>sᴛᴀᴛᴜs: {'✅ ᴏɴ' if w.get('on') else '⛔ ᴏꜰꜰ'} | ᴛʏᴘᴇ: {smallcaps(w.get('ft') or 'text')}\nʙᴜᴛᴛᴏɴs: {sum(len(r) for r in w.get('b') or [])} | ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ: {fmt_s(w.get('ad', 0))}</blockquote>\n"
    else:
        t += "<blockquote>ɴᴏ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ sᴇᴛ ʏᴇᴛ.</blockquote>\n"
    t += ("\n<blockquote>ᴛᴀᴘ <b>✏️ sᴇᴛ / ᴄʜᴀɴɢᴇ</b>, ᴛʜᴇɴ sᴇɴᴅ ᴍᴇ ᴛʜᴇ ᴡᴇʟᴄᴏᴍᴇ.\n"
          f"ᴠᴀʀɪᴀʙʟᴇs: <code>{WEL_VARS}</code>\nʙᴜᴛᴛᴏɴs: <code>[Join](buttonurl:https://t.me/x)</code> (ᴀᴅᴅ <code>:same</code> <code>:red</code> <code>:green</code> <code>:blue</code>)</blockquote>")
    rows = [[R("✏️ sᴇᴛ / ᴄʜᴀɴɢᴇ", cb=f"wl:{g}:set", s=BTN_COLOR["set"])]]
    if has:
        rows += [[R("👁 ᴘʀᴇᴠɪᴇᴡ", cb=f"wl:{g}:prev"), R("🔛 ᴏɴ / ᴏꜰꜰ", cb=f"wl:{g}:tog")],
                 [R(f"⏱ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ: {fmt_s(w.get('ad', 0))}", cb=f"wl:{g}:ad"), R("🗑 ʀᴇᴍᴏᴠᴇ", cb=f"wl:{g}:rm", s=BTN_COLOR["delete"])]]
    return t, rows

@app.on_message(filters.command("setwel"))
async def setwel(c, m):
    if m.chat.type in GRP:
        await think(c, m)
        return await ship(c, m, "<b>👋 ᴡᴇʟᴄᴏᴍᴇ ᴇᴅɪᴛᴏʀ</b>\n\n<blockquote>sᴇᴛ ᴛʜᴇ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ ɪɴ ᴍʏ ᴘᴍ 👇</blockquote>",
                          [[R("ᴏᴘᴇɴ ᴘᴍ", url=f"https://t.me/{c.me.username}?start=setwel", s="primary")]])
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    txt, rows = await wel_view(c, g)
    await ship(c, m, txt, rows)

@app.on_callback_query(filters.regex(r"^wl:"))
async def wel_cb(c, q):
    _, g, a = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer("ᴀᴅᴍɪɴs ᴏɴʟʏ.", show_alert=True)
    w = await wc.find_one({"_id": g}) or {}
    if a == "set":
        await qanim(q)
        PEND[q.from_user.id] = g
        await q.answer()
        await q.message.reply_text("<b>✏️ ᴇᴅɪᴛɪɴɢ</b>\n\n<blockquote>sᴇɴᴅ ᴍᴇ ᴛʜᴇ ɴᴇᴡ ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ ɴᴏᴡ - ᴛᴇxᴛ, ᴏʀ ᴀ ᴘʜᴏᴛᴏ/ᴠɪᴅᴇᴏ/ɢɪꜰ ᴡɪᴛʜ ᴀ ᴄᴀᴘᴛɪᴏɴ.\nʙᴜᴛᴛᴏɴs: <code>[Name](buttonurl:https://...)</code>\nsᴇɴᴅ /cancel ᴛᴏ sᴛᴏᴘ.</blockquote>")
        txt, rows = await wel_view(c, g)
        try: await ui_edit(q.message.chat.id, q.message.id, txt, rows)
        except Exception: pass
        return
    if a == "prev":
        await qanim(q)
        d = {"t": await wel_text(c, w.get("t"), [q.from_user], SimpleNamespace(title=await title(c, g), id=g)),
             "ft": w.get("ft"), "fid": w.get("fid"), "b": w.get("b")}
        await q.answer()
        await send(c, q.message, d)
        txt, rows = await wel_view(c, g)
        try: await ui_edit(q.message.chat.id, q.message.id, txt, rows)
        except Exception: pass
        return
    await qanim(q)
    if a == "tog": await wc.update_one({"_id": g}, {"$set": {"on": not w.get("on")}}, upsert=True)
    elif a == "rm": await wc.delete_one({"_id": g})
    elif a == "ad":
        cur = w.get("ad", 0)
        await wc.update_one({"_id": g}, {"$set": {"ad": WS[(WS.index(cur) + 1) % len(WS)] if cur in WS else 0}}, upsert=True)
    await q.answer()
    txt, rows = await wel_view(c, g)
    try: await ui_edit(q.message.chat.id, q.message.id, txt, rows)
    except Exception: pass

async def _pend(_, __, m): return bool(m.from_user) and m.from_user.id in PEND
pend = filters.create(_pend)

@app.on_message(filters.command("cancel") & filters.private)
async def cancel(c, m):
    await think(c, m, ticks=2, delay=0.3)
    PEND.pop(m.from_user.id, None)
    await ship(c, m, "<b>❌ ᴄᴀɴᴄᴇʟʟᴇᴅ</b>")

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
    if not t and not fid: PEND[uid] = g; return await m.reply_text("<b>❌ ɪɴᴠᴀʟɪᴅ ɪɴᴘᴜᴛ</b>\n\n<blockquote>sᴇɴᴅ ᴛᴇxᴛ, ᴏʀ ᴍᴇᴅɪᴀ ᴡɪᴛʜ ᴀ ᴄᴀᴘᴛɪᴏɴ.</blockquote>")
    if fid and ft not in ("sticker", "video_note") and len(t) > 1024: PEND[uid] = g; return await m.reply_text("<b>❌ ᴛᴏᴏ ʟᴏɴɢ</b>\n\n<blockquote>ᴄᴀᴘᴛɪᴏɴ ᴍᴀx ɪs 1024 ᴄʜᴀʀᴀᴄᴛᴇʀs - sᴇɴᴅ ᴀ sʜᴏʀᴛᴇʀ ᴏɴᴇ.</blockquote>")
    await wc.update_one({"_id": g}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "on": True}}, upsert=True)
    txt, rows = await wel_view(c, g)
    await m.reply_text("<b>✅ sᴀᴠᴇᴅ</b>\n\n<blockquote>ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ sᴀᴠᴇᴅ!</blockquote>")
    await ui_send(m.chat.id, txt, rows, reply_id=m.id)
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
    if r and w.get("ad"): asyncio.create_task(later(c, g, r if isinstance(r, int) else getattr(r, "id", r), w["ad"]))

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
        except Exception as e:
            if type(e).__name__ == "FloodWait": await asyncio.sleep(e.value + 1); continue
            if type(e).__name__ in ("ChannelPrivate", "ChatWriteForbidden", "UserNotParticipant"): await ch.delete_one({"_id": cid}); return False
            return False
    return False

@app.on_message(filters.command("broadcast"))
async def broadcast(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    if not m.reply_to_message: return await m.reply_text("<b>❌ ʀᴇᴘʟʏ ᴍɪssɪɴɢ</b>\n\n<blockquote>ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴍᴇssᴀɢᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ʙʀᴏᴀᴅᴄᴀsᴛ ᴡɪᴛʜ /broadcast</blockquote>")
    s = await think(c, m, "ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ", ttl=3600)
    ids = [d["_id"] async for d in ch.find()]
    ok = 0
    for i in ids:
        ok += await bsend(m.reply_to_message, i)
        await asyncio.sleep(0.3)
    LD.pop((m.chat.id, m.from_user.id), None)
    if s:
        try: await ui_edit(m.chat.id, s.id, f"<b>✅ ᴅᴏɴᴇ</b>\n\n<blockquote>ʙʀᴏᴀᴅᴄᴀsᴛ ᴅᴏɴᴇ: ✅ {ok} sᴇɴᴛ, ❌ {len(ids) - ok} ꜰᴀɪʟᴇᴅ.</blockquote>")
        except Exception: pass

@app.on_message(filters.command("stats"))
async def stats(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    g, h = await ch.count_documents({"type": "group"}), await ch.count_documents({"type": "channel"})
    await ship(c, m, f"<b>📊 ʙᴏᴛ sᴛᴀᴛɪsᴛɪᴄs</b>\n\n<blockquote>ɢʀᴏᴜᴘs: {g}\nᴄʜᴀɴɴᴇʟs: {h}\nꜰɪʟᴛᴇʀs: {await fl.count_documents({})}\nᴀᴅᴍɪɴs: {len(ADMINS)}</blockquote>")

@app.on_message(filters.command(["addadmin", "rmadmin"]))
async def admin_cmd(c, m):
    if not m.from_user or m.from_user.id != OWNER: return
    r = m.reply_to_message
    try: u = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await m.reply_text("<b>❌ ᴜsᴀɢᴇ ᴇʀʀᴏʀ</b>\n\n<blockquote>ᴜsᴀɢᴇ: /addadmin ᴜsᴇʀ_ɪᴅ (ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ)</blockquote>")
    await think(c, m, ticks=2, delay=0.3)
    add = m.command[0] == "addadmin"
    if add: await ad.update_one({"_id": u}, {"$set": {"a": 1}}, upsert=True); ADMINS.add(u)
    else: await ad.delete_one({"_id": u}); ADMINS.discard(u)
    await ship(c, m, f"<b>✅ ᴜᴘᴅᴀᴛᴇᴅ</b>\n\n<blockquote>{'ᴀᴅᴅᴇᴅ' if add else 'ʀᴇᴍᴏᴠᴇᴅ'} ᴀᴅᴍɪɴ <code>{u}</code></blockquote>")

@app.on_message(filters.command("admins"))
async def admins(c, m):
    if m.from_user and sudo(m.from_user.id):
        await think(c, m, ticks=2, delay=0.3)
        await ship(c, m, f"<b>👑 ᴀᴅᴍɪɴ ʟɪsᴛ</b>\n\n<blockquote>ᴏᴡɴᴇʀ: <code>{OWNER}</code>\nᴀᴅᴍɪɴs: " + (", ".join(f"<code>{a}</code>" for a in ADMINS) or "ɴᴏɴᴇ") + "</blockquote>")

async def web_start():
    a = web.Application()
    for p in ("/", "/health"): a.router.add_get(p, lambda r: web.Response(text="ok"))
    r = web.AppRunner(a); await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()

async def ping():
    u = os.environ.get("RENDER_EXTERNAL_URL")
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
