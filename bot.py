import asyncio
import logging

from pyrogram import Client, filters, idle
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database as db
from config import API_HASH, API_ID, BOT_TOKEN
from utils import keyword_matches, parse_buttons, split_keyword

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("filterbot")

app = Client(
    "filterbot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML,
)

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
MEDIA_TYPES = ("photo", "video", "animation", "document", "audio", "voice", "video_note", "sticker")
NO_CAPTION = ("sticker", "video_note")

# group_id -> keywords sorted longest first (invalidated whenever filters change)
KW_CACHE: dict[int, list[str]] = {}

HELP_TEXT = """<b>Filter Bot</b>

Save text / media filters with inline buttons. When someone sends the keyword in the group, I reply with it.

<b>Admin commands</b> (group, or PM after /connect)
• <code>/filter keyword</code> (reply to a message / media) - save a filter
• <code>/filter keyword your text here</code> - save a text filter
• <code>/filter "multi word keyword" ...</code> - keyword with spaces
• <code>/filters</code> - list filters
• <code>/del keyword</code> - delete a filter
• <code>/delall</code> - delete all filters (group owner only)

<b>Buttons</b> (put in the text/caption or after the keyword)
<code>[Google](buttonurl:https://google.com)</code>
<code>[One](buttonurl:https://a.com)</code> + <code>[Two](buttonurl:https://b.com:same)</code> -> same row

<b>Connect</b>
• Send <code>/connect</code> in your group, then manage filters from my PM
• or in PM: <code>/connect -100xxxxxxxxxx</code> (group id)
• <code>/connections</code> - switch / remove groups
• <code>/disconnect</code> - disconnect

Text supports HTML formatting (bold, italic, links, spoilers) because it's copied from your message."""


# ---------------------------------------------------------------- helpers
async def is_admin(client: Client, chat_id: int, user_id: int, owner_only: bool = False) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    allowed = (ChatMemberStatus.OWNER,) if owner_only else (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    return member.status in allowed


async def get_target(client: Client, message: Message, owner_only: bool = False):
    """Return the group id the command should act on, or None (after replying with why)."""
    if not message.from_user:
        return None
    uid = message.from_user.id

    if message.chat.type in GROUP_TYPES:
        if not await is_admin(client, message.chat.id, uid, owner_only):
            await message.reply_text("Only the group owner can do this." if owner_only else "Only admins can use this command.")
            return None
        return message.chat.id

    gid = await db.get_active(uid)
    if not gid:
        await message.reply_text(
            "You're not connected to any group.\nSend <code>/connect</code> in your group, or "
            "<code>/connect -100xxxxxxxxxx</code> here."
        )
        return None
    if not await is_admin(client, gid, uid, owner_only):
        await message.reply_text("You're no longer an admin (or owner) of the connected group.")
        return None
    return gid


def extract_media(msg: Message):
    for t in MEDIA_TYPES:
        m = getattr(msg, t, None)
        if m:
            return t, m.file_id
    return None, None


def build_markup(rows):
    if not rows:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(b["text"], url=b["url"]) for b in row] for row in rows])


async def refresh_cache(group_id: int):
    KW_CACHE[group_id] = await db.get_keywords(group_id)


async def get_cached_keywords(group_id: int):
    if group_id not in KW_CACHE:
        await refresh_cache(group_id)
    return KW_CACHE[group_id]


async def send_filter(client: Client, message: Message, data: dict):
    markup = build_markup(data.get("buttons"))
    text = data.get("text") or None
    ft, fid = data.get("file_type"), data.get("file_id")
    kw = dict(chat_id=message.chat.id, reply_to_message_id=message.id, reply_markup=markup)

    for _ in range(2):
        try:
            if not ft:
                await client.send_message(text=text or "​", disable_web_page_preview=True, **kw)
            else:
                send = {
                    "photo": client.send_photo,
                    "video": client.send_video,
                    "animation": client.send_animation,
                    "document": client.send_document,
                    "audio": client.send_audio,
                    "voice": client.send_voice,
                    "video_note": client.send_video_note,
                    "sticker": client.send_sticker,
                }[ft]
                extra = {} if ft in NO_CAPTION else {"caption": text}
                await send(**{ft: fid}, **extra, **kw)
            return
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            log.warning("could not send filter in %s: %s", message.chat.id, e)
            return


