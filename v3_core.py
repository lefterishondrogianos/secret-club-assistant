from __future__ import annotations

import asyncio
import html
import logging
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import ChatPermissions, Update, User
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

VERSION: Final[str] = "3.0.0"
DATABASE_PATH: Final[str] = os.getenv("DATABASE_PATH", "/data/secret_club.db")
ENV_MAIN_GROUP_ID: Final[int] = int(os.getenv("MAIN_GROUP_ID", "0") or 0)
ENV_ADMIN_CHAT_ID: Final[int] = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
ADMIN_USER_IDS: Final[set[int]] = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
TIMEZONE_NAME: Final[str] = os.getenv("TIMEZONE", "Europe/Athens")
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    LOCAL_TZ = timezone.utc

OPENAI_API_KEY: Final[str] = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL: Final[str] = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
AI_DAILY_LIMIT: Final[int] = max(1, int(os.getenv("AI_DAILY_LIMIT", "5")))
INACTIVE_DAYS: Final[int] = max(7, int(os.getenv("INACTIVE_DAYS", "90")))
INACTIVE_WARNING_DAYS: Final[int] = max(1, int(os.getenv("INACTIVE_WARNING_DAYS", "7")))
XP_COOLDOWN_SECONDS: Final[int] = max(20, int(os.getenv("XP_COOLDOWN_SECONDS", "60")))

FEATURE_DEFAULTS: Final[dict[str, bool]] = {
    "levels": os.getenv("LEVELS_ENABLED", "true").lower() == "true",
    "level_notices": os.getenv("LEVEL_UP_NOTICES", "false").lower() == "true",
    "auto_approve": os.getenv("AUTO_APPROVE_JOIN_REQUESTS", "false").lower() == "true",
    "inactivity": os.getenv("INACTIVE_CHECK_ENABLED", "false").lower() == "true",
    "inactive_kick": os.getenv("INACTIVE_AUTO_KICK", "false").lower() == "true",
    "presentation_verified": os.getenv("PRESENTATION_REQUIRES_VERIFIED", "false").lower() == "true",
    "ai": os.getenv("AI_ENABLED", "true").lower() == "true",
}

WEEKDAYS: Final[dict[str, int]] = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
    "ΔΕΥ": 0, "ΤΡΙ": 1, "ΤΕΤ": 2, "ΠΕΜ": 3, "ΠΑΡ": 4, "ΣΑΒ": 5, "ΚΥΡ": 6,
}

logger = logging.getLogger("secret-club-v3-core")

SCAM_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\b(?:στείλε|δώσε)\s+(?:μου\s+)?(?:τον\s+)?(?:κωδικό|otp)\b", re.I),
    re.compile(r"\b(?:εγγυημέν(?:ο|η|α)\s+(?:κέρδ(?:ος|η)|απόδοση))\b", re.I),
    re.compile(r"\b(?:πλήρωσε|στείλε χρήματα)\s+(?:για|ώστε)\s+(?:verification|επαλήθευση)\b", re.I),
]


def db_connect() -> sqlite3.Connection:
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def initialize_v3() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                scope_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(scope_id, key)
            );
            CREATE TABLE IF NOT EXISTS daily_activity (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                activity_date TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, user_id, activity_date)
            );
            CREATE TABLE IF NOT EXISTS moderation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                actor_id INTEGER,
                target_id INTEGER,
                action TEXT NOT NULL,
                reason TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                category TEXT NOT NULL,
                region TEXT NOT NULL,
                note TEXT,
                photo_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                decided_at INTEGER,
                decided_by INTEGER,
                admin_message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                category TEXT NOT NULL,
                subject TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                claimed_by INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ticket_message_map (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                ticket_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS presentations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                region TEXT NOT NULL,
                ages TEXT NOT NULL,
                looking_for TEXT NOT NULL,
                bio TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at INTEGER NOT NULL,
                published_at INTEGER,
                group_message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                schedule_kind TEXT NOT NULL,
                schedule_spec TEXT NOT NULL,
                text TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_key TEXT,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, usage_date)
            );
            CREATE TABLE IF NOT EXISTS verified_users (
                user_id INTEGER PRIMARY KEY,
                verified INTEGER NOT NULL DEFAULT 1,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_v3_tickets_status ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_v3_verifications_status ON verifications(status);
            CREATE INDEX IF NOT EXISTS idx_v3_schedules_enabled ON schedules(enabled);
            """
        )
        # Safe migration from v2 members table.
        _ensure_column(conn, "members", "verified", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "members", "xp", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "members", "level", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "members", "message_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "members", "last_xp_at", "INTEGER")


def get_setting(scope_id: int, key: str) -> Optional[str]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE scope_id = ? AND key = ?", (scope_id, key)
        ).fetchone()
    return str(row["value"]) if row else None


def set_setting(scope_id: int, key: str, value: str) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(scope_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(scope_id, key) DO UPDATE SET value = excluded.value
            """,
            (scope_id, key, value),
        )


