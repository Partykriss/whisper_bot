import os
import re
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from openai import OpenAI
from dotenv import load_dotenv
from app.media.config import SETTINGS
from app.media.sources import Source
from app.media.process import MediaError
from app.payments import send_plan_invoices
from app.services.billing import expiry_warning_text
from app.services.tasks import record_payment
from app.services.users import get_or_create_user
from app.services.media_jobs import MediaJobs
from app.events.runtime import Runtime
from app.ui.messages import format_duration

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
session = None
if SETTINGS.telegram_api_base:
    session = AiohttpSession(api=TelegramAPIServer.from_base(
        SETTINGS.telegram_api_base, is_local=True))
bot = Bot(token=os.getenv("BOT_TOKEN"), session=session)
dp = Dispatcher()
jobs = MediaJobs(bot, client)
logging.basicConfig(level=logging.INFO)


@dp.message(F.voice | F.audio | F.video | F.video_note | F.document)
async def handle_media_message(message: types.Message):
    media = message.voice or message.audio or message.video or message.video_note or message.document
    get_or_create_user(message.from_user.id)
    await jobs.submit(message.from_user.id, message.chat.id,
                      Source(file_id=media.file_id, duration=getattr(media, 'duration', None),
                             size=media.file_size), media.file_unique_id)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_or_create_user(message.from_user.id)
    balance_sec = max(0, int(user.seconds or 0) - user.reserved_seconds)
    expiry = user.paid_until
    plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
    plan_display = plan_name.upper()
    expiry_label = "Limits refresh" if plan_name == "trial" else "Plan expires"

    if plan_name == "admin":
        await message.answer("Your plan: ADMIN, unlimited")
        return

    await message.answer(
        "Your plan: {plan}\n"
        "Balance: {balance}\n"
        "{expiry_label}: {expiry}{warning}".format(
            plan=plan_display,
            balance=format_duration(balance_sec, force_hours=False),
            expiry_label=expiry_label,
            expiry=expiry.strftime("%Y-%m-%d") if expiry else "-",
            warning=expiry_warning_text(plan_name, user.paid_until),
        )
    )


@dp.message(Command("payment"))
async def cmd_payment(message: types.Message):
    await send_plan_invoices(bot, message.chat.id)


@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: types.PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@dp.callback_query(F.data == "pay_confirm_yes")
async def pay_confirm_yes_handler(callback: types.CallbackQuery):
    await callback.answer()
    await send_plan_invoices(bot, callback.message.chat.id)
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data == "pay_confirm_no")
async def pay_confirm_no_handler(callback: types.CallbackQuery):
    await callback.answer("Cancelled")
    await callback.message.edit_reply_markup(reply_markup=None)


@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    get_or_create_user(message.from_user.id)
    record_payment(message.from_user.id, message.successful_payment, message.chat.id)


@dp.message(F.text.regexp(r"https?://", mode="search"))
async def handle_media_link(message: types.Message):
    get_or_create_user(message.from_user.id)
    match = re.search(r"https?://[^\s<>]+", message.text)
    try:
        await jobs.submit(message.from_user.id, message.chat.id,
                          Source(url=match.group()), match.group())
    except (MediaError, ValueError):
        await message.answer('Send a valid public media link or a single YouTube video.')


async def main():
    try:
        async with Runtime(bot, jobs):
            print("Bot is running...")
            await dp.start_polling(bot, close_bot_session=False)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
