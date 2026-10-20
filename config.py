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

# Log channel: ID of a channel/group where the bot posts logs (bot started, new users, new groups,
# filters triggered, filters saved/deleted, moderation). Leave 0 and use /setlog in the bot instead.
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0") or 0)

# Start menu images (Direct URLs for preview mode, or Telegram File IDs)
START_IMGS = [
    "https://i.ibb.co/237ZFmGc/c-VVvvq-Pc.jpg", "https://i.ibb.co/Bvg3P2t/NJk-Mma6j.jpg", "https://i.ibb.co/JFM7ptss/uq-X5-CAUy.jpg", "https://i.ibb.co/tM1fHz1g/WLlz5wpd.jpg"
    # Add your other ibb links here
]

# Emojis that cycle during the /start animation
LOAD_EMOJIS = ["⚡️"]

# Chance (0.0 - 1.0) that the small "wait a sec..." loading animation is shown
# on commands / button taps. The /start "starting" animation always plays.
ANIM_CHANCE = 0.35

# Live (animated) reaction put on a plain /start message - one is picked at random.
# Must be emojis Telegram allows as reactions. Leave empty [] to turn off.
START_REACTIONS = ["🔥", "❤", "🎉", "🥰", "😍", "👍"]

#  Button Colours: "red" | "green" | "blue" | "" (default/transparent)
BTN_COLOR = {
    "letters": "",        # ᴀ ʟ ʏ ᴀ letter buttons
    "add_group": "green", # ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ
    "commands": "blue",   # ᴄᴏᴍᴍᴀɴᴅs
    "settings": "blue",   # sᴇᴛᴛɪɴɢs
    "connections": "blue",# ᴄᴏɴɴᴇᴄᴛɪᴏɴs
    "about": "blue",      # ᴀʙᴏᴜᴛ
    "back": "red",        # ◀ ʙᴀᴄᴋ buttons
    "close": "red",       # ᴄ ʟ ᴏ s ᴇ letter buttons (deletes the start menu + your /start)
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
    "fire": ("5431449001695289639", "🔥"),
    "settings": ("5318940267334622539", "⚙️"),
    "heart": ("5388790256772331442", "❤️"),   # add your ID (or use /addemoji in the bot)
    "crown": ("6088924467810015164", "👑"),   # add your ID (or use /addemoji in the bot)
    "check": ("5447153029759397673", "✅"),   # add your ID (or use /addemoji in the bot)
    "back":("5447651546613449378", "◀️"),
    "note":("5956561916573782596", "📋"),
    "Maintain":("5445372439397691719", "▶"),
    "On":("5445279663809130789", "🔛"),
    "Test":("5208441126859593160", "🧪"),
    "stats":("6084477132254218612", "📊"),
    "down":("6122969015708033515", "👇"),
    "bot":("5791935081383726280", "🤖"),
    "Antiforward":("5447434637880098257", "↪️"),
    "pencil":("5445128296276718145", "✏️"),
    "flood":("5445358884480916784", "🌊"),
    "block":("5208579781288808848", "⛔"),
    "start":("5350427505805238170", "⚡️"),
    "lock":("5291873529464122510", "🔒")
    
}

# Normal emoji -> premium (custom) emoji ID. Every bot menu/reply automatically swaps
# these emojis for the premium version. Leave an ID "" to keep the normal emoji.
# Get IDs by forwarding a message with the premium emoji to @RawDataBot (custom_emoji_id).
# Easier: send /addemoji to the bot (owner/admins) with premium emojis in the message,
# or reply /addemoji to a message that has them - they are saved and used everywhere.
# If Telegram rejects an ID, the message is still sent with the normal emoji.
EMOJI_IDS = {
    "👋": PREMIUM_EMOJIS["wave"][0],
    "🔥": PREMIUM_EMOJIS["fire"][0],
    "⚙️": PREMIUM_EMOJIS["settings"][0],
    "❤️": PREMIUM_EMOJIS["heart"][0],
    "👑": PREMIUM_EMOJIS["crown"][0],
    "✅": PREMIUM_EMOJIS["check"][0],
    "❌": "",
    "⛔": "",
    "⚠️": "",
    "🚫": "",
    "🚨": "",
    "📋": "",
    "📭": "",
    "📊": "",
    "📎": "",
    "🔗": "",
    "🗑": "",
    "✏️": "",
    "👁": "",
    "🔛": "",
    "⏱": "",
    "🌊": "",
    "🧪": "",
    "↪️": "",
    "↩️": "",
    "❓": "",
    "🆔": "",
    "👤": "",
    "🤖": "",
    "👥": "",
    "✨": "",
    "⏳": "",
    "🔄": "",
    "▶": "",
}

# ==========================================
#  📜 HELP MENU TEXT (normal emojis are swapped for premium ones automatically)
# ==========================================
HELP_TEXT = """<b>🤖 filter bot</b>

<blockquote expandable><b>👥 admins</b> (group, or pm after <code>/connect</code>)
• <code>/filter</code> word - reply to a msg/media to save it
• <code>/filter</code> "two words" text - text filter
• <code>/filters</code> - list with 🗑 delete buttons
• <code>/settings</code> - protection panel (anti-link, flood, etc.)
• <code>/allowlink</code> domain.com - links that anti-link ignores
• <code>/warn</code> (reply) reason | <code>/unwarn</code> | <code>/warns</code>
• <code>/setwel</code> - welcome message editor (pm only)
• <code>/export</code> - save filters as a file | <code>/import</code> (reply to it) [replace]
• <code>/copyfilters</code> -100id - copy filters from another group
• <code>/smallcaps</code> on|off - small caps text in this group</blockquote>
<blockquote expandable><b>⚙️ filter modes & buttons</b>
• <code>regex:^hi</code> or <code>contains:price</code> (default = whole word)
• reply to a post (media + buttons) with <code>/filter any words</code>
• buttons: <code>[Name](buttonurl:https://x.com)</code>
  add <code>:same</code> (same row), <code>:red</code> <code>:green</code> <code>:blue</code>
• <code>/color</code> word red green - set button colours
• close matches ("blue" for filter "blue box") are offered as buttons
• bot owner's filters: buttons are blue, green, blue, green... by default</blockquote>
<blockquote expandable><b>👑 bot owner / admins</b>
• <code>/broadcast</code> (reply) | <code>/stats</code> | <code>/addadmin</code> id | <code>/rmadmin</code> id
• <code>/addemoji</code> (send or reply with premium emojis) | <code>/emojis</code> | <code>/delemoji</code> emoji
• <code>/setlog</code> -100id - log channel | <code>/logs</code> - choose what is logged
• <code>/info</code> - view user info</blockquote>
"""

# ==========================================
#  🔤 SMALL CAPS DICTIONARY
# ==========================================
SC = {"a":"ᴀ","b":"ʙ","c":"ᴄ","d":"ᴅ","e":"ᴇ","f":"ꜰ","g":"ɢ","h":"ʜ","i":"ɪ","j":"ᴊ","k":"ᴋ","l":"ʟ",
      "m":"ᴍ","n":"ɴ","o":"ᴏ","p":"ᴘ","q":"ǫ","r":"ʀ","s":"ꜱ","t":"ᴛ","u":"ᴜ","v":"ᴠ","w":"ᴡ","x":"x","y":"ʏ","z":"ᴢ"}