import asyncio, contextvars, difflib, hashlib, html, io, json, logging, os, random, re, time
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
try: from config import LOG_CHANNEL
except ImportError: LOG_CHANNEL = 0

logging.basicConfig(level=logging.INFO); log = logging.getLogger("fb")

db = AsyncIOMotorClient(MONGO_URI)[DB_NAME]
fl, cn, ch, ad, st, wc, wn, em, us = db.filters, db.connections, db.chats, db.admins, db.settings, db.welcome, db.warns, db.emojis, db.users
app = Client("fb", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, parse_mode=ParseMode.HTML, in_memory=True)

GRP = (T.GROUP, T.SUPERGROUP)
MEDIA = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")
KW, ADMINS, SEEN = {}, set(), set()
BTN, TAG = re.compile(r"\[(.+?)\]\(buttonurl:(.+?)((?::(?:same|red|green|blue))*)\)"), re.compile(r"<[^>]+>")
STY = {"red": "danger", "green": "success", "blue": "primary"}
plain = lambda s: html.unescape(TAG.sub("", s)).strip()

# ---------------------------------------------------- helpers
SCON = contextvars.ContextVar("scon", default=True)   # per-group small-caps switch (set for every update)
def smallcaps(s):
    if not SCON.get(): return s or ""
    return "".join(SC.get(ch, ch) for ch in (s or "").lower())
def is_url(img): return img.startswith("http")
def sty(w):
    w = (w or "").lower()
    return STY.get(w) or (w if w in ("danger", "success", "primary") else None)

def pe(name):
    """Helper to easily use Premium Emojis defined in config.py"""
    data = PREMIUM_EMOJIS.get(name)
    if not data: return "✨"
    return data[1]

def greet():
    h = datetime.now().hour
    if 5 <= h < 12: return smallcaps("good morning")
    if 12 <= h < 17: return smallcaps("good afternoon")
    if 17 <= h < 22: return smallcaps("good evening")
    return smallcaps("good night")

def start_txt(u):
    return (f"<blockquote><b>{smallcaps('hey')} {smallcaps(u.first_name or 'friend')} ツ, {greet()} {pe('wave')}</blockquote></b>\n\n"
            f"{smallcaps('i am the most powerful auto filter bot with premium features,')}\n"
            f"<b>{smallcaps('just add me to your group and enjoy!')} {pe('fire')}\n\n"
            f"{maint_txt()}</b>")

def maint_txt():
    # Fixed mismatched HTML tags
    return f'<blockquote>▶ <b>{smallcaps("maintained by")} :</b> <a href="{MAINTAINER_URL}"><b>{MAINTAINER}</b></a></blockquote>'

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
            ic = b.get("i")
            if not ic and _PE_RX.match(nb["text"] or ""):
                e = _PE_RX.match(nb["text"])[0]; ic = _PE_ID.get(e)
                if ic and nb["text"][len(e):].strip(): nb["text"] = nb["text"][len(e):].strip()
                else: ic = None
            if ic: nb["icon_custom_emoji_id"] = str(ic)
            if b.get("url"): nb["url"] = b["url"]
            if b.get("cb") or b.get("callback_data"): nb["callback_data"] = b.get("cb") or b.get("callback_data")
            sv = b.get("style") or b.get("s")
            if sv: nb["style"] = sv if sv in ("danger", "success", "primary") else sty(sv)
            nr.append({k: v for k, v in nb.items() if v})
        kb.append(nr)
    return {"inline_keyboard": kb}

async def _post(method, pl):
    async with ClientSession(timeout=ClientTimeout(total=30)) as s:
        async with s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=pl) as r:
            j = await r.json()
    if not j.get("ok"): raise RuntimeError(j.get("description"))
    return j["result"]

ICON_OFF = [0.0]   # time until which premium button icons are skipped (Telegram refused them)

def _btns(pl):
    return [b for r in (pl.get("reply_markup") or {}).get("inline_keyboard", []) for b in r]

def _drop_icons(pl):
    for b in _btns(pl):
        i = b.pop("icon_custom_emoji_id", None)
        if i: b["text"] = (_ID2E.get(i, "") + " " + b["text"]).strip()

async def raw_post(method, pl):
    if time.time() < ICON_OFF[0]: _drop_icons(pl)
    try: return await _post(method, pl)
    except RuntimeError as e:
        if "not modified" in str(e).lower() or not any(b.get("icon_custom_emoji_id") for b in _btns(pl)): raise
        log.warning("%s with premium button icons failed (%s) - retrying without", method, e)
        _drop_icons(pl)
        r = await _post(method, pl)
        ICON_OFF[0] = time.time() + 3600
        return r

async def react(chat, mid, emoji=None):
    try:
        await raw_post("setMessageReaction", {"chat_id": chat, "message_id": mid, "is_big": True,
                                              "reaction": [{"type": "emoji", "emoji": emoji or random.choice(START_REACTIONS)}]})
    except Exception as e: log.warning("react failed (%s)", e)

# ------------------------------------------------ log channel
LOGC, LOGOFF = int(LOG_CHANNEL or 0), set()
LOG_KINDS = {"start": "bot started", "user": "new users", "group": "groups added / removed",
             "hit": "filters triggered", "filter": "filters saved / deleted", "mod": "moderation"}

async def tolog(kind, text):
    if not LOGC or kind in LOGOFF: return
    try: await ui_send(LOGC, text)
    except Exception as e: log.warning("log channel send failed (%s) - /setlog again and make the bot admin there", e)

def L(kind, text): asyncio.create_task(tolog(kind, text))

def mlink(g, mid, uname=None):
    if uname: return f"https://t.me/{uname}/{mid}"
    if str(g).startswith("-100"): return f"https://t.me/c/{str(g)[4:]}/{mid}"

# ---------------------------------- button colours of a replied post (Bot API getUpdates sees the style, Pyrogram can't)
STYLES = {}
def _grab(msg, chat):
    rm = (msg or {}).get("reply_markup")
    if not rm: return
    d = {}
    for row in rm.get("inline_keyboard", []):
        for b in row:
            v = {k: b[f] for k, f in (("s", "style"), ("i", "icon_custom_emoji_id")) if b.get(f)}
            if v: d[(b.get("text"), b.get("url"))] = v
    STYLES[(chat, msg["message_id"])] = d
    while len(STYLES) > 1500: STYLES.pop(next(iter(STYLES)))

async def style_poll():
    off, warned = -1, False
    while True:
        try:
            ups = await raw_post("getUpdates", {"offset": off, "timeout": 20, "allowed_updates": ["message", "channel_post"]})
            for u in ups:
                off = u["update_id"] + 1
                msg = u.get("message") or u.get("channel_post")
                if not msg: continue
                cid = msg["chat"]["id"]
                _grab(msg, cid); _grab(msg.get("reply_to_message"), cid)
        except Exception as e:
            if not warned: log.warning("style poll: %s (button colours of replied posts may not copy)", e); warned = True
            await asyncio.sleep(8)

async def style_for(chat, mid, wait=3.0):
    t = time.time()
    while (chat, mid) not in STYLES and time.time() - t < wait: await asyncio.sleep(0.15)
    return STYLES.get((chat, mid), {})

def _lpo(url=None):
    if not url: return {"is_disabled": True}
    return {"url": url, "show_above_text": True, "prefer_large_media": True}

def _strip_pe(t):
    return re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', t or "")

_PE_SKIP = re.compile(r'(<tg-emoji[^>]*>.*?</tg-emoji>|<code>.*?</code>|<pre>.*?</pre>|<[^>]+>)', re.S)
_PE_ID, _PE_RX, _ID2E = {}, re.compile(r"(?!x)x"), {}

def pe_load(extra=None):
    global _PE_RX
    _PE_ID.clear(); _ID2E.clear()
    for d in (EMOJI_IDS, {f: i for i, f in PREMIUM_EMOJIS.values()}, extra or {}):
        for e, i in d.items():
            if not (e and i): continue
            b = e.replace("\ufe0f", "")
            for v in {e, b, b + "\ufe0f"}: _PE_ID[v] = str(i)
            _ID2E.setdefault(str(i), e)
    _PE_RX = re.compile("|".join(re.escape(e) for e in sorted(_PE_ID, key=len, reverse=True)) or r"(?!x)x")
