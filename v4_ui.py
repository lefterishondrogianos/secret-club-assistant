from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from typing import Optional

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from telegram.helpers import create_deep_linked_url

from v3_core import (
    ADMIN_USER_IDS,
    DATABASE_PATH,
    FEATURE_DEFAULTS,
    TIMEZONE_NAME,
    VERSION,
    db_connect,
    feature_enabled,
    get_admin_chat_id,
    get_main_group_id,
    get_setting,
    is_admin,
    is_verified,
    set_setting,
    set_verified,
)

logger = logging.getLogger("secret-club-v4-ui")

BOT_USERNAME = ""
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
GROUP_URL = os.getenv("GROUP_URL", "").strip()
RULES_GATE_ENABLED = os.getenv("RULES_GATE_ENABLED", "true").lower() == "true"
ANTI_LINKS_ENABLED = os.getenv("ANTI_LINKS", "true").lower() == "true"
ANTI_SPAM_ENABLED = os.getenv("ANTI_SPAM", "true").lower() == "true"

HOME_TEXT = """
🖤 <b>SECRET CLUB ASSISTANT</b>

Καλώς ήρθες στο ιδιωτικό κέντρο εξυπηρέτησης του Secret Club.

Εδώ μπορείς να ενημερωθείς, να κάνεις verification, να δημιουργήσεις παρουσίαση ή να επικοινωνήσεις με τη διαχείριση χωρίς να γεμίζει η κύρια ομάδα με μηνύματα.

👇 Διάλεξε την ενότητα που χρειάζεσαι:
""".strip()

RULES_TEXT = """
📜 <b>ΚΑΝΟΝΕΣ SECRET CLUB</b>

✅ Σεβόμαστε όλα τα μέλη και τα προσωπικά τους όρια.
✅ Η διακριτικότητα και η συναίνεση είναι απαραίτητες.
✅ Απαγορεύονται spam, απάτες και ανεπιθύμητες διαφημίσεις.
✅ Δεν κοινοποιούμε φωτογραφίες, μηνύματα ή στοιχεία τρίτων χωρίς άδεια.
✅ Δεν ζητάμε κωδικούς, τραπεζικά στοιχεία, ταυτότητες ή ακριβείς διευθύνσεις.
✅ Οι διαχειριστές μπορούν να απομακρύνουν μέλη που παραβιάζουν τους κανόνες.

🔞 Η κοινότητα απευθύνεται αποκλειστικά σε ενήλικες.
""".strip()

SAFETY_TEXT = """
🛡️ <b>ΑΣΦΑΛΕΙΑ & ΙΔΙΩΤΙΚΟΤΗΤΑ</b>

• Μίλησε αρκετά πριν από οποιαδήποτε συνάντηση.
• Η πρώτη συνάντηση καλό είναι να γίνεται σε δημόσιο χώρο.
• Μην κοινοποιείς κωδικούς, οικονομικά στοιχεία ή ακριβή διεύθυνση.
• Μην αποδέχεσαι πίεση και σεβάσου πάντα τη συναίνεση.
• Αν κάτι σε ανησυχήσει, σταμάτησε την επικοινωνία και κάνε αναφορά.
""".strip()

FAQ_TEXT = """
❓ <b>ΣΥΧΝΕΣ ΕΡΩΤΗΣΕΙΣ</b>

<b>Είναι δωρεάν;</b>
Ναι, η κοινότητα είναι δωρεάν.

<b>Πώς κάνω verification;</b>
Πάτησε «Verification» και ακολούθησε τα βήματα ιδιωτικά.

<b>Πώς κάνω παρουσίαση;</b>
Πάτησε «Δημιουργία παρουσίασης» και η Assistant θα σε καθοδηγήσει.

<b>Πώς επικοινωνώ με admins;</b>
Άνοιξε Ticket ή χρησιμοποίησε την Αναφορά μέλους.
""".strip()

TAGS_TEXT = """
🏷️ <b>TAGS & ΠΕΡΙΟΧΕΣ</b>

Στην παρουσίασή σου χρησιμοποίησε ένα tag κατηγορίας και ένα tag περιοχής.

<b>Κατηγορίες</b>
#Couple  #BiCouple  #SingleM  #SingleF
#BiSingle  #Lesbian  #Gay

<b>Παράδειγμα</b>
<code>#Couple
#Ρόδος</code>

Στην αναζήτηση του Telegram μπορείς να γράψεις το hashtag της περιοχής σου.
""".strip()

PRIVACY_TEXT = """
🔐 <b>ΙΔΙΩΤΙΚΟΤΗΤΑ</b>

Το bot κρατά μόνο τα δεδομένα που χρειάζονται για verification, tickets, παρουσιάσεις, activity και moderation.

Δεν στέλνεις ποτέ ταυτότητα, τραπεζικά στοιχεία, κωδικούς ή ακριβή διεύθυνση.

Με <code>/deletemydata</code> μπορείς να διαγράψεις τα προσωπικά δεδομένα που τηρεί το bot.
""".strip()


def configure_bot(username: str) -> None:
    global BOT_USERNAME
    BOT_USERNAME = (username or "").strip().lstrip("@")


def deep_link(payload: str = "home") -> str:
    safe_payload = (payload or "home")[:64]
    if BOT_USERNAME:
        return create_deep_linked_url(BOT_USERNAME, safe_payload)
    return "https://t.me/"


