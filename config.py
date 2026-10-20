import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
#  🔑 CORE BOT SETTINGS
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
    "https://i.ibb.co/237ZFmGc/c-VVvvq-Pc.jpg",
    "https://i.ibb.co/Bvg3P2t/NJk-Mma6j.jpg",
    # Add your other ibb links here
]

# Emojis that cycle during the /start animation
LOAD_EMOJIS = ["⚙️", "🔄", "⏳", "✨"]

#  Button Colours: "red" | "green" | "blue" | "" (default/transparent)
BTN_COLOR = {
    "letters": "",        # ᴀ ʟ ʏ ᴀ letter buttons
    "add_group": "green", # ᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
    "commands": "blue",   # ᴄᴏᴍᴍᴀɴᴅs
    "settings": "blue",   # sᴇᴛᴛɪɴɢs
    "connections": "blue",# ᴄᴏɴɴᴇᴄᴛɪᴏɴs
    "about": "blue",      # ʙᴏᴜᴛ
    "back": "red",        # ◀ ʙᴄᴋ buttons
    "delete": "red",      # 🗑 delete buttons
    "set": "blue",        # ✏️ Set / change buttons
}

# Default colour for ALL filter & welcome buttons saved by users
DEFAULT_BTN_COLOR = ""

# ==========================================
#  ✨ PREMIUM EMOJIS (IDs & Fallbacks)
# ==========================================
# Get IDs from @RawDataBot. The second item is the fallback emoji for non-premium users.
PREMIUM_EMOJIS = {
    "wave": ("5368324170671202286", "👋"),
    "fire": ("5431449001695289639", ""),
    "settings": ("5318940267334622539", "⚙️"),
    "heart": ("5368324170671202286", "❤️"), 
    "crown": ("5465421548791234567", ""), # Replace ID with yours!
    "check": ("5465421548791234568", "✅"), # Replace ID with yours!
}

# ==========================================
#  📜 HELP MENU TEXT (Use raw <tg-emoji> tags here)
# ==========================================
HELP_TEXT = """<b><tg-emoji emoji-id="5465421548791234567">🤖</tg-emoji> FILTER BOT</b>

<blockquote>
<b><tg-emoji emoji-id="5465421548791234568">👥</tg-emoji> ADMINS</b> (GROUP, OR PM AFTER /CONNECT)
• <code>/filter</code> WORD - REPLY TO A MSG/MEDIA TO SAVE IT
• <code>/filter</code> "TWO WORDS" TEXT - TEXT FILTER
• <code>/filters</code> - LIST WITH 🗑 DELETE BUTTONS
• <code>/settings</code> - PROTECTION PANEL (ANTI-LINK, FLOOD, ETC.)
• <code>/allowlink</code> DOMAIN.COM - LINKS THAT ANTI-LINK IGNORES
• <code>/warn</code> (REPLY) REASON | <code>/unwarn</code> | <code>/warns</code>
• <code>/setwel</code> - WELCOME MESSAGE EDITOR (PM ONLY)
</blockquote>

<blockquote>
<b><tg-emoji emoji-id="5318940267334622539">⚙️</tg-emoji> FILTER MODES & BUTTONS</b>
• <code>regex:^hi</code> OR <code>contains:price</code> (DEFAULT = WHOLE WORD)
• REPLY TO A POST (MEDIA + BUTTONS) WITH <code>/filter ANY WORDS</code>
• BUTTONS: <code>[Name](buttonurl:https://x.com)</code>
  ADD <code>:same</code> (SAME ROW), <code>:red</code> <code>:green</code> <code>:blue</code>
• <code>/color</code> WORD RED GREEN - SET BUTTON COLOURS
</blockquote>

<blockquote>
<b><tg-emoji emoji-id="5465421548791234567">👑</tg-emoji> BOT OWNER / ADMINS</b>
• <code>/broadcast</code> (REPLY) | <code>/stats</code> | <code>/addadmin</code> ID | <code>/rmadmin</code> ID
• <code>/info</code> - VIEW USER INFO
</blockquote>
"""

# ==========================================
#  🔤 SMALL CAPS DICTIONARY
# ==========================================
SC = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ",
      "m":"ᴍ","n":"ɴ","o":"","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}