async def group_title(client: Client, gid: int) -> str:
    try:
        return (await client.get_chat(gid)).title or str(gid)
    except Exception:
        return str(gid)


# --------------------------------------------------------------- commands
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    await message.reply_text(HELP_TEXT, disable_web_page_preview=True)


@app.on_message(filters.command("help"))
async def help_cmd(client: Client, message: Message):
    await message.reply_text(HELP_TEXT, disable_web_page_preview=True)


@app.on_message(filters.command(["filter", "add"]))
async def add_filter_cmd(client: Client, message: Message):
    gid = await get_target(client, message)
    if gid is None:
        return

    raw = message.text.html if message.text else ""
    parts = raw.split(None, 1)
    keyword, rest = split_keyword(parts[1] if len(parts) > 1 else "")
    if not keyword:
        return await message.reply_text(
            "Usage:\n<code>/filter keyword</code> (reply to a message/media)\n"
            "<code>/filter keyword text [Button](buttonurl:https://example.com)</code>"
        )
    if len(keyword) > 64:
        return await message.reply_text("Keyword is too long (max 64 characters).")

    rest_text, rest_btns = parse_buttons(rest)
    reply = message.reply_to_message
    file_type = file_id = None

    if reply:
        file_type, file_id = extract_media(reply)
        body = reply.text or reply.caption
        body_text, body_btns = parse_buttons(body.html if body else "")
        text = body_text or rest_text
        buttons = body_btns + rest_btns
    else:
        text, buttons = rest_text, rest_btns

    if not text and not file_id:
        return await message.reply_text("Give me some text or reply to a message/media to save as the filter.")
    if file_id and file_type not in NO_CAPTION and len(text) > 1024:
        return await message.reply_text("Captions are limited to 1024 characters. Shorten the text.")

    await db.save_filter(gid, keyword, text, file_type, file_id, buttons)
    await refresh_cache(gid)

    label = f"<b>{await group_title(client, gid)}</b>" if message.chat.type == ChatType.PRIVATE else "this group"
    await message.reply_text(f"Filter <code>{keyword}</code> saved in {label}.")


@app.on_message(filters.command(["filters", "viewfilters"]))
async def list_filters_cmd(client: Client, message: Message):
    gid = await get_target(client, message)
    if gid is None:
        return
    kws = sorted(await db.get_keywords(gid))
    if not kws:
        return await message.reply_text("No filters here yet. Add one with /filter.")
    body = "\n".join(f"• <code>{k}</code>" for k in kws)
    title = await group_title(client, gid)
    await message.reply_text(f"<b>Filters in {title}</b> ({len(kws)})\n\n{body}")


@app.on_message(filters.command(["del", "stop"]))
async def del_filter_cmd(client: Client, message: Message):
    gid = await get_target(client, message)
    if gid is None:
        return
    raw = message.text.html if message.text else ""
    parts = raw.split(None, 1)
    keyword, _ = split_keyword(parts[1] if len(parts) > 1 else "")
    if not keyword:
        return await message.reply_text("Usage: <code>/del keyword</code>")
    if await db.delete_filter(gid, keyword):
        await refresh_cache(gid)
        await message.reply_text(f"Filter <code>{keyword}</code> deleted.")
    else:
        await message.reply_text("No such filter.")


@app.on_message(filters.command("delall"))
async def delall_cmd(client: Client, message: Message):
    gid = await get_target(client, message, owner_only=True)
    if gid is None:
        return
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Yes, delete all", callback_data=f"delall:yes:{gid}"),
          InlineKeyboardButton("Cancel", callback_data="delall:no:0")]]
    )
    await message.reply_text("Delete <b>all</b> filters? This cannot be undone.", reply_markup=kb)