def group_welcome_keyboard(chat_id: int, user_id: int, rules_gate: bool) -> InlineKeyboardMarkup:
    # Όλα τα κουμπιά της κύριας ομάδας ανοίγουν το προσωπικό chat με το bot.
    # Η αποδοχή κανόνων γίνεται με βάση τον χρήστη που πάτησε το deep link,
    # όχι με user id μέσα στο URL. Έτσι το link παραμένει ασφαλές και λειτουργεί
    # ακόμη κι αν το welcome έχει προωθηθεί ή έχει λήξει.
    rules_payload = "join" if rules_gate else "rules"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📜 Κανόνες", url=deep_link(rules_payload)),
            InlineKeyboardButton("✅ Verification", url=deep_link("verify")),
        ],
        [
            InlineKeyboardButton("❓ Συχνές Ερωτήσεις", url=deep_link("faq")),
            InlineKeyboardButton("💬 Βοήθεια / Ticket", url=deep_link("support")),
        ],
        [InlineKeyboardButton("🤖 Άνοιγμα Secret Club Assistant", url=deep_link("home"))],
    ])


def member_home_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📜 Κανόνες", callback_data="v4:page:rules"),
            InlineKeyboardButton("🛡️ Ασφάλεια", callback_data="v4:page:safety"),
        ],
        [
            InlineKeyboardButton("✅ Verification", callback_data="v4:page:verify"),
            InlineKeyboardButton("✨ Παρουσίαση", callback_data="v4:page:presentation"),
        ],
        [
            InlineKeyboardButton("🚨 Αναφορά", callback_data="v4:page:report"),
            InlineKeyboardButton("🎫 Ticket", callback_data="v4:page:support"),
        ],
        [
            InlineKeyboardButton("🏷️ Tags & Περιοχές", callback_data="v4:page:tags"),
            InlineKeyboardButton("❓ FAQ", callback_data="v4:page:faq"),
        ],
        [
            InlineKeyboardButton("📊 Το rank μου", callback_data="v4:page:rank"),
            InlineKeyboardButton("🤖 Ρώτησε Assistant", callback_data="v4:page:assistant"),
        ],
        [InlineKeyboardButton("🔐 Ιδιωτικότητα", callback_data="v4:page:privacy")],
    ]
    links: list[InlineKeyboardButton] = []
    if GROUP_URL:
        links.append(InlineKeyboardButton("🖤 Κύρια ομάδα", url=GROUP_URL))
    if ADMIN_USERNAME:
        links.append(InlineKeyboardButton("👑 Admin", url=f"https://t.me/{ADMIN_USERNAME}"))
    if links:
        rows.append(links)
    return InlineKeyboardMarkup(rows)


