import asyncio
import html
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Optional

from telegram import (
    ChatMember,
    ChatPermissions,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ──────────────────────────────────────────────────────────────────────────────
# ΡΥΘΜΙΣΕΙΣ ΑΠΟ RAILWAY VARIABLES
# ──────────────────────────────────────────────────────────────────────────────

BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME: str = ""
ADMIN_USERNAME: Final[str] = os.getenv("ADMIN_USERNAME", "").strip().lstrip("@")
GROUP_URL: Final[str] = os.getenv("GROUP_URL", "").strip()
LOG_CHAT_ID: Final[int] = int(os.getenv("LOG_CHAT_ID", "0") or 0)

WELCOME_ENABLED: Final[bool] = os.getenv("WELCOME_ENABLED", "true").lower() == "true"
RULES_GATE_ENABLED: Final[bool] = os.getenv("RULES_GATE_ENABLED", "true").lower() == "true"
AUTO_APPROVE_JOIN_REQUESTS: Final[bool] = (
    os.getenv("AUTO_APPROVE_JOIN_REQUESTS", "false").lower() == "true"
)

ANTI_LINKS: Final[bool] = os.getenv("ANTI_LINKS", "true").lower() == "true"
ANTI_SPAM: Final[bool] = os.getenv("ANTI_SPAM", "true").lower() == "true"
SPAM_MAX_MESSAGES: Final[int] = int(os.getenv("SPAM_MAX_MESSAGES", "6"))
SPAM_WINDOW_SECONDS: Final[int] = int(os.getenv("SPAM_WINDOW_SECONDS", "10"))
REPEAT_MAX: Final[int] = int(os.getenv("REPEAT_MAX", "3"))

INACTIVE_CHECK_ENABLED: Final[bool] = (
    os.getenv("INACTIVE_CHECK_ENABLED", "false").lower() == "true"
)
INACTIVE_DAYS: Final[int] = int(os.getenv("INACTIVE_DAYS", "90"))
INACTIVE_AUTO_KICK: Final[bool] = (
    os.getenv("INACTIVE_AUTO_KICK", "false").lower() == "true"
)
INACTIVE_WARNING_DAYS: Final[int] = int(os.getenv("INACTIVE_WARNING_DAYS", "7"))

DATABASE_PATH: Final[str] = os.getenv("DATABASE_PATH", "/data/secret_club.db")
DEFAULT_MUTE_MINUTES: Final[int] = int(os.getenv("DEFAULT_MUTE_MINUTES", "60"))

# Βασική λίστα απαγορευμένων όρων. Πρόσθεσε δικούς σου μέσω /blockword.
DEFAULT_BLOCKED_WORDS = {
    word.strip().lower()
    for word in os.getenv("BLOCKED_WORDS", "").split(",")
    if word.strip()
}

URL_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|@\w{5,})",
    flags=re.IGNORECASE,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
from v3_features import initialize_v3, register_v3_handlers, v3_menu_rows
from v3_core import admin_log as v3_admin_log, get_admin_chat_id, get_main_group_id
from v4_ui import (
    admin_callback as v4_admin_callback,
    admin_commands as v4_admin_commands,
    configure_bot as configure_v4_bot,
    group_welcome_keyboard,
    initialize_v4,
    member_callback as v4_member_callback,
    panel_command as v4_panel_command,
    is_control_admin as v4_is_control_admin,
    private_commands as v4_private_commands,
    start_router as v4_start_router,
)

logger = logging.getLogger("secret-club-assistant")

# Πρόχειρη μνήμη για flood/repeated messages.
message_times: dict[tuple[int, int], deque[float]] = defaultdict(deque)
recent_texts: dict[tuple[int, int], deque[str]] = defaultdict(lambda: deque(maxlen=6))


# ──────────────────────────────────────────────────────────────────────────────
# ΒΑΣΗ ΔΕΔΟΜΕΝΩΝ
# ──────────────────────────────────────────────────────────────────────────────

def db_connect() -> sqlite3.Connection:
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                joined_at INTEGER,
                last_active INTEGER,
                rules_accepted INTEGER NOT NULL DEFAULT 0,
                warnings INTEGER NOT NULL DEFAULT 0,
                inactive_warned_at INTEGER,
                is_exempt INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS blocked_words (
                chat_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                PRIMARY KEY (chat_id, word)
            );

            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (chat_id, key)
            );

            CREATE INDEX IF NOT EXISTS idx_members_last_active
            ON members(chat_id, last_active);
            """
        )


def upsert_member(
    chat_id: int,
    user_id: int,
    username: Optional[str],
    first_name: Optional[str],
    *,
    joined: bool = False,
    active: bool = False,
) -> None:
    now = int(time.time())
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO members (
                chat_id, user_id, username, first_name,
                joined_at, last_active
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                joined_at = CASE
                    WHEN ? = 1 AND members.joined_at IS NULL THEN excluded.joined_at
                    ELSE members.joined_at
                END,
                last_active = CASE
                    WHEN ? = 1 THEN excluded.last_active
                    ELSE members.last_active
                END
            """,
            (
                chat_id,
                user_id,
                username,
                first_name,
                now if joined else None,
                now if active or joined else None,
                int(joined),
                int(active or joined),
            ),
        )


def set_rules_accepted(chat_id: int, user_id: int, accepted: bool = True) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            UPDATE members
            SET rules_accepted = ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (int(accepted), chat_id, user_id),
        )


def add_warning(chat_id: int, user_id: int) -> int:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO members(chat_id, user_id, warnings)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET warnings = warnings + 1
            """,
            (chat_id, user_id),
        )
        row = conn.execute(
            "SELECT warnings FROM members WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        ).fetchone()
        return int(row["warnings"])


def clear_warnings(chat_id: int, user_id: int) -> None:
    with db_connect() as conn:
        conn.execute(
            "UPDATE members SET warnings = 0 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )


def set_exempt(chat_id: int, user_id: int, exempt: bool) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO members(chat_id, user_id, is_exempt)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET is_exempt = excluded.is_exempt
            """,
            (chat_id, user_id, int(exempt)),
        )


def get_blocked_words(chat_id: int) -> set[str]:
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT word FROM blocked_words WHERE chat_id = ?",
            (chat_id,),
        ).fetchall()
    return DEFAULT_BLOCKED_WORDS | {str(row["word"]).lower() for row in rows}


def add_blocked_word(chat_id: int, word: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blocked_words(chat_id, word) VALUES (?, ?)",
            (chat_id, word.lower()),
        )


def remove_blocked_word(chat_id: int, word: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM blocked_words WHERE chat_id = ? AND word = ?",
            (chat_id, word.lower()),
        )


def get_setting(chat_id: int, key: str) -> Optional[str]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id = ? AND key = ?",
            (chat_id, key),
        ).fetchone()
    return str(row["value"]) if row else None


