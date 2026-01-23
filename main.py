import os
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import OpenAI
from dotenv import load_dotenv

from app.database import SessionLocal
from app.models import User
from app.payments import send_plan_invoices
from app.services.billing import add_one_month, get_plan_limit_seconds, is_period_expired, expiry_warning_text
from app.services.tasks import (
    get_cached_result,
    save_task_to_db,
    update_task_in_db,
    get_pending_task_for_user,
    get_pending_task_by_file_unique_id,
    record_payment,
)
from app.services.transcription import transcribe_media, process_task_by_file_id
from app.services.users import (
    get_or_create_user,
    subtract_user_seconds,
    apply_payment_to_user,
    is_paid_plan,
    get_admin_user_ids,
)
from app.ui.messages import format_duration, send_expiry_warning_if_needed

# Load environment variables
load_dotenv()

# Initialize clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)


def _confirm_pay_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes", callback_data="pay_confirm_yes")
    builder.button(text="No", callback_data="pay_confirm_no")
    builder.adjust(2)
    return builder.as_markup()


# --- Component 3: Telegram Media Handler ---
@dp.message(F.voice | F.audio | F.video | F.video_note)
async def handle_media_message(message: types.Message):
    media = message.voice or message.audio or message.video or message.video_note

    cached_text = get_cached_result(media.file_unique_id)
    if cached_text:
        await message.answer(f"Found in cache:\n\n{cached_text}")
        await send_expiry_warning_if_needed(bot, message.from_user.id, message.chat.id)
        return

    user = get_or_create_user(message.from_user.id)
    plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
    was_paid_plan = plan_name in {"plus", "pro"}

    if plan_name != "admin" and is_period_expired(user.paid_until):
        with SessionLocal() as db:
            db_user = db.query(User).filter(User.telegram_id == message.from_user.id).first()
            if db_user:
                db_user.plan = "trial"
                db_user.seconds = get_plan_limit_seconds("trial")
                db_user.paid_until = add_one_month(datetime.utcnow())
                db.commit()
        user = get_or_create_user(message.from_user.id)
        plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
        if was_paid_plan:
            await message.answer(
                "Your plan has expired. It was reset to trial.\n"
                "Balance: {balance}".format(
                    balance=format_duration(int(user.seconds or 0)),
                )
            )

    if plan_name != "admin":
        remaining_sec = max(0, int(user.seconds or 0))
        duration_sec = getattr(media, "duration", None)
        if duration_sec is not None and duration_sec > remaining_sec:
            await message.answer(
                "Limit reached for plan {plan}.\n"
                "Remaining: {remaining}, file duration: {duration}.".format(
                    plan=plan_name,
                    remaining=format_duration(remaining_sec),
                    duration=format_duration(duration_sec),
                )
            )
            pending = get_pending_task_by_file_unique_id(media.file_unique_id)
            if not pending:
                save_task_to_db(
                    user_id=message.from_user.id,
                    file_unique_id=media.file_unique_id,
                    status="pending_payment",
                    file_id=media.file_id,
                    chat_id=message.chat.id,
                    duration_seconds=duration_sec,
                )
            if is_paid_plan(plan_name):
                await message.answer(
                    "You already have a paid plan.\n"
                    "The period will be extended and limits will be added to your balance.\n"
                    "Proceed with payment?",
                    reply_markup=_confirm_pay_keyboard(),
                )
            else:
                await send_plan_invoices(bot, message.chat.id)
            return
    else:
        duration_sec = getattr(media, "duration", None)

    file_id = media.file_id
    file = await bot.get_file(file_id)

    ext = file.file_path.split(".")[-1]
    local_path = f"downloads/{file_id}.{ext}"
    os.makedirs("downloads", exist_ok=True)

    await bot.download_file(file.file_path, local_path)

    task_id = save_task_to_db(
        message.from_user.id,
        media.file_unique_id,
        status="processing",
        file_id=media.file_id,
        chat_id=message.chat.id,
        duration_seconds=duration_sec,
    )
    status_msg = await message.answer("Processing your media...")

    try:
        text = await asyncio.to_thread(transcribe_media, client, local_path)
        duration_seconds = duration_sec

        update_task_in_db(task_id, text, "completed", duration_seconds=duration_seconds)
        if duration_seconds:
            subtract_user_seconds(message.from_user.id, duration_seconds)
        await status_msg.edit_text(text)
        await send_expiry_warning_if_needed(bot, message.from_user.id, message.chat.id)

    except Exception as e:
        logging.error(f"Error: {e}")
        update_task_in_db(task_id, str(e), "failed")
        await status_msg.edit_text("Failed to process media.")
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_or_create_user(message.from_user.id)
    balance_sec = int(user.seconds or 0)
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
    payment = message.successful_payment
    payload = payment.invoice_payload
    user = get_or_create_user(message.from_user.id)
    current_plan_name = user.plan.value if hasattr(user.plan, "value") else str(user.plan)
    was_paid = is_paid_plan(current_plan_name)

    if payload == "plan_plus":
        record_payment(message.from_user.id, "plus", payment)
        apply_payment_to_user(message.from_user.id, "plus")
        if was_paid:
            await message.answer("Payment successful. Balance increased and period extended.")
        else:
            await message.answer("Payment successful. Your plan was updated to Plus.")
    elif payload == "plan_pro":
        record_payment(message.from_user.id, "pro", payment)
        apply_payment_to_user(message.from_user.id, "pro")
        if was_paid:
            await message.answer("Payment successful. Balance increased and period extended.")
        else:
            await message.answer("Payment successful. Your plan was updated to Pro.")
    else:
        record_payment(message.from_user.id, "trial", payment)
        await message.answer("Payment successful.")

    pending = get_pending_task_for_user(message.from_user.id)
    if pending:
        user_after = get_or_create_user(message.from_user.id)
        remaining_sec = int(user_after.seconds or 0)
        if pending.duration_seconds and pending.duration_seconds > remaining_sec:
            await bot.send_message(
                pending.chat_id or message.chat.id,
                "Payment successful, but the balance is still insufficient for the requested file.",
            )
            return
        await process_task_by_file_id(bot, client, pending, message.from_user.id)

    admin_ids = get_admin_user_ids()
    if admin_ids:
        text = "New payment: user {user_id}, plan {plan}, amount {amount} {currency}.".format(
            user_id=message.from_user.id,
            plan=payload.replace("plan_", ""),
            amount=payment.total_amount,
            currency=payment.currency,
        )
        for admin_id in admin_ids:
            await bot.send_message(admin_id, text)


async def main():
    print("Bot is running...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