def home_back_keyboard(*, extra: Optional[list[list[InlineKeyboardButton]]] = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append([InlineKeyboardButton("🏠 Αρχικό μενού", callback_data="v4:home")])
    return InlineKeyboardMarkup(rows)


def private_commands() -> list[BotCommand]:
    return [
        BotCommand("start", "Άνοιγμα της Assistant"),
        BotCommand("menu", "Κεντρικό μενού"),
        BotCommand("help", "Βοήθεια και επιλογές"),
        BotCommand("verify", "Αίτηση verification"),
        BotCommand("presentation", "Δημιουργία παρουσίασης"),
        BotCommand("ticket", "Επικοινωνία με admins"),
        BotCommand("report", "Αναφορά μέλους"),
        BotCommand("ask", "Ρώτησε την Assistant"),
        BotCommand("rank", "Η κατάταξή μου"),
        BotCommand("privacy", "Πολιτική ιδιωτικότητας"),
    ]


def admin_commands() -> list[BotCommand]:
    return [
        BotCommand("panel", "Κεντρικό admin panel"),
        BotCommand("setupstatus", "Έλεγχος σύνδεσης ομάδων"),
        BotCommand("setwelcome", "Ρύθμιση welcome"),
        BotCommand("verifyuser", "Χειροκίνητο verification"),
        BotCommand("unverifyuser", "Αφαίρεση verification"),
        BotCommand("verifyallknown", "Verify γνωστών μελών"),
        BotCommand("verifiedlist", "Λίστα verified"),
        BotCommand("unverifiedlist", "Λίστα unverified"),
        BotCommand("announce", "Ανακοίνωση στην κύρια ομάδα"),
        BotCommand("inactive", "Λίστα ανενεργών"),
        BotCommand("adminhelp", "Όλες οι admin εντολές"),
    ]


async def _delete_user_message(update: Update) -> None:
    message = update.effective_message
    if not message or update.callback_query:
        return
    try:
        await message.delete()
    except TelegramError:
        pass


async def show_member_screen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    chat = update.effective_chat
    if not chat or chat.type != ChatType.PRIVATE:
        if update.effective_message:
            await update.effective_message.reply_text(
                "🔐 Η λειτουργία ανοίγει σε προσωπικό chat με την Assistant.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🤖 Άνοιγμα Assistant", url=deep_link("home"))]]),
            )
        return

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            if query.message:
                context.user_data["v4_ui_message_id"] = query.message.message_id
            return
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                return
        except TelegramError:
            pass

    message_id = context.user_data.get("v4_ui_message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=int(message_id),
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            await _delete_user_message(update)
            return
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                await _delete_user_message(update)
                return
        except TelegramError:
            pass

    sent = await context.bot.send_message(
        chat_id=chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    context.user_data["v4_ui_message_id"] = sent.message_id
    await _delete_user_message(update)


def _verification_screen(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    if is_verified(user_id):
        return (
            "✅ <b>VERIFICATION</b>\n\nΗ κατάστασή σου είναι: <b>VERIFIED</b>.\n\nΔεν χρειάζεται να κάνεις νέα αίτηση.",
            home_back_keyboard(),
        )
    return (
        "✅ <b>VERIFICATION</b>\n\nΗ διαδικασία γίνεται ιδιωτικά και η αίτηση αποστέλλεται μόνο στην ομάδα διαχείρισης.\n\nΔεν ζητάμε ταυτότητα, τραπεζικά στοιχεία ή κωδικούς.",
        home_back_keyboard(extra=[[InlineKeyboardButton("✅ Έναρξη Verification", callback_data="flow:verify")]]),
    )


def _rank_screen(user_id: int) -> str:
    chat_id = get_main_group_id()
    if not chat_id:
        return "📊 <b>ΤΟ RANK ΜΟΥ</b>\n\nΗ κύρια ομάδα δεν έχει συνδεθεί ακόμη."
    with db_connect() as conn:
        row = conn.execute(
            "SELECT xp,level,message_count FROM members WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
        if not row:
            return "📊 <b>ΤΟ RANK ΜΟΥ</b>\n\nΔεν υπάρχει ακόμη καταγεγραμμένη δραστηριότητα."
        position_row = conn.execute(
            "SELECT COUNT(*) + 1 AS pos FROM members WHERE chat_id=? AND xp>?",
            (chat_id, int(row["xp"] or 0)),
        ).fetchone()
    return (
        "📊 <b>ΤΟ RANK ΜΟΥ</b>\n\n"
        f"Level: <b>{int(row['level'] or 1)}</b>\n"
        f"XP: <b>{int(row['xp'] or 0)}</b>\n"
        f"Μηνύματα: <b>{int(row['message_count'] or 0)}</b>\n"
        f"Θέση: <b>#{int(position_row['pos'] or 1)}</b>"
    )


async def _show_rules_route(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    request_access: bool = False,
) -> None:
    user = update.effective_user
    if not user:
        return

    main_id = get_main_group_id()
    accepted = False
    known_member = False
    if main_id:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT rules_accepted FROM members WHERE chat_id=? AND user_id=?",
                (main_id, user.id),
            ).fetchone()
        if row:
            known_member = True
            accepted = bool(row["rules_accepted"])
        elif request_access:
            try:
                membership = await context.bot.get_chat_member(main_id, user.id)
                known_member = membership.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}
            except TelegramError:
                known_member = False

    text = RULES_TEXT
    extra: list[list[InlineKeyboardButton]] = []
    if RULES_GATE_ENABLED and main_id and known_member:
        if accepted:
            text += "\n\n✅ <b>Έχεις ήδη αποδεχτεί τους κανόνες.</b>"
        else:
            text += (
                "\n\n🔐 Για να ενεργοποιηθεί η δυνατότητα αποστολής μηνυμάτων "
                "στην κύρια ομάδα, πάτησε αποδοχή."
            )
            extra.append([
                InlineKeyboardButton(
                    "✅ Αποδέχομαι τους κανόνες",
                    callback_data=f"v4:accept:{main_id}",
                )
            ])
    elif request_access and RULES_GATE_ENABLED:
        text += (
            "\n\nℹ️ Δεν βρέθηκε ενεργή συμμετοχή σου στην κύρια ομάδα. "
            "Μπες πρώτα στην ομάδα και άνοιξε ξανά τους Κανόνες."
        )

    await show_member_screen(
        update,
        context,
        text,
        home_back_keyboard(extra=extra),
    )


async def show_route(update: Update, context: ContextTypes.DEFAULT_TYPE, route: str) -> None:
    route = (route or "home").lower()
    user = update.effective_user
    if not user:
        return

    if route in {"", "start", "home", "menu"}:
        await show_member_screen(update, context, HOME_TEXT, member_home_keyboard())
        return
    if route in {"rules", "join"}:
        await _show_rules_route(update, context, request_access=(route == "join"))
        return
    if route == "safety":
        await show_member_screen(update, context, SAFETY_TEXT, home_back_keyboard())
        return
    if route == "faq":
        await show_member_screen(update, context, FAQ_TEXT, home_back_keyboard())
        return
    if route == "tags":
        await show_member_screen(update, context, TAGS_TEXT, home_back_keyboard())
        return
    if route == "privacy":
        await show_member_screen(update, context, PRIVACY_TEXT, home_back_keyboard())
        return
    if route == "verify":
        text, keyboard = _verification_screen(user.id)
        await show_member_screen(update, context, text, keyboard)
        return
    if route == "presentation":
        await show_member_screen(
            update,
            context,
            "✨ <b>ΔΗΜΙΟΥΡΓΙΑ ΠΑΡΟΥΣΙΑΣΗΣ</b>\n\nΗ Assistant θα σε καθοδηγήσει βήμα-βήμα και θα σου δείξει προεπισκόπηση πριν από τη δημοσίευση.",
            home_back_keyboard(extra=[[InlineKeyboardButton("✨ Έναρξη", callback_data="flow:presentation")]]),
        )
        return
    if route == "report":
        await show_member_screen(
            update,
            context,
            "🚨 <b>ΑΝΑΦΟΡΑ ΜΕΛΟΥΣ</b>\n\nΗ αναφορά στέλνεται ιδιωτικά στους admins. Θα χρειαστείς username ή όνομα, περιγραφή και προαιρετικά screenshot.",
            home_back_keyboard(extra=[[InlineKeyboardButton("🚨 Έναρξη Αναφοράς", callback_data="flow:report")]]),
        )
        return
    if route in {"support", "ticket"}:
        await show_member_screen(
            update,
            context,
            "🎫 <b>ΕΠΙΚΟΙΝΩΝΙΑ ΜΕ ADMINS</b>\n\nΆνοιξε ιδιωτικό ticket. Η συνομιλία θα μεταφερθεί στο ξεχωριστό admin chat.",
            home_back_keyboard(extra=[[InlineKeyboardButton("🎫 Άνοιγμα Ticket", callback_data="flow:ticket")]]),
        )
        return
    if route in {"assistant", "ask"}:
        await show_member_screen(
            update,
            context,
            "🤖 <b>SECRET CLUB ASSISTANT</b>\n\nΡώτησε για κανόνες, ασφάλεια, verification, tickets ή λειτουργίες του bot.",
            home_back_keyboard(extra=[[InlineKeyboardButton("🤖 Κάνε ερώτηση", callback_data="flow:ask")]]),
        )
        return
    if route == "rank":
        await show_member_screen(update, context, _rank_screen(user.id), home_back_keyboard())
        return

    await show_member_screen(update, context, HOME_TEXT, member_home_keyboard())


async def start_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        if update.effective_message:
            await update.effective_message.reply_text(
                "🤖 Άνοιξε την Assistant σε προσωπικό chat.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Άνοιγμα Assistant", url=deep_link("home"))]]),
            )
        return

    payload = context.args[0] if context.args else "home"

    # Συμβατότητα με προσωποποιημένα links παλιότερων εκδόσεων.
    if payload.startswith("join_"):
        parts = payload.split("_", 2)
        if len(parts) == 3:
            try:
                expected_user_id = int(parts[2])
            except ValueError:
                expected_user_id = update.effective_user.id
            if update.effective_user.id != expected_user_id:
                await show_member_screen(
                    update,
                    context,
                    "⛔ Αυτός ο παλιός σύνδεσμος καλωσορίσματος ανήκει σε άλλο μέλος.",
                    home_back_keyboard(),
                )
                return
        payload = "join"

    await show_route(update, context, payload)