@app.on_callback_query(filters.regex(r"^delall:"))
async def delall_cb(client: Client, query: CallbackQuery):
    _, choice, gid = query.data.split(":")
    if choice != "yes":
        return await query.message.edit_text("Cancelled.")
    gid = int(gid)
    if not await is_admin(client, gid, query.from_user.id, owner_only=True):
        return await query.answer("Only the group owner can do this.", show_alert=True)
    n = await db.delete_all_filters(gid)
    await refresh_cache(gid)
    await query.message.edit_text(f"Deleted {n} filter(s).")


# ------------------------------------------------------------ connections
@app.on_message(filters.command("connect"))
async def connect_cmd(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id

    if message.chat.type in GROUP_TYPES:
        gid = message.chat.id
    else:
        args = message.command[1:]
        if not args:
            return await message.reply_text(
                "Send <code>/connect</code> in your group, or here: <code>/connect -100xxxxxxxxxx</code>"
            )
        try:
            gid = int(args[0])
        except ValueError:
            return await message.reply_text("Group id must be a number like <code>-1001234567890</code>.")
        try:
            await client.get_chat(gid)
        except Exception:
            return await message.reply_text("I can't see that group. Add me to it first and check the id.")

    if not await is_admin(client, gid, uid):
        return await message.reply_text("You must be an admin of that group to connect it.")

    await db.add_connection(uid, gid)
    title = await group_title(client, gid)

    if message.chat.type in GROUP_TYPES:
        await message.reply_text("Connected! You can now manage this group's filters from my PM.")
        try:
            await client.send_message(uid, f"Connected to <b>{title}</b>. Use /filter, /filters, /del here.")
        except Exception:
            pass  # user hasn't started the bot yet
    else:
        await message.reply_text(f"Connected to <b>{title}</b>.")


@app.on_message(filters.command("disconnect"))
async def disconnect_cmd(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if message.chat.type in GROUP_TYPES:
        gid = message.chat.id
    else:
        gid = await db.get_active(uid)
    if not gid:
        return await message.reply_text("You're not connected to any group.")
    await db.remove_connection(uid, gid)
    await message.reply_text("Disconnected.")


async def connections_markup(client: Client, uid: int):
    groups, active = await db.get_connections(uid)
    if not groups:
        return None, None
    rows = []
    for gid in groups:
        title = await group_title(client, gid)
        mark = "✅ " if gid == active else ""
        rows.append([
            InlineKeyboardButton(f"{mark}{title}", callback_data=f"conn:sel:{gid}"),
            InlineKeyboardButton("🗑", callback_data=f"conn:dis:{gid}"),
        ])
    return InlineKeyboardMarkup(rows), active


@app.on_message(filters.command("connections") & filters.private)
async def connections_cmd(client: Client, message: Message):
    markup, _ = await connections_markup(client, message.from_user.id)
    if not markup:
        return await message.reply_text("No connected groups. Use /connect in your group first.")
    await message.reply_text("Your groups (tap to select, 🗑 to remove):", reply_markup=markup)


@app.on_callback_query(filters.regex(r"^conn:"))
async def connections_cb(client: Client, query: CallbackQuery):
    _, action, gid = query.data.split(":")
    gid, uid = int(gid), query.from_user.id
    if action == "sel":
        await db.set_active(uid, gid)
        await query.answer("Selected")
    else:
        await db.remove_connection(uid, gid)
        await query.answer("Removed")
    markup, _ = await connections_markup(client, uid)
    if markup:
        await query.message.edit_reply_markup(markup)
    else:
        await query.message.edit_text("No connected groups left.")


# --------------------------------------------------------- keyword trigger
@app.on_message(filters.group & filters.text & ~filters.regex(r"^[/!]"), group=10)
async def keyword_listener(client: Client, message: Message):
    gid = message.chat.id
    keywords = await get_cached_keywords(gid)
    if not keywords:
        return
    for kw in keywords:
        if keyword_matches(kw, message.text):
            data = await db.get_filter(gid, kw)
            if data:
                await send_filter(client, message, data)
            return


# -------------------------------------------------------------------- main
async def main():
    await db.init_db()
    await app.start()
    me = await app.get_me()
    log.info("Started as @%s", me.username)
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
