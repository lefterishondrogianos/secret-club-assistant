from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime
from typing import Any, Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from v3_core import (
    AI_DAILY_LIMIT,
    LOCAL_TZ,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    admin_log,
    db_connect,
    feature_enabled,
    format_user,
    get_admin_chat_id,
    get_main_group_id,
    is_admin,
    is_verified,
    require_private,
    set_verified,
)

(
    VERIFY_AGE,
    VERIFY_CATEGORY,
    VERIFY_REGION,
    VERIFY_NOTE,
    VERIFY_PHOTO,
    TICKET_CATEGORY,
    TICKET_MESSAGE,
    REPORT_USER,
    REPORT_DETAILS,
    REPORT_EVIDENCE,
    PRES_CATEGORY,
    PRES_REGION,
    PRES_AGES,
    PRES_LOOKING,
    PRES_BIO,
    PRES_CONFIRM,
    AI_QUESTION,
) = range(17)

URL_RE = re.compile(r"(?:(?:https?://|www\.)[^\s]+|(?:t\.me|telegram\.me)/[^\s]+)", re.I)
USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,}")


def extract_text(message: Message) -> str:
    return (message.text or message.caption or "").strip()


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("verify", "ticket", "report", "presentation", "ticket_reply_id"):
        context.user_data.pop(key, None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("Η διαδικασία ακυρώθηκε.")
    elif update.effective_message:
        await update.effective_message.reply_text("Η διαδικασία ακυρώθηκε.")
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Verification
# -----------------------------------------------------------------------------

async def verify_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_private(update):
        return ConversationHandler.END
    sender = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    if not get_admin_chat_id():
        await sender.reply_text("⚙️ Ένας admin πρέπει πρώτα να τρέξει /setupadminchat στην ιδιωτική ομάδα admins.")
        return ConversationHandler.END
    if is_verified(update.effective_user.id):
        await sender.reply_text("✅ Είσαι ήδη verified.")
        return ConversationHandler.END
    context.user_data["verify"] = {}
    await sender.reply_text(
        "✅ <b>Verification</b>\n\nΕπιβεβαίωσε ότι είσαι 18+. Δεν ζητάμε ταυτότητα, τραπεζικά στοιχεία ή κωδικούς.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔞 Είμαι 18+", callback_data="verify:adult")],
            [InlineKeyboardButton("❌ Ακύρωση", callback_data="verify:cancel")],
        ]),
    )
    return VERIFY_AGE


async def verify_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "verify:cancel":
        await query.edit_message_text("Η αίτηση ακυρώθηκε.")
        return ConversationHandler.END
    await query.edit_message_text(
        "Διάλεξε κατηγορία:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Couple", callback_data="verifycat:Couple"), InlineKeyboardButton("Single", callback_data="verifycat:Single")],
            [InlineKeyboardButton("Bi Couple", callback_data="verifycat:Bi Couple"), InlineKeyboardButton("Bi Single", callback_data="verifycat:Bi Single")],
            [InlineKeyboardButton("Lesbian", callback_data="verifycat:Lesbian"), InlineKeyboardButton("Gay", callback_data="verifycat:Gay")],
            [InlineKeyboardButton("Άλλο", callback_data="verifycat:Άλλο")],
        ]),
    )
    return VERIFY_CATEGORY


async def verify_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["verify"]["category"] = query.data.split(":", 1)[1]
    await query.edit_message_text("📍 Γράψε περιοχή ή πόλη, χωρίς ακριβή διεύθυνση:")
    return VERIFY_REGION


async def verify_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 80:
        await update.effective_message.reply_text("Γράψε περιοχή έως 80 χαρακτήρες.")
        return VERIFY_REGION
    context.user_data["verify"]["region"] = text
    await update.effective_message.reply_text("📝 Γράψε λίγα λόγια για την αίτηση, έως 500 χαρακτήρες:")
    return VERIFY_NOTE


async def verify_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 500:
        await update.effective_message.reply_text("Το κείμενο πρέπει να είναι 2–500 χαρακτήρες.")
        return VERIFY_NOTE
    context.user_data["verify"]["note"] = text
    await update.effective_message.reply_text(
        "📷 Προαιρετικά στείλε μία πρόσφατη φωτογραφία. Μη στείλεις ταυτότητα ή έγγραφο.\nΓράψε <code>skip</code> για παράλειψη.",
        parse_mode=ParseMode.HTML,
    )
    return VERIFY_PHOTO