def get_main_group_id() -> int:
    if ENV_MAIN_GROUP_ID:
        return ENV_MAIN_GROUP_ID
    value = get_setting(0, "main_group_id")
    return int(value) if value and value.lstrip("-").isdigit() else 0


def get_admin_chat_id() -> int:
    if ENV_ADMIN_CHAT_ID:
        return ENV_ADMIN_CHAT_ID
    value = get_setting(0, "admin_chat_id")
    return int(value) if value and value.lstrip("-").isdigit() else 0


def feature_enabled(chat_id: int, feature: str) -> bool:
    value = get_setting(chat_id, f"feature:{feature}")
    if value is None:
        return FEATURE_DEFAULTS.get(feature, False)
    return value.lower() == "true"


async def is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_USER_IDS:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except TelegramError:
        return False


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat and user and await is_admin(context, chat.id, user.id):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Αυτή η εντολή είναι μόνο για admins.")
    return False


async def require_private(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        return True
    message = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
    if message:
        await message.reply_text("🔐 Αυτή η λειτουργία γίνεται σε προσωπικό μήνυμα με το bot.")
    return False


def format_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> str:
    label = f"@{html.escape(username)}" if username else html.escape(first_name or "Μέλος")
    return f"<a href='tg://user?id={user_id}'>{label}</a>"


async def admin_log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    logger.info(re.sub(r"<[^>]+>", "", text))
    admin_chat_id = get_admin_chat_id()
    if not admin_chat_id:
        return
    try:
        await context.bot.send_message(admin_chat_id, text, parse_mode=ParseMode.HTML)
    except TelegramError:
        logger.exception("Admin log failed")


def is_verified(user_id: int) -> bool:
    with db_connect() as conn:
        direct = conn.execute(
            "SELECT verified FROM verified_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if direct:
            return bool(direct["verified"])
        row = conn.execute(
            "SELECT MAX(verified) AS verified FROM members WHERE user_id = ?", (user_id,)
        ).fetchone()
    return bool(row and row["verified"])


def set_verified(user_id: int, value: bool) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO verified_users(user_id,verified,updated_at) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET verified=excluded.verified,updated_at=excluded.updated_at
            """,
            (user_id, int(value), int(time.time())),
        )
        conn.execute("UPDATE members SET verified = ? WHERE user_id = ?", (int(value), user_id))


def level_from_xp(xp: int) -> int:
    return max(1, int(math.sqrt(max(0, xp) / 20)) + 1)


def xp_for_next_level(level: int) -> int:
    return level * level * 20


def update_activity(chat_id: int, user: User) -> tuple[int, int, bool]:
    now = int(time.time())
    today = datetime.now(LOCAL_TZ).date().isoformat()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user.id)
        ).fetchone()
        if not row:
            conn.execute(
                """
                INSERT INTO members(chat_id, user_id, username, first_name, joined_at, last_active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chat_id, user.id, user.username, user.first_name, now, now),
            )
            row = conn.execute(
                "SELECT * FROM members WHERE chat_id = ? AND user_id = ?", (chat_id, user.id)
            ).fetchone()
        old_level = int(row["level"] or 1)
        last_xp_at = int(row["last_xp_at"] or 0)
        gain = 1 if now - last_xp_at >= XP_COOLDOWN_SECONDS else 0
        xp = int(row["xp"] or 0) + gain
        level = level_from_xp(xp)
        conn.execute(
            """
            UPDATE members SET username=?, first_name=?, last_active=?, inactive_warned_at=NULL,
                message_count=message_count+1, xp=?, level=?,
                last_xp_at=CASE WHEN ?=1 THEN ? ELSE last_xp_at END
            WHERE chat_id=? AND user_id=?
            """,
            (user.username, user.first_name, now, xp, level, gain, now, chat_id, user.id),
        )
        conn.execute(
            """
            INSERT INTO daily_activity(chat_id, user_id, activity_date, message_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(chat_id, user_id, activity_date)
            DO UPDATE SET message_count=message_count+1
            """,
            (chat_id, user.id, today),
        )
    return xp, level, level > old_level


async def smart_scam_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message or user.is_bot:
        return
    if await is_admin(context, chat.id, user.id):
        return
    text = (message.text or message.caption or "").strip()
    if not text or not any(pattern.search(text) for pattern in SCAM_PATTERNS):
        return
    try:
        await message.delete()
    except TelegramError:
        pass
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO members(chat_id,user_id,username,first_name,warnings) VALUES(?,?,?,?,1)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET warnings=warnings+1
            """,
            (chat.id,user.id,user.username,user.first_name),
        )
        row = conn.execute(
            "SELECT warnings FROM members WHERE chat_id=? AND user_id=?", (chat.id,user.id)
        ).fetchone()
        warnings = int(row["warnings"])
    await context.bot.send_message(
        chat.id,
        f"🚨 {user.mention_html()}, το μήνυμα αφαιρέθηκε ως πιθανό scam. Προειδοποιήσεις: {warnings}/3",
        parse_mode=ParseMode.HTML,
    )
    if warnings >= 3:
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user.id,
                permissions=ChatPermissions.no_permissions(),
                until_date=datetime.now(timezone.utc) + timedelta(minutes=60),
            )
        except TelegramError:
            pass


async def v3_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if not chat or not user or not message or user.is_bot:
        return
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    _, level, leveled = update_activity(chat.id, user)
    if leveled and feature_enabled(chat.id, "levels") and feature_enabled(chat.id, "level_notices"):
        await message.reply_text(
            f"🎉 {user.mention_html()} ανέβηκε στο level <b>{level}</b>!",
            parse_mode=ParseMode.HTML,
        )


async def setup_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await update.effective_message.reply_text("Η εντολή γίνεται μέσα στην κύρια ομάδα.")
        return
    if not await require_admin(update, context):
        return
    set_setting(0, "main_group_id", str(chat.id))
    await update.effective_message.reply_text("✅ Αυτή ορίστηκε ως κύρια ομάδα Secret Club.")


async def setup_admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await update.effective_message.reply_text("Η εντολή γίνεται στην ιδιωτική ομάδα admins.")
        return
    if not await is_admin(context, chat.id, update.effective_user.id):
        await update.effective_message.reply_text("Μόνο admin αυτής της ομάδας.")
        return
    if chat.id == get_main_group_id():
        await update.effective_message.reply_text("❌ Η admin ομάδα πρέπει να είναι διαφορετική.")
        return
    set_setting(0, "admin_chat_id", str(chat.id))
    await update.effective_message.reply_text(
        "✅ Αυτή ορίστηκε ως admin chat για verification, tickets και reports."
    )


async def setup_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(
        f"⚙️ <b>V3 SETUP</b>\n\n"
        f"Κύρια ομάδα: {'✅' if get_main_group_id() else '❌'}\n"
        f"Admin chat: {'✅' if get_admin_chat_id() else '❌'}\n"
        f"Volume/database: <code>{html.escape(DATABASE_PATH)}</code>\n"
        f"Timezone: <code>{html.escape(TIMEZONE_NAME)}</code>\n"
        f"AI: {'API' if OPENAI_API_KEY else 'FAQ mode'}",
        parse_mode=ParseMode.HTML,
    )


async def feature_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    if not context.args:
        lines = ["⚙️ <b>V3 FEATURES</b>"]
        for name in FEATURE_DEFAULTS:
            lines.append(f"<code>{name}</code>: <b>{'ON' if feature_enabled(chat_id, name) else 'OFF'}</b>")
        lines.append("\nΧρήση: <code>/feature inactivity on</code>")
        await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Χρήση: /feature όνομα on|off")
        return
    name, value = context.args[0].lower(), context.args[1].lower()
    if name not in FEATURE_DEFAULTS or value not in {"on", "off", "true", "false"}:
        await update.effective_message.reply_text("Λάθος feature ή τιμή. Γράψε /feature.")
        return
    enabled = value in {"on", "true"}
    set_setting(chat_id, f"feature:{name}", str(enabled).lower())
    await update.effective_message.reply_text(f"✅ {name}: {'ON' if enabled else 'OFF'}")

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat_id = update.effective_chat.id
    now = int(time.time())
    with db_connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM members WHERE chat_id=?", (chat_id,)).fetchone()["c"]
        active7 = conn.execute(
            "SELECT COUNT(*) c FROM members WHERE chat_id=? AND last_active>=?",
            (chat_id, now - 7 * 86400),
        ).fetchone()["c"]
        active30 = conn.execute(
            "SELECT COUNT(*) c FROM members WHERE chat_id=? AND last_active>=?",
            (chat_id, now - 30 * 86400),
        ).fetchone()["c"]
        verified = conn.execute(
            "SELECT COUNT(*) c FROM members WHERE chat_id=? AND verified=1", (chat_id,)
        ).fetchone()["c"]
        open_tickets = conn.execute("SELECT COUNT(*) c FROM tickets WHERE status='open'").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) c FROM verifications WHERE status='pending'").fetchone()["c"]
        today = datetime.now(LOCAL_TZ).date().isoformat()
        today_messages = conn.execute(
            "SELECT COALESCE(SUM(message_count),0) c FROM daily_activity WHERE chat_id=? AND activity_date=?",
            (chat_id, today),
        ).fetchone()["c"]
    await update.effective_message.reply_text(
        f"📊 <b>SECRET CLUB DASHBOARD</b>\n\n"
        f"Καταγεγραμμένα μέλη: <b>{total}</b>\n"
        f"Ενεργά 7 ημερών: <b>{active7}</b>\n"
        f"Ενεργά 30 ημερών: <b>{active30}</b>\n"
        f"Verified: <b>{verified}</b>\n"
        f"Μηνύματα σήμερα: <b>{today_messages}</b>\n"
        f"Ανοιχτά tickets: <b>{open_tickets}</b>\n"
        f"Pending verification: <b>{pending}</b>",
        parse_mode=ParseMode.HTML,
    )


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == ChatType.PRIVATE:
        chat_id = get_main_group_id()
    if not chat_id:
        await update.effective_message.reply_text("Η κύρια ομάδα δεν έχει συνδεθεί.")
        return
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE chat_id=? AND user_id=?",
            (chat_id, update.effective_user.id),
        ).fetchone()
    if not row:
        await update.effective_message.reply_text("Δεν έχει καταγραφεί ακόμα δραστηριότητά σου.")
        return
    level, xp = int(row["level"]), int(row["xp"])
    await update.effective_message.reply_text(
        f"🏆 {update.effective_user.mention_html()}\n\n"
        f"Level: <b>{level}</b>\nXP: <b>{xp}</b>\n"
        f"Επόμενο level: <b>{xp_for_next_level(level)}</b> XP\n"
        f"Μηνύματα: <b>{int(row['message_count'])}</b>",
        parse_mode=ParseMode.HTML,
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.effective_chat.type == ChatType.PRIVATE:
        chat_id = get_main_group_id()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT username, first_name, xp, level FROM members
            WHERE chat_id=? ORDER BY xp DESC, message_count DESC LIMIT 10
            """,
            (chat_id,),
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("Δεν υπάρχουν ακόμα στατιστικά.")
        return
    lines = ["🏆 <b>TOP 10 ΔΡΑΣΤΗΡΙΟΤΗΤΑΣ</b>"]
    for i, row in enumerate(rows, 1):
        name = f"@{html.escape(row['username'])}" if row["username"] else html.escape(row["first_name"] or "Μέλος")
        lines.append(f"{i}. {name} — Level {int(row['level'])} / {int(row['xp'])} XP")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("/announce κείμενο")
        return
    await context.bot.send_message(
        update.effective_chat.id,
        f"📢 <b>ΑΝΑΚΟΙΝΩΣΗ</b>\n\n{html.escape(text)}",
        parse_mode=ParseMode.HTML,
    )


def valid_hhmm(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value))