def set_setting(chat_id: int, key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(chat_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(chat_id, key) DO UPDATE SET value = excluded.value
            """,
            (chat_id, key, value),
        )


def delete_setting(chat_id: int, key: str) -> None:
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM settings WHERE chat_id = ? AND key = ?",
            (chat_id, key),
        )


# ──────────────────────────────────────────────────────────────────────────────
# ΚΕΙΜΕΝΑ ΚΑΙ ΜΕΝΟΥ
# ──────────────────────────────────────────────────────────────────────────────

WELCOME_TEXT = """
🖤 <b>SECRET CLUB ASSISTANT</b>

Καλώς ήρθες!

Εδώ θα βρεις εύκολα:
• κανόνες και ασφάλεια
• πρότυπα παρουσίασης
• tags κατηγορίας και περιοχής
• αναφορά μέλους
• συχνές ερωτήσεις

👇 Διάλεξε αυτό που χρειάζεσαι:
""".strip()

RULES_TEXT = """
📜 <b>ΚΑΝΟΝΕΣ SECRET CLUB</b>

✅ Σεβόμαστε όλα τα μέλη.
✅ Η διακριτικότητα είναι απαραίτητη.
✅ Απαγορεύονται spam και ανεπιθύμητες διαφημίσεις.
✅ Απαγορεύεται η προώθηση επαγγελματικών σεξουαλικών υπηρεσιών.
✅ Δεν κοινοποιούμε φωτογραφίες, μηνύματα ή στοιχεία άλλων χωρίς άδεια.
✅ Η συναίνεση και τα προσωπικά όρια είναι απαραίτητα.
✅ Οι προσωπικές γνωριμίες και συναντήσεις γίνονται με ευθύνη των μελών.
✅ Οι διαχειριστές μπορούν να απομακρύνουν μέλη που παραβιάζουν τους κανόνες.

🔞 Η κοινότητα απευθύνεται αποκλειστικά σε ενήλικες.
""".strip()

PAGES = {
    "rules": RULES_TEXT,
    "tags": """
🏷️ <b>TAGS ΚΑΤΗΓΟΡΙΑΣ & ΠΕΡΙΟΧΗΣ</b>

Στην παρουσίασή σου βάλε:
1️⃣ ένα tag κατηγορίας
2️⃣ ένα tag περιοχής

<b>Κατηγορίες</b>
#Couple  #BiCouple
#SingleM  #SingleF
#BiSingle  #Lesbian  #Gay

<b>Περιοχές</b>
#Αττική  #Αθήνα  #Πειραιάς
#Θεσσαλονίκη  #Πάτρα  #Λάρισα
#Βόλος  #Τρίκαλα  #Καρδίτσα
#Λαμία  #Χαλκίδα  #Κατερίνη
#Σέρρες  #Καβάλα  #Δράμα
#Ξάνθη  #Κομοτηνή  #Αλεξανδρούπολη
#Ιωάννινα  #Άρτα  #Πρέβεζα
#Ηγουμενίτσα  #Αγρίνιο  #Μεσολόγγι
#Κόρινθος  #Ναύπλιο  #Τρίπολη
#Καλαμάτα  #Σπάρτη  #Πύργος
#Ηράκλειο  #Χανιά  #Ρέθυμνο
#Λασίθι  #ΆγιοςΝικόλαος
#Κέρκυρα  #Ζάκυνθος  #Κεφαλονιά
#Λευκάδα  #Λέσβος  #Χίος
#Σάμος  #Ικαρία  #Λήμνος
#Ρόδος  #Κως  #Κάλυμνος
#Κάρπαθος  #Λέρος  #Πάτμος
#Σύμη  #Νίσυρος  #Αστυπάλαια
#Καστελλόριζο  #Δωδεκάνησα
#Σύρος  #Μύκονος  #Πάρος
#Νάξος  #Σαντορίνη  #Μήλος
#Τήνος  #Άνδρος  #Ίος
#Αμοργός  #Κυκλάδες  #ΒόρειοΑιγαίο

<b>Παράδειγμα</b>
#Couple
#Ρόδος

🔎 Στην αναζήτηση του Telegram γράψε π.χ. #Ρόδος.
""".strip(),
    "couple": """
👫 <b>ΠΡΟΤΥΠΟ COUPLE</b>

<code>#Couple
#Περιοχή

👫 Ηλικίες:

❤️ Αναζητούμε:

📝 Λίγα λόγια για εμάς:</code>
""".strip(),
    "single": """
👤 <b>ΠΡΟΤΥΠΟ SINGLE</b>

<code>#SingleM ή #SingleF
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),
    "bicouple": """
🌈 <b>ΠΡΟΤΥΠΟ BI COUPLE</b>

<code>#BiCouple
#Περιοχή

👫 Ηλικίες:

❤️ Αναζητούμε:

📝 Λίγα λόγια για εμάς:</code>
""".strip(),
    "bisingle": """
🩷 <b>ΠΡΟΤΥΠΟ BI SINGLE</b>

<code>#BiSingle
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),
    "lesbian": """
👭 <b>ΠΡΟΤΥΠΟ LESBIAN</b>

<code>#Lesbian
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),
    "gay": """
👬 <b>ΠΡΟΤΥΠΟ GAY</b>

<code>#Gay
#Περιοχή

🎂 Ηλικία:

❤️ Αναζητώ:

📝 Λίγα λόγια για εμένα:</code>
""".strip(),
    "safety": """
🛡️ <b>ΣΥΜΒΟΥΛΕΣ ΑΣΦΑΛΕΙΑΣ</b>

• Μίλησε πρώτα αρκετά με το άλλο μέλος.
• Η πρώτη συνάντηση καλό είναι να γίνεται σε δημόσιο χώρο.
• Ενημέρωσε ένα πρόσωπο εμπιστοσύνης για το πού βρίσκεσαι.
• Μην κοινοποιείς προσωπικά στοιχεία πριν δημιουργηθεί εμπιστοσύνη.
• Σεβάσου πάντα τα όρια και τη συναίνεση.
• Μην πιέζεις και μην αποδέχεσαι πίεση.
• Αν κάτι σε ανησυχήσει, σταμάτησε την επικοινωνία και ενημέρωσε admins.
""".strip(),
    "report": """
🚨 <b>ΑΝΑΦΟΡΑ ΜΕΛΟΥΣ</b>

Στείλε στον admin:

• username του μέλους
• σύντομη περιγραφή
• screenshots, εφόσον υπάρχουν
• ημερομηνία ή ώρα

Οι αναφορές εξετάζονται με διακριτικότητα.
""".strip(),
    "faq": """
❓ <b>ΣΥΧΝΕΣ ΕΡΩΤΗΣΕΙΣ</b>

<b>Είναι δωρεάν;</b>
Ναι, η κοινότητα είναι εντελώς δωρεάν.

<b>Πώς βρίσκω άτομα από την περιοχή μου;</b>
Πάτησε «Tags & Περιοχές» και αναζήτησε το hashtag στο Telegram.

<b>Επιτρέπονται επαγγελματικές υπηρεσίες;</b>
Όχι.

<b>Πώς κάνω αναφορά;</b>
Πάτησε «Αναφορά» και ακολούθησε τις οδηγίες.
""".strip(),
}


