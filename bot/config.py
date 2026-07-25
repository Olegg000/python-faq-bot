import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
RATE_PROVIDER = os.getenv("RATE_PROVIDER", "demo")  # demo | exchangerate
