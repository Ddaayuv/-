import asyncio
import logging
import time

from aiogram.types import ErrorEvent
from aiogram.exceptions import TelegramAPIError

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeChat, BotCommandScopeDefault

from .backup import backup_loop
from .config import runtime, settings
from .db import Session, db_counts, init_db
from .handlers import admin, user, quiz
from .middlewares import DbMiddleware, MembershipMiddleware, ThrottleMiddleware
from .handlers.quiz import quiz_maintenance_loop, recover_group_quiz_sessions


ERROR_NOTIFY_INTERVAL = 60.0
_last_error_notification = 0.0


async def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    try:
        me = await bot.get_me()
        runtime["bot_username"] = me.username or ""
        dp = Dispatcher(storage=MemoryStorage())

        async def handle_update_error(event: ErrorEvent) -> bool:
            global _last_error_notification
            error = event.exception
            logging.getLogger("bot.errors").exception(
                "Unhandled update error; update_id=%s", event.update.update_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            update = event.update
            message = update.message or update.edited_message
            callback = update.callback_query
            if message and message.chat.type == "private":
                try:
                    await message.answer("⚠️ حدث خطأ غير متوقع أثناء تنفيذ طلبك. حاول مرة أخرى لاحقاً.")
                except TelegramAPIError:
                    logging.getLogger("bot.errors").warning("Could not send error notice to user")
            elif callback:
                try:
                    await callback.answer("حدث خطأ غير متوقع. حاول مرة أخرى.", show_alert=True)
                except TelegramAPIError:
                    logging.getLogger("bot.errors").warning("Could not answer failed callback")
            now = time.monotonic()
            if now - _last_error_notification >= ERROR_NOTIFY_INTERVAL:
                _last_error_notification = now
                for owner_id in settings.owner_ids:
                    try:
                        await bot.send_message(
                            owner_id,
                            f"⚠️ خطأ غير معالج في البوت\nUpdate: <code>{update.update_id}</code>\n"
                            f"النوع: <code>{type(error).__name__}</code>\n"
                            f"التفاصيل: <code>{str(error)[:700]}</code>",
                        )
                    except TelegramAPIError:
                        logging.getLogger("bot.errors").warning("Could not notify owner %s", owner_id)
            return True

        dp.errors.register(handle_update_error)

        # Middlewares must be attached to message/callback observers, not dp.update.
        # Message/CallbackQuery provide from_user and are handled by our middleware.
        for observer in (dp.message, dp.callback_query):
            observer.outer_middleware(DbMiddleware())
            observer.outer_middleware(ThrottleMiddleware())
            observer.outer_middleware(MembershipMiddleware())

        dp.include_router(admin.router)
        dp.include_router(quiz.router)
        dp.include_router(user.router)

        public_commands = [
            BotCommand(command="start", description="بدء استخدام الأكاديمية"),
            BotCommand(command="menu", description="القائمة الرئيسية"),
            BotCommand(command="search", description="بحث في المحاضرات"),
            BotCommand(command="myquizzes", description="اختباراتي المحفوظة"),
        ]
        group_commands = [
            BotCommand(command="quiz", description="بدء اختبار (للمشرفين)"),
            BotCommand(command="quizsettings", description="إعدادات اختبارات المجموعة"),
            BotCommand(command="quizpause", description="إيقاف الاختبار مؤقتاً"),
            BotCommand(command="quizresume", description="استئناف الاختبار"),
            BotCommand(command="quizstop", description="إنهاء الاختبار"),
            BotCommand(command="quizresults", description="عرض الترتيب"),
            BotCommand(command="quizhistory", description="سجل الاختبارات"),
            BotCommand(command="quizexport", description="تصدير النتائج"),
            BotCommand(command="quizschedule", description="جدولة اختبار"),
        ]
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
        owner_commands = public_commands + [
            BotCommand(command="adminpanel", description="لوحة المطوّر"),
            BotCommand(command="broadcast", description="إذاعة رسالة للمستخدمين"),
            BotCommand(command="quizgroups", description="إدارة مجموعات الاختبارات"),
            BotCommand(command="status", description="حالة البوت"),
            BotCommand(command="backup", description="نسخة احتياطية فورية"),
        ]
        for owner_id in settings.owner_ids:
            try:
                await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=owner_id))
            except Exception:
                logging.exception("تعذّر ضبط قائمة أوامر المطوّر للحساب %s", owner_id)

        async with Session() as _s:
            counts = await db_counts(_s)
        database_kind = "PostgreSQL" if settings.db_url.startswith("postgresql") else "SQLite"
        logging.info(
            "Bot %s @%s started; database=%s; counts=%s",
            settings.bot_version, runtime["bot_username"], database_kind, counts,
        )
        if counts["users"] == 0 and counts["items"] == 0:
            for owner_id in settings.owner_ids:
                try:
                    await bot.send_message(owner_id, "⚠️ قاعدة البيانات فارغة عند التشغيل. إذا كان عندك محتوى سابق فالتخزين غير دائم: فعّل Volume على Railway (DB_PATH=/app/data/bot.db) أو استخدم PostgreSQL عبر DATABASE_URL، ثم استرجع آخر نسخة بالأمر /restore.")
                except Exception:
                    logging.exception("Could not warn owner %s about empty database", owner_id)
        if settings.backup_chat_id is None:
            for owner_id in settings.owner_ids:
                try:
                    await bot.send_message(
                        owner_id,
                        "⚠️ النسخ الاحتياطي التلقائي غير مفعّل: لم يتم ضبط BACKUP_CHAT_ID. "
                        "يمكنك استخدام /backup لاستلام نسخة فورية في الخاص.",
                    )
                except Exception:
                    logging.exception("Could not warn owner %s about missing BACKUP_CHAT_ID", owner_id)
        await recover_group_quiz_sessions(bot)
        backup_task = asyncio.create_task(backup_loop(bot), name="daily-backup")
        quiz_task = asyncio.create_task(quiz_maintenance_loop(bot), name="group-quiz-maintenance")
        logging.info("Polling Telegram updates for bot version %s", settings.bot_version)
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            for task in (backup_task, quiz_task):
                task.cancel()
            for task in (backup_task, quiz_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