async def member_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    if data == "v4:home":
        await show_route(update, context, "home")
        return
    if data.startswith("v4:page:"):
        await show_route(update, context, data.split(":", 2)[2])
        return
    if data.startswith("v4:accept:"):
        parts = data.split(":")
        try:
            chat_id = int(parts[2])
        except (IndexError, ValueError):
            await query.answer("Μη έγκυρο κουμπί.", show_alert=True)
            return

        # Παλιό callback format: v4:accept:<chat_id>:<user_id>
        if len(parts) >= 4:
            try:
                expected_user_id = int(parts[3])
            except ValueError:
                expected_user_id = query.from_user.id
            if query.from_user.id != expected_user_id:
                await query.answer("Αυτό το κουμπί είναι για το νέο μέλος.", show_alert=True)
                return

        main_id = get_main_group_id()
        if not main_id or chat_id != main_id:
            await query.answer("Η κύρια ομάδα έχει αλλάξει. Άνοιξε ξανά τους Κανόνες.", show_alert=True)
            return

        user_id = query.from_user.id
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}:
                await query.answer("Δεν είσαι πλέον μέλος της ομάδας.", show_alert=True)
                return
            with db_connect() as conn:
                conn.execute(
                    """
                    INSERT INTO members(chat_id,user_id,username,first_name,rules_accepted)
                    VALUES(?,?,?,?,1)
                    ON CONFLICT(chat_id,user_id) DO UPDATE SET
                        username=excluded.username,
                        first_name=excluded.first_name,
                        rules_accepted=1
                    """,
                    (chat_id, user_id, query.from_user.username, query.from_user.first_name),
                )
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions.all_permissions(),
            )
        except TelegramError:
            await query.answer("Δεν μπόρεσα να ενεργοποιήσω την πρόσβαση. Επικοινώνησε με admin.", show_alert=True)
            return

        await show_member_screen(
            update,
            context,
            "✅ <b>ΟΙ ΚΑΝΟΝΕΣ ΕΓΙΝΑΝ ΑΠΟΔΕΚΤΟΙ</b>\n\nΗ πρόσβασή σου στην ομάδα ενεργοποιήθηκε. Καλώς ήρθες στο Secret Club!",
            member_home_keyboard(),
        )
        return
    await query.answer()


async def is_control_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    main_id = get_main_group_id()
    admin_id = get_admin_chat_id()
    for chat_id in (main_id, admin_id):
        if chat_id and await is_admin(context, chat_id, user_id):
            return True
    return False


