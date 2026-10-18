import os

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "FilterBot")

if not (API_ID and API_HASH and BOT_TOKEN and MONGO_URI):
    raise SystemExit("Set API_ID, API_HASH, BOT_TOKEN and MONGO_URI")