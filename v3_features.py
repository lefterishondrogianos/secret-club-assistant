from __future__ import annotations

import html
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ChatJoinRequestHandler, ContextTypes

from v3_core import (
    admin_log,
    feature_enabled,
    format_user,
    get_admin_chat_id,
    initialize_v3,
    is_admin,
    register_core_handlers,
)
from v3_flows import register_flow_handlers


def v3_menu_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton("✨ Δημιουργία παρουσίασης", callback_data="flow:presentation")],
        [
            InlineKeyboardButton("✅ Verification", callback_data="flow:verify"),
            InlineKeyboardButton("🎫 Ticket", callback_data="flow:ticket"),
        ],
        [
            InlineKeyboardButton("🚨 Αναφορά μέλους", callback_data="flow:report"),
            InlineKeyboardButton("🤖 Βοηθός", callback_data="flow:ask"),
        ],
    ]


async def join_request_v3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    if not request:
        return
    chat_id = request.chat.id
    user = request.from_user
    if feature_enabled(chat_id, "auto_approve"):
        return  # Το βασικό bot χειρίζεται το auto-approve.
    admin_chat_id = get_admin_chat_id()
    if not admin_chat_id:
        return
    try:
        await context.bot.send_message(
            admin_chat_id,
            f"📥 <b>ΑΙΤΗΜΑ ΕΙΣΟΔΟΥ</b>\n\nΟμάδα: <b>{html.escape(request.chat.title or str(chat_id))}</b>\nΜέλος: {format_user(user.id,user.username,user.first_name)}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Έγκριση", callback_data=f"v3join:approve:{chat_id}:{user.id}"),
                InlineKeyboardButton("❌ Απόρριψη", callback_data=f"v3join:reject:{chat_id}:{user.id}"),
            ]]),
        )
    except TelegramError:
        pass


async def join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await is_admin(context, query.message.chat.id, query.from_user.id):
        await query.answer("Μόνο admins.", show_alert=True)
        return
    try:
        _, action, chat_text, user_text = query.data.split(":", 3)
        chat_id, user_id = int(chat_text), int(user_text)
        if action == "approve":
            await context.bot.approve_chat_join_request(chat_id, user_id)
            label = "✅ Εγκρίθηκε"
        else:
            await context.bot.decline_chat_join_request(chat_id, user_id)
            label = "❌ Απορρίφθηκε"
        await query.answer(label)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(label)
    except TelegramError:
        await query.answer("Το αίτημα έχει ήδη διαχειριστεί.", show_alert=True)


def register_v3_handlers(application: Application) -> None:
    register_core_handlers(application)
    register_flow_handlers(application)
    application.add_handler(ChatJoinRequestHandler(join_request_v3), group=1)
    application.add_handler(CallbackQueryHandler(join_callback, pattern=r"^v3join:"), group=-3)


__all__ = ["initialize_v3", "register_v3_handlers", "v3_menu_rows"]
