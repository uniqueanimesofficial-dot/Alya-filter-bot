import asyncio, html, logging, os, re
asyncio.set_event_loop(asyncio.new_event_loop())  # Pyrogram needs a loop at import time (Python 3.12+)
from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.enums import ChatAction, ChatMemberStatus as S, ChatType as T, MessageEntityType as E, ParseMode
from pyrogram.errors import ChannelPrivate, ChatWriteForbidden, FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M

load_dotenv(); env = os.environ.get
API_ID, API_HASH, TOKEN, MONGO = int(env("API_ID", 0)), env("API_HASH"), env("BOT_TOKEN"), env("MONGO_URI")
OWNER = int(env("OWNER_ID", 0))
assert API_ID and API_HASH and TOKEN and MONGO and OWNER, "set API_ID, API_HASH, BOT_TOKEN, MONGO_URI, OWNER_ID"
logging.basicConfig(level=logging.INFO); log = logging.getLogger("fb")

db = AsyncIOMotorClient(MONGO)[env("DB_NAME", "FilterBot")]
fl, cn, ch, ad, st = db.filters, db.connections, db.chats, db.admins, db.settings
app = Client("fb", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN, parse_mode=ParseMode.HTML, in_memory=True)

GRP = (T.GROUP, T.SUPERGROUP)
MEDIA = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")
KW, LINK, ADMINS, SEEN = {}, set(), set(), set()
BTN, TAG = re.compile(r"\[(.+?)\]\(buttonurl:(.+?)((?::(?:same|red|green|blue))*)\)"), re.compile(r"<[^>]+>")
STY = {"red": "danger", "green": "success", "blue": "primary"}  # Telegram button styles
plain = lambda s: html.unescape(TAG.sub("", s)).strip()

HELP = """<b>Filter Bot</b>
<b>Admins</b> (group, or PM after /connect)
/filter word - reply to a msg/media to save it
/filter "two words" text - text filter
/filters - list | /del word - delete | /delall - delete all (owner)
/antilink on|off - delete links from members
Reply to a post (media + buttons) with <code>/filter any words</code> - media, caption and buttons are copied
Buttons: <code>[Name](buttonurl:https://x.com)</code> - add after the url: <code>:same</code> (same row), <code>:red</code> <code>:green</code> <code>:blue</code> (colour)
/color word red green - set button colours of a saved filter (Telegram can't pass colours when copying)
/connect (in group) or /connect -100id (in PM) | /connections | /disconnect
<b>Bot owner/admins</b>
/broadcast (reply) - send to all groups &amp; channels | /stats
/addadmin id | /rmadmin id | /admins (owner only)"""


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


def kwsplit(s):
    s = (s or "").strip()
    m = re.match(r'^(?:"|&quot;|“)(.+?)(?:"|&quot;|”)\s*(.*)$', s, re.S)
    if m: k, b = m[1], m[2]
    else:
        p = s.split(None, 1)
        k, b = (p[0], p[1] if len(p) > 1 else "") if p else ("", "")
    return plain(k).lower(), b.strip()


def sudo(u): return u == OWNER or u in ADMINS


async def is_adm(c, cid, uid, owner=False, trust=False):
    if sudo(uid): return True
    try: m = await c.get_chat_member(cid, uid)
    except Exception: return trust  # trust=True: group unreachable right now (e.g. after restart) -> rely on saved connection
    return m.status in ((S.OWNER,) if owner else (S.OWNER, S.ADMINISTRATOR))


async def target(c, m, owner=False):
    if not m.from_user: return None
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        g = (await cn.find_one({"_id": u}) or {}).get("active")
        if not g:
            await m.reply_text("Not connected. Send /connect in your group, or /connect -100id here.")
            return None
    if await is_adm(c, g, u, owner, trust=(m.chat.type not in GRP and not owner)): return g
    await m.reply_text("Only the group owner can do this." if owner else "Admins only.")
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
    """Send via the Bot API over HTTPS - needed for coloured buttons (Pyrogram 2.0.x can't send `style`)."""
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