async def verify_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    photo_id: Optional[str] = message.photo[-1].file_id if message.photo else None
    if not photo_id and extract_text(message).lower() not in {"skip", "παράλειψη", "χωρίς"}:
        await message.reply_text("Στείλε φωτογραφία ή γράψε skip.")
        return VERIFY_PHOTO
    data = context.user_data["verify"]
    user = update.effective_user
    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO verifications(user_id,username,first_name,category,region,note,photo_file_id,status,created_at)
            VALUES(?,?,?,?,?,?,?,'pending',?)
            """,
            (user.id, user.username, user.first_name, data["category"], data["region"], data["note"], photo_id, int(time.time())),
        )
        verification_id = int(cur.lastrowid)
    text = (
        f"✅ <b>VERIFICATION #{verification_id}</b>\n\n"
        f"Μέλος: {format_user(user.id,user.username,user.first_name)}\n"
        f"Κατηγορία: <b>{html.escape(data['category'])}</b>\n"
        f"Περιοχή: <b>{html.escape(data['region'])}</b>\n"
        f"Σημείωση: {html.escape(data['note'])}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Έγκριση", callback_data=f"v3verify:approve:{verification_id}"),
        InlineKeyboardButton("❌ Απόρριψη", callback_data=f"v3verify:reject:{verification_id}"),
    ]])
    try:
        if photo_id:
            sent = await context.bot.send_photo(get_admin_chat_id(), photo_id, caption=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        else:
            sent = await context.bot.send_message(get_admin_chat_id(), text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        with db_connect() as conn:
            conn.execute("UPDATE verifications SET admin_message_id=? WHERE id=?", (sent.message_id, verification_id))
        await message.reply_text(f"✅ Η αίτηση #{verification_id} στάλθηκε στους admins.")
    except TelegramError:
        await message.reply_text("❌ Δεν μπόρεσα να στείλω την αίτηση.")
    context.user_data.pop("verify", None)
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Tickets
# -----------------------------------------------------------------------------

async def ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_private(update):
        return ConversationHandler.END
    sender = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    if not get_admin_chat_id():
        await sender.reply_text("⚙️ Το ticket system χρειάζεται πρώτα /setupadminchat.")
        return ConversationHandler.END
    context.user_data["ticket"] = {}
    await sender.reply_text(
        "🎫 <b>Νέο Ticket</b>\n\nΔιάλεξε κατηγορία:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❓ Βοήθεια", callback_data="ticketcat:Βοήθεια"), InlineKeyboardButton("⚙️ Τεχνικό", callback_data="ticketcat:Τεχνικό")],
            [InlineKeyboardButton("✅ Verification", callback_data="ticketcat:Verification"), InlineKeyboardButton("🚨 Αναφορά", callback_data="ticketcat:Αναφορά")],
            [InlineKeyboardButton("💬 Άλλο", callback_data="ticketcat:Άλλο")],
            [InlineKeyboardButton("❌ Ακύρωση", callback_data="ticketcat:cancel")],
        ]),
    )
    return TICKET_CATEGORY


async def ticket_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    if category == "cancel":
        await query.edit_message_text("Το ticket ακυρώθηκε.")
        return ConversationHandler.END
    context.user_data["ticket"]["category"] = category
    await query.edit_message_text("Γράψε το μήνυμα. Μπορείς να στείλεις κείμενο, φωτογραφία ή screenshot.")
    return TICKET_MESSAGE


async def create_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, subject: str) -> int:
    user, message = update.effective_user, update.effective_message
    now = int(time.time())
    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tickets(user_id,username,category,subject,status,created_at,updated_at)
            VALUES(?,?,?,?,'open',?,?)
            """,
            (user.id, user.username, category, subject[:1000], now, now),
        )
        ticket_id = int(cur.lastrowid)
    card = await context.bot.send_message(
        get_admin_chat_id(),
        f"🎫 <b>TICKET #{ticket_id}</b>\n\nΑπό: {format_user(user.id,user.username,user.first_name)}\nΚατηγορία: <b>{html.escape(category)}</b>\nΜήνυμα: {html.escape(subject[:1000] or '[πολυμέσο]')}\n\nΑπάντησε σε αυτό το μήνυμα για απάντηση.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🙋 Ανάληψη", callback_data=f"v3ticket:claim:{ticket_id}"),
            InlineKeyboardButton("✅ Κλείσιμο", callback_data=f"v3ticket:close:{ticket_id}"),
        ]]),
    )
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO ticket_message_map VALUES(?,?,?,'admin')", (get_admin_chat_id(), card.message_id, ticket_id))
    if message.photo or message.document or message.video or message.voice:
        copied = await context.bot.copy_message(get_admin_chat_id(), message.chat_id, message.message_id)
        with db_connect() as conn:
            conn.execute("INSERT OR REPLACE INTO ticket_message_map VALUES(?,?,?,'admin')", (get_admin_chat_id(), copied.message_id, ticket_id))
    anchor = await message.reply_text(
        f"✅ Το ticket #{ticket_id} άνοιξε.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✉️ Νέο μήνυμα", callback_data=f"v3ticketuser:reply:{ticket_id}"),
            InlineKeyboardButton("🔒 Κλείσιμο", callback_data=f"v3ticketuser:close:{ticket_id}"),
        ]]),
    )
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO ticket_message_map VALUES(?,?,?,'user')", (user.id, anchor.message_id, ticket_id))
    return ticket_id