pe_load()

def pz(t):
    if not t or not _PE_ID: return t
    parts, rx, ids = _PE_SKIP.split(t), _PE_RX, _PE_ID
    for n in range(0, len(parts), 2):
        parts[n] = rx.sub(lambda x: f'<tg-emoji emoji-id="{ids[x[0]]}">{x[0]}</tg-emoji>', parts[n])
    return "".join(parts)

async def pe_post(method, pl, key):
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
    try: await sc_ctx(q.message.chat.id, q.message.chat.type)
    except Exception: pass
    if q.message.chat.type == T.PRIVATE and not q.data.startswith("fj:") and not await fs_ok(c, q.from_user.id):
        return await q.answer(smallcaps("join the channel first, then send /start."), show_alert=True)
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

def start_kb(idx, uname, sid=0):
    c = BTN_COLOR
    return [
        [R(ch, cb=f"sb:img:{idx}:{sid}", s=c["letters"]) for ch in smallcaps(BOT_NAME)],
        [R(smallcaps("• add me to your group •"), url=f"https://t.me/{uname}?startgroup=true", s=c["add_group"])],
        [R(smallcaps("• commands •"), cb="sb:help", s=c["commands"]), R(smallcaps("• settings •"), cb="sb:set", s=c["settings"])],
        [R(smallcaps("• connections •"), cb="sb:conn", s=c["connections"]), R(smallcaps("• about •"), cb="sb:about", s=c["about"])],
        [R(ch, cb=f"sb:x:{sid}", s=c.get("close", "red")) for ch in smallcaps("close")],
    ]

SRC = {}
def sid_of(q, v=""):
    return int(v) if v.isdigit() and int(v) else SRC.get((q.message.chat.id, q.message.id), 0)

def sc_html(t):
    parts = _PE_SKIP.split(t or "")
    for n in range(0, len(parts), 2): parts[n] = smallcaps(parts[n])
    return "".join(parts)

BACK = lambda: [[R(smallcaps("◀ back"), cb="sb:back", s=BTN_COLOR["back"])]]

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

def owner_colors(rows):
    """bot owner's filters: buttons without a colour alternate blue, green, blue, green..."""
    i = 0
    for row in rows:
        for b in row:
            if not b.get("s"): b["s"] = "primary" if i % 2 == 0 else "success"
            i += 1

async def auto_pe(*msgs):
    """premium emojis found in a message the owner/admin uses for a filter are saved and reused everywhere"""
    found = [x for mm in msgs for x in custom_emojis(mm)]
    if not found: return
    for e, i in found: await em.update_one({"_id": e}, {"$set": {"id": i}}, upsert=True)
    await pe_reload()

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
    v = None
    try: v = (await asyncio.wait_for(c.get_chat(g), 5)).title
    except Exception: pass
    if not v:
        try: v = (await asyncio.wait_for(raw_post("getChat", {"chat_id": g}), 8)).get("title")
        except Exception: pass
    if not v:
        v = ((await ch.find_one({"_id": g}, {"title": 1})) or {}).get("title")
        if not v: return t[0] if t else smallcaps("unknown group")
    else: await ch.update_one({"_id": g}, {"$set": {"title": v}}, upsert=True)
    TITLES[g] = (v, time.time()); return v

async def conn_set(u, g, add=True):
    d = await cn.find_one({"_id": u}) or {}
    gs = [x for x in d.get("groups", []) if x != g]
    if add: gs.append(g)
    act = g if add else (d.get("active") if d.get("active") != g else (gs[-1] if gs else None))
    await cn.update_one({"_id": u}, {"$set": {"groups": gs, "active": act}}, upsert=True)
    if not add: await ch.update_one({"_id": g, "conn": u}, {"$unset": {"conn": "", "conn_name": ""}})

async def api_send(chat, reply_id, d):
    ft = d.get("ft")
    kb = raw_kb(d.get("b"))["inline_keyboard"]
    pl = {"chat_id": chat}
    if reply_id: pl["reply_parameters"] = {"message_id": reply_id, "allow_sending_without_reply": True}
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

async def sc_ctx(chat_id, ctype):
    SCON.set(not (await cfg(chat_id)).get("nsc") if ctype in GRP else True)

@app.on_message(group=-10)
async def _sc_hook(c, m): await sc_ctx(m.chat.id, m.chat.type)

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
            sm = await send_start(c, m.chat.id, START_IMGS[idx], start_txt(m.from_user), start_kb(idx, c.me.username, m.id), reply_id=m.id)
        else:
            sm = await ui_send(m.chat.id, start_txt(m.from_user), start_kb(0, c.me.username, m.id), reply_id=m.id)
        if sm: SRC[(m.chat.id, sm)] = m.id
        return
    await think(c, m)
    await ship(c, m, sc_html(HELP_TEXT))

async def sb_show(c, q, txt, rows):
    try: await edit_any(q.message.chat.id, q.message.id, txt, rows)
    except Exception as e:
        log.warning("sb_show: edit failed (%s) -> sending new", e)
        await ui_send(q.message.chat.id, txt, rows)