# ---------------------------------------------------------------- basics
@app.on_message(group=-1)
async def debug_log(c, m):  # shows in Render logs whenever a message reaches the bot
    log.info("update: chat=%s user=%s text=%r", m.chat.id, m.from_user and m.from_user.id, (m.text or m.caption or "")[:40])


@app.on_message(filters.command(["start", "help"]))
async def start(c, m):
    await c.send_chat_action(m.chat.id, ChatAction.TYPING)
    await m.reply_text(HELP)


# --------------------------------------------------------------- filters
@app.on_message(filters.command(["filter", "add"]))
async def add(c, m):
    g = await target(c, m)
    if g is None: return
    p = (m.text.html if m.text else "").split(None, 1)
    arg = p[1] if len(p) > 1 else ""
    k, rest = kwsplit(arg)
    r, ft, fid = m.reply_to_message, None, None
    if r and not arg.lstrip().startswith(('"', "&quot;", "“")):
        k, rest = plain(arg).lower(), ""  # replying: everything after /filter is the keyword (multi-word OK)
    if not k or len(k) > 64: return await m.reply_text("Usage: reply to a message with /filter keyword\nor /filter word text")
    t, b = parse(rest)
    if r:
        for x in MEDIA:
            if getattr(r, x, None): ft, fid = x, getattr(r, x).file_id; break
        body = r.text or r.caption
        bt, bb = parse(body.html if body else "")
        t, b = bt or t, bb + b
        kb = getattr(r.reply_markup, "inline_keyboard", None)  # copy the real inline buttons of the replied message
        if kb:
            rows = [[{"text": x.text, "url": x.url} for x in row if getattr(x, "url", None)] for row in kb]
            b = [row for row in rows if row] + b
    if not t and not fid: return await m.reply_text("Reply to a message/media or add some text.")
    if fid and len(t) > 1024: return await m.reply_text("Caption max is 1024 characters.")
    await fl.update_one({"g": g, "k": k}, {"$set": {"t": t, "ft": ft, "fid": fid, "b": b}}, upsert=True)
    KW.pop(g, None)
    await m.reply_text(f"Filter <code>{k}</code> saved.")


@app.on_message(filters.command("filters"))
async def lst(c, m):
    g = await target(c, m)
    if g is None: return
    ks = sorted([d["k"] async for d in fl.find({"g": g}, {"k": 1})])
    await m.reply_text(f"<b>Filters in {await title(c, g)}</b> ({len(ks)})\n\n" + "\n".join(f"• <code>{k}</code>" for k in ks) if ks else "No filters yet.")


@app.on_message(filters.command(["del", "stop"]))
async def rm(c, m):
    g = await target(c, m)
    if g is None: return
    p = (m.text.html or "").split(None, 1)
    k, _ = kwsplit(p[1] if len(p) > 1 else "")
    if not k: return await m.reply_text("Usage: /del word")
    r = await fl.delete_one({"g": g, "k": k}); KW.pop(g, None)
    await m.reply_text("Deleted." if r.deleted_count else "No such filter.")


@app.on_message(filters.command("delall"))
async def delall(c, m):
    g = await target(c, m, owner=True)
    if g is None: return
    await m.reply_text("Delete <b>all</b> filters?", reply_markup=M([[B("Yes", callback_data=f"da:y:{g}"), B("Cancel", callback_data="da:n:0")]]))


@app.on_callback_query(filters.regex(r"^da:"))
async def delall_cb(c, q):
    _, a, g = q.data.split(":"); g = int(g)
    if a != "y": return await q.message.edit_text("Cancelled.")
    if not await is_adm(c, g, q.from_user.id, True): return await q.answer("Owner only.", show_alert=True)
    r = await fl.delete_many({"g": g}); KW.pop(g, None)
    await q.message.edit_text(f"Deleted {r.deleted_count} filter(s).")