def main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📜 Κανόνες", callback_data="page:rules"),
            InlineKeyboardButton("🏷️ Tags & Περιοχές", callback_data="page:tags"),
        ],
        [
            InlineKeyboardButton("👫 Couple", callback_data="page:couple"),
            InlineKeyboardButton("👤 Single", callback_data="page:single"),
        ],
        [
            InlineKeyboardButton("🌈 Bi Couple", callback_data="page:bicouple"),
            InlineKeyboardButton("🩷 Bi Single", callback_data="page:bisingle"),
        ],
        [
            InlineKeyboardButton("👭 Lesbian", callback_data="page:lesbian"),
            InlineKeyboardButton("👬 Gay", callback_data="page:gay"),
        ],
        [
            InlineKeyboardButton("🛡️ Ασφάλεια", callback_data="page:safety"),
            InlineKeyboardButton("🚨 Αναφορά", callback_data="page:report"),
        ],
        [InlineKeyboardButton("❓ Συχνές ερωτήσεις", callback_data="page:faq")],
    ]
    rows.extend(v3_menu_rows())

    links = []
    if ADMIN_USERNAME:
        links.append(
            InlineKeyboardButton(
                "👑 Επικοινωνία με Admin",
                url=f"https://t.me/{ADMIN_USERNAME}",
            )
        )
    if GROUP_URL:
        links.append(InlineKeyboardButton("🖤 Secret Club", url=GROUP_URL))
    if links:
        rows.append(links)

    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Επιστροφή", callback_data="menu")]]
    )


def rules_accept_keyboard(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Αποδέχομαι τους κανόνες",
                callback_data=f"accept:{chat_id}:{user_id}",
            )
        ]]
    )


# ──────────────────────────────────────────────────────────────────────────────
# ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ
# ──────────────────────────────────────────────────────────────────────────────

async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except TelegramError:
        return False


async def reply_admin_only(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Αυτή η εντολή είναι μόνο για admins.")


async def log_action(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    logger.info(text)
    if LOG_CHAT_ID:
        try:
            await context.bot.send_message(LOG_CHAT_ID, text)
        except TelegramError:
            logger.exception("Αποτυχία αποστολής admin log")
    try:
        await v3_admin_log(context, html.escape(text))
    except TelegramError:
        logger.exception("Αποτυχία αποστολής log στο admin chat")


async def delete_safely(message) -> bool:
    try:
        await message.delete()
        return True
    except TelegramError:
        return False


def target_from_reply(update: Update):
    message = update.effective_message
    if not message or not message.reply_to_message:
        return None
    return message.reply_to_message.from_user


async def restrict_user(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
    minutes: int,
) -> None:
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
        ),
        until_date=until,
    )


