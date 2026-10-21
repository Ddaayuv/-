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
from .handlers import admin, user, quiz
from .middlewares import DbMiddleware, MembershipMiddleware, ThrottleMiddleware


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await init_db()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    try:
        me = await bot.get_me()
        runtime["bot_username"] = me.username or ""
        dp = Dispatcher(storage=MemoryStorage())

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
        await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
        owner_commands = public_commands + [
            BotCommand(command="adminpanel", description="لوحة المطوّر"),
            BotCommand(command="broadcast", description="إذاعة رسالة للمستخدمين"),
        ]
        for owner_id in settings.owner_ids:
            try:
                await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=owner_id))
            except Exception:
                logging.exception("تعذّر ضبط قائمة أوامر المطوّر للحساب %s", owner_id)

        async with Session() as _s:
            counts = await db_counts(_s)
        logging.info("DB: %s (%s)", counts, settings.db_url.split("://")[0])
        if counts["users"] == 0 and counts["items"] == 0:
            for owner_id in settings.owner_ids:
                try:
                    await bot.send_message(owner_id, "⚠️ قاعدة البيانات فارغة عند التشغيل. إذا كان عندك محتوى سابق فالتخزين غير دائم: فعّل Volume على Railway (DB_PATH=/app/data/bot.db) أو استخدم PostgreSQL عبر DATABASE_URL، ثم استرجع آخر نسخة بالأمر /restore.")
                except Exception:
                    pass
        backup_task = asyncio.create_task(backup_loop(bot), name="daily-backup")
        logging.info("Bot @%s started; polling Telegram updates", runtime["bot_username"])
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