@cbq(r"^sb:")
async def sb_cb(c, q):
    p = q.data.split(":"); a = p[1]; chat = q.message.chat.id
    if a == "x":
        await q.answer()
        sid = sid_of(q, p[2] if len(p) > 2 else ""); SRC.pop((chat, q.message.id), None)
        ids = [q.message.id] + ([sid] if sid else [])
        try: await raw_post("deleteMessages", {"chat_id": chat, "message_ids": ids})
        except Exception:
            for i in ids:
                try: await raw_post("deleteMessage", {"chat_id": chat, "message_id": i})
                except Exception: pass
        return
    if a == "img":
        if not START_IMGS: return await q.answer(smallcaps("no images added yet."), show_alert=True)
        cur = int(p[2]) if len(p) > 3 and p[2].lstrip("-").isdigit() else -1
        idx = random.choice([i for i in range(len(START_IMGS)) if i != cur] or [0])
        await q.answer()
        await update_start(c, q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username, sid_of(q, p[3] if len(p) > 3 else "")))
        return
    if a == "back":
        await q.answer(); await qanim(q)
        if START_IMGS:
            idx = random.randrange(len(START_IMGS))
            await update_start(c, q, START_IMGS[idx], start_txt(q.from_user), start_kb(idx, c.me.username, sid_of(q)))
        else:
            await sb_show(c, q, start_txt(q.from_user), start_kb(0, c.me.username, sid_of(q)))
        return
    if a == "help":
        await q.answer(); await qanim(q)
        return await sb_show(c, q, sc_html(HELP_TEXT), BACK())
    if a == "about":
        await q.answer(); await qanim(q)
        txt = (f"<b>❓ {smallcaps('about')} {smallcaps(BOT_NAME)}</b>\n\n<blockquote>{smallcaps('i am an auto filter bot with premium features.')}</blockquote>\n"
               f"{maint_txt()}")
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
            sm = await style_for(m.chat.id, r.id)
            rows = [[{"text": x.text, "url": x.url, **sm.get((x.text, x.url), {})} for x in row if getattr(x, "url", None)] for row in kb]
            b = [row for row in rows if row] + b
    if m.from_user.id == OWNER: owner_colors(b)
    if sudo(m.from_user.id): await auto_pe(m, r)
    if not t and not fid: return await ship(c, m, f"<b>❌ {smallcaps('empty filter')}</b>\n\n<blockquote>{smallcaps('reply to a message/media or add some text.')}</blockquote>")
    if fid and len(t) > 1024: return await ship(c, m, f"<b>❌ {smallcaps('caption too long')}</b>\n\n<blockquote>{smallcaps('caption max is 1024 characters.')}</blockquote>")
    await fl.update_one({"g": g, "k": k}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b, "mt": mt}}, upsert=True)
    KW.pop(g, None)
    match_txt = f" ({smallcaps(mt)} {smallcaps('match')})" if mt != "word" else ""
    nb, nc = sum(len(x) for x in b), sum(1 for x in b for y in x if y.get("s"))
    ni = sum(1 for x in b for y in x if y.get("i"))
    col_txt = ""
    if nb: col_txt = f"\n🎨 {nc}/{nb} {smallcaps('buttons coloured')}" + ("" if nc else f"\n{smallcaps('colours not detected - use')} <code>/color {html.escape(k)} red green</code>")
    if ni: col_txt += f"\n✨ {ni} {smallcaps('premium emoji icon(s) kept')}"
    await ship(c, m, f"<b>✅ {smallcaps('filter saved')}</b>\n\n<blockquote>{smallcaps('keyword')}: <code>{html.escape(k)}</code>{match_txt}{col_txt}</blockquote>")
    L("filter", f"<b>➕ {smallcaps('filter saved')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('keyword')}: <code>{html.escape(k)}</code>\n{smallcaps('by')}: {m.from_user.mention} (<code>{m.from_user.id}</code>)</blockquote>")

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
        if k:
            await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
            L("filter", f"<b>🗑 {smallcaps('filter deleted')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('keyword')}: <code>{html.escape(k)}</code>\n{smallcaps('by')}: {q.from_user.mention}</blockquote>")
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
        L("filter", f"<b>🗑 {smallcaps('filter deleted')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('keyword')}: <code>{html.escape(k)}</code>\n{smallcaps('by')}: {m.from_user.mention}</blockquote>")
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
    L("filter", f"<b>🗑 {smallcaps('all filters deleted')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('count')}: {r.deleted_count}\n{smallcaps('by')}: {q.from_user.mention}</blockquote>")
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
    first = (await ch.find_one({"_id": g}) or {})
    fu = first.get("conn")
    if not fu:                                          # connections made before this rule existed
        o = await cn.find_one({"groups": g, "_id": {"$ne": u}})
        if o:
            fu = o["_id"]
            try: first["conn_name"] = (await c.get_users(fu)).first_name
            except Exception: pass
    if fu and fu != u and not sudo(u) and await cn.find_one({"_id": fu, "groups": g}) and await is_adm(c, g, fu):
        who = f'<a href="tg://user?id={fu}">{html.escape(first.get("conn_name") or str(fu))}</a>'
        return await reply(m, f"<b>❌ {smallcaps('already connected')}</b>\n\n<blockquote>{smallcaps('this group is already connected by')} {who} ({smallcaps('first connected')}).\n{smallcaps('only they can manage it from pm. ask them to /disconnect first.')}</blockquote>")
    await think(c, m)
    if m.chat.type in GRP and m.chat.title: TITLES[g] = (m.chat.title, time.time()); await ch.update_one({"_id": g}, {"$set": {"title": m.chat.title}}, upsert=True)
    await conn_set(u, g)
    await ch.update_one({"_id": g}, {"$set": {"conn": u, "conn_name": m.from_user.first_name or str(u)}}, upsert=True)
    if m.chat.type in GRP:
        try: await ui_send(u, f"<b>✅ {smallcaps('connected')}</b>\n\n<blockquote>{smallcaps('link alerts for')} <b>{html.escape(smallcaps(await title(c, g)))}</b> {smallcaps('will come here.')}</blockquote>")
        except Exception:
            bg(tmp(c, g, f"<b>🔔 {smallcaps('start me in pm')}</b>\n\n<blockquote>{m.from_user.mention}, {smallcaps('press start in my pm to receive link alerts and manage filters.')}</blockquote>", 45,
                   [[R(smallcaps("open pm"), url=f"https://t.me/{c.me.username}?start=alerts", s="primary")]]))
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
    rows = [[R((("✅ " if g == d.get("active") else "👥 ") + smallcaps(t))[:64], cb=f"cn:s:{g}"), R("🗑", cb=f"cn:d:{g}", s=BTN_COLOR["delete"])] for g, t in zip(gs, ts)]
    return rows or None

def conn_txt(has):
    if has: return f"<b>🔗 {smallcaps('your groups')}</b>\n\n<blockquote>{smallcaps('tap a group to select it, 🗑 to remove. ✅ = active group.')}</blockquote>"
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
            [R(f"🧹 {smallcaps('delete join/leave msgs')} {'⛔' if cf.get('nds') else '✅'}", cb=f"st:{g}:nds", s="red" if cf.get("nds") else "green")],
            [R(f"💬 {smallcaps('strict chat matching')} {'⛔' if cf.get('loose') else '✅'}", cb=f"st:{g}:loose", s="red" if cf.get("loose") else "green")],
            [R(f"🔤 {smallcaps('small caps')} {'⛔' if cf.get('nsc') else '✅'}", cb=f"st:{g}:nsc", s="red" if cf.get("nsc") else "green")],
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
    if k in ("al", "af", "am", "fd", "aw", "tm", "nsc", "nds", "loose"): await cfg_set(g, **{k: not cf.get(k)})
    if k == "nsc" and q.message.chat.type in GRP: SCON.set(not cf.get("nsc"))
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

BG = set()
def bg(coro):
    t = asyncio.create_task(coro); BG.add(t)
    def done(x):
        BG.discard(x)
        if not x.cancelled() and x.exception(): log.error("background task failed: %r", x.exception())
    t.add_done_callback(done); return t

async def tmp(c, g, text, secs=60, rows=None):
    mid = None
    try: mid = await ui_send(g, text, rows)
    except Exception as e:
        log.warning("notice via bot api failed in %s (%s) - trying pyrogram", g, e)
        try: mid = (await c.send_message(g, text, disable_web_page_preview=True)).id
        except Exception as e2:
            log.error("cannot send notice in %s: %s | %s - give the bot the 'send messages' right", g, e, e2)
            try: mid = (await c.send_message(g, plain(text) or ".")).id
            except Exception: return
    await asyncio.sleep(secs)
    try: await c.delete_messages(g, mid)
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
    L("mod", f"<b>🚫 {smallcaps('warn limit reached')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('user')}: {u.mention} (<code>{u.id}</code>)\n{smallcaps('action')}: {done}\n{smallcaps('reason')}: {html.escape(why)}</blockquote>")
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
    # Fixed: no backslash inside f-string expression
    if await is_adm(c, g, u.id):
        return await ship(c, m, f"<b>❌ {smallcaps('cannot warn')}</b>\n\n<blockquote>{smallcaps('i cannot warn admins.')}</blockquote>")
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

ADMC, NUDGE = {}, {}
async def gc_admins(c, g):
    """human admins of the group (cached 10 min) + bot owner/admins + users who /connect-ed it"""
    t = ADMC.get(g)
    if t and time.time() - t[1] < 600: ids = set(t[0])
    else:
        ids = set()
        try:
            async for mem in c.get_chat_members(g, filter=ChatMembersFilter.ADMINISTRATORS):
                if mem.user and not mem.user.is_bot: ids.add(mem.user.id)
        except Exception as e: log.warning("cannot list admins of %s: %s", g, e)
        ADMC[g] = (ids, time.time())
    ids.add(OWNER); ids.update(ADMINS)
    ids.update([d["_id"] async for d in cn.find({"groups": g})])          # who /connect-ed this group
    by = (await ch.find_one({"_id": g}) or {}).get("by")                   # who added the bot to it
    if by: ids.add(by)
    pri = {d["_id"] async for d in cn.find({"groups": g})} | ({by} if by else set())
    return ids, pri