async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    raw = update.effective_message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.effective_message.reply_text(
            "<code>/schedule daily 21:00 | Κείμενο</code>\n"
            "<code>/schedule weekly FRI 21:00 | Κείμενο</code>\n"
            "<code>/schedule once 2026-08-01 21:00 | Κείμενο</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    left, text = [part.strip() for part in raw.split("|", 1)]
    parts = left.split()
    kind = parts[0].lower() if parts else ""
    spec = ""
    if kind == "daily" and len(parts) == 2 and valid_hhmm(parts[1]):
        spec = parts[1]
    elif kind == "weekly" and len(parts) == 3 and parts[1].upper() in WEEKDAYS and valid_hhmm(parts[2]):
        spec = f"{parts[1].upper()}|{parts[2]}"
    elif kind == "once" and len(parts) == 3 and valid_hhmm(parts[2]):
        try:
            datetime.strptime(parts[1], "%Y-%m-%d")
            spec = f"{parts[1]}T{parts[2]}"
        except ValueError:
            pass
    if not spec or not text:
        await update.effective_message.reply_text("❌ Λάθος μορφή. Γράψε /schedule για παραδείγματα.")
        return
    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO schedules(chat_id,created_by,schedule_kind,schedule_spec,text,enabled,created_at)
            VALUES(?,?,?,?,?,1,?)
            """,
            (update.effective_chat.id, update.effective_user.id, kind, spec, text[:3500], int(time.time())),
        )
        schedule_id = int(cur.lastrowid)
    await update.effective_message.reply_text(f"✅ Πρόγραμμα #{schedule_id} δημιουργήθηκε.")


async def schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE chat_id=? AND enabled=1 ORDER BY id",
            (update.effective_chat.id,),
        ).fetchall()
    if not rows:
        await update.effective_message.reply_text("Δεν υπάρχουν ενεργά προγράμματα.")
        return
    lines = ["📅 <b>ΠΡΟΓΡΑΜΜΑΤΑ</b>"]
    for row in rows[:30]:
        lines.append(f"#{row['id']} — {html.escape(row['schedule_kind'])} {html.escape(row['schedule_spec'])}\n{html.escape(row['text'][:100])}")
    await update.effective_message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def delschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("/delschedule αριθμός")
        return
    with db_connect() as conn:
        cur = conn.execute(
            "UPDATE schedules SET enabled=0 WHERE id=? AND chat_id=?",
            (int(context.args[0]), update.effective_chat.id),
        )
    await update.effective_message.reply_text("✅ Διαγράφηκε." if cur.rowcount else "Δεν βρέθηκε.")


def schedule_due(row: sqlite3.Row, now: datetime) -> tuple[bool, str]:
    kind, spec = str(row["schedule_kind"]), str(row["schedule_spec"])
    key = now.strftime("%Y-%m-%dT%H:%M")
    if kind == "daily":
        return now.strftime("%H:%M") == spec, key
    if kind == "weekly":
        day, hhmm = spec.split("|", 1)
        return now.weekday() == WEEKDAYS.get(day.upper(), -1) and now.strftime("%H:%M") == hhmm, key
    if kind == "once":
        return key == spec, key
    return False, key


async def schedule_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(LOCAL_TZ).replace(second=0, microsecond=0)
    with db_connect() as conn:
        rows = conn.execute("SELECT * FROM schedules WHERE enabled=1").fetchall()
    for row in rows:
        due, key = schedule_due(row, now)
        if not due or row["last_run_key"] == key:
            continue
        try:
            await context.bot.send_message(
                int(row["chat_id"]),
                f"📢 <b>ΑΝΑΚΟΙΝΩΣΗ</b>\n\n{html.escape(row['text'])}",
                parse_mode=ParseMode.HTML,
            )
            with db_connect() as conn:
                conn.execute("UPDATE schedules SET last_run_key=? WHERE id=?", (key, int(row["id"])))
                if row["schedule_kind"] == "once":
                    conn.execute("UPDATE schedules SET enabled=0 WHERE id=?", (int(row["id"]),))
        except TelegramError:
            logger.exception("Schedule failed")


def inactive_candidates(chat_id: int) -> list[sqlite3.Row]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=INACTIVE_DAYS)).timestamp())
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT * FROM members WHERE chat_id=? AND is_exempt=0
            AND COALESCE(last_active,joined_at,0)>0
            AND COALESCE(last_active,joined_at,0)<?
            ORDER BY COALESCE(last_active,joined_at,0)
            """,
            (chat_id, cutoff),
        ).fetchall()