def _admin_counts(main_id: int) -> dict[str, int]:
    result = {
        "members": 0,
        "verified": 0,
        "pending_verify": 0,
        "open_tickets": 0,
        "new_24h": 0,
    }
    if not main_id:
        return result
    now = int(time.time())
    with db_connect() as conn:
        result["members"] = int(conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=?", (main_id,)).fetchone()["c"])
        result["verified"] = int(conn.execute(
            "SELECT COUNT(DISTINCT m.user_id) c FROM members m LEFT JOIN verified_users v ON v.user_id=m.user_id WHERE m.chat_id=? AND COALESCE(v.verified,m.verified,0)=1",
            (main_id,),
        ).fetchone()["c"])
        result["pending_verify"] = int(conn.execute("SELECT COUNT(*) c FROM verifications WHERE status='pending'").fetchone()["c"])
        result["open_tickets"] = int(conn.execute("SELECT COUNT(*) c FROM tickets WHERE status!='closed'").fetchone()["c"])
        result["new_24h"] = int(conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=? AND joined_at>=?", (main_id, now - 86400)).fetchone()["c"])
    return result


def admin_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Status", callback_data="v4admin:status"),
            InlineKeyboardButton("🔗 Setup", callback_data="v4admin:setup"),
        ],
        [
            InlineKeyboardButton("👋 Welcome", callback_data="v4admin:welcome"),
            InlineKeyboardButton("✅ Verification", callback_data="v4admin:verification"),
        ],
        [
            InlineKeyboardButton("🎫 Tickets", callback_data="v4admin:tickets"),
            InlineKeyboardButton("👥 Members", callback_data="v4admin:members"),
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="v4admin:broadcast"),
            InlineKeyboardButton("⚙️ Settings", callback_data="v4admin:settings"),
        ],
        [
            InlineKeyboardButton("📜 Logs", callback_data="v4admin:logs"),
            InlineKeyboardButton("👁 Assistant Preview", url=deep_link("home")),
        ],
        [InlineKeyboardButton("🔄 Ανανέωση", callback_data="v4admin:home")],
        [InlineKeyboardButton("❌ Κλείσιμο", callback_data="v4admin:close")],
    ])


def admin_back_keyboard(*, extra: Optional[list[list[InlineKeyboardButton]]] = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="v4admin:home")])
    return InlineKeyboardMarkup(rows)


def admin_home_text() -> str:
    main_id = get_main_group_id()
    admin_id = get_admin_chat_id()
    counts = _admin_counts(main_id)
    return (
        "🛡️ <b>SECRET CLUB CONTROL CENTER</b>\n\n"
        f"Κύρια ομάδα: <b>{'✅ Συνδεδεμένη' if main_id else '❌ Δεν έχει συνδεθεί'}</b>\n"
        f"Admin chat: <b>{'✅ Συνδεδεμένο' if admin_id else '❌ Δεν έχει συνδεθεί'}</b>\n\n"
        f"👥 Γνωστά μέλη: <b>{counts['members']}</b>\n"
        f"✅ Verified: <b>{counts['verified']}</b>\n"
        f"⏳ Αιτήσεις verification: <b>{counts['pending_verify']}</b>\n"
        f"🎫 Ανοιχτά tickets: <b>{counts['open_tickets']}</b>\n"
        f"🆕 Νέα μέλη 24ώρου: <b>{counts['new_24h']}</b>\n\n"
        "Επίλεξε ενότητα διαχείρισης:"
    )


async def _edit_admin(query, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await query.answer()
    except TelegramError:
        pass
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup, disable_web_page_preview=True)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if not chat or not user or not message:
        return
    admin_id = get_admin_chat_id()
    if not admin_id:
        await message.reply_text(
            "⚙️ Δεν έχει οριστεί admin chat. Μέσα στην ιδιωτική ομάδα admins γράψε /setupadminchat."
        )
        return
    if chat.id != admin_id:
        await message.reply_text("🔐 Η διαχείριση γίνεται αποκλειστικά στο συνδεδεμένο admin chat.")
        return
    if not await is_control_admin(context, user.id):
        await message.reply_text("⛔ Δεν έχεις δικαίωμα πρόσβασης στο Control Center.")
        return
    # Ανανεώνει το command menu του τρίτου chat κάθε φορά που ανοίγει το panel.
    # Έτσι, μετά το /setupadminchat δεν απαιτείται δεύτερο redeploy.
    try:
        await context.bot.set_my_commands(admin_commands(), scope=BotCommandScopeChat(admin_id))
    except TelegramError:
        logger.warning("Δεν μπόρεσα να ανανεώσω το admin command scope", exc_info=True)
    await message.reply_text(admin_home_text(), parse_mode=ParseMode.HTML, reply_markup=admin_home_keyboard())


def _status_text() -> str:
    main_id = get_main_group_id()
    admin_id = get_admin_chat_id()
    counts = _admin_counts(main_id)
    welcome_enabled = get_setting(main_id, "welcome_enabled") if main_id else None
    auto_delete = get_setting(main_id, "welcome_autodelete") if main_id else None
    return (
        "🤖 <b>BOT STATUS — V4.0</b>\n\n"
        f"Έκδοση core: <b>{html.escape(VERSION)}</b>\n"
        f"Bot username: <b>@{html.escape(BOT_USERNAME or 'unknown')}</b>\n"
        f"Κύρια ομάδα ID: <code>{main_id or 'NOT SET'}</code>\n"
        f"Admin chat ID: <code>{admin_id or 'NOT SET'}</code>\n"
        f"Database: <code>{html.escape(DATABASE_PATH)}</code>\n"
        f"Timezone: <code>{html.escape(TIMEZONE_NAME)}</code>\n\n"
        f"Welcome: <b>{'ON' if welcome_enabled != '0' else 'OFF'}</b>\n"
        f"Welcome auto-delete: <b>{'OFF' if auto_delete == '0' else (auto_delete or '60') + ' sec'}</b>\n"
        f"Rules gate: <b>{'ON' if RULES_GATE_ENABLED else 'OFF'}</b>\n"
        f"Anti-links: <b>{'ON' if ANTI_LINKS_ENABLED else 'OFF'}</b>\n"
        f"Anti-spam: <b>{'ON' if ANTI_SPAM_ENABLED else 'OFF'}</b>\n"
        f"Members: <b>{counts['members']}</b>\n"
        f"Verified: <b>{counts['verified']}</b>\n"
        f"Pending verification: <b>{counts['pending_verify']}</b>\n"
        f"Open tickets: <b>{counts['open_tickets']}</b>"
    )