async def unrestrict_user(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> None:
    await context.bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions.all_permissions(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# ΙΔΙΩΤΙΚΟ ΜΕΝΟΥ
# ──────────────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await v4_start_router(update, context)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.args = []
    await v4_start_router(update, context)


def _welcome_value(chat_id: int, key: str, default: str = "") -> str:
    value = get_setting(chat_id, f"welcome_{key}")
    return default if value is None else value


DEFAULT_WELCOME_TEXT = (
    "🖤 <b>Welcome to Secret Club</b> 🖤\n\n"
    "Καλώς ήρθες {mention}!\n\n"
    "Χαιρόμαστε που έγινες μέλος του <b>Secret Club</b>.\n\n"
    "📌 Πάτησε τα κουμπιά παρακάτω για να ενημερωθείς και να ξεκινήσεις.\n\n"
    "✨ Σου ευχόμαστε όμορφες γνωριμίες και καλή διασκέδαση!"
)


def _render_welcome(text: str, user, chat) -> str:
    values = {
        "mention": user.mention_html(),
        "first_name": html.escape(user.first_name or "μέλος"),
        "username": html.escape(f"@{user.username}" if user.username else "χωρίς username"),
        "user_id": str(user.id),
        "group": html.escape(chat.title or "την ομάδα"),
    }
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def welcome_keyboard(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    return group_welcome_keyboard(chat_id, user_id, RULES_GATE_ENABLED)


async def _delete_message_later(bot, chat_id: int, message_id: int, seconds: int) -> None:
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


async def _send_custom_welcome(context: ContextTypes.DEFAULT_TYPE, chat, user, *, destination_chat_id: Optional[int] = None) -> bool:
    if _welcome_value(chat.id, "enabled", "1") != "1":
        return False
    kind = _welcome_value(chat.id, "type", "text")
    text = _render_welcome(_welcome_value(chat.id, "text", DEFAULT_WELCOME_TEXT), user, chat)
    file_id = _welcome_value(chat.id, "file_id")
    markup = welcome_keyboard(chat.id, user.id)
    send_chat_id = destination_chat_id or chat.id
    kwargs = {"chat_id": send_chat_id, "parse_mode": ParseMode.HTML, "reply_markup": markup}
    sent = None
    if kind == "text":
        sent = await context.bot.send_message(text=text, **kwargs)
    elif kind == "photo" and file_id:
        sent = await context.bot.send_photo(photo=file_id, caption=text or None, **kwargs)
    elif kind == "video" and file_id:
        sent = await context.bot.send_video(video=file_id, caption=text or None, **kwargs)
    elif kind == "animation" and file_id:
        sent = await context.bot.send_animation(animation=file_id, caption=text or None, **kwargs)
    elif kind == "document" and file_id:
        sent = await context.bot.send_document(document=file_id, caption=text or None, **kwargs)
    else:
        sent = await context.bot.send_message(text=text, **kwargs)
    try:
        seconds = max(0, int(_welcome_value(chat.id, "autodelete", "60")))
    except ValueError:
        seconds = 60
    if sent and seconds > 0:
        context.application.create_task(
            _delete_message_later(context.bot, send_chat_id, sent.message_id, seconds)
        )
    return True


async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    current_chat = update.effective_chat
    actor = update.effective_user
    if not message or not current_chat or not actor:
        return

    main_id = get_main_group_id()
    admin_id = get_admin_chat_id()
    if not main_id:
        await message.reply_text("⚙️ Πρώτα σύνδεσε την κύρια ομάδα γράφοντας /setupgroup μέσα σε αυτή.")
        return
    if admin_id and current_chat.id != admin_id:
        await message.reply_text("🔐 Οι ρυθμίσεις welcome γίνονται από το συνδεδεμένο admin chat.")
        return
    if current_chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.reply_text("Η /setwelcome χρησιμοποιείται στο admin chat.")
        return
    if not await v4_is_control_admin(context, actor.id):
        await reply_admin_only(update)
        return

    try:
        target_chat = await context.bot.get_chat(main_id)
    except TelegramError:
        await message.reply_text("❌ Δεν μπορώ να διαβάσω την κύρια ομάδα. Έλεγξε ότι το bot είναι admin.")
        return

    arg = " ".join(context.args).strip()
    command = arg.lower()
    if command.startswith("autodelete"):
        parts = command.split()
        if len(parts) != 2:
            await message.reply_text("Χρήση: /setwelcome autodelete 60 ή /setwelcome autodelete off")
            return
        if parts[1] in {"off", "0", "disable"}:
            set_setting(main_id, "welcome_autodelete", "0")
            await message.reply_text("🗑️ Η αυτόματη διαγραφή απενεργοποιήθηκε.")
            return
        try:
            seconds = int(parts[1])
        except ValueError:
            await message.reply_text("Ο χρόνος πρέπει να είναι αριθμός δευτερολέπτων.")
            return
        if seconds < 10 or seconds > 86400:
            await message.reply_text("Δώσε χρόνο από 10 έως 86400 δευτερόλεπτα.")
            return
        set_setting(main_id, "welcome_autodelete", str(seconds))
        await message.reply_text(f"🗑️ Το welcome θα διαγράφεται μετά από {seconds} δευτερόλεπτα.")
        return
    if command in {"off", "disable"}:
        set_setting(main_id, "welcome_enabled", "0")
        await message.reply_text("🔕 Το welcome απενεργοποιήθηκε.")
        return
    if command in {"on", "enable"}:
        set_setting(main_id, "welcome_enabled", "1")
        await message.reply_text("🔔 Το welcome ενεργοποιήθηκε.")
        return
    if command in {"reset", "delete", "clear"}:
        for key in ("enabled", "type", "text", "file_id", "autodelete"):
            delete_setting(main_id, f"welcome_{key}")
        await message.reply_text("♻️ Το welcome επανήλθε στο επαγγελματικό προεπιλεγμένο μήνυμα.")
        return
    if command == "status":
        kind = _welcome_value(main_id, "type", "text")
        enabled = _welcome_value(main_id, "enabled", "1") == "1"
        autodelete = _welcome_value(main_id, "autodelete", "60")
        await message.reply_text(
            f"👋 Welcome: {'ενεργό' if enabled else 'ανενεργό'}\nΤύπος: {kind}\n"
            f"Αυτόματη διαγραφή: {'OFF' if autodelete == '0' else autodelete + ' δευτ.'}\n"
            "Κουμπιά: deep links προς Κανόνες, Verification, FAQ, Ticket και Assistant\n"
            "Placeholders: {mention} {first_name} {username} {user_id} {group}"
        )
        return
    if command == "preview":
        await _send_custom_welcome(context, target_chat, actor, destination_chat_id=current_chat.id)
        return

    source = message.reply_to_message
    kind = ""
    file_id = ""
    text = arg
    if source:
        if source.photo:
            kind, file_id = "photo", source.photo[-1].file_id
        elif source.video:
            kind, file_id = "video", source.video.file_id
        elif source.animation:
            kind, file_id = "animation", source.animation.file_id
        elif source.document:
            kind, file_id = "document", source.document.file_id
        elif source.text:
            kind = "text"

        if not text:
            text = source.caption or source.text or _welcome_value(
                main_id, "text", DEFAULT_WELCOME_TEXT
            )
    elif text:
        current_kind = _welcome_value(main_id, "type", "text")
        current_file = _welcome_value(main_id, "file_id")
        if current_kind != "text" and current_file:
            kind, file_id = current_kind, current_file
        else:
            kind = "text"

    if text and text != DEFAULT_WELCOME_TEXT:
        placeholders = {
            "{mention}": "__PH_MENTION__",
            "{first_name}": "__PH_FIRST__",
            "{username}": "__PH_USERNAME__",
            "{user_id}": "__PH_USERID__",
            "{group}": "__PH_GROUP__",
        }
        for token, marker in placeholders.items():
            text = text.replace(token, marker)
        text = html.escape(text)
        for token, marker in placeholders.items():
            text = text.replace(marker, token)

    if not kind:
        await message.reply_text(
            "Χρήση στο admin chat:\n"
            "• /setwelcome Το μήνυμά σου\n"
            "• reply σε φωτογραφία με /setwelcome Το κείμενό σου\n"
            "• /setwelcome autodelete 60 | autodelete off\n"
            "• /setwelcome preview | status | on | off | reset\n\n"
            "Τα deep-link κουμπιά προστίθενται αυτόματα.\n"
            "Placeholders: {mention} {first_name} {username} {user_id} {group}"
        )
        return
    if kind != "text" and len(text) > 1000:
        await message.reply_text("Η λεζάντα πολυμέσου πρέπει να είναι έως 1000 χαρακτήρες.")
        return
    if kind == "text" and len(text) > 4000:
        await message.reply_text("Το μήνυμα welcome πρέπει να είναι έως 4000 χαρακτήρες.")
        return

    set_setting(main_id, "welcome_type", kind)
    set_setting(main_id, "welcome_text", text)
    set_setting(main_id, "welcome_file_id", file_id)
    set_setting(main_id, "welcome_enabled", "1")
    if get_setting(main_id, "welcome_autodelete") is None:
        set_setting(main_id, "welcome_autodelete", "60")
    await message.reply_text("✅ Το welcome αποθηκεύτηκε στην κύρια ομάδα. Πάτησε Preview από το /panel ή γράψε /setwelcome preview.")


def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👋 Welcome", callback_data="panel:welcome"), InlineKeyboardButton("✅ Verification", callback_data="panel:verification")],
        [InlineKeyboardButton("👥 Members", callback_data="panel:members"), InlineKeyboardButton("📢 Broadcast", callback_data="panel:broadcast")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="panel:settings"), InlineKeyboardButton("📊 Statistics", callback_data="panel:stats")],
        [InlineKeyboardButton("🤖 Bot Status", callback_data="panel:status")],
        [InlineKeyboardButton("❌ Κλείσιμο", callback_data="panel:close")],
    ])


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await v4_panel_command(update, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""

    if data.startswith("v4admin:"):
        await v4_admin_callback(update, context)
        return
    if data.startswith("v4:"):
        await v4_member_callback(update, context)
        return

    if data.startswith("welcome:"):
        try:
            _, action, expected = data.split(":", 2)
            expected_user_id = int(expected)
        except (ValueError, TypeError):
            await query.answer("Μη έγκυρο κουμπί.", show_alert=True)
            return
        if query.from_user.id != expected_user_id:
            await query.answer("Αυτό το κουμπί είναι για το νέο μέλος.", show_alert=True)
            return
        alerts = {
            "rules": "📜 Σεβάσου όλα τα μέλη, μην κοινοποιείς ιδιωτικό υλικό, μην κάνεις spam και ακολούθησε τις οδηγίες των admins.",
            "verify": "✅ Για verification ακολούθησε τη διαδικασία του bot με /verify ή επικοινώνησε με admin.",
            "faq": "❓ Η ομάδα είναι δωρεάν. Για βοήθεια χρησιμοποίησε /ask ή το κουμπί επικοινωνίας με admin.",
            "admin": (f"💬 Επικοινώνησε με @{ADMIN_USERNAME}." if ADMIN_USERNAME else "💬 Κάνε tag έναν διαχειριστή της ομάδας για βοήθεια."),
        }
        await query.answer(alerts.get(action, "Δεν βρέθηκε επιλογή."), show_alert=True)
        return

    if data.startswith("panel:"):
        chat_id = query.message.chat.id
        if not await is_admin(context, chat_id, query.from_user.id):
            await query.answer("Μόνο admins.", show_alert=True)
            return
        await query.answer()
        action = data.split(":", 1)[1]
        if action == "close":
            await query.message.delete()
            return
        if action == "home":
            await query.edit_message_text("🛡️ <b>SECRET CLUB ADMIN PANEL</b>\n\nΕπίλεξε λειτουργία:", parse_mode=ParseMode.HTML, reply_markup=panel_keyboard())
            return
        back = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Πίσω", callback_data="panel:home")]])
        if action == "status":
            me = await context.bot.get_me()
            text = (
                "🤖 <b>BOT STATUS</b>\n\n"
                f"Bot: <b>@{html.escape(me.username or '')}</b>\n"
                f"Welcome: <b>{'ON' if _welcome_value(chat_id,'enabled','1') == '1' else 'OFF'}</b>\n"
                f"Welcome auto-delete: <b>{_welcome_value(chat_id,'autodelete','60')} δευτ.</b>\n"
                f"Rules gate: <b>{'ON' if RULES_GATE_ENABLED else 'OFF'}</b>\n"
                f"Anti-links: <b>{'ON' if ANTI_LINKS else 'OFF'}</b>\n"
                f"Anti-spam: <b>{'ON' if ANTI_SPAM else 'OFF'}</b>\n"
                f"Inactive check: <b>{'ON' if INACTIVE_CHECK_ENABLED else 'OFF'}</b>\n"
                f"Inactive days: <b>{INACTIVE_DAYS}</b>\n"
                f"Auto-kick inactive: <b>{'ON' if INACTIVE_AUTO_KICK else 'OFF'}</b>\n"
                f"Auto-approve joins: <b>{'ON' if AUTO_APPROVE_JOIN_REQUESTS else 'OFF'}</b>"
            )
        elif action == "welcome":
            text = ("👋 <b>WELCOME</b>\n\n"
                    f"Κατάσταση: <b>{'ON' if _welcome_value(chat_id,'enabled','1') == '1' else 'OFF'}</b>\n"
                    f"Auto-delete: <b>{_welcome_value(chat_id,'autodelete','60')} δευτ.</b>\n\n"
                    "Εντολές:\n<code>/setwelcome</code>\n<code>/setwelcome preview</code>\n"
                    "<code>/setwelcome on</code> / <code>off</code>\n<code>/setwelcome autodelete 60</code>")
        elif action == "verification":
            text = ("✅ <b>VERIFICATION</b>\n\n"
                    "Κάνε reply σε μέλος με <code>/verifyuser</code> ή χρησιμοποίησε ID.\n"
                    "<code>/verifyallknown</code> — όλα τα γνωστά μέλη\n"
                    "<code>/verifiedlist</code> — λίστα verified\n"
                    "<code>/unverifiedlist</code> — λίστα unverified")
        elif action in {"members", "stats"}:
            now = int(time.time())
            day_ago = now - 86400
            with db_connect() as conn:
                total = int(conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=?", (chat_id,)).fetchone()["c"])
                new_today = int(conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=? AND joined_at>=?", (chat_id,day_ago)).fetchone()["c"])
                verified = int(conn.execute("SELECT COUNT(DISTINCT m.user_id) c FROM members m LEFT JOIN verified_users v ON v.user_id=m.user_id WHERE m.chat_id=? AND COALESCE(v.verified,m.verified,0)=1", (chat_id,)).fetchone()["c"])
            text = (f"📊 <b>{'STATISTICS' if action == 'stats' else 'MEMBERS'}</b>\n\n"
                    f"Γνωστά μέλη: <b>{total}</b>\nVerified: <b>{verified}</b>\n"
                    f"Unverified: <b>{max(0,total-verified)}</b>\nΝέα τις τελευταίες 24 ώρες: <b>{new_today}</b>")
        elif action == "broadcast":
            text = ("📢 <b>BROADCAST</b>\n\nΓια ανακοίνωση γράψε:\n"
                    "<code>/announce Το μήνυμά σου</code>\n\n"
                    "Η αποστολή παραμένει προστατευμένη για admins.")
        else:
            text = ("⚙️ <b>SETTINGS</b>\n\n"
                    "<code>/setwelcome on|off</code>\n<code>/feature</code> — λειτουργίες V3\n"
                    "<code>/botstatus</code> — κατάσταση bot\n<code>/adminhelp</code> — όλες οι εντολές")
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back)
        return

    if data == "menu":
        await query.answer()
        await query.edit_message_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("page:"):
        await query.answer()
        page = data.split(":", 1)[1]
        text = PAGES.get(page, "Δεν βρέθηκε αυτή η επιλογή.")
        rows = []
        if page == "report" and ADMIN_USERNAME:
            rows.append([
                InlineKeyboardButton(
                    "📩 Μήνυμα στον Admin",
                    url=f"https://t.me/{ADMIN_USERNAME}",
                )
            ])
        rows.append([InlineKeyboardButton("⬅️ Επιστροφή", callback_data="menu")])
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("accept:"):
        _, chat_id_str, expected_user_id_str = data.split(":")
        chat_id = int(chat_id_str)
        expected_user_id = int(expected_user_id_str)

        if query.from_user.id != expected_user_id:
            await query.answer(
                "Αυτό το κουμπί είναι για το νέο μέλος.",
                show_alert=True,
            )
            return

        await query.answer("✅ Οι κανόνες έγιναν αποδεκτοί.")
        set_rules_accepted(chat_id, expected_user_id, True)
        try:
            await unrestrict_user(context, chat_id, expected_user_id)
        except TelegramError:
            logger.exception("Δεν μπόρεσα να άρω τον περιορισμό")

        await query.edit_message_text(
            "✅ Αποδέχτηκες τους κανόνες. Καλώς ήρθες στο Secret Club!",
        )
        await log_action(
            context,
            f"✅ Rules accepted | chat={chat_id} | user={expected_user_id}",
        )


# ──────────────────────────────────────────────────────────────────────────────
# WELCOME / CHAT MEMBERS / JOIN REQUESTS
# ──────────────────────────────────────────────────────────────────────────────

async def welcome_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or not message.new_chat_members:
        return
    main_id = get_main_group_id()
    if not main_id or chat.id != main_id:
        return

    for user in message.new_chat_members:
        if user.is_bot:
            continue

        upsert_member(
            chat.id,
            user.id,
            user.username,
            user.first_name,
            joined=True,
            active=True,
        )

        if RULES_GATE_ENABLED:
            try:
                await restrict_user(context, chat.id, user.id, minutes=24 * 60)
            except TelegramError:
                logger.exception("Δεν μπόρεσα να περιορίσω νέο μέλος")

        custom_sent = False
        try:
            custom_sent = await _send_custom_welcome(context, chat, user)
        except TelegramError:
            logger.exception("Δεν μπόρεσα να στείλω το προσαρμοσμένο welcome")

        if WELCOME_ENABLED and not custom_sent:
            gate_note = (
                "\n\n🔐 Πάτησε «Κανόνες» και αποδέξου τους ιδιωτικά για να ενεργοποιηθεί η πρόσβασή σου."
                if RULES_GATE_ENABLED else ""
            )
            await message.reply_text(
                f"👋 Καλώς ήρθες {user.mention_html()} στο 🖤 <b>Secret Club</b>!\n\n"
                "Όλες οι πληροφορίες και οι διαδικασίες ανοίγουν ιδιωτικά στην Assistant."
                f"{gate_note}",
                parse_mode=ParseMode.HTML,
                reply_markup=welcome_keyboard(chat.id, user.id),
            )

        await log_action(
            context,
            f"➕ New member | chat={chat.id} | user={user.id} @{user.username or '-'}",
        )


async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.chat_member
    if not result:
        return
    main_id = get_main_group_id()
    if not main_id or result.chat.id != main_id:
        return
    user = result.new_chat_member.user
    if result.new_chat_member.status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    }:
        upsert_member(
            result.chat.id,
            user.id,
            user.username,
            user.first_name,
            joined=True,
            active=True,
        )


async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    if not request:
        return
    main_id = get_main_group_id()
    if not main_id or request.chat.id != main_id:
        return

    user = request.from_user
    await log_action(
        context,
        f"📥 Join request | chat={request.chat.id} | user={user.id} @{user.username or '-'}",
    )

    if AUTO_APPROVE_JOIN_REQUESTS:
        try:
            await request.approve()
            await log_action(
                context,
                f"✅ Auto-approved join request | chat={request.chat.id} | user={user.id}",
            )
        except TelegramError:
            logger.exception("Αποτυχία αυτόματης έγκρισης")


# ──────────────────────────────────────────────────────────────────────────────
# ACTIVITY / ANTISPAM / MODERATION
# ──────────────────────────────────────────────────────────────────────────────

async def activity_and_moderation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if (
        not message
        or not chat
        or not user
        or user.is_bot
        or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}
    ):
        return
    main_id = get_main_group_id()
    if not main_id or chat.id != main_id:
        return

    upsert_member(
        chat.id,
        user.id,
        user.username,
        user.first_name,
        active=True,
    )

    # Οι admins δεν περνούν από αυτόματη διαγραφή.
    if await is_admin(context, chat.id, user.id):
        return

    text = (message.text or message.caption or "").strip()
    lowered = text.lower()

    blocked_words = get_blocked_words(chat.id)
    matched_word = next((word for word in blocked_words if word in lowered), None)
    if matched_word:
        await delete_safely(message)
        warnings = add_warning(chat.id, user.id)
        notice = await context.bot.send_message(
            chat.id,
            f"⚠️ {user.mention_html()}, το μήνυμα αφαιρέθηκε λόγω απαγορευμένου όρου. "
            f"Προειδοποιήσεις: {warnings}/3",
            parse_mode=ParseMode.HTML,
        )
        context.job_queue.run_once(
            lambda c: c.bot.delete_message(chat.id, notice.message_id),
            when=12,
        )
        if warnings >= 3:
            try:
                await restrict_user(context, chat.id, user.id, DEFAULT_MUTE_MINUTES)
            except TelegramError:
                logger.exception("Αποτυχία αυτόματου mute")
        return

    if ANTI_LINKS and text and URL_RE.search(text):
        await delete_safely(message)
        warnings = add_warning(chat.id, user.id)
        await context.bot.send_message(
            chat.id,
            f"🔗 {user.mention_html()}, τα links/προωθητικά usernames δεν επιτρέπονται. "
            f"Προειδοποιήσεις: {warnings}/3",
            parse_mode=ParseMode.HTML,
        )
        if warnings >= 3:
            try:
                await restrict_user(context, chat.id, user.id, DEFAULT_MUTE_MINUTES)
            except TelegramError:
                logger.exception("Αποτυχία αυτόματου mute")
        return

    key = (chat.id, user.id)
    now = time.time()

    if ANTI_SPAM:
        times = message_times[key]
        times.append(now)
        while times and now - times[0] > SPAM_WINDOW_SECONDS:
            times.popleft()

        if len(times) > SPAM_MAX_MESSAGES:
            await delete_safely(message)
            try:
                await restrict_user(context, chat.id, user.id, 10)
                await context.bot.send_message(
                    chat.id,
                    f"🚫 {user.mention_html()} έγινε mute για 10 λεπτά λόγω flood.",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                logger.exception("Αποτυχία flood mute")
            times.clear()
            return

    if text:
        normalized = re.sub(r"\s+", " ", lowered)
        texts = recent_texts[key]
        texts.append(normalized)
        if len(texts) >= REPEAT_MAX and list(texts)[-REPEAT_MAX:].count(normalized) >= REPEAT_MAX:
            await delete_safely(message)
            try:
                await restrict_user(context, chat.id, user.id, 10)
                await context.bot.send_message(
                    chat.id,
                    f"🚫 {user.mention_html()} έγινε mute για επαναλαμβανόμενα μηνύματα.",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                logger.exception("Αποτυχία repeat mute")
            texts.clear()


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /warn [λόγος]"
        )
        return

    reason = " ".join(context.args).strip() or "Παραβίαση κανόνων"
    warnings = add_warning(chat.id, target.id)
    await update.effective_message.reply_text(
        f"⚠️ {target.mention_html()} προειδοποίηση {warnings}/3\nΛόγος: {reason}",
        parse_mode=ParseMode.HTML,
    )

    if warnings >= 3:
        await restrict_user(context, chat.id, target.id, DEFAULT_MUTE_MINUTES)
        await update.effective_message.reply_text(
            f"🔇 Αυτόματο mute {DEFAULT_MUTE_MINUTES} λεπτών λόγω 3 προειδοποιήσεων."
        )

    await log_action(
        context,
        f"⚠️ Warn | chat={chat.id} | admin={actor.id} | target={target.id} | reason={reason}",
    )


async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /clearwarns"
        )
        return
    clear_warnings(chat.id, target.id)
    await update.effective_message.reply_text("✅ Οι προειδοποιήσεις μηδενίστηκαν.")