async def inactive_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    rows = inactive_candidates(update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text(f"✅ Δεν βρέθηκαν inactive για {INACTIVE_DAYS}+ ημέρες.")
        return
    lines = [f"🕒 <b>INACTIVE {INACTIVE_DAYS}+ ΗΜΕΡΕΣ</b>"]
    for row in rows[:40]:
        last = datetime.fromtimestamp(int(row["last_active"] or row["joined_at"]), tz=LOCAL_TZ).strftime("%d/%m/%Y")
        lines.append(f"• {format_user(int(row['user_id']), row['username'], row['first_name'])} — {last}")
    lines.append("\n⚠️ Η καταγραφή αρχίζει όταν το bot δει είσοδο ή μήνυμα μέλους.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def process_inactive(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> tuple[int, int, int]:
    warned = kicked = skipped = 0
    now = int(time.time())
    grace = INACTIVE_WARNING_DAYS * 86400
    auto_kick = feature_enabled(chat_id, "inactive_kick")
    for row in inactive_candidates(chat_id):
        user_id = int(row["user_id"])
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
        except TelegramError:
            skipped += 1
            continue
        if member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.LEFT, ChatMemberStatus.BANNED}:
            skipped += 1
            continue
        warned_at = int(row["inactive_warned_at"] or 0)
        if not warned_at:
            try:
                await context.bot.send_message(
                    chat_id,
                    f"🕒 {format_user(user_id,row['username'],row['first_name'])}, δεν έχει καταγραφεί δραστηριότητα για {INACTIVE_DAYS}+ ημέρες. Γράψε μέσα στις επόμενες {INACTIVE_WARNING_DAYS} ημέρες για να παραμείνεις.",
                    parse_mode=ParseMode.HTML,
                )
                with db_connect() as conn:
                    conn.execute("UPDATE members SET inactive_warned_at=? WHERE chat_id=? AND user_id=?", (now, chat_id, user_id))
                warned += 1
            except TelegramError:
                skipped += 1
        elif auto_kick and now - warned_at >= grace:
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
                kicked += 1
            except TelegramError:
                skipped += 1
        else:
            skipped += 1
        await asyncio.sleep(0.05)
    return warned, kicked, skipped


async def inactive_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    warned, kicked, skipped = await process_inactive(context, update.effective_chat.id)
    await update.effective_message.reply_text(
        f"✅ Έλεγχος ολοκληρώθηκε.\nΠροειδοποιήθηκαν: {warned}\nΑφαιρέθηκαν: {kicked}\nΠαραλείφθηκαν: {skipped}"
    )


async def inactivity_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(LOCAL_TZ)
    if now.hour != 4:
        return
    today = now.date().isoformat()
    if get_setting(0, "last_inactive_run") == today:
        return
    set_setting(0, "last_inactive_run", today)
    chat_id = get_main_group_id()
    if chat_id and feature_enabled(chat_id, "inactivity"):
        await process_inactive(context, chat_id)


async def v3_adminhelp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    await update.effective_message.reply_text(
        """
👑 <b>V3 COMMANDS</b>

/setupgroup
/setupadminchat
/setupstatus
/feature
/dashboard
/rank
/top
/announce κείμενο
/schedule
/schedules
/delschedule αριθμός
/inactive
/inactive_run

Τα moderation commands της v2 παραμένουν κανονικά.
""".strip(),
        parse_mode=ParseMode.HTML,
    )


def register_core_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("setupgroup", setup_group), group=-2)
    application.add_handler(CommandHandler("setupadminchat", setup_admin_chat), group=-2)
    application.add_handler(CommandHandler("setupstatus", setup_status), group=-2)
    application.add_handler(CommandHandler("feature", feature_command), group=-2)
    application.add_handler(CommandHandler("dashboard", dashboard), group=-2)
    application.add_handler(CommandHandler("rank", rank), group=-2)
    application.add_handler(CommandHandler("top", top), group=-2)
    application.add_handler(CommandHandler("announce", announce), group=-2)
    application.add_handler(CommandHandler("schedule", schedule), group=-2)
    application.add_handler(CommandHandler("event", schedule), group=-2)
    application.add_handler(CommandHandler("schedules", schedules), group=-2)
    application.add_handler(CommandHandler("delschedule", delschedule), group=-2)
    application.add_handler(CommandHandler("inactive", inactive_list), group=-2)
    application.add_handler(CommandHandler("inactive_run", inactive_run), group=-2)
    application.add_handler(CommandHandler("v3help", v3_adminhelp), group=-2)
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL & ~filters.COMMAND, smart_scam_filter),
        group=9,
    )
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL & ~filters.COMMAND, v3_activity),
        group=20,
    )
    if application.job_queue:
        application.job_queue.run_repeating(schedule_job, interval=30, first=10, name="v3-schedules")
        application.job_queue.run_repeating(inactivity_job, interval=1800, first=60, name="v3-inactivity")
