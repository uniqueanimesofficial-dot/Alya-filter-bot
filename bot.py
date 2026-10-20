import asyncio, hashlib, html, logging, os, random, re, time
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace
asyncio.set_event_loop(asyncio.new_event_loop())
from aiohttp import ClientSession, ClientTimeout, web
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle, raw, utils
from pyrogram.enums import ChatAction, ChatMembersFilter, ChatMemberStatus as S, ChatType as T, MessageEntityType as E, ParseMode
from pymongo import ReturnDocument
from pyrogram.types import ChatPermissions, InlineKeyboardButton as B, InlineKeyboardMarkup as M, User

# Import all settings from config.py
from config import (
    API_ID, API_HASH, BOT_TOKEN, MONGO_URI, DB_NAME, OWNER_ID as OWNER,
    BOT_NAME, MAINTAINER, MAINTAINER_URL, START_IMGS, LOAD_EMOJIS, START_REACTIONS, ANIM_CHANCE,
    BTN_COLOR, DEFAULT_BTN_COLOR, PREMIUM_EMOJIS, EMOJI_IDS, HELP_TEXT, SC
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
    if 5 <= h < 12: return smallcaps("good morning")
    if 12 <= h < 17: return smallcaps("good afternoon")
    if 17 <= h < 22: return smallcaps("good evening")
    return smallcaps("good night")

def start_txt(u):
    return (f"{smallcaps('hey')} {smallcaps(u.first_name or 'friend')} ツ, {greet()} {pe('wave')}\n\n"
            f"{smallcaps('i am the most powerful auto filter bot with premium features,')}\n"
            f"{smallcaps('just add me to your group and enjoy!')} {pe('fire')}\n\n"
            f'<blockquote>▶ <b>{smallcaps("maintained by")} :</b> <a href="{MAINTAINER_URL}">{MAINTAINER}</a> ❞</blockquote>')

# ------------------------------------------- raw Bot API layer
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

async def react(chat, mid, emoji=None):
    try:
        await raw_post("setMessageReaction", {"chat_id": chat, "message_id": mid, "is_big": True,
                                              "reaction": [{"type": "emoji", "emoji": emoji or random.choice(START_REACTIONS)}]})
    except Exception as e: log.warning("react failed (%s)", e)

def _lpo(url=None):
    # Pin the preview to the image URL so Telegram never picks another link (or none).
    if not url: return {"is_disabled": True}
    return {"url": url, "show_above_text": True, "prefer_large_media": True}

def _strip_pe(t):
    return re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', t or "")

_PE_SKIP = re.compile(r'(<tg-emoji[^>]*>.*?</tg-emoji>|<code>.*?</code>|<pre>.*?</pre>|<[^>]+>)', re.S)
_PE_RX = re.compile("|".join(re.escape(e) for e in sorted((e for e, i in EMOJI_IDS.items() if i), key=len, reverse=True)) or r"(?!x)x")
_PE_ID = {e: i for e, i in EMOJI_IDS.items() if i}

def pz(t):
    """Swap normal emojis for premium ones (EMOJI_IDS in config), skipping tags/code."""
    if not t or not _PE_ID: return t
    parts = _PE_SKIP.split(t)
    for n in range(0, len(parts), 2):
        parts[n] = _PE_RX.sub(lambda x: f'<tg-emoji emoji-id="{_PE_ID[x[0]]}">{x[0]}</tg-emoji>', parts[n])
    return "".join(parts)

async def pe_post(method, pl, key):
    """Send with premium emojis; if Telegram rejects them, retry with normal emojis."""
    pl[key] = pz(pl[key])
    try: return await raw_post(method, pl)
    except RuntimeError as e:
        if "not modified" in str(e).lower() or "tg-emoji" not in (pl[key] or ""): raise
        log.warning("%s with premium emojis failed (%s) - retrying without", method, e)
        pl[key] = _strip_pe(pl[key])
        return await raw_post(method, pl)

async def ui_send(chat, text, rows=None, reply_id=None, preview=None):
    pl = {"chat_id": chat, "text": text, "parse_mode": "HTML", "link_preview_options": _lpo(preview)}
    if reply_id: pl["reply_parameters"] = {"message_id": reply_id, "allow_sending_without_reply": True}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return (await pe_post("sendMessage", pl, "text"))["message_id"]

async def ui_edit(chat, mid, text, rows=None, preview=None):
    pl = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML", "link_preview_options": _lpo(preview)}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return await pe_post("editMessageText", pl, "text")

async def reply(m, text, rows=None): return await ui_send(m.chat.id, text, rows, reply_id=m.id)

async def raw_send_photo(chat, photo, caption, rows, reply_id=None):
    pl = {"chat_id": chat, "photo": photo, "caption": caption, "parse_mode": "HTML"}
    if reply_id: pl["reply_parameters"] = {"message_id": reply_id, "allow_sending_without_reply": True}
    if rows: pl["reply_markup"] = raw_kb(rows)
    return (await pe_post("sendPhoto", pl, "caption"))["message_id"]

async def raw_edit_media(chat, mid, photo, caption, rows):
    try:
        return await raw_post("editMessageMedia", {"chat_id": chat, "message_id": mid,
                              "media": {"type": "photo", "media": photo, "caption": pz(caption), "parse_mode": "HTML"},
                              "reply_markup": raw_kb(rows)})
    except RuntimeError as e:
        if "not modified" in str(e).lower(): return
        return await raw_post("editMessageMedia", {"chat_id": chat, "message_id": mid,
                              "media": {"type": "photo", "media": photo, "caption": _strip_pe(caption), "parse_mode": "HTML"},
                              "reply_markup": raw_kb(rows)})

# --------------------------------------- callback queries via Bot API
# Pyrogram 2.0.106 (layer 158) can't decode coloured buttons, so its CallbackQuery
# parser hangs re-fetching the message. Handle raw updates and reply over the Bot API.
app.dispatcher.update_parsers.pop(raw.types.UpdateBotCallbackQuery, None)
CBQ = []
def cbq(pattern):
    rx = re.compile(pattern)
    def deco(f): CBQ.append((rx, f)); return f
    return deco

class CQMsg:
    def __init__(self, chat_id, ctype, mid):
        self.chat, self.id = SimpleNamespace(id=chat_id, type=ctype), mid
    async def delete(self):
        try: await raw_post("deleteMessage", {"chat_id": self.chat.id, "message_id": self.id})
        except Exception: pass
    async def reply_text(self, text, rows=None): return await ui_send(self.chat.id, text, rows, reply_id=self.id)

class CQ:
    def __init__(self, c, u, users):
        self.id, self.data, self.answered = str(u.query_id), (u.data or b"").decode("utf-8", "ignore"), False
        ru = users.get(u.user_id)
        try: self.from_user = User._parse(c, ru) if ru else None
        except Exception: self.from_user = None
        if not self.from_user:
            self.from_user = SimpleNamespace(id=u.user_id, first_name="friend", last_name=None, username=None, is_premium=False,
                                             is_bot=False, mention=f'<a href="tg://user?id={u.user_id}">friend</a>')
        pt = {raw.types.PeerUser: T.PRIVATE, raw.types.PeerChat: T.GROUP}.get(type(u.peer), T.SUPERGROUP)
        self.message = CQMsg(utils.get_peer_id(u.peer), pt, u.msg_id)
    async def answer(self, text=None, show_alert=False):
        if self.answered: return
        self.answered = True
        pl = {"callback_query_id": self.id, "show_alert": show_alert}
        if text: pl["text"] = text
        try: await raw_post("answerCallbackQuery", pl)
        except Exception as e: log.warning("answer failed: %s", e)

@app.on_raw_update(group=-5)
async def raw_cb(c, u, users, chats):
    if not isinstance(u, raw.types.UpdateBotCallbackQuery): return
    q = CQ(c, u, users)
    for rx, f in CBQ:
        if rx.search(q.data):
            try: await f(c, q)
            except Exception as e: log.exception("callback %r failed: %s", q.data, e)
            break
    await q.answer()

async def edit_any(chat, mid, text, rows=None):
    try: return await ui_edit(chat, mid, text, rows)
    except RuntimeError as e:
        if "not modified" in str(e).lower(): return
        if "no text" not in str(e).lower(): raise
    pl = {"chat_id": chat, "message_id": mid, "caption": text, "parse_mode": "HTML"}
    if rows: pl["reply_markup"] = raw_kb(rows)
    try: return await pe_post("editMessageCaption", pl, "caption")
    except RuntimeError as e:
        if "not modified" not in str(e).lower(): raise

def start_kb(idx, uname):
    c = BTN_COLOR
    return [
        [R(ch, cb=f"sb:img:{idx}", s=c["letters"]) for ch in smallcaps(BOT_NAME)],
        [R(smallcaps("add me to your group"), url=f"https://t.me/{uname}?startgroup=true", s=c["add_group"])],
        [R(smallcaps("commands"), cb="sb:help", s=c["commands"]), R(smallcaps("settings"), cb="sb:set", s=c["settings"])],
        [R(smallcaps("connections"), cb="sb:conn", s=c["connections"]), R(smallcaps("about"), cb="sb:about", s=c["about"])],
    ]

BACK = lambda: [[R(smallcaps("◀ back"), cb="sb:back", s=BTN_COLOR["back"])]]

# ------------------------- bulletproof start-screen sender
async def send_start(c, chat, img, caption, kb, reply_id=None):
    if is_url(img):
        try: return await ui_send(chat, f'<a href="{img}">\u200b</a>\n{caption}', kb, reply_id=reply_id, preview=img)
        except Exception as e: log.warning("start: preview failed (%s)", e)
    try: return await raw_send_photo(chat, img, caption, kb, reply_id)
    except Exception as e: log.warning("start: photo failed (%s)", e)
    try: return await ui_send(chat, caption, kb, reply_id=reply_id)
    except Exception as e: log.warning("start: text failed (%s)", e)
    return await ui_send(chat, _strip_pe(caption), kb, reply_id=reply_id)

async def update_start(c, q, img, caption, kb):
    """Edit the current message into the start screen - never deletes it."""
    chat, mid = q.message.chat.id, q.message.id
    if is_url(img):
        try: return await ui_edit(chat, mid, f'<a href="{img}">\u200b</a>\n{caption}', kb, preview=img)
        except Exception as e:
            if "not modified" in str(e).lower(): return
            log.warning("update_start: preview edit failed (%s)", e)
    try: return await raw_edit_media(chat, mid, img, caption, kb)
    except Exception as e: log.warning("update_start: media edit failed (%s)", e)
    try: return await edit_any(chat, mid, caption, kb)
    except Exception as e: log.warning("update_start: text edit failed (%s) -> sending new", e)
    await send_start(c, chat, img, caption, kb)

# ------------------------------------------------------- loading animations
LD = {}
async def _ttl(msg, ttl):
    await asyncio.sleep(ttl)
    try: await msg.delete()
    except Exception: pass

def anim_roll(): return random.random() < ANIM_CHANCE

async def think(c, m, base="wait a sec", emojis=None, ticks=3, delay=0.4, ttl=10, force=False):
    if not m.from_user: return None
    if not force and not anim_roll(): return None
    msg = None
    for i in range(1, ticks + 1):
        body = f"<b><i>{smallcaps(base)}{'.' * i}</i></b>"
        if emojis: body = f"{emojis[(i - 1) % len(emojis)]} {body}"
        try:
            if msg is None: msg = CQMsg(m.chat.id, m.chat.type, await ui_send(m.chat.id, body))
            else: await ui_edit(m.chat.id, msg.id, body)
        except Exception: pass
        await asyncio.sleep(delay)
    if msg is None: return None
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

async def qanim(q, base="wait a sec", ticks=3, delay=0.3):
    if not anim_roll(): return
    for i in range(1, ticks + 1):
        body = f"<b><i>{smallcaps(base)}{'.' * i}</i></b>"
        try: await edit_any(q.message.chat.id, q.message.id, body)
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
            await reply(m, f"<b>❌ {smallcaps('not connected')}</b>\n\n<blockquote>{smallcaps('send /connect in your group, or /connect -100id here.')}</blockquote>")
            return None
    if await is_adm(c, g, u, owner, trust=(m.chat.type not in GRP and not owner)): return g
    await clear_ld(c, m.chat.id, u)
    perm_msg = smallcaps('only the group owner can do this.') if owner else smallcaps('admins only.')
    await reply(m, f"<b>❌ {smallcaps('permission denied')}</b>\n\n<blockquote>{perm_msg}</blockquote>")
    return None

TITLES = {}
async def title(c, g):
    t = TITLES.get(g)
    if t and time.time() - t[1] < 600: return t[0]
    try: v = (await asyncio.wait_for(c.get_chat(g), 5)).title or str(g)
    except Exception: return t[0] if t else str(g)
    TITLES[g] = (v, time.time()); return v

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

async def send(c, m, d, reply_id=None):
    d = dict(d)
    if d.get("b"): d["b"] = apply_default(d["b"])
    reply_id = reply_id or m.id
    try:
        r = await api_send(m.chat.id, reply_id, d)
        return r.get("message_id") if isinstance(r, dict) else r
    except Exception as e:
        log.warning("raw send failed (%s) - falling back to pyrogram", e)
    mk = M([[B(b["text"], url=b.get("url")) for b in r] for r in d.get("b") or []]) if d.get("b") else None
    ft, kw = d.get("ft"), dict(chat_id=m.chat.id, reply_to_message_id=reply_id, reply_markup=mk)
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
        if only and START_REACTIONS: asyncio.create_task(react(m.chat.id, m.id))
        await think(c, m, "starting", emojis=LOAD_EMOJIS if only else None, ttl=15, force=True)
        await clear_ld(c, m.chat.id, m.from_user.id)
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            await send_start(c, m.chat.id, START_IMGS[idx], start_txt(m.from_user), start_kb(idx, c.me.username), reply_id=m.id)
        else:
            await ui_send(m.chat.id, start_txt(m.from_user), start_kb(0, c.me.username), reply_id=m.id)
        return
    await think(c, m)
    await ship(c, m, HELP_TEXT)

async def sb_show(c, q, txt, rows):
    """Edit the tapped message in place (text or photo caption) - never deletes it."""
    try: await edit_any(q.message.chat.id, q.message.id, txt, rows)
    except Exception as e:
        log.warning("sb_show: edit failed (%s) -> sending new", e)
        await ui_send(q.message.chat.id, txt, rows)

@cbq(r"^sb:")
async def sb_cb(c, q):
    p = q.data.split(":"); a = p[1]; chat = q.message.chat.id
    if a == "img":
        if not START_IMGS: return await q.answer(smallcaps("no images added yet."), show_alert=True)
        cur = int(p[2]) if len(p) > 2 and p[2].lstrip("-").isdigit() else -1
        idx = random.choice([i for i in range(len(START_IMGS)) if i != cur] or [0])
        await q.answer()
        await update_start(c, q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        return
    if a == "back":
        await q.answer(); await qanim(q)
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            await update_start(c, q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username))
        else:
            await sb_show(c, q, start_txt(q.from_user), start_kb(0, c.me.username))
        return
    if a == "help":
        await q.answer(); await qanim(q)
        return await sb_show(c, q, HELP_TEXT, BACK())
    if a == "about":
        await q.answer(); await qanim(q)
        txt = (f"<b>❓ {smallcaps('about')} {smallcaps(BOT_NAME)}</b>\n\n<blockquote>{smallcaps('i am an auto filter bot with premium features.')}\n"
               f"{smallcaps('maintained by')} : <a href=\"{MAINTAINER_URL}\">{MAINTAINER}</a></blockquote>")
        return await sb_show(c, q, txt, BACK())
    if a == "set":
        g = (await cn.find_one({"_id": q.from_user.id}) or {}).get("active")
        if not g: return await q.answer(smallcaps("connect a group first: send /connect in your group."), show_alert=True)
        if not await is_adm(c, g, q.from_user.id, trust=True): return await q.answer(smallcaps("admins only."), show_alert=True)
        await q.answer(); await qanim(q)
        cf = await cfg(g)
        return await sb_show(c, q, st_txt(cf), st_kb(g, cf) + BACK())
    if a == "conn":
        await q.answer()
        rows = await conn_kb(c, q.from_user.id)
        return await sb_show(c, q, conn_txt(bool(rows)), (rows or []) + BACK())

@app.on_message(filters.command("info"))
async def info_cmd(c, m):
    await think(c, m)
    u = m.reply_to_message.from_user if m.reply_to_message and m.reply_to_message.from_user else m.from_user
    if not u: return await ship(c, m, f"<b>❌ {smallcaps('no user')}</b>\n\n<blockquote>{smallcaps('reply to a user or send in a group to see info.')}</blockquote>")
    text = f"<b>{pe('crown')} {smallcaps('user info')}</b>\n\n<blockquote>"
    text += f"<b>🆔 {smallcaps('id')}:</b> <code>{u.id}</code>\n<b>👤 {smallcaps('first name')}:</b> {html.escape(u.first_name or smallcaps('none'))}\n"
    if u.last_name: text += f"<b>👤 {smallcaps('last name')}:</b> {html.escape(u.last_name)}\n"
    if u.username: text += f"<b>🔗 {smallcaps('username')}:</b> @{u.username}\n"
    text += f"<b>👑 {smallcaps('premium')}:</b> {'✅ ' + smallcaps('yes') if u.is_premium else '❌ ' + smallcaps('no')}\n<b>🤖 {smallcaps('bot')}:</b> {'✅ ' + smallcaps('yes') if u.is_bot else '❌ ' + smallcaps('no')}\n"
    text += f"<b>🔗 {smallcaps('profile')}:</b> <a href='tg://user?id={u.id}'>{smallcaps('click here')}</a></blockquote>"
    await ship(c, m, text)

@app.on_message(filters.command("getid") & filters.private)
async def getid(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    r = m.reply_to_message
    if not r: return await reply(m, f"<b>❌ {smallcaps('reply to a photo/video with /getid')}</b>")
    fid = None
    for x in MEDIA:
        obj = getattr(r, x, None)
        if obj: fid = getattr(obj, "file_id", None); break
    await reply(m, f"<b>📎 {smallcaps('file_id')}:</b>\n<code>{fid}</code>" if fid else f"<b>❌ {smallcaps('no media found')}</b>")

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
        return await ship(c, m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('reply to a message with /filter keyword')}\n{smallcaps('or /filter word text')}\n\n<b>{smallcaps('modes')}:</b>\n• <code>contains:price</code>\n• <code>regex:^hi(llo)?$</code></blockquote>")
    if mt == "regex":
        try: re.compile(k)
        except re.error as e: return await ship(c, m, f"<b>❌ {smallcaps('invalid regex')}</b>\n\n<blockquote>{html.escape(str(e))}</blockquote>")
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
    if not t and not fid: return await ship(c, m, f"<b>❌ {smallcaps('empty filter')}</b>\n\n<blockquote>{smallcaps('reply to a message/media or add some text.')}</blockquote>")
    if fid and len(t) > 1024: return await ship(c, m, f"<b>❌ {smallcaps('caption too long')}</b>\n\n<blockquote>{smallcaps('caption max is 1024 characters.')}</blockquote>")
    await fl.update_one({"g": g, "k": k}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "mt": mt}}, upsert=True)
    KW.pop(g, None)
    match_txt = f" ({smallcaps(mt)} {smallcaps('match')})" if mt != "word" else ""
    await ship(c, m, f"<b>✅ {smallcaps('filter saved')}</b>\n\n<blockquote>{smallcaps('keyword')}: <code>{html.escape(k)}</code>{match_txt}</blockquote>")

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
    if not has: return f"<b>📭 {smallcaps('no filters')}</b>\n\n<blockquote>{smallcaps('no filters yet. reply to a post with /filter keyword to add one.')}</blockquote>"
    return f"<b>📋 {smallcaps('filters in')} {html.escape(smallcaps(t))}</b> ({n})\n\n<blockquote>{smallcaps('tap 🗑 to delete a filter.')}</blockquote>"