async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /mute [λεπτά]"
        )
        return

    try:
        minutes = int(context.args[0]) if context.args else DEFAULT_MUTE_MINUTES
        minutes = max(1, min(minutes, 43200))
    except ValueError:
        minutes = DEFAULT_MUTE_MINUTES

    await restrict_user(context, chat.id, target.id, minutes)
    await update.effective_message.reply_text(
        f"🔇 {target.mention_html()} έγινε mute για {minutes} λεπτά.",
        parse_mode=ParseMode.HTML,
    )
    await log_action(
        context,
        f"🔇 Mute | chat={chat.id} | admin={actor.id} | target={target.id} | min={minutes}",
    )


async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /unmute"
        )
        return

    await unrestrict_user(context, chat.id, target.id)
    await update.effective_message.reply_text(
        f"🔊 {target.mention_html()} μπορεί να γράψει ξανά.",
        parse_mode=ParseMode.HTML,
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /ban [λόγος]"
        )
        return

    reason = " ".join(context.args).strip() or "Παραβίαση κανόνων"
    await context.bot.ban_chat_member(chat.id, target.id)
    await update.effective_message.reply_text(
        f"⛔ {target.mention_html()} απομακρύνθηκε.\nΛόγος: {reason}",
        parse_mode=ParseMode.HTML,
    )
    await log_action(
        context,
        f"⛔ Ban | chat={chat.id} | admin={actor.id} | target={target.id} | reason={reason}",
    )


