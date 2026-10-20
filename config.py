import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
#  🔑 CORE BOT SETTINGS (Environment Vars)
# ==========================================
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "FilterBot")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

if not (API_ID and API_HASH and BOT_TOKEN and MONGO_URI and OWNER_ID):
    raise SystemExit("❌ Missing variables! Set API_ID, API_HASH, BOT_TOKEN, MONGO_URI, and OWNER_ID in your .env file")

# ==========================================
#  🎨 UI & BRANDING SETTINGS
# ==========================================
BOT_NAME = "ALYA"
MAINTAINER = "Pʀɨʍɛ ƈօʀɛ"
MAINTAINER_URL = "https://t.me/PrimeCoreHQ"

# Start menu images (Direct URLs for preview mode, or Telegram File IDs)
START_IMGS = [
    "https://files.catbox.moe/sla8rd.jpg",
    "https://files.catbox.moe/9tqcsy.jpg",
    "https://files.catbox.moe/t3c8bc.jpg",
    "https://files.catbox.moe/xh6fdc.jpg",
]

# Emojis that cycle during the /start animation
LOAD_EMOJIS = ["⚙️", "🔄", "⏳", "✨"]

# 🎨 Button Colours: "red" | "green" | "blue" | "" (default/transparent)
BTN_COLOR = {
    "letters": "",        # ᴀ ʟ ʏ ᴀ letter buttons
    "add_group": "green", # ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
    "commands": "blue",   # ᴄᴏᴍᴍᴀɴᴅs
    "settings": "blue",   # sᴇᴛᴛɪɴɢs
    "connections": "blue",# ᴄᴏɴɴᴇᴄᴛɪᴏɴs
    "about": "blue",      # ᴀʙᴏᴜᴛ
    "back": "red",        # ◀ ʙᴀᴄᴋ buttons
    "delete": "red",      # 🗑 delete buttons
    "set": "blue",        # ✏️ Set / change buttons
}

# Default colour for ALL filter & welcome buttons saved by users
# Leave as "" for default, or set to "red", "green", "blue"
DEFAULT_BTN_COLOR = ""

# ==========================================
#  ✨ PREMIUM EMOJIS (IDs & Fallbacks)
# ==========================================
# Get IDs from @RawDataBot. The second item is the fallback emoji for non-premium users.
# Add your own here! Format: "name": ("ID", "Fallback")
PREMIUM_EMOJIS = {
    "wave": ("5368324170671202286", "👋"),
    "fire": ("5431449001695289639", "🔥"),
    "settings": ("5318940267334622539", "⚙️"),
    "heart": ("5368324170671202286", "❤️"), # Replace ID with your own!
}

# ==========================================
#  📜 HELP MENU TEXT
# ==========================================
HELP_TEXT = """<b>🤖 ꜰɪʟᴛᴇʀ ʙᴏᴛ</b>

<blockquote>
<b>👥 ᴀᴅᴍɪɴs</b> (ɢʀᴏᴜᴘ, ᴏʀ ᴘᴍ ᴀꜰᴛᴇʀ /connect)
• <code>/filter</code> ᴡᴏʀᴅ - ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍsɢ/ᴍᴇᴅɪᴀ ᴛᴏ sᴀᴠᴇ ɪᴛ
• <code>/filter</code> "two words" ᴛᴇxᴛ - ᴛᴇxᴛ ꜰɪʟᴛᴇʀ
• <code>/filters</code> - ʟɪsᴛ ᴡɪᴛʜ 🗑 ᴅᴇʟᴇᴛᴇ ʙᴜᴛᴛᴏɴs
• <code>/settings</code> - ᴘʀᴏᴛᴇᴄᴛɪᴏɴ ᴘᴀɴᴇʟ (ᴀɴᴛɪ-ʟɪɴᴋ, ꜰʟᴏᴏᴅ, ᴇᴛᴄ.)
• <code>/allowlink</code> domain.com - ʟɪɴᴋs ᴛʜᴀᴛ ᴀɴᴛɪ-ʟɪɴᴋ ɪɢɴᴏʀᴇs
• <code>/warn</code> (ʀᴇᴘʟʏ) ʀᴇᴀsᴏɴ | <code>/unwarn</code> | <code>/warns</code>
• <code>/setwel</code> - ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ ᴇᴅɪᴛᴏʀ (ᴘᴍ ᴏɴʟʏ)
</blockquote>

<blockquote>
<b>⚙️ ꜰɪʟᴛᴇʀ ᴍᴏᴅᴇs &amp; ʙᴜᴛᴛᴏɴs</b>
• <code>regex:^hi</code> ᴏʀ <code>contains:price</code> (ᴅᴇꜰᴀᴜʟᴛ = ᴡʜᴏʟᴇ ᴡᴏʀᴅ)
• ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘᴏsᴛ (ᴍᴇᴅɪᴀ + ʙᴜᴛᴛᴏɴs) ᴡɪᴛʜ <code>/filter any words</code>
• ʙᴜᴛᴛᴏɴs: <code>[Name](buttonurl:https://x.com)</code>
  ᴀᴅᴅ <code>:same</code> (sᴀᴍᴇ ʀᴏᴡ), <code>:red</code> <code>:green</code> <code>:blue</code>
• <code>/color</code> ᴡᴏʀᴅ ʀᴇᴅ ɢʀᴇᴇɴ - sᴇᴛ ʙᴜᴛᴛᴏɴ ᴄᴏʟᴏᴜʀs
</blockquote>

<blockquote>
<b>👑 ʙᴏᴛ ᴏᴡɴᴇʀ / ᴀᴅᴍɪɴs</b>
• <code>/broadcast</code> (ʀᴇᴘʟʏ) | <code>/stats</code> | <code>/addadmin</code> ɪᴅ | <code>/rmadmin</code> ɪᴅ
• <code>/info</code> - ᴠɪᴇᴡ ᴜsᴇʀ ɪɴꜰᴏ
</blockquote>
"""

# ==========================================
#  🔤 SMALL CAPS DICTIONARY
# ==========================================
SC = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ꜰ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ",
      "m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}