async def ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subject = extract_text(update.effective_message) or "[πολυμέσο]"
    category = context.user_data.get("ticket", {}).get("category", "Άλλο")
    try:
        await create_ticket(update, context, category, subject)
    except TelegramError:
        await update.effective_message.reply_text("❌ Δεν μπόρεσα να ανοίξω το ticket.")
    context.user_data.pop("ticket", None)
    return ConversationHandler.END

async def ticket_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, ticket_text = query.data.split(":", 2)
    ticket_id = int(ticket_text)
    with db_connect() as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE id=? AND user_id=?", (ticket_id, query.from_user.id)).fetchone()
    if not ticket:
        await query.message.reply_text("Δεν βρέθηκε αυτό το ticket.")
        return
    if action == "close":
        with db_connect() as conn:
            conn.execute("UPDATE tickets SET status='closed',updated_at=? WHERE id=?", (int(time.time()), ticket_id))
        await query.edit_message_text(f"🔒 Το ticket #{ticket_id} έκλεισε.")
        await admin_log(context, f"🔒 Το ticket #{ticket_id} έκλεισε από το μέλος.")
        return
    if ticket["status"] == "closed":
        await query.message.reply_text("Το ticket είναι ήδη κλειστό.")
        return
    context.user_data["ticket_reply_id"] = ticket_id
    await query.message.reply_text(f"✉️ Στείλε τώρα το νέο μήνυμα για το ticket #{ticket_id}.")


async def pending_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    ticket_id = context.user_data.get("ticket_reply_id")
    if not ticket_id:
        return
    user, message = update.effective_user, update.effective_message
    with db_connect() as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE id=? AND user_id=? AND status!='closed'", (ticket_id, user.id)).fetchone()
    if not ticket:
        context.user_data.pop("ticket_reply_id", None)
        await message.reply_text("Το ticket δεν είναι πλέον ανοιχτό.")
        return
    header = await context.bot.send_message(
        get_admin_chat_id(),
        f"✉️ <b>Νέο μήνυμα ticket #{ticket_id}</b>\nΑπό: {format_user(user.id,user.username,user.first_name)}\nΑπάντησε σε αυτό το μήνυμα.",
        parse_mode=ParseMode.HTML,
    )
    copied = await context.bot.copy_message(get_admin_chat_id(), message.chat_id, message.message_id)
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO ticket_message_map VALUES(?,?,?,'admin')", (get_admin_chat_id(), header.message_id, ticket_id))
        conn.execute("INSERT OR REPLACE INTO ticket_message_map VALUES(?,?,?,'admin')", (get_admin_chat_id(), copied.message_id, ticket_id))
        conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (int(time.time()), ticket_id))
    context.user_data.pop("ticket_reply_id", None)
    await message.reply_text(f"✅ Το μήνυμα στάλθηκε στο ticket #{ticket_id}.")


