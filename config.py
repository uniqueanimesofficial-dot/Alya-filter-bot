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
    "add_group": "green", # ADD ME TO YOUR GROUP
    "commands": "blue",   # COMMANDS
    "settings": "blue",   # SETTINGS
    "connections": "blue",# CONNECTIONS
    "about": "blue",      # ABOUT
    "back": "red",        # ◀ BACK buttons
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
    "sparkle": ("5368324170671202286", "✨"),
    "heart": ("5368324170671202286", "❤️"), # Replace ID with your own!
}

# ==========================================
#  📜 HELP MENU TEXT
# ==========================================
HELP_TEXT = """<b>🤖 Filter Bot</b>

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
• <code>/broadcast</code> (reply) | <code>/stats</code> | <code>/addadmin</code> id | <code>/rmadmin</code> id
• <code>/info</code> - view user info
</blockquote>
"""

# ==========================================
#  🔤 SMALL CAPS DICTIONARY
# ==========================================
SC = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ꜰ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ",
      "m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}