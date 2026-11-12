import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _ints(v: str) -> list[int]:
    values = []
    for part in (v or "").replace(" ", "").split(","):
        if part:
            try:
                values.append(int(part))
            except ValueError as exc:
                raise SystemExit(f"OWNER_IDS يحتوي قيمة غير صحيحة: {part}") from exc
    return values


def _opt_int(v: str | None, name: str) -> int | None:
    value = (v or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} يجب أن يكون رقماً صحيحاً") from exc


def _bool(v: str | None, default: bool, name: str) -> bool:
    if v is None or v.strip() == "":
        return default
    value = v.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise SystemExit(f"{name} يجب أن تكون إحدى القيم: true/false أو 1/0")


def _choice(value: str, allowed: set[str], name: str) -> str:
    normalized = value.strip().upper()
    if normalized not in allowed:
        raise SystemExit(f"{name} غير صالح. القيم المسموحة: {', '.join(sorted(allowed))}")
    return normalized


def _database_url() -> tuple[str, str]:
    raw = os.getenv("DATABASE_URL", "").strip()
    path = os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db"
    if raw:
        if raw.startswith("postgres://"):
            raw = "postgresql+asyncpg://" + raw[len("postgres://"):]
        elif raw.startswith("postgresql://"):
            raw = "postgresql+asyncpg://" + raw[len("postgresql://"):]
        if raw.startswith("sqlite:///"):
            raw = raw.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if raw.startswith("sqlite+aiosqlite:///"):
            local_path = raw.split("sqlite+aiosqlite:///", 1)[1]
            if local_path and local_path != ":memory:":
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            return raw, local_path or path
        return raw, path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return f"sqlite+aiosqlite:///{path}", path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_ids: list[int]
    academy_name: str
    db_path: str
    db_url: str
    channel_id: int | None
    channel_link: str
    require_membership: bool
    announce: bool
    backup_chat_id: int | None
    backup_hour: int
    timezone: str
    log_level: str
    bot_version: str
    redis_url: str | None = None
    new_days: int = 7


def _load() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN غير موجود في متغيرات البيئة / ملف .env")
    owners = _ints(os.getenv("OWNER_IDS", ""))
    if not owners:
        raise SystemExit("OWNER_IDS غير موجود في متغيرات البيئة / ملف .env")
    db_url, db_path = _database_url()
    try:
        backup_hour = int(os.getenv("BACKUP_HOUR", "3"))
    except ValueError as exc:
        raise SystemExit("BACKUP_HOUR يجب أن يكون رقماً بين 0 و23") from exc
    if not 0 <= backup_hour <= 23:
        raise SystemExit("BACKUP_HOUR يجب أن يكون بين 0 و23")

    channel_id = _opt_int(os.getenv("CHANNEL_ID"), "CHANNEL_ID")
    backup_chat_id = _opt_int(os.getenv("BACKUP_CHAT_ID"), "BACKUP_CHAT_ID")
    if channel_id == 0 or backup_chat_id == 0:
        raise SystemExit("CHANNEL_ID وBACKUP_CHAT_ID لا يمكن أن يساويا صفراً")

    timezone_name = os.getenv("TIMEZONE", "Asia/Baghdad").strip() or "Asia/Baghdad"
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"TIMEZONE غير صالح أو غير معروف: {timezone_name}") from exc

    log_level = _choice(os.getenv("LOG_LEVEL", "INFO"), {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}, "LOG_LEVEL")
    bot_version = os.getenv("BOT_VERSION", "development").strip() or "development"
    redis_url = os.getenv("REDIS_URL", "").strip() or None
    if redis_url and not redis_url.startswith(("redis://", "rediss://", "unix://")):
        raise SystemExit("REDIS_URL يجب أن يبدأ بـ redis:// أو rediss:// أو unix://")
    return Settings(
        bot_token=token, owner_ids=owners,
        academy_name=os.getenv("ACADEMY_NAME", "أكاديمية الأمن السيبراني"),
        db_path=db_path, db_url=db_url,
        channel_id=channel_id,
        channel_link=os.getenv("CHANNEL_LINK", "").strip(),
        require_membership=_bool(os.getenv("REQUIRE_MEMBERSHIP"), False, "REQUIRE_MEMBERSHIP"),
        announce=_bool(os.getenv("ANNOUNCE"), True, "ANNOUNCE"),
        backup_chat_id=backup_chat_id,
        backup_hour=backup_hour,
        timezone=timezone_name,
        log_level=log_level,
        bot_version=bot_version,
        redis_url=redis_url,
    )


settings = _load()
runtime: dict[str, str] = {"bot_username": ""}