def _welcome_panel_text(main_id: int) -> str:
    if not main_id:
        return "👋 <b>WELCOME</b>\n\nΠρέπει πρώτα να συνδεθεί η κύρια ομάδα με /setupgroup."
    enabled = get_setting(main_id, "welcome_enabled") != "0"
    kind = get_setting(main_id, "welcome_type") or "text"
    auto = get_setting(main_id, "welcome_autodelete") or "60"
    return (
        "👋 <b>WELCOME SETTINGS</b>\n\n"
        f"Κατάσταση: <b>{'ON' if enabled else 'OFF'}</b>\n"
        f"Τύπος: <b>{html.escape(kind)}</b>\n"
        f"Auto-delete: <b>{'OFF' if auto == '0' else auto + ' sec'}</b>\n"
        "Κουμπιά: <b>Deep links προς την Assistant</b>\n"
        "Rules gate: <b>Ιδιωτική αποδοχή κανόνων</b>\n\n"
        "Για νέο κείμενο ή φωτογραφία, κάνε reply στο υλικό μέσα στο admin chat με <code>/setwelcome κείμενο</code>."
    )


def _welcome_panel_keyboard(main_id: int) -> InlineKeyboardMarkup:
    enabled = get_setting(main_id, "welcome_enabled") != "0" if main_id else False
    return admin_back_keyboard(extra=[
        [InlineKeyboardButton("🔕 OFF" if enabled else "🔔 ON", callback_data="v4admin:welcome_toggle")],
        [
            InlineKeyboardButton("30s", callback_data="v4admin:welcome_auto:30"),
            InlineKeyboardButton("60s", callback_data="v4admin:welcome_auto:60"),
            InlineKeyboardButton("120s", callback_data="v4admin:welcome_auto:120"),
            InlineKeyboardButton("OFF", callback_data="v4admin:welcome_auto:0"),
        ],
        [InlineKeyboardButton("👁 Preview", callback_data="v4admin:welcome_preview")],
    ])


async def _delete_later(bot, chat_id: int, message_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass


async def send_welcome_preview(context: ContextTypes.DEFAULT_TYPE, destination_chat_id: int, user) -> None:
    main_id = get_main_group_id()
    if not main_id:
        await context.bot.send_message(destination_chat_id, "❌ Δεν έχει συνδεθεί κύρια ομάδα.")
        return
    try:
        group = await context.bot.get_chat(main_id)
    except TelegramError:
        group = type("Group", (), {"id": main_id, "title": "Secret Club"})()
    default_text = (
        "🖤 <b>Welcome to Secret Club</b> 🖤\n\n"
        "Καλώς ήρθες {mention}!\n\n"
        "📌 Πάτησε τα κουμπιά παρακάτω για να ενημερωθείς και να ξεκινήσεις."
    )
    text = get_setting(main_id, "welcome_text") or default_text
    values = {
        "{mention}": user.mention_html(),
        "{first_name}": html.escape(user.first_name or "μέλος"),
        "{username}": html.escape(f"@{user.username}" if user.username else "χωρίς username"),
        "{user_id}": str(user.id),
        "{group}": html.escape(getattr(group, "title", None) or "Secret Club"),
    }
    for token, value in values.items():
        text = text.replace(token, value)
    kind = get_setting(main_id, "welcome_type") or "text"
    file_id = get_setting(main_id, "welcome_file_id") or ""
    markup = group_welcome_keyboard(main_id, user.id, True)
    kwargs = dict(chat_id=destination_chat_id, parse_mode=ParseMode.HTML, reply_markup=markup)
    if kind == "photo" and file_id:
        sent = await context.bot.send_photo(photo=file_id, caption=text, **kwargs)
    elif kind == "video" and file_id:
        sent = await context.bot.send_video(video=file_id, caption=text, **kwargs)
    elif kind == "animation" and file_id:
        sent = await context.bot.send_animation(animation=file_id, caption=text, **kwargs)
    elif kind == "document" and file_id:
        sent = await context.bot.send_document(document=file_id, caption=text, **kwargs)
    else:
        sent = await context.bot.send_message(text=text, **kwargs)
    context.application.create_task(_delete_later(context.bot, destination_chat_id, sent.message_id, 60))


def _verification_panel_text(main_id: int) -> str:
    counts = _admin_counts(main_id)
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT id,username,first_name,category,created_at FROM verifications WHERE status='pending' ORDER BY id DESC LIMIT 5"
        ).fetchall()
    lines = [
        "✅ <b>VERIFICATION CENTER</b>",
        "",
        f"Verified: <b>{counts['verified']}</b>",
        f"Unverified γνωστά μέλη: <b>{max(0, counts['members'] - counts['verified'])}</b>",
        f"Pending αιτήσεις: <b>{counts['pending_verify']}</b>",
    ]
    if rows:
        lines.append("\n<b>Τελευταίες pending αιτήσεις</b>")
        for row in rows:
            label = f"@{row['username']}" if row["username"] else row["first_name"] or "Μέλος"
            lines.append(f"• #{row['id']} — {html.escape(label)} — {html.escape(row['category'])}")
    lines.append("\nΟι κάρτες έγκρισης εμφανίζονται αυτόματα στο admin chat.")
    return "\n".join(lines)