async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα του μέλους με /kick [λόγος]"
        )
        return

    reason = " ".join(context.args).strip() or "Απομάκρυνση από admin"
    await context.bot.ban_chat_member(chat.id, target.id)
    await context.bot.unban_chat_member(chat.id, target.id, only_if_banned=True)
    await update.effective_message.reply_text(
        f"👢 {target.mention_html()} αφαιρέθηκε και μπορεί να ξαναμπεί με νέο invite.",
        parse_mode=ParseMode.HTML,
    )
    await log_action(
        context,
        f"👢 Kick | chat={chat.id} | admin={actor.id} | target={target.id} | reason={reason}",
    )


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    message = update.effective_message
    if not chat or not actor or not message:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not message.reply_to_message:
        await message.reply_text(
            "Απάντησε στο πρώτο μήνυμα που θέλεις να διαγραφεί με /purge"
        )
        return

    start_id = message.reply_to_message.message_id
    end_id = message.message_id
    deleted = 0
    for message_id in range(start_id, end_id + 1):
        try:
            await context.bot.delete_message(chat.id, message_id)
            deleted += 1
        except TelegramError:
            pass
        await asyncio.sleep(0.035)

    await log_action(
        context,
        f"🧹 Purge | chat={chat.id} | admin={actor.id} | deleted={deleted}",
    )