async def admin_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message, actor = update.effective_chat, update.effective_message, update.effective_user
    if not chat or chat.id != get_admin_chat_id() or not message.reply_to_message or actor.is_bot:
        return
    if not await is_admin(context, chat.id, actor.id):
        return
    with db_connect() as conn:
        mapping = conn.execute(
            "SELECT ticket_id FROM ticket_message_map WHERE chat_id=? AND message_id=?",
            (chat.id, message.reply_to_message.message_id),
        ).fetchone()
        if not mapping:
            return
        ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (int(mapping["ticket_id"]),)).fetchone()
    if not ticket or ticket["status"] == "closed":
        await message.reply_text("Το ticket είναι κλειστό ή δεν υπάρχει.")
        return
    try:
        await context.bot.send_message(int(ticket["user_id"]), f"📩 <b>Απάντηση admins — Ticket #{ticket['id']}</b>", parse_mode=ParseMode.HTML)
        await context.bot.copy_message(int(ticket["user_id"]), chat.id, message.message_id)
        with db_connect() as conn:
            conn.execute("UPDATE tickets SET updated_at=? WHERE id=?", (int(time.time()), int(ticket["id"])))
        await message.reply_text("✅ Η απάντηση στάλθηκε.")
    except Forbidden:
        await message.reply_text("❌ Το μέλος έχει μπλοκάρει το bot ή δεν έχει πατήσει Start.")


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_private(update):
        return ConversationHandler.END
    sender = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    if not get_admin_chat_id():
        await sender.reply_text("Το σύστημα αναφορών χρειάζεται πρώτα /setupadminchat.")
        return ConversationHandler.END
    context.user_data["report"] = {}
    await sender.reply_text("🚨 <b>Αναφορά μέλους</b>\n\nΓράψε το @username του μέλους:", parse_mode=ParseMode.HTML)
    return REPORT_USER


async def report_user_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not text or len(text) > 100:
        await update.effective_message.reply_text("Γράψε ένα σύντομο username ή όνομα.")
        return REPORT_USER
    context.user_data["report"]["user"] = text
    await update.effective_message.reply_text("Περιέγραψε τι συνέβη, έως 1500 χαρακτήρες:")
    return REPORT_DETAILS


async def report_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 5 <= len(text) <= 1500:
        await update.effective_message.reply_text("Η περιγραφή πρέπει να είναι 5–1500 χαρακτήρες.")
        return REPORT_DETAILS
    context.user_data["report"]["details"] = text
    await update.effective_message.reply_text("Στείλε προαιρετικά screenshot/απόδειξη ή γράψε skip:")
    return REPORT_EVIDENCE


async def report_evidence(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    has_media = bool(message.photo or message.document or message.video)
    if not has_media and extract_text(message).lower() not in {"skip", "παράλειψη", "χωρίς"}:
        await message.reply_text("Στείλε screenshot/αρχείο ή γράψε skip.")
        return REPORT_EVIDENCE
    data = context.user_data["report"]
    subject = f"Αναφερόμενο μέλος: {data['user']}\nΠεριγραφή: {data['details']}"
    try:
        ticket_id = await create_ticket(update, context, "Αναφορά μέλους", subject)
        await message.reply_text(f"🚨 Η αναφορά καταχωρήθηκε ως ticket #{ticket_id}.")
    except TelegramError:
        await message.reply_text("❌ Δεν μπόρεσα να στείλω την αναφορά.")
    context.user_data.pop("report", None)
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Presentation builder
# -----------------------------------------------------------------------------

async def presentation_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_private(update):
        return ConversationHandler.END
    sender = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    context.user_data["presentation"] = {}
    await sender.reply_text(
        "✨ <b>Δημιουργία παρουσίασης</b>\n\nΔιάλεξε κατηγορία:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Couple", callback_data="prescat:Couple"), InlineKeyboardButton("Single M", callback_data="prescat:SingleM")],
            [InlineKeyboardButton("Single F", callback_data="prescat:SingleF"), InlineKeyboardButton("Bi Couple", callback_data="prescat:BiCouple")],
            [InlineKeyboardButton("Bi Single", callback_data="prescat:BiSingle"), InlineKeyboardButton("Lesbian", callback_data="prescat:Lesbian")],
            [InlineKeyboardButton("Gay", callback_data="prescat:Gay")],
            [InlineKeyboardButton("❌ Ακύρωση", callback_data="prescat:cancel")],
        ]),
    )
    return PRES_CATEGORY


