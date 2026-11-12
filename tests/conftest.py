import os

# bot.config reads these at import time; tests never contact Telegram or a real database.
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_REAL_000000000000000")
os.environ.setdefault("OWNER_IDS", "111")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("DB_PATH", ":memory:")