async def report(c, g, m, found):
    u = m.from_user
    r = await db.linklog.find_one_and_update({"g": g, "u": u.id}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    warns = (await wn.find_one({"g": g, "u": u.id}) or {}).get("n", 0)
    when = (datetime.utcnow() + timedelta(minutes=int(os.environ.get("TZ_MIN", "330")))).strftime("%d %b %Y, %I:%M:%S %p")
    ls = "\n".join(f"• <code>{html.escape(x)}</code>" for x in found) or "-"
    body = html.escape((m.text or m.caption or "")[:300])
    text = (f"🚨 <b>{smallcaps('link deleted')}</b>\n\n"
            f"<blockquote><b>{smallcaps('group')}:</b> {html.escape(await title(c, g))} (<code>{g}</code>)\n<b>{smallcaps('time')}:</b> {when}</blockquote>\n"
            f"<blockquote><b>{smallcaps('user')}:</b> {u.mention}\n<b>{smallcaps('id')}:</b> <code>{u.id}</code>\n"
            f"<b>{smallcaps('username')}:</b> {'@' + u.username if u.username else smallcaps('none')}\n"
            f"<b>{smallcaps('premium')}:</b> {smallcaps('yes') if u.is_premium else smallcaps('no')}\n"
            f"<b>{smallcaps('links sent here')}:</b> {r['n']} | <b>{smallcaps('warnings')}:</b> {warns}</blockquote>\n"
            f"<blockquote><b>{smallcaps('link(s)')}:</b>\n{ls}\n\n<b>{smallcaps('message')}:</b>\n{body}</blockquote>")
    rows = [[R(f"🚫 {smallcaps('block')}", cb=f"lk:b:{g}:{u.id}", s="danger"), R(f"🔇 {smallcaps('mute')}", cb=f"lk:m:{g}:{u.id}", s="primary")],
            [R(f"👢 {smallcaps('kick')}", cb=f"lk:k:{g}:{u.id}", s="success"), R(f"❌ {smallcaps('cancel')}", cb=f"lk:x:{g}:{u.id}")],
            [R(f"👤 {smallcaps('open profile')}", url=f"tg://user?id={u.id}")]]
    sent, (ids, pri), failed = 0, await gc_admins(c, g), []
    for uid in ids:
        try: await ui_send(uid, text, rows); sent += 1
        except Exception as e:
            log.warning("link report: cannot PM %s (%s) - that person must press Start in my PM", uid, e)
            if uid in pri: failed.append(uid)
    log.info("link report for %s in %s sent to %s of %s people", u.id, g, sent, len(ids))

@cbq(r"^lk:")
async def lk_cb(c, q):
    _, a, g, u = q.data.split(":"); g, u = int(g), int(u)
    if not await is_adm(c, g, q.from_user.id, trust=True): return await q.answer(smallcaps("admins only."), show_alert=True)
    if a == "x":
        await q.answer(smallcaps("cancelled"))
        return await q.message.delete()
    try:
        if a == "m": await c.restrict_chat_member(g, u, ChatPermissions())
        else:
            await c.ban_chat_member(g, u)
            if a == "k": await c.unban_chat_member(g, u)
        res = {"m": smallcaps("muted"), "b": smallcaps("blocked"), "k": smallcaps("kicked")}[a]
    except Exception as e:
        return await q.answer(smallcaps("failed - i need the 'restrict members' right."), show_alert=True)
    await q.answer(res)
    L("mod", f"<b>🛡 {res}</b>\n\n<blockquote>{smallcaps('group')}: <code>{g}</code>\n{smallcaps('user')}: <code>{u}</code>\n{smallcaps('by')}: {q.from_user.mention} ({smallcaps('from link report')})</blockquote>")
    try: await edit_any(q.message.chat.id, q.message.id, f"<b>✅ {res}</b>\n\n<blockquote>{smallcaps('user')} <code>{u}</code> {res}.</blockquote>")
    except Exception: pass

async def punish(c, g, m, why):
    cf = await cfg(g)
    try: await m.delete()
    except Exception as e:
        log.warning("guard: cannot delete in %s (%s) - make the bot admin with 'Delete messages'", g, e); return False
    note, kb = f"<b>⚠️ {smallcaps('warning')}</b>\n\n<blockquote>{m.from_user.mention}, {why}. {smallcaps('your message was deleted.')}</blockquote>", None
    if cf.get("aw"):
        try: note, kb = await add_warn(c, g, m.from_user, why)
        except Exception as e: log.error("add_warn failed in %s: %s", g, e)
    log.info("guard: deleted a message of %s in %s (%s)", m.from_user.id, g, plain(why))
    bg(tmp(c, g, note, 60, kb))
    L("mod", f"<b>🛡 {smallcaps('message deleted')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(m.chat.title or '')} (<code>{g}</code>)\n{smallcaps('user')}: {m.from_user.mention} (<code>{m.from_user.id}</code>)\n{smallcaps('reason')}: {why}</blockquote>")
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
            L("mod", f"<b>🌊 {smallcaps('flood mute')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(m.chat.title or '')} (<code>{g}</code>)\n{smallcaps('user')}: {u.mention} (<code>{u.id}</code>)</blockquote>")
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
            bg(report(c, g, m, bad)); m.stop_propagation()

@app.on_message(filters.group & filters.service, group=10)
async def del_service(c, m):
    if (await cfg(m.chat.id)).get("nds"): return
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
    if kb:
        sm = await style_for(m.chat.id, m.id, 1.5)
        b = [r for r in ([{"text": x.text, "url": x.url, **sm.get((x.text, x.url), {})} for x in row if getattr(x, "url", None)] for row in kb) if r] + b
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
        if mt == "contains": return k.lower() in txt.lower()
        if mt == "regex": return re.search(k, txt, re.I) is not None
        return re.search(rf"(?<!\w){re.escape(k)}(?!\w)", txt, re.I) is not None
    except re.error: return False

def stem(w):
    w = w.lower()
    for suf, rep_ in (("ies", "y"), ("es", ""), ("s", "")):
        if len(w) > 3 and w.endswith(suf): return w[:-len(suf)] + rep_
    return w

def toks(t): return [stem(x) for x in re.findall(r"\w+", t.lower()) if len(x) >= 3]

def close_matches(txt, ks, n=5):
    mt = set(toks(txt))
    if not mt or len(txt) > 60 or len(txt.split()) > 8: return []
    out = []
    for k, kind in ks:
        if kind == "regex": continue
        kt = set(toks(k))
        if not kt: continue
        sc = 0
        if mt & kt and (mt <= kt or kt <= mt): sc = 1 + len(mt & kt) / max(len(mt), len(kt))
        else:
            r = difflib.SequenceMatcher(None, " ".join(sorted(mt)), " ".join(sorted(kt))).ratio()
            if r >= 0.82: sc = r
        if sc: out.append((sc, k))
    return [k for _, k in sorted(out, key=lambda x: (-x[0], x[1]))[:n]]

def nrm(t): return " ".join(re.sub(r"[^\w\s]", " ", (t or "").lower()).split())

def search_keys(q, ks, n=8):
    """every non-regex filter that shares words with the query, best first"""
    nq, mt, out = nrm(q), set(toks(q)), []
    for k, kind in ks:
        if kind == "regex": continue
        nk, kt, sc = nrm(k), set(toks(k)), 0
        if mt & kt:
            sc = 1 + len(mt & kt) / len(mt | kt)
            if mt <= kt or kt <= mt: sc += 2
        if nq and (nq in nk or nk in nq): sc = max(sc, 2.5)
        r = difflib.SequenceMatcher(None, nq, nk).ratio()
        if r >= 0.6: sc = max(sc, r)
        if sc: out.append((sc, k))
    return [k for _, k in sorted(out, key=lambda x: (-x[0], x[1]))[:n]]

SUG = {}
async def show_buttons(c, m, g, ks, head="did you mean", ttl=60):
    rows = [[R(k[:40], cb=f"fm:{g}:{kh(k)}:{m.id}", s="primary")] for k in ks]
    try:
        mid = await ui_send(m.chat.id, f"<b>🔎 {smallcaps(head)}</b>\n\n<blockquote>{smallcaps('tap the one you want and i will send it.')}</blockquote>", rows, reply_id=m.id)
        bg(_ttl(CQMsg(m.chat.id, m.chat.type, mid), ttl))
    except Exception as e: log.warning("suggest failed: %s", e)

async def suggest(c, m, g, n, ks):
    """chat mode: the message contains a keyword plus extra words (e.g. 'blue box s02') -> offer buttons"""
    u = m.from_user
    if not u or len(n.split()) > 8: return
    mset = set(n.split())
    cand = [k for k, mt in ks if mt == "word" and len(nrm(k)) >= 3 and set(nrm(k).split()) <= mset]
    if not cand: return
    now = time.time()
    if now - SUG.get((g, u.id), 0) < 20: return
    SUG[(g, u.id)] = now
    if len(SUG) > 2000: SUG.clear()
    await show_buttons(c, m, g, cand[:6])

async def deliver(c, m, g, k, reply_id=None):
    d = await fl.find_one({"g": g, "k": k})
    if not d: return
    if m.from_user and not await fs_ok(c, m.from_user.id):
        return await fs_prompt(c, m, f"fj:{g}:{kh(k)}:{m.id}", 45)
    rid = reply_id or (m.reply_to_message.id if m.reply_to_message else m.id)
    try:
        await c.send_chat_action(m.chat.id, ChatAction.TYPING)
        if anim_roll():
            loader = await ui_send(m.chat.id, f"<b><i>{smallcaps('finding...')}</i></b>", reply_id=m.id)
            await asyncio.sleep(0.8)
            await CQMsg(m.chat.id, m.chat.type, loader).delete()
    except Exception: pass
    await send(c, m, d, reply_id=rid)
    u, lk = m.from_user, mlink(g, m.id, m.chat.username)
    L("hit", f"<b>🎯 {smallcaps('filter triggered')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(m.chat.title or str(g))} (<code>{g}</code>)\n{smallcaps('keyword')}: <code>{html.escape(k)}</code>\n"
             f"{smallcaps('user')}: {u.mention + ' (<code>' + str(u.id) + '</code>)' if u else smallcaps('anonymous')}" + (f"\n<a href=\"{lk}\">{smallcaps('open message')}</a>" if lk else "") + "</blockquote>")

@app.on_message(filters.command(["s", "search"]))
async def search_cmd(c, m):
    if not m.from_user: return
    g = m.chat.id if m.chat.type in GRP else (await cn.find_one({"_id": m.from_user.id}) or {}).get("active")
    if not g:
        return await reply(m, f"<b>❌ {smallcaps('not connected')}</b>\n\n<blockquote>{smallcaps('send /connect in your group first.')}</blockquote>")
    q = (m.text or "").split(None, 1)
    q = q[1].strip() if len(q) > 1 else ""
    if not q:
        return await ship(c, m, f"<b>🔎 {smallcaps('search')}</b>\n\n<blockquote>{smallcaps('usage:')} <code>/s blue box</code></blockquote>")
    await think(c, m)
    ks, n = await kws(g), nrm(q)
    exact = next((k for k, mt in ks if mt == "word" and nrm(k) == n), None)
    if exact:
        await clear_ld(c, m.chat.id, m.from_user.id)
        return await deliver(c, m, g, exact, reply_id=m.id)
    res = search_keys(q, ks)
    await clear_ld(c, m.chat.id, m.from_user.id)
    if not res:
        mid = await reply(m, f"<b>📭 {smallcaps('no results')}</b>\n\n<blockquote>{smallcaps('nothing found for')} <code>{html.escape(q)}</code></blockquote>")
        return bg(_ttl(CQMsg(m.chat.id, m.chat.type, mid), 20))
    await show_buttons(c, m, g, res, "results", 120)

@cbq(r"^fm:")
async def fm_cb(c, q):
    _, g, h, mid = q.data.split(":"); g = int(g)
    if not await fs_ok(c, q.from_user.id): return await q.answer(smallcaps("join the channel first - use the filter again to get the join button."), show_alert=True)
    k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == h), None)
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if not d: return await q.answer(smallcaps("filter not found."), show_alert=True)
    await q.answer()
    d = dict(d)
    if d.get("b"): d["b"] = apply_default(d["b"])
    try: await api_send(q.message.chat.id, int(mid), d)
    except Exception as e: log.warning("fm send failed: %s", e)
    await q.message.delete()
    L("hit", f"<b>🎯 {smallcaps('filter picked from suggestions')}</b>\n\n<blockquote>{smallcaps('group')}: <code>{g}</code>\n{smallcaps('keyword')}: <code>{html.escape(k)}</code>\n{smallcaps('user')}: {q.from_user.mention} (<code>{q.from_user.id}</code>)</blockquote>")