async def presentation_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    if category == "cancel":
        await query.edit_message_text("Η δημιουργία ακυρώθηκε.")
        return ConversationHandler.END
    context.user_data["presentation"]["category"] = category
    await query.edit_message_text("📍 Γράψε περιοχή ή πόλη, χωρίς ακριβή διεύθυνση:")
    return PRES_REGION


async def presentation_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 60:
        await update.effective_message.reply_text("Γράψε περιοχή έως 60 χαρακτήρες.")
        return PRES_REGION
    context.user_data["presentation"]["region"] = text
    await update.effective_message.reply_text("🎂 Γράψε ηλικία ή ηλικίες, μόνο για ενήλικες:")
    return PRES_AGES


async def presentation_ages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 60:
        await update.effective_message.reply_text("Γράψε έως 60 χαρακτήρες.")
        return PRES_AGES
    numbers = [int(x) for x in re.findall(r"\b\d{2}\b", text)]
    if any(10 <= n < 18 for n in numbers):
        await update.effective_message.reply_text("🔞 Η κοινότητα είναι μόνο για ενήλικες.")
        return PRES_AGES
    context.user_data["presentation"]["ages"] = text
    await update.effective_message.reply_text("❤️ Τι αναζητάς/αναζητάτε;")
    return PRES_LOOKING


async def presentation_looking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 300:
        await update.effective_message.reply_text("Το κείμενο πρέπει να είναι 2–300 χαρακτήρες.")
        return PRES_LOOKING
    context.user_data["presentation"]["looking"] = text
    await update.effective_message.reply_text("📝 Γράψε λίγα λόγια για εσένα/εσάς, έως 600 χαρακτήρες:")
    return PRES_BIO


def presentation_text(user, data: dict[str, str]) -> str:
    category = html.escape(data["category"])
    region = re.sub(r"\s+", "", html.escape(data["region"]))
    plural = data["category"] in {"Couple", "BiCouple"}
    return (
        f"#{category}\n#{region}\n\n"
        f"{'👫 Ηλικίες' if plural else '🎂 Ηλικία'}: {html.escape(data['ages'])}\n\n"
        f"{'❤️ Αναζητούμε' if plural else '❤️ Αναζητώ'}: {html.escape(data['looking'])}\n\n"
        f"{'📝 Λίγα λόγια για εμάς' if plural else '📝 Λίγα λόγια για εμένα'}: {html.escape(data['bio'])}\n\n"
        f"👤 {('@' + html.escape(user.username)) if user.username else user.mention_html()}"
    )


async def presentation_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = extract_text(update.effective_message)
    if not 2 <= len(text) <= 600:
        await update.effective_message.reply_text("Το κείμενο πρέπει να είναι 2–600 χαρακτήρες.")
        return PRES_BIO
    data = context.user_data["presentation"]
    data["bio"] = text
    combined = " ".join(data.values())
    if URL_RE.search(combined) or USERNAME_RE.search(combined):
        await update.effective_message.reply_text("❌ Η παρουσίαση δεν πρέπει να περιέχει links ή διαφημιστικά usernames.")
        return PRES_BIO
    preview = presentation_text(update.effective_user, data)
    await update.effective_message.reply_text(
        f"👁️ <b>ΠΡΟΕΠΙΣΚΟΠΗΣΗ</b>\n\n{preview}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Δημοσίευση", callback_data="presconfirm:publish")],
            [InlineKeyboardButton("📋 Μόνο κείμενο", callback_data="presconfirm:text")],
            [InlineKeyboardButton("❌ Ακύρωση", callback_data="presconfirm:cancel")],
        ]),
    )
    return PRES_CONFIRM