# ----------------------------------------------------------- connections
@app.on_message(filters.command("color"))
async def color(c, m):
    """/color keyword red green blue none ... -> colour of each button of a filter, in order."""
    g = await target(c, m)
    if g is None: return
    p = (m.text.html or "").split(None, 1)
    k, rest = kwsplit(p[1] if len(p) > 1 else "")
    cols = rest.lower().replace(",", " ").split()
    d = await fl.find_one({"g": g, "k": k}) if k else None
    if not k or not cols: return await m.reply_text('Usage: /color keyword red green blue none\n(one colour per button, in order; use quotes for multi-word keywords: <code>/color "my word" red green</code>)')
    if not d or not d.get("b"): return await m.reply_text("No such filter, or it has no buttons.")
    i = 0
    for row in d["b"]:
        for b in row:
            col = cols[i] if i < len(cols) else None
            i += 1
            if col in STY: b["s"] = STY[col]
            else: b.pop("s", None)
    await fl.update_one({"g": g, "k": k}, {"$set": {"b": d["b"]}})
    await m.reply_text(f"Button colours updated for <code>{k}</code>.")


@app.on_message(filters.command("connect"))
async def connect(c, m):
    if not m.from_user: return
    u = m.from_user.id
    if m.chat.type in GRP: g = m.chat.id
    else:
        if len(m.command) < 2:
            return await m.reply_text("Best way: send /connect <b>inside your group</b>.\nOr here: <code>/connect -100xxxxxxxxxx</code>")
        try: g = int(m.command[1])
        except ValueError: return await m.reply_text("The group id must be a number like <code>-1001234567890</code>.")
        try: await c.get_chat(g)
        except Exception as e:
            return await m.reply_text(f"I can't reach that group ({type(e).__name__}).\nMake sure I'm added to it, then send /connect <b>inside the group</b> instead.")
    if not await is_adm(c, g, u): return await m.reply_text("You must be an admin of that group to connect it.")
    await conn_set(u, g)
    await m.reply_text(f"Connected to <b>{await title(c, g)}</b>. Manage filters from my PM.")


@app.on_message(filters.command("disconnect"))
async def disconnect(c, m):
    if not m.from_user: return
    g = m.chat.id if m.chat.type in GRP else (await cn.find_one({"_id": m.from_user.id}) or {}).get("active")
    if g: await conn_set(m.from_user.id, g, False)
    await m.reply_text("Disconnected." if g else "Not connected.")


async def conn_kb(c, u):
    d = await cn.find_one({"_id": u}) or {}
    rows = [[B(("✅ " if g == d.get("active") else "") + await title(c, g), callback_data=f"cn:s:{g}"), B("🗑", callback_data=f"cn:d:{g}")] for g in d.get("groups", [])]
    return M(rows) if rows else None


@app.on_message(filters.command("connections") & filters.private)
async def conns(c, m):
    kb = await conn_kb(c, m.from_user.id)
    await m.reply_text("Your groups (tap to select, 🗑 to remove):" if kb else "No connections.", reply_markup=kb)


@app.on_callback_query(filters.regex(r"^cn:"))
async def conn_cb(c, q):
    _, a, g = q.data.split(":"); g, u = int(g), q.from_user.id
    if a == "s": await cn.update_one({"_id": u}, {"$set": {"active": g}})
    else: await conn_set(u, g, False)
    await q.answer("Done")
    kb = await conn_kb(c, u)
    await (q.message.edit_reply_markup(kb) if kb else q.message.edit_text("No connections left."))


# -------------------------------------------------------------- antilink
@app.on_message(filters.command("antilink") & filters.group)
async def antilink_cmd(c, m):
    if not m.from_user or not await is_adm(c, m.chat.id, m.from_user.id): return
    a = (m.command[1:] or [""])[0].lower()
    if a not in ("on", "off"): return await m.reply_text(f"Usage: /antilink on|off (now {'on' if m.chat.id in LINK else 'off'})")
    (LINK.add if a == "on" else LINK.discard)(m.chat.id)
    await st.update_one({"_id": m.chat.id}, {"$set": {"al": a == "on"}}, upsert=True)
    await m.reply_text(f"Anti-link <b>{a}</b>. I need the 'delete messages' right.")


async def warn(c, g, who):
    w = await c.send_message(g, f"{who} links are not allowed here.")
    await asyncio.sleep(5)
    await w.delete()