async def panel(c, m, g):
    kb, n = await fl_kb(g)
    await ship(c, m, panel_txt(await title(c, g), n, bool(kb)), kb)

@app.on_message(filters.command("filters"))
async def lst(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    await panel(c, m, g)

@cbq(r"^f[dpv]:")
async def fl_cb(c, q):
    p = q.data.split(":"); a, g = p[0], int(p[1])
    cd = await cn.find_one({"_id": q.from_user.id}) or {}
    trust = q.message.chat.type == T.PRIVATE and g in cd.get("groups", [])
    if not await is_adm(c, g, q.from_user.id, trust=trust): return await q.answer(smallcaps("admins only."), show_alert=True)
    page = int(p[2]) if a == "fp" else int(p[3])
    if a == "fv":
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        return await q.answer(k or smallcaps("already deleted"), show_alert=True)
    await qanim(q)
    if a == "fp": await q.answer()
    else:
        k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == p[2]), None)
        if k: await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
        await q.answer(smallcaps("deleted 🗑") if k else smallcaps("already deleted"))
    kb, n = await fl_kb(g, page)
    try: await edit_any(q.message.chat.id, q.message.id, panel_txt(await title(c, g), n, bool(kb)), kb)
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
    if r.deleted_count:
        await ship(c, m, f"<b>✅ {smallcaps('deleted')}</b>\n\n<blockquote>{smallcaps('filter')} <code>{k}</code> {smallcaps('deleted.')}</blockquote>")
    else:
        await ship(c, m, f"<b>❌ {smallcaps('not found')}</b>\n\n<blockquote>{smallcaps('no such filter. send /filters and tap 🗑 instead.')}</blockquote>")