async def blockword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    word = " ".join(context.args).strip().lower()
    if not word:
        await update.effective_message.reply_text("/blockword λέξη ή φράση")
        return
    add_blocked_word(chat.id, word)
    await update.effective_message.reply_text(f"✅ Προστέθηκε: {word}")


async def unblockword_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    word = " ".join(context.args).strip().lower()
    if not word:
        await update.effective_message.reply_text("/unblockword λέξη ή φράση")
        return
    remove_blocked_word(chat.id, word)
    await update.effective_message.reply_text(f"✅ Αφαιρέθηκε: {word}")


async def words_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    words = sorted(get_blocked_words(chat.id))
    await update.effective_message.reply_text(
        "🚫 Απαγορευμένοι όροι:\n" + ("\n".join(words) if words else "Κανένας")
    )


async def exempt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα μέλους με /exempt"
        )
        return
    set_exempt(chat.id, target.id, True)
    await update.effective_message.reply_text(
        "✅ Το μέλος εξαιρέθηκε από τον έλεγχο αδράνειας."
    )


async def unexempt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    target = target_from_reply(update)
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    if not target:
        await update.effective_message.reply_text(
            "Απάντησε στο μήνυμα μέλους με /unexempt"
        )
        return
    set_exempt(chat.id, target.id, False)
    await update.effective_message.reply_text(
        "✅ Αφαιρέθηκε η εξαίρεση αδράνειας."
    )


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return
    await update.effective_message.reply_text(
        """
👑 <b>ADMIN COMMANDS</b>

Απάντησε στο μήνυμα μέλους:

/warn [λόγος] — προειδοποίηση
/clearwarns — μηδενισμός προειδοποιήσεων
/mute [λεπτά] — προσωρινό mute
/unmute — άρση mute
/kick [λόγος] — αφαίρεση, μπορεί να ξαναμπεί
/ban [λόγος] — αποκλεισμός
/exempt — εξαίρεση από inactivity
/unexempt — αφαίρεση εξαίρεσης

Άλλες εντολές:
/purge — ως reply στο πρώτο μήνυμα
/blockword λέξη
/unblockword λέξη
/words
/inactive — λίστα υποψήφιων inactive
/inactive_run — χειροκίνητος έλεγχος
/botstatus — έλεγχος ρυθμίσεων

⚠️ Το inactivity αφορά μόνο μέλη που το bot έχει δει από τότε που εγκαταστάθηκε.
""".strip(),
        parse_mode=ParseMode.HTML,
    )


async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return

    me = await context.bot.get_me()
    await update.effective_message.reply_text(
        f"""
🤖 <b>BOT STATUS</b>

Bot: @{me.username}
Welcome: {WELCOME_ENABLED}
Rules gate: {RULES_GATE_ENABLED}
Anti-links: {ANTI_LINKS}
Anti-spam: {ANTI_SPAM}
Inactive check: {INACTIVE_CHECK_ENABLED}
Inactive days: {INACTIVE_DAYS}
Auto-kick inactive: {INACTIVE_AUTO_KICK}
Auto-approve join requests: {AUTO_APPROVE_JOIN_REQUESTS}
""".strip(),
        parse_mode=ParseMode.HTML,
    )


# ──────────────────────────────────────────────────────────────────────────────
# INACTIVE USERS
# ──────────────────────────────────────────────────────────────────────────────