async def presentation_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    data = context.user_data.get("presentation")
    if action == "cancel" or not data:
        context.user_data.pop("presentation", None)
        await query.edit_message_text("Η παρουσίαση ακυρώθηκε.")
        return ConversationHandler.END
    text = presentation_text(query.from_user, data)
    if action == "text":
        await query.edit_message_text(f"📋 <b>Η παρουσίασή σου</b>\n\n{text}", parse_mode=ParseMode.HTML)
        context.user_data.pop("presentation", None)
        return ConversationHandler.END
    group_id = get_main_group_id()
    if not group_id:
        await query.message.reply_text("⚙️ Ένας admin πρέπει πρώτα να τρέξει /setupgroup στην κύρια ομάδα.")
        return PRES_CONFIRM
    if feature_enabled(group_id, "presentation_verified") and not is_verified(query.from_user.id):
        await query.message.reply_text("🔒 Για δημοσίευση απαιτείται πρώτα verification.")
        return PRES_CONFIRM
    try:
        sent = await context.bot.send_message(group_id, text, parse_mode=ParseMode.HTML)
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO presentations(user_id,category,region,ages,looking_for,bio,status,created_at,published_at,group_message_id)
                VALUES(?,?,?,?,?,?,'published',?,?,?)
                """,
                (query.from_user.id, data["category"], data["region"], data["ages"], data["looking"], data["bio"], int(time.time()), int(time.time()), sent.message_id),
            )
        await query.edit_message_text("✅ Η παρουσίασή σου δημοσιεύτηκε.")
    except TelegramError:
        await query.message.reply_text("❌ Δεν μπόρεσα να δημοσιεύσω. Έλεγξε τα δικαιώματα του bot.")
        return PRES_CONFIRM
    context.user_data.pop("presentation", None)
    return ConversationHandler.END

# -----------------------------------------------------------------------------
# FAQ / optional AI
# -----------------------------------------------------------------------------

LOCAL_FAQ = [
    ({"κανόνες", "rules"}, "Οι βασικοί κανόνες είναι σεβασμός, διακριτικότητα, συναίνεση, καθόλου spam/scam και καμία κοινοποίηση στοιχείων τρίτων."),
    ({"παρουσίαση", "προφίλ", "template"}, "Πάτησε «Δημιουργία παρουσίασης» στο /start και το bot θα σε καθοδηγήσει."),
    ({"verification", "verified", "επαλήθευση"}, "Το verification είναι αίτηση προς τους admins. Δεν χρειάζεται να στείλεις ταυτότητα, τραπεζικά στοιχεία ή κωδικούς."),
    ({"αναφορά", "report", "παρενόχληση"}, "Άνοιξε /start → «Αναφορά μέλους» και στείλε username, περιγραφή και προαιρετικά screenshots."),
    ({"ασφάλεια", "συνάντηση"}, "Η πρώτη συνάντηση καλό είναι να γίνει σε δημόσιο χώρο. Μην κοινοποιείς κωδικούς ή οικονομικά στοιχεία."),
]


def local_answer(question: str) -> str:
    words = set(re.findall(r"[\wΑ-Ωα-ωΆ-Ώά-ώ]+", question.lower()))
    best = (0, "")
    for keys, answer in LOCAL_FAQ:
        score = len(words & keys)
        if score > best[0]:
            best = (score, answer)
    return best[1] or "Δεν έχω έτοιμη απάντηση γι’ αυτό. Άνοιξε ticket για να σε βοηθήσουν οι admins."


def ai_usage_allowed(user_id: int) -> bool:
    today = datetime.now(LOCAL_TZ).date().isoformat()
    with db_connect() as conn:
        row = conn.execute("SELECT request_count FROM ai_usage WHERE user_id=? AND usage_date=?", (user_id, today)).fetchone()
    return not row or int(row["request_count"]) < AI_DAILY_LIMIT


def increment_ai_usage(user_id: int) -> None:
    today = datetime.now(LOCAL_TZ).date().isoformat()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_usage(user_id,usage_date,request_count) VALUES(?,?,1)
            ON CONFLICT(user_id,usage_date) DO UPDATE SET request_count=request_count+1
            """,
            (user_id, today),
        )


def extract_ai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    chunks: list[str] = []
    for item in payload.get("output", []):
        if isinstance(item, dict):
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
    return "\n".join(chunks).strip()