@app.on_message(filters.command("delall"))
async def delall(c, m):
    g = await target(c, m, owner=True)
    if g is None: return
    await think(c, m)
    await ship(c, m, f"<b>⚠️ {smallcaps('warning')}</b>\n\n<blockquote>{smallcaps('delete all filters?')}</blockquote>",
               [[R(smallcaps("yes"), cb=f"da:y:{g}", s="danger"), R(smallcaps("cancel"), cb="da:n:0")]])

@cbq(r"^da:")
async def delall_cb(c, q):
    _, a, g = q.data.split(":"); g = int(g)
    if a != "y":
        await qanim(q)
        return await edit_any(q.message.chat.id, q.message.id, f"<b>❌ {smallcaps('cancelled')}</b>")
    if not await is_adm(c, g, q.from_user.id, True): return await q.answer(smallcaps("owner only."), show_alert=True)
    await qanim(q)
    r = await fl.delete_many({"g": g}); KW.pop(g, None)
    await edit_any(q.message.chat.id, q.message.id, f"<b>✅ {smallcaps('success')}</b>\n\n<blockquote>{smallcaps('deleted')} {r.deleted_count} {smallcaps('filter(s).')}</blockquote>")

@app.on_message(filters.command("color"))
async def color(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    p = (m.text.html or "").split(None, 1)
    k, rest = kwsplit(p[1] if len(p) > 1 else "")
    cols = rest.lower().replace(",", " ").split()
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if not k or not cols: return await ship(c, m, f'<b>❌ {smallcaps("usage error")}</b>\n\n<blockquote>{smallcaps("usage: /color keyword red green blue none")}\n{smallcaps("(one colour per button, in order)")}</blockquote>')
    if not d or not d.get("b"): return await ship(c, m, f"<b>❌ {smallcaps('no buttons')}</b>\n\n<blockquote>{smallcaps('no such filter, or it has no buttons.')}</blockquote>")
    i = 0
    for row in d["b"]:
        for b in row:
            col = cols[i] if i < len(cols) else None
            i += 1
            if col in STY: b["s"] = STY[col]
            else: b.pop("s", None)
    await fl.update_one({"g": g, "k": k}, {"$set": {"b": d["b"]}})
    await ship(c, m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{smallcaps('button colours updated for')} <code>{k}</code>.</blockquote>")

@app.on_message(filters.command("connect"))
async def connect(c, m):
    if not m.from_user: return
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        if len(m.command) < 2:
            return await reply(m, f"<b>❌ {smallcaps('how to connect')}</b>\n\n<blockquote>{smallcaps('best way: send /connect inside your group.')}\n{smallcaps('or here:')} <code>/connect -100xxxxxxxxxx</code></blockquote>")
        try: g = int(m.command[1])
        except ValueError: return await reply(m, f"<b>❌ {smallcaps('invalid id')}</b>\n\n<blockquote>{smallcaps('the group id must be a number like')} <code>-1001234567890</code>.</blockquote>")
        try: await c.get_chat(g)
        except Exception as e:
            m1 = smallcaps("i can't reach that group")
            m2 = smallcaps("make sure i'm added to it, then send /connect inside the group instead.")
            return await reply(m, f"<b>❌ {smallcaps('unreachable')}</b>\n\n<blockquote>{m1} ({type(e).__name__}).\n{m2}</blockquote>")
    if not await is_adm(c, g, u): return await reply(m, f"<b>❌ {smallcaps('not admin')}</b>\n\n<blockquote>{smallcaps('you must be an admin of that group to connect it.')}</blockquote>")
    await think(c, m)
    await conn_set(u, g)
    await ship(c, m, f"<b>✅ {smallcaps('connected')}</b>\n\n<blockquote>{smallcaps('connected to')} <b>{html.escape(smallcaps(await title(c, g)))}</b>. {smallcaps('manage filters from my pm.')}</blockquote>")

@app.on_message(filters.command("disconnect"))
async def disconnect(c, m):
    if not m.from_user: return
    await think(c, m)
    g = m.chat.id if m.chat.type in GRP else (await cn.find_one({"_id": m.from_user.id}) or {}).get("active")
    if g: await conn_set(m.from_user.id, g, False)
    await ship(c, m, f"<b>✅ {smallcaps('disconnected')}</b>" if g else f"<b>❌ {smallcaps('not connected')}</b>")

async def conn_kb(c, u):
    d = await cn.find_one({"_id": u}) or {}
    gs = d.get("groups", [])
    ts = await asyncio.gather(*(title(c, g) for g in gs))
    rows = [[R((("✅ " if g == d.get("active") else "") + smallcaps(t))[:64] or str(g), cb=f"cn:s:{g}"), R("🗑", cb=f"cn:d:{g}", s=BTN_COLOR["delete"])] for g, t in zip(gs, ts)]
    return rows or None

def conn_txt(has):
    if has: return f"<b>🔗 {smallcaps('your groups')}</b>\n\n<blockquote>{smallcaps('tap to select, 🗑 to remove.')}</blockquote>"
    return f"<b>📭 {smallcaps('no connections')}</b>\n\n<blockquote>{smallcaps('send /connect in your group first.')}</blockquote>"

@app.on_message(filters.command("connections") & filters.private)
async def conns(c, m):
    await think(c, m)
    kb = await conn_kb(c, m.from_user.id)
    await ship(c, m, conn_txt(bool(kb)), kb)

@cbq(r"^cn:")
async def conn_cb(c, q):
    _, a, g = q.data.split(":"); g, u = int(g), q.from_user.id
    if a == "s": await cn.update_one({"_id": u}, {"$set": {"active": g}})
    else: await conn_set(u, g, False)
    await q.answer(smallcaps("connected ✅") if a == "s" else smallcaps("removed 🗑"))
    kb = await conn_kb(c, u)
    rows = (kb or []) + BACK()
    await sb_show(c, q, conn_txt(bool(kb)), rows)

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
    return (f"<b>⚙️ {smallcaps('group protection')}</b>\n\n"
            f"<blockquote>{smallcaps('admins & the owner are always allowed.')}\n{smallcaps('turn on 🧪 test mode to test with your own account.')}\n\n"
            f"• <b>{smallcaps('anti-link')}</b>: {smallcaps('deletes links (except allowed) and pms the owner.')}\n"
            f"• <b>{smallcaps('anti-forward')}</b> / <b>{smallcaps('anti-mention')}</b>: {smallcaps('delete forwarded messages / @mentions.')}\n"
            f"• <b>{smallcaps('flood')}</b>: {cf.get('fn', 6)} {smallcaps('messages in')} {cf.get('ft', 5)}s = 10 {smallcaps('min mute.')}\n"
            f"• <b>{smallcaps('auto-warn')}</b>: {smallcaps('each deleted message adds a warning; at the limit the user is punished.')}</blockquote>"
            f"\n<i>{smallcaps('bot needs admin rights: delete messages + restrict members.')}</i>")

def st_kb(g, cf):
    b = lambda lb, k: R(f"{lb} {'✅' if cf.get(k) else '⛔'}", cb=f"st:{g}:{k}", s="green" if cf.get(k) else "red")
    return [[b(f"🔗 {smallcaps('anti-link')}", "al"), b(f"↪️ {smallcaps('anti-forward')}", "af")],
            [b(f"@ {smallcaps('anti-mention')}", "am"), b(f"🌊 {smallcaps('flood')}", "fd")],
            [b(f"⚠️ {smallcaps('auto-warn')}", "aw"), b(f"🧪 {smallcaps('test mode')}", "tm")],
            [R("➖", cb=f"st:{g}:w-"), R(f"{smallcaps('warn limit')}: {cf.get('wl', 3)}", cb=f"st:{g}:w0"), R("➕", cb=f"st:{g}:w+")],
            [R(f"{smallcaps('warn action')}: {smallcaps(cf.get('wa', 'mute'))}", cb=f"st:{g}:wa")]]

@app.on_message(filters.command(["settings", "antilink"]))
async def settings_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    if m.command[0] == "antilink" and m.command[1:2] in (["on"], ["off"]): await cfg_set(g, al=m.command[1] == "on")
    await think(c, m)
    cf = await cfg(g)
    await ship(c, m, st_txt(cf), st_kb(g, cf))

@cbq(r"^st:")
async def st_cb(c, q):
    _, g, k = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer(smallcaps("admins only."), show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if k in ("al", "af", "am", "fd", "aw", "tm"): await cfg_set(g, **{k: not cf.get(k)})
    elif k in ("w+", "w-"): await cfg_set(g, wl=max(2, min(10, cf.get("wl", 3) + (1 if k == "w+" else -1))))
    elif k == "wa": await cfg_set(g, wa={"mute": "kick", "kick": "ban"}.get(cf.get("wa", "mute"), "mute"))
    await q.answer()
    try: await edit_any(q.message.chat.id, q.message.id, st_txt(cf), st_kb(g, cf))
    except Exception: pass

@app.on_message(filters.command("setflood"))
async def setflood(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    try:
        n, t = int(m.command[1]), int(m.command[2]); assert 2 <= n <= 30 and 1 <= t <= 60
    except Exception: return await ship(c, m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('usage: /setflood messages seconds')}\n({smallcaps('e.g.')} <code>/setflood 6 5</code>)</blockquote>")
    await cfg_set(g, fn=n, ft=t)
    await ship(c, m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{smallcaps('flood limit')}: {n} {smallcaps('messages in')} {t} {smallcaps('seconds.')}</blockquote>")

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
        return f"<b>⚠️ {smallcaps('warning added')}</b>\n\n<blockquote>{u.mention} {smallcaps('warned')} <b>({n}/{lim})</b>\n{smallcaps('reason')}: {html.escape(why)}</blockquote>", [[R(f"🗑 {smallcaps('remove warn')}", cb=f"uw:{g}:{u.id}")]]
    await wn.delete_one({"g": g, "u": u.id})
    act = cf.get("wa", "mute")
    try:
        if act == "mute": await c.restrict_chat_member(g, u.id, ChatPermissions())
        else:
            await c.ban_chat_member(g, u.id)
            if act == "kick": await c.unban_chat_member(g, u.id)
        done = {"mute": smallcaps("muted"), "kick": smallcaps("kicked"), "ban": smallcaps("banned")}[act]
    except Exception as e:
        log.warning("warn action failed in %s: %s", g, e); done = smallcaps("not punished (i need the 'restrict members' admin right)")
    return f"<b>🚫 {smallcaps('limit reached')}</b>\n\n<blockquote>{u.mention} {smallcaps('reached')} {lim} {smallcaps('warnings and was')} {done}.\n{smallcaps('reason')}: {html.escape(why)}</blockquote>", [[R(f"↩️ {smallcaps('undo')}", cb=f"ub:{g}:{u.id}")]]

async def tgt_user(c, m):
    if m.chat.type not in GRP or not m.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id if m.from_user else 0)
        m1 = smallcaps("use this in a group, as a reply to the user's message.")
        await reply(m, f"<b>❌ {smallcaps('group only')}</b>\n\n<blockquote>{m1}</blockquote>"); return None, None
    if not await is_adm(c, m.chat.id, m.from_user.id):
        await clear_ld(c, m.chat.id, m.from_user.id)
        await reply(m, f"<b>❌ {smallcaps('admins only')}</b>"); return None, None
    r = m.reply_to_message
    if not r or not r.from_user:
        await clear_ld(c, m.chat.id, m.from_user.id)
        m1 = smallcaps("reply to the user's message.")
        await reply(m, f"<b>❌ {smallcaps('reply missing')}</b>\n\n<blockquote>{m1}</blockquote>"); return None, None
    return m.chat.id, r.from_user

@app.on_message(filters.command("warn"))
async def warn_cmd(c, m):
    g, u = await tgt_user(c, m)
    if not g: return
    if await is_adm(c, g, u.id): return await ship(c, m, f"<b>❌ {smallcaps('cannot warn')}</b>\n\n<blockquote>{smallcaps('i can’t warn admins.')}</blockquote>")
    await think(c, m)
    txt, kb = await add_warn(c, g, u, " ".join(m.command[1:]) or smallcaps("no reason given"))
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
    await ship(c, m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{u.mention} {smallcaps('now has')} {left} {smallcaps('warning(s).')}</blockquote>")

@app.on_message(filters.command("warns") & filters.group)
async def warns_cmd(c, m):
    await think(c, m)
    r = m.reply_to_message
    u = r.from_user if r and r.from_user else m.from_user
    if not u: return
    d = await wn.find_one({"g": m.chat.id, "u": u.id})
    await ship(c, m, f"<b>⚠️ {smallcaps('warnings')}</b>\n\n<blockquote>{u.mention}: <b>{(d or {}).get('n', 0)}/{(await cfg(m.chat.id)).get('wl', 3)}</b> {smallcaps('warnings.')}</blockquote>")

@app.on_message(filters.command(["unmute", "unban"]) & filters.group)
async def unrestrict(c, m):
    if not m.from_user or not await is_adm(c, m.chat.id, m.from_user.id): return
    await think(c, m)
    r = m.reply_to_message
    try: uid = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await ship(c, m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('reply to the user, or:')} <code>/unmute user_id</code></blockquote>")
    try: await c.unban_chat_member(m.chat.id, uid); await ship(c, m, f"<b>✅ {smallcaps('done')}</b>\n\n<blockquote>{smallcaps('restrictions removed.')}</blockquote>")
    except Exception as e:
        m1 = smallcaps("i need the 'restrict members' right")
        await ship(c, m, f"<b>❌ {smallcaps('failed')}</b>\n\n<blockquote>{type(e).__name__} ({m1})</blockquote>")

@cbq(r"^u[wb]:")
async def undo_cb(c, q):
    a, g, u = q.data.split(":"); g, u = int(g), int(u)
    if not await is_adm(c, g, q.from_user.id): return await q.answer(smallcaps("admins only."), show_alert=True)
    await qanim(q)
    if a == "uw":
        r = await wn.find_one_and_update({"g": g, "u": u, "n": {"$gt": 0}}, {"$inc": {"n": -1}}, return_document=ReturnDocument.AFTER)
        msg = f"<b>✅ {smallcaps('removed')}</b>\n\n<blockquote>{smallcaps('one warning removed')} ({r['n'] if r else 0} {smallcaps('left')}).</blockquote>"
    else:
        try: await c.unban_chat_member(g, u)
        except Exception: pass
        msg = f"<b>↩️ {smallcaps('undone')}</b>\n\n<blockquote>{smallcaps('punishment undone.')}</blockquote>"
    await q.answer(smallcaps("done"))
    try: await edit_any(q.message.chat.id, q.message.id, msg)
    except Exception: pass

def norm(u): return re.sub(r"^(?:https?://)?(?:www\.)?", "", u.strip().lower()).rstrip("/")

def allowed(link, wl):
    n = norm(link); host = re.split(r"[/?#]", n)[0]
    return any(n == a or n.startswith(a + "/") or ("/" not in a and (host == a or host.endswith("." + a))) for a in wl)

def dom_view(g, cf):
    ds = cf.get("dom", [])
    if not ds:
        return (f"<b>🔗 {smallcaps('allowed links')}</b>\n\n<blockquote>{smallcaps('no allowed links yet.')}\n{smallcaps('add:')} <code>/allowlink domain.com</code> {smallcaps('or')} <code>/allowlink t.me/yourchannel</code></blockquote>"), None
    return (f"<b>🔗 {smallcaps('allowed links')}</b>\n\n<blockquote>{smallcaps('anti-link ignores these domains.')}\n{smallcaps('add more:')} <code>/allowlink domain.com</code></blockquote>",
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

@cbq(r"^d[dn]:")
async def dom_cb(c, q):
    a, g, h = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer(smallcaps("admins only."), show_alert=True)
    await qanim(q)
    cf = await cfg(g)
    if a == "dd": await cfg_set(g, dom=[d for d in cf.get("dom", []) if kh(d) != h])
    await q.answer(smallcaps("removed") if a == "dd" else None)
    txt, kb = dom_view(g, cf)
    try: await edit_any(q.message.chat.id, q.message.id, txt, kb)
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
    text = (f"🚨 <b>{smallcaps('link deleted')}</b>\n<b>{smallcaps('group')}:</b> {html.escape(await title(c, g))} (<code>{g}</code>)\n"
            f"<b>{smallcaps('user')}:</b> {u.mention}\n<b>{smallcaps('id')}:</b> <code>{u.id}</code>\n"
            f"<b>{smallcaps('username')}:</b> {'@' + u.username if u.username else smallcaps('none')}\n<b>{smallcaps('link(s)')}:</b>\n{ls}")
    owner = await gc_owner(c, g)
    if owner:
        try: return await ui_send(owner, text)
        except Exception as e: log.info("cannot PM owner %s of %s (%s) - owner must /start the bot", owner, g, e)
    for uid in [d["_id"] async for d in cn.find({"groups": g})]:
        try: await ui_send(uid, text)
        except Exception: pass

async def punish(c, g, m, why):
    cf = await cfg(g)
    try: await m.delete()
    except Exception as e:
        log.warning("guard: cannot delete in %s (%s) - make the bot admin with 'Delete messages'", g, e); return False
    note, kb = f"<b>⚠️ {smallcaps('deleted')}</b>\n\n<blockquote>{m.from_user.mention}, {why}. {smallcaps('your message was deleted.')}</blockquote>", None
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
            asyncio.create_task(tmp(c, g, f"<b>🌊 {smallcaps('flood mute')}</b>\n\n<blockquote>{u.mention} {smallcaps('was muted for 10 minutes for flooding.')}</blockquote>", 60, [[R(f"↩️ {smallcaps('undo')}", cb=f"ub:{g}:{u.id}")]]))
            if cf.get("aw"):
                wt, wk = await add_warn(c, g, u, smallcaps("flooding")); asyncio.create_task(tmp(c, g, wt, 60, wk))
            m.stop_propagation()
    fwd = getattr(m, "forward_date", None) or getattr(m, "forward_from_chat", None) or getattr(m, "forward_sender_name", None)
    if cf.get("af") and fwd and await punish(c, g, m, smallcaps("forwarded messages are not allowed here")): m.stop_propagation()
    if cf.get("am") and any(e.type == E.MENTION for e in ents) and await punish(c, g, m, smallcaps("@mentions are not allowed here")): m.stop_propagation()
    if cf.get("al") and any(e.type in (E.URL, E.TEXT_LINK) for e in ents):
        bad = [x for x in links_of(m) if not allowed(x, cf.get("dom", []))]
        if bad and await punish(c, g, m, smallcaps("links are not allowed here")):
            asyncio.create_task(report(c, g, m, bad)); m.stop_propagation()

@app.on_message(filters.group & (filters.new_chat_members | filters.left_chat_member), group=10)
async def del_service(c, m):
    try: await m.delete()
    except Exception: pass

WEL_VARS = "{first} {last} {fullname} {mention} {username} {id} {group} {count}"
WS = [0, 60, 300, 600]
def fmt_s(s): return smallcaps("off") if not s else f"{s // 60}{smallcaps('m')}"

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
    t = f"<b>👋 {smallcaps('welcome message')}</b>\n\n"
    if has:
        t += f"<blockquote>{smallcaps('status')}: {'✅ ' + smallcaps('on') if w.get('on') else '⛔ ' + smallcaps('off')} | {smallcaps('type')}: {smallcaps(w.get('ft') or 'text')}\n{smallcaps('buttons')}: {sum(len(r) for r in w.get('b') or [])} | {smallcaps('auto-delete')}: {fmt_s(w.get('ad', 0))}</blockquote>\n"
    else:
        t += f"<blockquote>{smallcaps('no welcome message set yet.')}</blockquote>\n"
    t += (f"\n<blockquote>{smallcaps('tap')} <b>✏️ {smallcaps('set / change')}</b>, {smallcaps('then send me the welcome.')}\n"
          f"{smallcaps('variables')}: <code>{WEL_VARS}</code>\n{smallcaps('buttons')}: <code>[Join](buttonurl:https://t.me/x)</code> ({smallcaps('add')} <code>:same</code> <code>:red</code> <code>:green</code> <code>:blue</code>)</blockquote>")
    rows = [[R(f"✏️ {smallcaps('set / change')}", cb=f"wl:{g}:set", s=BTN_COLOR["set"])]]
    if has:
        rows += [[R(f"👁 {smallcaps('preview')}", cb=f"wl:{g}:prev"), R(f"🔛 {smallcaps('on') if w.get('on') else smallcaps('off')}", cb=f"wl:{g}:tog", s="green" if w.get("on") else "red")],
                 [R(f"⏱ {smallcaps('auto-delete')}: {fmt_s(w.get('ad', 0))}", cb=f"wl:{g}:ad"), R(f"🗑 {smallcaps('remove')}", cb=f"wl:{g}:rm", s=BTN_COLOR["delete"])]]
    return t, rows

@app.on_message(filters.command("setwel"))
async def setwel(c, m):
    if m.chat.type in GRP:
        await think(c, m)
        return await ship(c, m, f"<b>👋 {smallcaps('welcome editor')}</b>\n\n<blockquote>{smallcaps('set the welcome message in my pm 👇')}</blockquote>",
                          [[R(smallcaps("open pm"), url=f"https://t.me/{c.me.username}?start=setwel", s="primary")]])
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    txt, rows = await wel_view(c, g)
    await ship(c, m, txt, rows)

@cbq(r"^wl:")
async def wel_cb(c, q):
    _, g, a = q.data.split(":"); g = int(g)
    if not await can(c, q, g): return await q.answer(smallcaps("admins only."), show_alert=True)
    w = await wc.find_one({"_id": g}) or {}
    if a == "set":
        await qanim(q)
        PEND[q.from_user.id] = g
        await q.answer()
        await q.message.reply_text(f"<b>✏️ {smallcaps('editing')}</b>\n\n<blockquote>{smallcaps('send me the new welcome message now - text, or a photo/video/gif with a caption.')}\n{smallcaps('buttons:')} <code>[Name](buttonurl:https://...)</code>\n{smallcaps('send /cancel to stop.')}</blockquote>")
        txt, rows = await wel_view(c, g)
        try: await edit_any(q.message.chat.id, q.message.id, txt, rows)
        except Exception: pass
        return
    if a == "prev":
        await qanim(q)
        d = {"t": await wel_text(c, w.get("t"), [q.from_user], SimpleNamespace(title=await title(c, g), id=g)),
             "ft": w.get("ft"), "fid": w.get("fid"), "b": w.get("b")}
        await q.answer()
        await send(c, q.message, d)
        txt, rows = await wel_view(c, g)
        try: await edit_any(q.message.chat.id, q.message.id, txt, rows)
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
    try: await edit_any(q.message.chat.id, q.message.id, txt, rows)
    except Exception: pass

async def _pend(_, __, m): return bool(m.from_user) and m.from_user.id in PEND
pend = filters.create(_pend)

@app.on_message(filters.command("cancel") & filters.private)
async def cancel(c, m):
    await think(c, m, ticks=2, delay=0.3)
    PEND.pop(m.from_user.id, None)
    await ship(c, m, f"<b>❌ {smallcaps('cancelled')}</b>")

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
    if not t and not fid: PEND[uid] = g; return await reply(m, f"<b>❌ {smallcaps('invalid input')}</b>\n\n<blockquote>{smallcaps('send text, or media with a caption.')}</blockquote>")
    if fid and ft not in ("sticker", "video_note") and len(t) > 1024: PEND[uid] = g; return await reply(m, f"<b>❌ {smallcaps('too long')}</b>\n\n<blockquote>{smallcaps('caption max is 1024 characters - send a shorter one.')}</blockquote>")
    await wc.update_one({"_id": g}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "on": True}}, upsert=True)
    txt, rows = await wel_view(c, g)
    await reply(m, f"<b>✅ {smallcaps('saved')}</b>\n\n<blockquote>{smallcaps('welcome message saved!')}</blockquote>")
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
                rid = m.reply_to_message.id if m.reply_to_message else m.id
                try:
                    await c.send_chat_action(g, ChatAction.TYPING)
                    if anim_roll():
                        loader = await ui_send(g, f"<b><i>{smallcaps('finding...')}</i></b>", reply_id=m.id)
                        await asyncio.sleep(0.8)
                        await CQMsg(g, m.chat.type, loader).delete()
                except Exception: pass
                await send(c, m, d, reply_id=rid)
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
    if not m.reply_to_message: return await reply(m, f"<b>❌ {smallcaps('reply missing')}</b>\n\n<blockquote>{smallcaps('reply to the message you want to broadcast with /broadcast')}</blockquote>")
    s = await think(c, m, "broadcasting", ttl=3600, force=True)
    ids = [d["_id"] async for d in ch.find()]
    ok = 0
    for i in ids:
        ok += await bsend(m.reply_to_message, i)
        await asyncio.sleep(0.3)
    LD.pop((m.chat.id, m.from_user.id), None)
    if s:
        try: await ui_edit(m.chat.id, s.id, f"<b>✅ {smallcaps('done')}</b>\n\n<blockquote>{smallcaps('broadcast done')}: ✅ {ok} {smallcaps('sent')}, ❌ {len(ids) - ok} {smallcaps('failed.')}</blockquote>")
        except Exception: pass

@app.on_message(filters.command("stats"))
async def stats(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    g, h = await ch.count_documents({"type": "group"}), await ch.count_documents({"type": "channel"})
    await ship(c, m, f"<b>📊 {smallcaps('bot statistics')}</b>\n\n<blockquote>{smallcaps('groups')}: {g}\n{smallcaps('channels')}: {h}\n{smallcaps('filters')}: {await fl.count_documents({})}\n{smallcaps('admins')}: {len(ADMINS)}</blockquote>")

@app.on_message(filters.command(["addadmin", "rmadmin"]))
async def admin_cmd(c, m):
    if not m.from_user or m.from_user.id != OWNER: return
    r = m.reply_to_message
    try: u = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await reply(m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('usage: /addadmin user_id (or reply to a user)')}</blockquote>")
    await think(c, m, ticks=2, delay=0.3)
    add = m.command[0] == "addadmin"
    if add: await ad.update_one({"_id": u}, {"$set": {"a": 1}}, upsert=True); ADMINS.add(u)
    else: await ad.delete_one({"_id": u}); ADMINS.discard(u)
    status = smallcaps("added") if add else smallcaps("removed")
    await ship(c, m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{status} {smallcaps('admin')} <code>{u}</code></blockquote>")

@app.on_message(filters.command("admins"))
async def admins(c, m):
    if m.from_user and sudo(m.from_user.id):
        await think(c, m, ticks=2, delay=0.3)
        admin_list = ", ".join(f"<code>{a}</code>" for a in ADMINS) or smallcaps("none")
        await ship(c, m, f"<b>👑 {smallcaps('admin list')}</b>\n\n<blockquote>{smallcaps('owner')}: <code>{OWNER}</code>\n{smallcaps('admins')}: {admin_list}</blockquote>")

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