def inactive_candidates(chat_id: int) -> list[sqlite3.Row]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=INACTIVE_DAYS)).timestamp())
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT *
            FROM members
            WHERE chat_id = ?
              AND is_exempt = 0
              AND COALESCE(last_active, joined_at, 0) > 0
              AND COALESCE(last_active, joined_at, 0) < ?
            ORDER BY COALESCE(last_active, joined_at, 0) ASC
            """,
            (chat_id, cutoff),
        ).fetchall()


async def inactive_list_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return

    rows = inactive_candidates(chat.id)
    if not rows:
        await update.effective_message.reply_text(
            f"✅ Δεν βρέθηκαν καταγεγραμμένα μέλη ανενεργά για {INACTIVE_DAYS}+ ημέρες."
        )
        return

    lines = [f"🕒 Υποψήφιοι inactive ({INACTIVE_DAYS}+ ημέρες):"]
    for row in rows[:40]:
        username = f"@{row['username']}" if row["username"] else row["first_name"] or str(row["user_id"])
        last_active = datetime.fromtimestamp(
            int(row["last_active"] or row["joined_at"]),
            tz=timezone.utc,
        ).strftime("%d/%m/%Y")
        lines.append(f"• {username} — {last_active}")

    if len(rows) > 40:
        lines.append(f"…και ακόμα {len(rows) - 40}")

    lines.append(
        "\n⚠️ Η λίστα περιλαμβάνει μόνο μέλη που το bot έχει καταγράψει από την εγκατάστασή του."
    )
    await update.effective_message.reply_text("\n".join(lines))


async def process_inactive_for_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    *,
    manual: bool = False,
) -> tuple[int, int, int]:
    rows = inactive_candidates(chat_id)
    warned = 0
    kicked = 0
    skipped = 0
    now = int(time.time())
    warning_cooldown = now - (INACTIVE_WARNING_DAYS * 86400)

    for row in rows:
        user_id = int(row["user_id"])

        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
        except TelegramError:
            skipped += 1
            continue

        if member.status in {
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            ChatMemberStatus.LEFT,
            ChatMemberStatus.BANNED,
        }:
            skipped += 1
            continue

        if INACTIVE_AUTO_KICK:
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(
                    chat_id,
                    user_id,
                    only_if_banned=True,
                )
                kicked += 1
                await log_action(
                    context,
                    f"🕒 Inactive kick | chat={chat_id} | user={user_id}",
                )
            except TelegramError:
                skipped += 1
        else:
            warned_at = row["inactive_warned_at"]
            if warned_at and int(warned_at) > warning_cooldown:
                skipped += 1
                continue

            try:
                await context.bot.send_message(
                    chat_id,
                    f"🕒 <a href='tg://user?id={user_id}'>Μέλος</a>, "
                    f"δεν έχει καταγραφεί δραστηριότητα για {INACTIVE_DAYS}+ ημέρες. "
                    f"Γράψε ένα μήνυμα μέσα στις επόμενες {INACTIVE_WARNING_DAYS} ημέρες "
                    "για να παραμείνεις στην κοινότητα.",
                    parse_mode=ParseMode.HTML,
                )
                warned += 1
                with db_connect() as conn:
                    conn.execute(
                        """
                        UPDATE members
                        SET inactive_warned_at = ?
                        WHERE chat_id = ? AND user_id = ?
                        """,
                        (now, chat_id, user_id),
                    )
            except TelegramError:
                skipped += 1

        await asyncio.sleep(0.05)

    return warned, kicked, skipped


async def inactive_run_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat = update.effective_chat
    actor = update.effective_user
    if not chat or not actor:
        return
    if not await is_admin(context, chat.id, actor.id):
        await reply_admin_only(update)
        return

    warned, kicked, skipped = await process_inactive_for_chat(
        context,
        chat.id,
        manual=True,
    )
    await update.effective_message.reply_text(
        f"✅ Έλεγχος inactive ολοκληρώθηκε.\n"
        f"Προειδοποιήθηκαν: {warned}\n"
        f"Αφαιρέθηκαν: {kicked}\n"
        f"Παραλείφθηκαν: {skipped}"
    )


async def scheduled_inactive_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not INACTIVE_CHECK_ENABLED:
        return

    with db_connect() as conn:
        chat_rows = conn.execute(
            "SELECT DISTINCT chat_id FROM members"
        ).fetchall()

    for row in chat_rows:
        chat_id = int(row["chat_id"])
        try:
            await process_inactive_for_chat(context, chat_id)
        except TelegramError:
            logger.exception("Scheduled inactive check failed for %s", chat_id)


# ──────────────────────────────────────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    global BOT_USERNAME
    me = await application.bot.get_me()
    BOT_USERNAME = me.username or ""

    configure_v4_bot(BOT_USERNAME)
    private_commands = v4_private_commands()
    group_commands = [
        BotCommand("setupgroup", "Σύνδεση κύριας ομάδας"),
        BotCommand("setupadminchat", "Σύνδεση admin chat"),
        BotCommand("setupstatus", "Έλεγχος setup"),
        BotCommand("panel", "Admin Control Center"),
    ]

    await application.bot.set_my_commands(
        private_commands, scope=BotCommandScopeAllPrivateChats()
    )
    await application.bot.set_my_commands(
        group_commands, scope=BotCommandScopeAllGroupChats()
    )
    admin_chat_id = get_admin_chat_id()
    if admin_chat_id:
        await application.bot.set_my_commands(
            v4_admin_commands(), scope=BotCommandScopeChat(admin_chat_id)
        )
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    # Η v3 διαχειρίζεται το inactivity με ασφαλή έλεγχο δύο σταδίων.


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Λείπει η μεταβλητή BOT_TOKEN στο Railway.")

    init_db()
    initialize_v3()
    initialize_v4()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # V3 handlers μπαίνουν πρώτα, πριν από το γενικό callback της v2.
    register_v3_handlers(application)

    # Ιδιωτικό menu / inline buttons
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("help", menu_command))
    application.add_handler(
        CallbackQueryHandler(
            button_handler,
            pattern=r"^(?:v4:|v4admin:|menu$|page:|accept:|panel:|welcome:)",
        )
    )

    # Admin commands
    application.add_handler(CommandHandler("adminhelp", admin_help))
    application.add_handler(CommandHandler("botstatus", bot_status))
    application.add_handler(CommandHandler("setwelcome", setwelcome_command))
    application.add_handler(CommandHandler("panel", panel_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("clearwarns", unwarn_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("purge", purge_command))
    application.add_handler(CommandHandler("blockword", blockword_command))
    application.add_handler(CommandHandler("unblockword", unblockword_command))
    application.add_handler(CommandHandler("words", words_command))
    application.add_handler(CommandHandler("exempt", exempt_command))
    application.add_handler(CommandHandler("unexempt", unexempt_command))
    application.add_handler(CommandHandler("inactive", inactive_list_command))
    application.add_handler(CommandHandler("inactive_run", inactive_run_command))

    # Membership & join requests
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_members),
        group=0,
    )
    application.add_handler(
        ChatMemberHandler(track_chat_member, ChatMemberHandler.CHAT_MEMBER),
        group=0,
    )
    application.add_handler(ChatJoinRequestHandler(join_request), group=0)

    # Activity & moderation — μετά τα commands.
    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & ~filters.StatusUpdate.ALL
            & ~filters.COMMAND,
            activity_and_moderation,
        ),
        group=10,
    )

    application.add_error_handler(error_handler)

    logger.info("Secret Club Assistant v4.0 professional starting")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    run()