def _tickets_panel_text() -> str:
    with db_connect() as conn:
        open_count = int(conn.execute("SELECT COUNT(*) c FROM tickets WHERE status!='closed'").fetchone()["c"])
        unclaimed = int(conn.execute("SELECT COUNT(*) c FROM tickets WHERE status!='closed' AND claimed_by IS NULL").fetchone()["c"])
        rows = conn.execute("SELECT id,username,category,status FROM tickets WHERE status!='closed' ORDER BY updated_at DESC LIMIT 8").fetchall()
    lines = ["🎫 <b>TICKET CENTER</b>", "", f"Ανοιχτά: <b>{open_count}</b>", f"Χωρίς ανάληψη: <b>{unclaimed}</b>"]
    if rows:
        lines.append("\n<b>Πρόσφατα ανοιχτά</b>")
        for row in rows:
            label = f"@{row['username']}" if row["username"] else "χωρίς username"
            lines.append(f"• #{row['id']} — {html.escape(label)} — {html.escape(row['category'])}")
    lines.append("\nΑπάντησε με reply στην κάρτα του ticket για να σταλεί ιδιωτικά στο μέλος.")
    return "\n".join(lines)


def _members_panel_text(main_id: int) -> str:
    counts = _admin_counts(main_id)
    with db_connect() as conn:
        active_7d = int(conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=? AND last_active>=?", (main_id, int(time.time()) - 7 * 86400)).fetchone()["c"]) if main_id else 0
        warnings = int(conn.execute("SELECT COALESCE(SUM(warnings),0) c FROM members WHERE chat_id=?", (main_id,)).fetchone()["c"]) if main_id else 0
    return (
        "👥 <b>MEMBERS & STATISTICS</b>\n\n"
        f"Γνωστά μέλη: <b>{counts['members']}</b>\n"
        f"Verified: <b>{counts['verified']}</b>\n"
        f"Unverified: <b>{max(0, counts['members'] - counts['verified'])}</b>\n"
        f"Νέα μέλη 24ώρου: <b>{counts['new_24h']}</b>\n"
        f"Ενεργά 7 ημερών: <b>{active_7d}</b>\n"
        f"Συνολικές προειδοποιήσεις: <b>{warnings}</b>\n\n"
        "Οι αριθμοί αφορούν μέλη που έχει καταγράψει το bot."
    )


def _settings_text(main_id: int) -> str:
    lines = ["⚙️ <b>FEATURE SETTINGS</b>", ""]
    for feature in FEATURE_DEFAULTS:
        lines.append(f"{feature}: <b>{'ON' if feature_enabled(main_id, feature) else 'OFF'}</b>")
    return "\n".join(lines)


def _settings_keyboard(main_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    labels = {
        "levels": "Levels",
        "level_notices": "Level notices",
        "auto_approve": "Auto approve",
        "inactivity": "Inactivity",
        "inactive_kick": "Inactive kick",
        "presentation_verified": "Verified presentations",
        "ai": "Assistant AI",
    }
    for feature, label in labels.items():
        enabled = feature_enabled(main_id, feature)
        rows.append([InlineKeyboardButton(f"{'✅' if enabled else '❌'} {label}", callback_data=f"v4admin:feature:{feature}")])
    rows.append([InlineKeyboardButton("⬅️ Admin Panel", callback_data="v4admin:home")])
    return InlineKeyboardMarkup(rows)


def _logs_text(main_id: int) -> str:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT action,actor_id,target_id,reason,created_at FROM moderation_log WHERE chat_id IN (?,0) ORDER BY id DESC LIMIT 12",
            (main_id,),
        ).fetchall()
    if not rows:
        return "📜 <b>RECENT LOGS</b>\n\nΔεν υπάρχουν καταγεγραμμένα logs."
    lines = ["📜 <b>RECENT LOGS</b>", ""]
    for row in rows:
        stamp = time.strftime("%d/%m %H:%M", time.localtime(int(row["created_at"])))
        target = f" → {row['target_id']}" if row["target_id"] else ""
        lines.append(f"• <code>{stamp}</code> {html.escape(row['action'])}{target}")
    return "\n".join(lines)


def _record_admin_action(main_id: int, actor_id: int, action: str, reason: str = "") -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO moderation_log(chat_id,actor_id,target_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
            (main_id or 0, actor_id, None, action, reason, int(time.time())),
        )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    admin_id = get_admin_chat_id()
    if not admin_id or query.message.chat.id != admin_id:
        await query.answer("Το Control Center λειτουργεί μόνο στο admin chat.", show_alert=True)
        return
    if not await is_control_admin(context, query.from_user.id):
        await query.answer("Δεν έχεις δικαίωμα πρόσβασης.", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    main_id = get_main_group_id()

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except TelegramError:
            pass
        return
    if action == "home":
        await _edit_admin(query, admin_home_text(), admin_home_keyboard())
        return
    if action == "status":
        await _edit_admin(query, _status_text(), admin_back_keyboard(extra=[[InlineKeyboardButton("🔄 Refresh", callback_data="v4admin:status")]]))
        return
    if action == "setup":
        text = (
            "🔗 <b>SETUP</b>\n\n"
            f"Κύρια ομάδα: <code>{main_id or 'NOT SET'}</code>\n"
            f"Admin chat: <code>{admin_id or 'NOT SET'}</code>\n\n"
            "Στην κύρια ομάδα: <code>/setupgroup</code>\n"
            "Στην ιδιωτική ομάδα admins: <code>/setupadminchat</code>\n\n"
            "Το bot πρέπει να είναι admin και στις δύο ομάδες."
        )
        await _edit_admin(query, text, admin_back_keyboard())
        return
    if action == "welcome":
        await _edit_admin(query, _welcome_panel_text(main_id), _welcome_panel_keyboard(main_id))
        return
    if action == "welcome_toggle":
        if not main_id:
            await query.answer("Δεν έχει συνδεθεί κύρια ομάδα.", show_alert=True)
            return
        new_value = "0" if get_setting(main_id, "welcome_enabled") != "0" else "1"
        set_setting(main_id, "welcome_enabled", new_value)
        _record_admin_action(main_id, query.from_user.id, "welcome_toggle", new_value)
        await _edit_admin(query, _welcome_panel_text(main_id), _welcome_panel_keyboard(main_id))
        return
    if action == "welcome_auto" and len(parts) >= 3:
        if not main_id:
            await query.answer("Δεν έχει συνδεθεί κύρια ομάδα.", show_alert=True)
            return
        value = parts[2]
        if value not in {"0", "30", "60", "120"}:
            await query.answer("Μη έγκυρη τιμή.", show_alert=True)
            return
        set_setting(main_id, "welcome_autodelete", value)
        _record_admin_action(main_id, query.from_user.id, "welcome_autodelete", value)
        await _edit_admin(query, _welcome_panel_text(main_id), _welcome_panel_keyboard(main_id))
        return
    if action == "welcome_preview":
        await query.answer("Το preview στάλθηκε και θα διαγραφεί σε 60 δευτερόλεπτα.")
        await send_welcome_preview(context, admin_id, query.from_user)
        return
    if action == "verification":
        keyboard = admin_back_keyboard(extra=[
            [InlineKeyboardButton("✅ Verify όλα τα γνωστά", callback_data="v4admin:verifyall_confirm")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="v4admin:verification")],
        ])
        await _edit_admin(query, _verification_panel_text(main_id), keyboard)
        return
    if action == "verifyall_confirm":
        await _edit_admin(
            query,
            "⚠️ <b>ΕΠΙΒΕΒΑΙΩΣΗ</b>\n\nΘα γίνουν verified όλα τα γνωστά μέλη της κύριας ομάδας χωρίς να διαγραφεί καμία υπάρχουσα εγγραφή.",
            admin_back_keyboard(extra=[[InlineKeyboardButton("✅ Επιβεβαίωση Verify All", callback_data="v4admin:verifyall_execute")]]),
        )
        return
    if action == "verifyall_execute":
        if not main_id:
            await query.answer("Δεν έχει συνδεθεί κύρια ομάδα.", show_alert=True)
            return
        with db_connect() as conn:
            users = conn.execute("SELECT user_id,username,first_name FROM members WHERE chat_id=?", (main_id,)).fetchall()
        changed = 0
        for row in users:
            if not is_verified(int(row["user_id"])):
                changed += 1
            set_verified(
                int(row["user_id"]),
                True,
                actor_id=query.from_user.id,
                source="v4_panel_verifyallknown",
                chat_id=main_id,
                username=row["username"],
                first_name=row["first_name"],
            )
        _record_admin_action(main_id, query.from_user.id, "verify_all_known", str(len(users)))
        await query.answer(f"Νέα verified: {changed} — Γνωστά μέλη: {len(users)}.", show_alert=True)
        await _edit_admin(query, _verification_panel_text(main_id), admin_back_keyboard())
        return
    if action == "tickets":
        await _edit_admin(query, _tickets_panel_text(), admin_back_keyboard(extra=[[InlineKeyboardButton("🔄 Refresh", callback_data="v4admin:tickets")]]))
        return
    if action == "members":
        await _edit_admin(query, _members_panel_text(main_id), admin_back_keyboard(extra=[[InlineKeyboardButton("🔄 Refresh", callback_data="v4admin:members")]]))
        return
    if action == "broadcast":
        text = (
            "📢 <b>BROADCAST</b>\n\n"
            "Για άμεση ανακοίνωση στην κύρια ομάδα γράψε:\n"
            "<code>/announce Το μήνυμά σου</code>\n\n"
            "Για προγραμματισμένη ανακοίνωση χρησιμοποίησε <code>/schedule</code>."
        )
        await _edit_admin(query, text, admin_back_keyboard())
        return
    if action == "settings":
        await _edit_admin(query, _settings_text(main_id), _settings_keyboard(main_id))
        return
    if action == "feature" and len(parts) >= 3:
        feature = parts[2]
        if feature not in FEATURE_DEFAULTS or not main_id:
            await query.answer("Μη έγκυρη ρύθμιση.", show_alert=True)
            return
        new_value = not feature_enabled(main_id, feature)
        set_setting(main_id, f"feature:{feature}", str(new_value).lower())
        _record_admin_action(main_id, query.from_user.id, "feature_toggle", f"{feature}={new_value}")
        await _edit_admin(query, _settings_text(main_id), _settings_keyboard(main_id))
        return
    if action == "logs":
        await _edit_admin(query, _logs_text(main_id), admin_back_keyboard(extra=[[InlineKeyboardButton("🔄 Refresh", callback_data="v4admin:logs")]]))
        return

    await query.answer()


def initialize_v4() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES('4.0.0',?)",
            (int(time.time()),),
        )