@app.on_message(filters.group & ~filters.service, group=5)
async def antilink(c, m):
    g = m.chat.id
    if g not in LINK or not m.from_user: return
    if not any(e.type in (E.URL, E.TEXT_LINK) for e in (m.entities or []) + (m.caption_entities or [])): return
    if await is_adm(c, g, m.from_user.id): return
    try: await m.delete()
    except Exception: return
    asyncio.create_task(warn(c, g, m.from_user.mention))
    m.stop_propagation()


# --------------------------------------------------------- keyword trigger
@app.on_message(filters.group & filters.text & ~filters.regex(r"^[/!]"), group=10)
async def listen(c, m):
    g = m.chat.id
    if g not in SEEN:
        SEEN.add(g); await ch.update_one({"_id": g}, {"$set": {"type": "group"}}, upsert=True)
    if g not in KW: KW[g] = sorted([d["k"] async for d in fl.find({"g": g}, {"k": 1})], key=len, reverse=True)
    for k in KW[g]:
        if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", m.text, re.I):
            d = await fl.find_one({"g": g, "k": k})
            if d:
                await c.send_chat_action(g, ChatAction.TYPING)  # "typing..." animation
                await asyncio.sleep(1)
                await send(c, m, d)
            return


# ------------------------------------------------ track groups & channels
@app.on_chat_member_updated()
async def tracker(c, u):
    n, o = u.new_chat_member, u.old_chat_member
    x = n or o
    if u.chat.type == T.PRIVATE or not x or not x.user.is_self: return
    if n and n.status not in (S.LEFT, S.BANNED):
        await ch.update_one({"_id": u.chat.id}, {"$set": {"type": "channel" if u.chat.type == T.CHANNEL else "group"}}, upsert=True)
    else:
        await ch.delete_one({"_id": u.chat.id}); SEEN.discard(u.chat.id)


# ------------------------------------------------- owner / admins / broadcast
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
    if not m.reply_to_message: return await m.reply_text("Reply to the message you want to broadcast with /broadcast")
    ids = [d["_id"] async for d in ch.find()]
    s, ok = await m.reply_text(f"Broadcasting to {len(ids)} chats..."), 0
    for i in ids:
        ok += await bsend(m.reply_to_message, i)
        await asyncio.sleep(0.3)
    await s.edit_text(f"Broadcast done: ✅ {ok} sent, ❌ {len(ids) - ok} failed.")


@app.on_message(filters.command("stats"))
async def stats(c, m):
    if not (m.from_user and sudo(m.from_user.id)): return
    g, h = await ch.count_documents({"type": "group"}), await ch.count_documents({"type": "channel"})
    await m.reply_text(f"Groups: {g}\nChannels: {h}\nFilters: {await fl.count_documents({})}\nAdmins: {len(ADMINS)}")


@app.on_message(filters.command(["addadmin", "rmadmin"]))
async def admin_cmd(c, m):
    if not m.from_user or m.from_user.id != OWNER: return
    r = m.reply_to_message
    try: u = r.from_user.id if r and r.from_user else int(m.command[1])
    except Exception: return await m.reply_text("Usage: /addadmin user_id (or reply to a user)")
    add = m.command[0] == "addadmin"
    if add: await ad.update_one({"_id": u}, {"$set": {"a": 1}}, upsert=True); ADMINS.add(u)
    else: await ad.delete_one({"_id": u}); ADMINS.discard(u)
    await m.reply_text(f"{'Added' if add else 'Removed'} admin <code>{u}</code>")


@app.on_message(filters.command("admins"))
async def admins(c, m):
    if m.from_user and sudo(m.from_user.id):
        await m.reply_text(f"Owner: <code>{OWNER}</code>\nAdmins: " + (", ".join(f"<code>{a}</code>" for a in ADMINS) or "none"))


# ------------------------------------------------------ render keep-alive + main
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
        ADMINS.update([d["_id"] async for d in ad.find()]); LINK.update([d["_id"] async for d in st.find({"al": True})])
        log.info("MongoDB OK")
    except Exception as e:
        log.error("MongoDB problem (filters will not work): %s", e)
    await idle(); await app.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
