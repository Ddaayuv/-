import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from .backup import backup_loop
from .config import runtime, settings
from .db import Session, db_counts, init_db
from .error_handlers import handle_update_error
from .handlers import admin, fallback, quiz, user
from .logging_setup import configure_logging
from .middlewares import DbMiddleware, MembershipMiddleware, ThrottleMiddleware

log = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    try:
        await init_db()
    except Exception:
        log.exception("Database initialization failed; startup aborted")
        raise

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            link_preview_is_disabled=True,
        ),
    )
    backup_task: asyncio.Task | None = None
    try:
        me = await bot.get_me()
        runtime["bot_username"] = me.username or ""
        dp = Dispatcher(storage=MemoryStorage())
        dp.errors.register(handle_update_error)

        # Keep the existing middleware order and attach to message/callback updates.
        for observer in (dp.message, dp.callback_query):
            observer.outer_middleware(DbMiddleware())
            observer.outer_middleware(ThrottleMiddleware())
            observer.outer_middleware(MembershipMiddleware())

        # The fallback router must remain last so existing handlers take priority.
        dp.include_router(admin.router)
        dp.include_router(quiz.router)
        dp.include_router(user.router)
        dp.include_router(fallback.router)

        public_commands = [
            BotCommand(command="start", description="بدء استخدام الأكاديمية"),
            BotCommand(command="menu", description="القائمة الرئيسية"),
            BotCommand(command="search", description="بحث في المحاضرات"),
            BotCommand(command="myquizzes", description="اختباراتي المحفوظة"),
        ]
        await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())

        owner_commands = public_commands + [
            BotCommand(command="adminpanel", description="لوحة المطوّر"),
            BotCommand(command="broadcast", description="إذاعة رسالة للمستخدمين"),
        ]
        for owner_id in settings.owner_ids:
            try:
                await bot.set_my_commands(
                    owner_commands, scope=BotCommandScopeChat(chat_id=owner_id)
                )
            except Exception:
                log.exception("Could not configure developer commands for owner=%s", owner_id)

        async with Session() as session:
            counts = await db_counts(session)
        log.info(
            "Database initialized: %s (backend=%s)",
            counts, settings.db_url.split("://", 1)[0],
        )

        if counts["users"] == 0 and counts["items"] == 0:
            warning = (
                "⚠️ قاعدة البيانات فارغة عند التشغيل. إذا كان لديك محتوى سابق، "
                "تحقق من التخزين الدائم على Railway (DB_PATH=/app/data/bot.db) "
                "أو DATABASE_URL، ثم استرجع نسخة احتياطية موثوقة."
            )
            for owner_id in settings.owner_ids:
                try:
                    await bot.send_message(owner_id, warning)
                except Exception:
                    log.exception("Could not send empty-database warning to owner=%s", owner_id)

        backup_task = asyncio.create_task(backup_loop(bot), name="daily-backup")
        log.info("Bot @%s started; polling Telegram updates", runtime["bot_username"])
        await dp.start_polling(
            bot, allowed_updates=dp.resolve_used_update_types()
        )
    except asyncio.CancelledError:
        log.info("Bot polling was cancelled")
        raise
    except Exception:
        log.exception("Bot startup or polling failed")
        raise
    finally:
        if backup_task is not None:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Daily backup task stopped with an error")
        try:
            await bot.session.close()
        except Exception:
            log.exception("Could not close Telegram session cleanly")


if __name__ == "__main__":
    asyncio.run(main())