@app.on_message(filters.group & filters.text & ~filters.regex(r"^[/!]"), group=10)
async def listen(c, m):
    g = m.chat.id
    if g not in SEEN:
        SEEN.add(g); await ch.update_one({"_id": g}, {"$set": {"type": "group"}}, upsert=True)
    ks, n = await kws(g), nrm(m.text)
    if (await cfg(g)).get("loose"):                      # old behaviour: keyword anywhere in a message
        for k, mt in ks:
            if hit(k, mt, m.text): return await deliver(c, m, g, k)
        cm = close_matches(m.text, ks)
        if cm and m.from_user and time.time() - SUG.get((g, m.from_user.id), 0) > 20:
            SUG[(g, m.from_user.id)] = time.time(); await show_buttons(c, m, g, cm)
        return
    for k, mt in ks:                                      # strict: the whole message must be the keyword
        if (mt == "word" and nrm(k) == n) or (mt != "word" and hit(k, mt, m.text)): return await deliver(c, m, g, k)
    await suggest(c, m, g, n, ks)

@app.on_chat_member_updated()
async def tracker(c, u):
    n, o = u.new_chat_member, u.old_chat_member
    x = n or o
    if u.chat.type == T.PRIVATE or not x or not x.user.is_self: return
    kind = smallcaps("channel" if u.chat.type == T.CHANNEL else "group")
    who = u.from_user.mention + f" (<code>{u.from_user.id}</code>)" if u.from_user else smallcaps("unknown")
    if n and n.status not in (S.LEFT, S.BANNED):
        new = not await ch.find_one({"_id": u.chat.id})
        await ch.update_one({"_id": u.chat.id}, {"$set": {"type": "channel" if u.chat.type == T.CHANNEL else "group", "title": u.chat.title, **({"by": u.from_user.id} if u.from_user and not u.from_user.is_bot else {})}}, upsert=True)
        if new:
            try: cnt = await c.get_chat_members_count(u.chat.id)
            except Exception: cnt = "?"
            L("group", f"<b>➕ {smallcaps('added to a new')} {kind}</b>\n\n<blockquote>{smallcaps('name')}: {html.escape(u.chat.title or '')}\n{smallcaps('id')}: <code>{u.chat.id}</code>\n{smallcaps('username')}: {'@' + u.chat.username if u.chat.username else smallcaps('private')}\n{smallcaps('members')}: {cnt}\n{smallcaps('added by')}: {who}</blockquote>")
    else:
        await ch.delete_one({"_id": u.chat.id}); SEEN.discard(u.chat.id)
        L("group", f"<b>➖ {smallcaps('removed from')} {kind}</b>\n\n<blockquote>{smallcaps('name')}: {html.escape(u.chat.title or '')}\n{smallcaps('id')}: <code>{u.chat.id}</code>\n{smallcaps('by')}: {who}</blockquote>")

async def bsend(msg, cid, user=False):
    for _ in range(3):
        try: await msg.copy(cid); return True
        except Exception as e:
            n = type(e).__name__
            if n == "FloodWait": await asyncio.sleep(e.value + 1); continue
            if n in ("ChannelPrivate", "ChatWriteForbidden", "UserNotParticipant", "ChatAdminRequired"): await ch.delete_one({"_id": cid})
            elif n in ("UserIsBlocked", "InputUserDeactivated", "PeerIdInvalid", "UserDeactivated"): await us.delete_one({"_id": cid})
            return False
    return False

