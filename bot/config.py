import os
from dataclasses import dataclass

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


def _opt_int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    new_days: int = 7


def _load() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN غير موجود في متغيرات البيئة / ملف .env")
    owners = _ints(os.getenv("OWNER_IDS", ""))
    if not owners:
        raise SystemExit("OWNER_IDS غير موجود في متغيرات البيئة / ملف .env")
    db_url, db_path = _database_url()
    backup_hour = int(os.getenv("BACKUP_HOUR", "3"))
    if not 0 <= backup_hour <= 23:
        raise SystemExit("BACKUP_HOUR يجب أن يكون بين 0 و23")
    return Settings(
        bot_token=token, owner_ids=owners,
        academy_name=os.getenv("ACADEMY_NAME", "أكاديمية الأمن السيبراني"),
        db_path=db_path, db_url=db_url,
        channel_id=_opt_int(os.getenv("CHANNEL_ID")),
        channel_link=os.getenv("CHANNEL_LINK", "").strip(),
        require_membership=_bool(os.getenv("REQUIRE_MEMBERSHIP"), False),
        announce=_bool(os.getenv("ANNOUNCE"), True),
        backup_chat_id=_opt_int(os.getenv("BACKUP_CHAT_ID")),
        backup_hour=backup_hour,
    )


settings = _load()
runtime: dict[str, str] = {"bot_username": ""}