async def answer_question(question: str, user_id: int) -> str:
    if not OPENAI_API_KEY:
        return local_answer(question)
    if not ai_usage_allowed(user_id):
        return f"Έφτασες το ημερήσιο όριο των {AI_DAILY_LIMIT} ερωτήσεων. Άνοιξε ticket για βοήθεια."
    payload = {
        "model": OPENAI_MODEL,
        "instructions": (
            "Είσαι ο σύντομος ελληνόφωνος βοηθός του Secret Club. Απαντάς μόνο για κανόνες, ασφάλεια, "
            "λειτουργίες του bot, verification, tickets και παρουσιάσεις. Δεν ζητάς ή αποκαλύπτεις προσωπικά "
            "δεδομένα, έγγραφα, κωδικούς, τραπεζικά στοιχεία ή ακριβή διεύθυνση. Δεν δημιουργείς ρητό σεξουαλικό περιεχόμενο."
        ),
        "input": question[:2000],
        "max_output_tokens": 450,
        "store": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            text = extract_ai_text(response.json())
        increment_ai_usage(user_id)
        return text or local_answer(question)
    except (httpx.HTTPError, ValueError, KeyError):
        return local_answer(question)


async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_private(update):
        return ConversationHandler.END
    sender = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    chat_id = get_main_group_id() or 0
    if not feature_enabled(chat_id, "ai"):
        await sender.reply_text("Ο βοηθός είναι απενεργοποιημένος.")
        return ConversationHandler.END
    if context.args:
        question = " ".join(context.args).strip()
        await sender.reply_text("🤖 Επεξεργάζομαι την ερώτηση…")
        await sender.reply_text(await answer_question(question, update.effective_user.id))
        return ConversationHandler.END
    await sender.reply_text("🤖 Γράψε την ερώτησή σου για το Secret Club, τους κανόνες ή το bot:")
    return AI_QUESTION


async def ask_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    question = extract_text(update.effective_message)
    if not 2 <= len(question) <= 2000:
        await update.effective_message.reply_text("Η ερώτηση πρέπει να είναι 2–2000 χαρακτήρες.")
        return AI_QUESTION
    await update.effective_message.reply_text("🤖 Επεξεργάζομαι την ερώτηση…")
    await update.effective_message.reply_text(await answer_question(question, update.effective_user.id))
    return ConversationHandler.END


# -----------------------------------------------------------------------------
# Admin callbacks
# -----------------------------------------------------------------------------

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not await is_admin(context, query.message.chat.id, query.from_user.id):
        await query.answer("Μόνο admins.", show_alert=True)
        return
    data = query.data or ""
    if data.startswith("v3verify:"):
        _, action, id_text = data.split(":", 2)
        verification_id = int(id_text)
        with db_connect() as conn:
            row = conn.execute("SELECT * FROM verifications WHERE id=?", (verification_id,)).fetchone()
        if not row or row["status"] != "pending":
            await query.answer("Η αίτηση έχει ήδη κλείσει.", show_alert=True)
            return
        status = "approved" if action == "approve" else "rejected"
        with db_connect() as conn:
            conn.execute(
                "UPDATE verifications SET status=?,decided_at=?,decided_by=? WHERE id=?",
                (status, int(time.time()), query.from_user.id, verification_id),
            )
        if status == "approved":
            set_verified(int(row["user_id"]), True)
            user_text, label = f"✅ Η αίτηση verification #{verification_id} εγκρίθηκε.", "✅ ΕΓΚΡΙΘΗΚΕ"
        else:
            user_text, label = f"❌ Η αίτηση verification #{verification_id} δεν εγκρίθηκε. Άνοιξε ticket για πληροφορίες.", "❌ ΑΠΟΡΡΙΦΘΗΚΕ"
        try:
            await context.bot.send_message(int(row["user_id"]), user_text)
        except TelegramError:
            pass
        await query.answer(label)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(label)
        return
    if data.startswith("v3ticket:"):
        _, action, id_text = data.split(":", 2)
        ticket_id = int(id_text)
        with db_connect() as conn:
            ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket:
            await query.answer("Δεν βρέθηκε ticket.", show_alert=True)
            return
        if action == "claim":
            with db_connect() as conn:
                conn.execute("UPDATE tickets SET claimed_by=?,updated_at=? WHERE id=?", (query.from_user.id, int(time.time()), ticket_id))
            await query.answer("Ανέλαβες το ticket.")
            await query.message.reply_text(f"🙋 Το ticket #{ticket_id} αναλήφθηκε από {query.from_user.mention_html()}.", parse_mode=ParseMode.HTML)
        else:
            with db_connect() as conn:
                conn.execute("UPDATE tickets SET status='closed',updated_at=? WHERE id=?", (int(time.time()), ticket_id))
            try:
                await context.bot.send_message(int(ticket["user_id"]), f"🔒 Το ticket #{ticket_id} έκλεισε από τους admins.")
            except TelegramError:
                pass
            await query.answer("Το ticket έκλεισε.")
            await query.message.reply_text(f"🔒 Το ticket #{ticket_id} έκλεισε.")
        return


async def delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_private(update):
        return
    user_id = update.effective_user.id
    with db_connect() as conn:
        conn.execute("DELETE FROM verifications WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM presentations WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM tickets WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM ai_usage WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM daily_activity WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM members WHERE user_id=?", (user_id,))
        conn.execute("UPDATE moderation_log SET target_id=NULL WHERE target_id=?", (user_id,))
        conn.execute("UPDATE moderation_log SET actor_id=NULL WHERE actor_id=?", (user_id,))
    await update.effective_message.reply_text("✅ Τα προσωπικά δεδομένα που τηρούσε το bot διαγράφηκαν.")


PRIVACY_TEXT = """
🔐 <b>ΙΔΙΩΤΙΚΟΤΗΤΑ</b>

Το bot αποθηκεύει μόνο όσα χρειάζονται για activity, warnings, verification, tickets και παρουσιάσεις.
Δεν στέλνεις ποτέ ταυτότητα, τραπεζικά στοιχεία, κωδικούς ή ακριβή διεύθυνση.
Με <code>/deletemydata</code> διαγράφεις τα προσωπικά δεδομένα που τηρεί το bot.
""".strip()


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(PRIVACY_TEXT, parse_mode=ParseMode.HTML)


def register_flow_handlers(application: Application) -> None:
    verify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(verify_start, pattern=r"^flow:verify$"), CommandHandler("verify", verify_start)],
        states={
            VERIFY_AGE: [CallbackQueryHandler(verify_age, pattern=r"^verify:(adult|cancel)$")],
            VERIFY_CATEGORY: [CallbackQueryHandler(verify_category, pattern=r"^verifycat:")],
            VERIFY_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_region)],
            VERIFY_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_note)],
            VERIFY_PHOTO: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, verify_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True,
    )
    ticket_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ticket_start, pattern=r"^flow:ticket$"), CommandHandler("ticket", ticket_start)],
        states={
            TICKET_CATEGORY: [CallbackQueryHandler(ticket_category, pattern=r"^ticketcat:")],
            TICKET_MESSAGE: [MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VOICE) & ~filters.COMMAND, ticket_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True,
    )
    report_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(report_start, pattern=r"^flow:report$"), CommandHandler("report", report_start)],
        states={
            REPORT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_user_step)],
            REPORT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_details)],
            REPORT_EVIDENCE: [MessageHandler((filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO) & ~filters.COMMAND, report_evidence)],
        },
        fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True,
    )
    pres_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(presentation_start, pattern=r"^flow:presentation$"), CommandHandler("presentation", presentation_start)],
        states={
            PRES_CATEGORY: [CallbackQueryHandler(presentation_category, pattern=r"^prescat:")],
            PRES_REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, presentation_region)],
            PRES_AGES: [MessageHandler(filters.TEXT & ~filters.COMMAND, presentation_ages)],
            PRES_LOOKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, presentation_looking)],
            PRES_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, presentation_bio)],
            PRES_CONFIRM: [CallbackQueryHandler(presentation_confirm, pattern=r"^presconfirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True,
    )
    ask_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_start, pattern=r"^flow:ask$"), CommandHandler("ask", ask_start)],
        states={AI_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_step)]},
        fallbacks=[CommandHandler("cancel", cancel)], allow_reentry=True,
    )
    for handler in (verify_conv, ticket_conv, report_conv, pres_conv, ask_conv):
        application.add_handler(handler, group=-3)
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^v3(?:verify|ticket):"), group=-3)
    application.add_handler(CallbackQueryHandler(ticket_user_callback, pattern=r"^v3ticketuser:"), group=-3)
    application.add_handler(CommandHandler("privacy", privacy), group=-3)
    application.add_handler(CommandHandler("deletemydata", delete_my_data), group=-3)
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.REPLY & ~filters.COMMAND, admin_ticket_reply),
        group=5,
    )
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VOICE) & ~filters.COMMAND, pending_ticket_reply),
        group=6,
    )