BCQ = {}
@app.on_message(filters.command("broadcast"))
async def broadcast(c, m):
    if not (m.from_user and m.from_user.id == OWNER): return          # bot owner only
    r = m.reply_to_message
    if not r: return await reply(m, f"<b>❌ {smallcaps('reply missing')}</b>\n\n<blockquote>{smallcaps('reply to the message you want to broadcast with /broadcast')}</blockquote>")
    ng, nu = await ch.count_documents({}), await us.count_documents({})
    rows = [[R(f"👥 {smallcaps('groups & channels')} ({ng})", cb="bc:g", s="primary")],
            [R(f"👤 {smallcaps('bot pms (users)')} ({nu})", cb="bc:u", s="primary")],
            [R(f"📢 {smallcaps('both')} ({ng + nu})", cb="bc:b", s="success"), R(f"❌ {smallcaps('cancel')}", cb="bc:x", s="danger")]]
    pid = await ui_send(m.chat.id, f"<b>📢 {smallcaps('broadcast')}</b>\n\n<blockquote>{smallcaps('where should i send this message?')}\n"
                                   f"• {smallcaps('groups: groups/channels where i am added')}\n• {smallcaps('pms: users who started me')}</blockquote>", rows, reply_id=m.id)
    BCQ[pid] = r
    while len(BCQ) > 10: BCQ.pop(next(iter(BCQ)))

@cbq(r"^bc:")
async def bc_cb(c, q):
    if q.from_user.id != OWNER: return await q.answer(smallcaps("bot owner only."), show_alert=True)
    a, chat, mid = q.data.split(":")[1], q.message.chat.id, q.message.id
    r = BCQ.pop(mid, None)
    if a == "x":
        await q.answer()
        return await edit_any(chat, mid, f"<b>❌ {smallcaps('cancelled')}</b>")
    if not r: return await q.answer(smallcaps("expired - send /broadcast again."), show_alert=True)
    await q.answer()
    gs = [d["_id"] async for d in ch.find()] if a in ("g", "b") else []
    ps = [d["_id"] async for d in us.find()] if a in ("u", "b") else []
    sg = set(gs)
    todo = [(i, False) for i in gs] + [(i, True) for i in ps if i not in sg]
    ok = {False: 0, True: 0}; bad = 0
    for n, (i, isu) in enumerate(todo, 1):
        if await bsend(r, i, isu): ok[isu] += 1
        else: bad += 1
        await asyncio.sleep(0.1 if isu else 0.3)
        if n % 25 == 0:
            try: await edit_any(chat, mid, f"<b>📢 {smallcaps('broadcasting')}</b>\n\n<blockquote>{n}/{len(todo)}</blockquote>")
            except Exception: pass
    await edit_any(chat, mid, f"<b>✅ {smallcaps('broadcast done')}</b>\n\n<blockquote>👥 {smallcaps('groups/channels')}: {ok[False]}\n👤 {smallcaps('users')}: {ok[True]}\n❌ {smallcaps('failed')}: {bad}</blockquote>")
    L("start", f"<b>📢 {smallcaps('broadcast sent')}</b>\n\n<blockquote>{smallcaps('groups/channels')}: {ok[False]} | {smallcaps('users')}: {ok[True]} | {smallcaps('failed')}: {bad}</blockquote>")

# ---------------------------------------------------- force subscribe
FSUB, FSLINK, FSTITLE = 0, "", ""
FSC, FSP = {}, {}

async def fs_ok(c, uid, fresh=False):
    if not FSUB or sudo(uid): return True
    if not fresh and time.time() - FSC.get(uid, 0) < 300: return True
    try: st_ = (await raw_post("getChatMember", {"chat_id": FSUB, "user_id": uid}))
    except Exception as e:
        if "not found" in str(e).lower() and "user" in str(e).lower(): return False
        log.warning("force-sub check failed (%s) - add the bot as admin in the channel. letting the user in.", e); return True
    ok = st_.get("status") in ("creator", "administrator", "member") or (st_.get("status") == "restricted" and st_.get("is_member"))
    if ok: FSC[uid] = time.time()
    return bool(ok)

def fs_rows(cb):
    return [[R(f"📢 {smallcaps('join channel')}", url=FSLINK, s="primary")], [R(f"✅ {smallcaps('i joined')}", cb=cb, s="success")]]

async def fs_prompt(c, m, cb, ttl=None):
    u = m.from_user
    if time.time() - FSP.get((m.chat.id, u.id), 0) < 20: return
    FSP[(m.chat.id, u.id)] = time.time()
    if len(FSP) > 2000: FSP.clear()
    try:
        mid = await ui_send(m.chat.id, f"<b>🔒 {smallcaps('join required')}</b>\n\n<blockquote>{smallcaps('please join')} <b>{html.escape(FSTITLE or smallcaps('our channel'))}</b> {smallcaps('to use this bot, then tap')} <b>✅ {smallcaps('i joined')}</b>.</blockquote>", fs_rows(cb), reply_id=m.id)
        if ttl: bg(_ttl(CQMsg(m.chat.id, m.chat.type, mid), ttl))
    except Exception as e: log.warning("fsub prompt failed: %s", e)

@app.on_message(group=-8)
async def pm_gate(c, m):
    if m.chat.type != T.PRIVATE or not m.from_user: return
    uid = m.from_user.id
    r = await us.update_one({"_id": uid}, {"$set": {"n": m.from_user.first_name}, "$setOnInsert": {"t": time.time()}}, upsert=True)
    if r.upserted_id is not None:
        u = m.from_user
        L("user", f"<b>👤 {smallcaps('new user')}</b>\n\n<blockquote>{smallcaps('name')}: {u.mention}\n{smallcaps('id')}: <code>{u.id}</code>\n{smallcaps('username')}: {'@' + u.username if u.username else smallcaps('none')}\n{smallcaps('total users')}: {await us.count_documents({})}</blockquote>")
    if not await fs_ok(c, uid):
        await fs_prompt(c, m, "fj:0:0:0")
        m.stop_propagation()

@cbq(r"^fj:")
async def fj_cb(c, q):
    _, g, h, mid = q.data.split(":"); g = int(g)
    if not await fs_ok(c, q.from_user.id, fresh=True): return await q.answer(smallcaps("you have not joined yet."), show_alert=True)
    await q.answer(smallcaps("verified ✅"))
    if not g:
        return await edit_any(q.message.chat.id, q.message.id, f"<b>✅ {smallcaps('verified')}</b>\n\n<blockquote>{smallcaps('thanks! send /start now.')}</blockquote>")
    k = next((x for x in [d["k"] async for d in fl.find({"g": g}, {"k": 1})] if kh(x) == h), None)
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if d:
        d = dict(d)
        if d.get("b"): d["b"] = apply_default(d["b"])
        try: await api_send(q.message.chat.id, int(mid), d)
        except Exception as e: log.warning("fj send failed: %s", e)
    await q.message.delete()

@app.on_message(filters.command("fsub"))
async def fsub_cmd(c, m):
    global FSUB, FSLINK, FSTITLE
    if not (m.from_user and m.from_user.id == OWNER): return
    a = m.command[1:]
    if not a:
        cur = f"<code>{FSUB}</code> - {html.escape(FSTITLE)}" if FSUB else smallcaps("off")
        return await reply(m, f"<b>🔒 {smallcaps('force subscribe')}</b>\n\n<blockquote>{smallcaps('now')}: {cur}\n{smallcaps('set')}: <code>/fsub -100xxxxxxxxxx</code> {smallcaps('or')} <code>/fsub @channel</code>\n{smallcaps('private channel: add an invite link after the id.')}\n{smallcaps('turn off')}: <code>/fsub off</code>\n{smallcaps('the bot must be admin in the channel.')}</blockquote>")
    if a[0].lower() in ("off", "none", "0"): FSUB, FSLINK, FSTITLE = 0, "", ""
    else:
        try: ch_ = await raw_post("getChat", {"chat_id": int(a[0]) if a[0].lstrip("-").isdigit() else a[0]})
        except Exception as e: return await reply(m, f"<b>❌ {smallcaps('cannot find that channel')}</b>\n\n<blockquote><code>{html.escape(str(e))}</code>\n{smallcaps('add me as admin there first.')}</blockquote>")
        try: await raw_post("getChatMember", {"chat_id": ch_["id"], "user_id": m.from_user.id})
        except Exception as e: return await reply(m, f"<b>❌ {smallcaps('i need admin rights there')}</b>\n\n<blockquote><code>{html.escape(str(e))}</code></blockquote>")
        link = a[1] if len(a) > 1 and a[1].startswith("http") else (f"https://t.me/{ch_['username']}" if ch_.get("username") else ch_.get("invite_link") or "")
        if not link:
            try: link = await raw_post("exportChatInviteLink", {"chat_id": ch_["id"]})
            except Exception: return await reply(m, f"<b>❌ {smallcaps('no invite link')}</b>\n\n<blockquote>{smallcaps('send it too:')} <code>/fsub {ch_['id']} https://t.me/+xxxx</code></blockquote>")
        FSUB, FSLINK, FSTITLE = ch_["id"], link, ch_.get("title") or ""
        FSC.clear()
    await st.update_one({"_id": "bot"}, {"$set": {"fsub": FSUB, "fsub_link": FSLINK, "fsub_title": FSTITLE}}, upsert=True)
    await reply(m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{smallcaps('force subscribe')}: " + (f"<b>{html.escape(FSTITLE)}</b> (<code>{FSUB}</code>)" if FSUB else smallcaps("off")) + "</blockquote>")

@app.on_message(filters.command("stats"))
async def stats(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    g, h = await ch.count_documents({"type": "group"}), await ch.count_documents({"type": "channel"})
    await ship(c, m, f"<b>📊 {smallcaps('bot statistics')}</b>\n\n<blockquote>{smallcaps('groups')}: {g}\n{smallcaps('channels')}: {h}\n{smallcaps('filters')}: {await fl.count_documents({})}\n{smallcaps('users')}: {await us.count_documents({})}\n{smallcaps('admins')}: {len(ADMINS)}</blockquote>")

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

# ---------------------------------------------------- log channel commands
def lg_view():
    t = (f"<b>📋 {smallcaps('log channel')}</b>\n\n<blockquote>{smallcaps('channel')}: " + (f"<code>{LOGC}</code>" if LOGC else smallcaps("not set")) +
         f"\n{smallcaps('set it with')} <code>/setlog -100xxxxxxxxxx</code> {smallcaps('(or /setlog inside a group, /setlog off)')}\n"
         f"{smallcaps('the bot must be admin in the channel.')}</blockquote>\n<blockquote>{smallcaps('tap to choose what is logged.')}</blockquote>")
    return t, [[R(f"{'✅' if k not in LOGOFF else '⛔'} {smallcaps(v)}", cb=f"lg:{k}", s="green" if k not in LOGOFF else "red")] for k, v in LOG_KINDS.items()]

@app.on_message(filters.command("setlog"))
async def setlog(c, m):
    global LOGC
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    a = m.command[1] if len(m.command) > 1 else ""
    if a.lower() in ("off", "none", "0"): LOGC = 0
    else:
        try: cid = int(a) if a else (m.chat.id if m.chat.type != T.PRIVATE else None)
        except ValueError: cid = None
        if cid is None: return await ship(c, m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('usage:')} <code>/setlog -100xxxxxxxxxx</code>\n{smallcaps('or send /setlog inside the group you want to use.')}</blockquote>")
        try: await ui_send(cid, f"<b>✅ {smallcaps('log channel connected')}</b>\n\n<blockquote>{smallcaps('i will send bot logs here.')}</blockquote>")
        except Exception as e: return await ship(c, m, f"<b>❌ {smallcaps('cannot post there')}</b>\n\n<blockquote><code>{html.escape(str(e))}</code>\n{smallcaps('add me to that channel as an admin first.')}</blockquote>")
        LOGC = cid
    await st.update_one({"_id": "bot"}, {"$set": {"log": LOGC}}, upsert=True)
    t, rows = lg_view()
    await ship(c, m, t, rows)

@app.on_message(filters.command("logs"))
async def logs_cmd(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    await think(c, m)
    t, rows = lg_view()
    await ship(c, m, t, rows)

@cbq(r"^lg:")
async def lg_cb(c, q):
    if not sudo(q.from_user.id): return await q.answer(smallcaps("owner only."), show_alert=True)
    k = q.data.split(":")[1]
    if k in LOG_KINDS: LOGOFF.symmetric_difference_update({k})
    await st.update_one({"_id": "bot"}, {"$set": {"off": sorted(LOGOFF)}}, upsert=True)
    await q.answer()
    t, rows = lg_view()
    try: await edit_any(q.message.chat.id, q.message.id, t, rows)
    except Exception: pass

# ---------------------------------------------------- per-group small caps
@app.on_message(filters.command("smallcaps"))
async def sc_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    a = (m.command[1].lower() if len(m.command) > 1 else "")
    on = (a == "on") if a in ("on", "off") else bool((await cfg(g)).get("nsc"))
    await cfg_set(g, nsc=not on)
    if m.chat.type in GRP: SCON.set(on)
    await think(c, m)
    await ship(c, m, f"<b>✅ {smallcaps('updated')}</b>\n\n<blockquote>{smallcaps('small caps text is now')} <b>{smallcaps('on') if on else smallcaps('off')}</b> {smallcaps('for this group.')}</blockquote>")

# ---------------------------------------------------- filter export / import / copy
FKEYS = ("k", "t", "ft", "fid", "b", "mt")

def clean_filters(items):
    out = []
    for d in items[:1000]:
        if not isinstance(d, dict) or not isinstance(d.get("k"), str) or not d["k"] or len(d["k"]) > 100: continue
        mt = d.get("mt") if d.get("mt") in ("word", "regex", "contains") else "word"
        if mt == "regex":
            try: re.compile(d["k"])
            except re.error: continue
        ft = d.get("ft") if d.get("ft") in MEDIA else None
        if ft and not isinstance(d.get("fid"), str): continue
        t = d.get("t") if isinstance(d.get("t"), str) else ""
        if not t and not ft: continue
        b = []
        for row in d.get("b") or []:
            if not isinstance(row, list): continue
            r = [{k: x[k] for k in ("text", "url", "s", "i") if isinstance(x.get(k), str)} for x in row if isinstance(x, dict) and isinstance(x.get("text"), str) and isinstance(x.get("url"), str)]
            if r: b.append(r)
        out.append({"k": d["k"] if mt == "regex" else d["k"].lower(), "t": t, "ft": ft, "fid": d.get("fid") if ft else None, "b": b, "mt": mt})
    return out

async def merge_filters(g, items, replace=False):
    if replace: await fl.delete_many({"g": g})
    for d in items: await fl.update_one({"g": g, "k": d["k"]}, {"$set": {x: d[x] for x in FKEYS if x != "k"}}, upsert=True)
    KW.pop(g, None)

@app.on_message(filters.command("export"))
async def export_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    ds = [{x: d.get(x) for x in FKEYS} async for d in fl.find({"g": g})]
    if not ds: return await ship(c, m, f"<b>📭 {smallcaps('no filters')}</b>\n\n<blockquote>{smallcaps('nothing to export.')}</blockquote>")
    bio = io.BytesIO(json.dumps({"v": 1, "filters": ds}, ensure_ascii=False, indent=1).encode("utf-8")); bio.name = "filters.json"
    await clear_ld(c, m.chat.id, m.from_user.id)
    await c.send_document(m.chat.id, bio, file_name="filters.json", reply_to_message_id=m.id,
                          caption=f"<b>📤 {smallcaps('filters export')}</b> ({len(ds)})\n<blockquote>{smallcaps('reply to this file with /import in another group to copy these filters.')}</blockquote>")
    L("filter", f"<b>📤 {smallcaps('filters exported')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('count')}: {len(ds)}\n{smallcaps('by')}: {m.from_user.mention}</blockquote>")

def done_txt(n, total, replace):
    return f"<b>✅ {smallcaps('imported')}</b>\n\n<blockquote>{n} {smallcaps('filter(s) added')}" + (f" ({total - n} {smallcaps('skipped as invalid')})" if total > n else "") + (f"\n{smallcaps('old filters were replaced.')}" if replace else "") + "</blockquote>"

@app.on_message(filters.command("import"))
async def import_cmd(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    r = m.reply_to_message
    if not r or not r.document:
        return await ship(c, m, f"<b>❌ {smallcaps('file missing')}</b>\n\n<blockquote>{smallcaps('reply to an exported filters.json file with')} <code>/import</code>\n{smallcaps('add')} <code>/import replace</code> {smallcaps('to delete the old filters first.')}</blockquote>")
    if (r.document.file_size or 0) > 3_000_000: return await ship(c, m, f"<b>❌ {smallcaps('file too big')}</b>")
    try:
        bio = await c.download_media(r, in_memory=True)
        raw_items = json.loads(bytes(bio.getbuffer()).decode("utf-8"))["filters"]
        assert isinstance(raw_items, list)
    except Exception:
        return await ship(c, m, f"<b>❌ {smallcaps('invalid file')}</b>\n\n<blockquote>{smallcaps('send a file made by /export.')}</blockquote>")
    items, rep_ = clean_filters(raw_items), "replace" in [x.lower() for x in m.command[1:]]
    if not items: return await ship(c, m, f"<b>❌ {smallcaps('nothing to import')}</b>")
    await merge_filters(g, items, rep_)
    await ship(c, m, done_txt(len(items), len(raw_items), rep_))
    L("filter", f"<b>📥 {smallcaps('filters imported')}</b>\n\n<blockquote>{smallcaps('group')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('count')}: {len(items)}\n{smallcaps('by')}: {m.from_user.mention}</blockquote>")

@app.on_message(filters.command("copyfilters"))
async def copyfilters(c, m):
    g = await target(c, m)
    if g is None: return
    await think(c, m)
    try: src = int(m.command[1])
    except Exception: return await ship(c, m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote><code>/copyfilters -100sourcegroupid</code> [<code>replace</code>]\n{smallcaps('you must be an admin of both groups.')}</blockquote>")
    cd = await cn.find_one({"_id": m.from_user.id}) or {}
    if src == g or not await is_adm(c, src, m.from_user.id, trust=(m.chat.type == T.PRIVATE and src in cd.get("groups", []))):
        return await ship(c, m, f"<b>❌ {smallcaps('not allowed')}</b>\n\n<blockquote>{smallcaps('you must be an admin of the source group (and it must be a different group).')}</blockquote>")
    raw_items = [{x: d.get(x) for x in FKEYS} async for d in fl.find({"g": src})]
    items, rep_ = clean_filters(raw_items), "replace" in [x.lower() for x in m.command[2:]]
    if not items: return await ship(c, m, f"<b>📭 {smallcaps('no filters')}</b>\n\n<blockquote>{smallcaps('the source group has no filters.')}</blockquote>")
    await merge_filters(g, items, rep_)
    await ship(c, m, done_txt(len(items), len(raw_items), rep_))
    L("filter", f"<b>📥 {smallcaps('filters copied')}</b>\n\n<blockquote>{smallcaps('from')}: <code>{src}</code>\n{smallcaps('to')}: {html.escape(await title(c, g))} (<code>{g}</code>)\n{smallcaps('count')}: {len(items)}\n{smallcaps('by')}: {m.from_user.mention}</blockquote>")

# ---------------------------------------------------- premium emojis
def custom_emojis(msg):
    if not msg: return []
    t, ents = msg.text or msg.caption or "", msg.entities or msg.caption_entities or []
    u, out = t.encode("utf-16-le"), []
    for e in ents:
        if e.type == E.CUSTOM_EMOJI and getattr(e, "custom_emoji_id", None):
            ch_ = u[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le", "ignore")
            if ch_ and (ch_, str(e.custom_emoji_id)) not in out: out.append((ch_, str(e.custom_emoji_id)))
    return out

async def pe_reload(): pe_load({d["_id"]: d["id"] async for d in em.find()})

@app.on_message(filters.command("addemoji"))
async def addemoji(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    found = custom_emojis(m) + custom_emojis(m.reply_to_message)
    if not found and len(m.command) > 2 and m.command[2].isdigit(): found = [(m.command[1], m.command[2])]
    if not found:
        return await reply(m, f"<b>❌ {smallcaps('no premium emojis found')}</b>\n\n<blockquote>{smallcaps('send /addemoji followed by premium emojis, or reply /addemoji to a message that has them.')}\n"
                              f"{smallcaps('or')}: <code>/addemoji 🔥 5431449001695289639</code></blockquote>")
    for e, i in found: await em.update_one({"_id": e}, {"$set": {"id": i}}, upsert=True)
    await pe_reload()
    ls = "\n".join(f'<tg-emoji emoji-id="{i}">{e}</tg-emoji> → <code>{i}</code>' for e, i in found)
    txt = f"<b>✅ {smallcaps('premium emojis saved')}</b> ({len(found)})\n\n<blockquote>{ls}</blockquote>\n{smallcaps('they now replace the normal emojis in all bot menus.')}"
    try: await raw_post("sendMessage", {"chat_id": m.chat.id, "text": txt, "parse_mode": "HTML",
                                         "reply_parameters": {"message_id": m.id, "allow_sending_without_reply": True}})
    except RuntimeError as e:
        await reply(m, f"<b>⚠️ {smallcaps('saved, but telegram refused to show them')}</b>\n\n<blockquote><code>{html.escape(str(e))}</code>\n"
                       f"{smallcaps('telegram only lets a bot send premium emojis if the bot has a username bought on fragment. until then the normal emojis are shown.')}</blockquote>")

@app.on_message(filters.command("emojis"))
async def emojis_cmd(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    seen, ls = set(), []
    for e, i in _PE_ID.items():
        b = e.replace("\ufe0f", "")
        if b in seen: continue
        seen.add(b); ls.append(f"{e} <code>{i}</code>")
    await reply(m, f"<b>✨ {smallcaps('premium emojis')}</b> ({len(ls)})\n\n<blockquote>{chr(10).join(ls) or smallcaps('none yet - use /addemoji')}</blockquote>")

@app.on_message(filters.command("delemoji"))
async def delemoji(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    e = (m.command[1] if len(m.command) > 1 else "") or (custom_emojis(m.reply_to_message)[:1] or [("", "")])[0][0]
    if not e: return await reply(m, f"<b>❌ {smallcaps('usage error')}</b>\n\n<blockquote>{smallcaps('usage: /delemoji emoji')}</blockquote>")
    b = e.replace("\ufe0f", "")
    n = (await em.delete_many({"_id": {"$in": [e, b, b + "\ufe0f"]}})).deleted_count
    await pe_reload()
    await reply(m, f"<b>✅ {smallcaps('removed')}</b>\n\n<blockquote>{html.escape(e)} ({n}) - {smallcaps('emojis set in config.py stay until removed there.')}</blockquote>")

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
    global LOGC, FSUB, FSLINK, FSTITLE
    await web_start(); asyncio.create_task(ping()); asyncio.create_task(style_poll())
    log.info("connecting to Telegram...")
    await app.start(); log.info("STARTED as @%s - send /start to it", (await app.get_me()).username)
    try:
        await fl.create_index([("g", 1), ("k", 1)], unique=True)
        ADMINS.update([d["_id"] async for d in ad.find()])
        pe_load({d["_id"]: d["id"] async for d in em.find()})
        bs = await st.find_one({"_id": "bot"}) or {}
        if "log" in bs: LOGC = int(bs["log"] or 0)
        LOGOFF.update(bs.get("off", []))
        FSUB, FSLINK, FSTITLE = int(bs.get("fsub") or 0), bs.get("fsub_link", ""), bs.get("fsub_title", "")
        async for d in cn.find({}, {"_id": 1}): await us.update_one({"_id": d["_id"]}, {"$setOnInsert": {"t": 0}}, upsert=True)
        log.info("MongoDB OK")
    except Exception as e:
        log.error("MongoDB problem (filters will not work): %s", e)
    me = await app.get_me()
    await tolog("start", f"<b>🟢 {smallcaps('bot started')}</b>\n\n<blockquote>@{me.username}\n{smallcaps('time')}: <code>{datetime.now().strftime('%d %b %Y %H:%M:%S')}</code>\n"
                         f"{smallcaps('groups')}: {await ch.count_documents({'type': 'group'})} | {smallcaps('users')}: {await us.count_documents({})}</blockquote>")
    await idle(); await app.stop()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())