from __future__ import annotations

import html
import logging
import os
import sqlite3
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Final, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes, MessageReactionHandler

logger = logging.getLogger("secret-club-rank-v41")

DATABASE_PATH: Final[str] = os.getenv("DATABASE_PATH", "/data/secret_club.db")
ENV_MAIN_GROUP_ID: Final[int] = int(os.getenv("MAIN_GROUP_ID", "0") or 0)
TIMEZONE_NAME: Final[str] = os.getenv("TIMEZONE", "Europe/Athens")
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    LOCAL_TZ = timezone.utc

# Κάθε κανονικό μήνυμα μετρά χωρίς cooldown ή ημερήσιο όριο.
# Φωτογραφίες/βίντεο παίρνουν επιπλέον bonus, επειδή το βασικό μήνυμα
# περιλαμβάνεται ήδη στο message_count.
PHOTO_BONUS: Final[int] = 4
VIDEO_BONUS: Final[int] = 7
REPLY_SENT_POINTS: Final[int] = 2
REPLY_RECEIVED_POINTS: Final[int] = 3
REACTION_RECEIVED_POINTS: Final[int] = 3

SPICY_POSITION_TITLES: Final[dict[int, str]] = {
    1: "👑 Pleasure Royalty",
    2: "👅 Pussy Eater",
    3: "✊ Master Masturbator",
    4: "💦 Orgasm Architect",
    5: "🔥 Heat Maker",
    6: "💋 Tease Master",
    7: "😈 Midnight Seducer",
    8: "🍑 Booty Hunter",
    9: "🖤 Kink Explorer",
    10: "👀 Naughty Voyeur",
}


def db_connect() -> sqlite3.Connection:
    path = Path(DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def get_main_group_id() -> int:
    if ENV_MAIN_GROUP_ID:
        return ENV_MAIN_GROUP_ID
    with db_connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE chat_id=0 AND key='main_group_id'"
        ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def initialize_rank_system() -> None:
    """Non-destructive V4.1 schema migration.

    This function only creates new tables/indexes. It never drops, clears or
    overwrites verified_users or any legacy verification field.
    """
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rank_daily (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                activity_date TEXT NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                photos INTEGER NOT NULL DEFAULT 0,
                videos INTEGER NOT NULL DEFAULT 0,
                replies_sent INTEGER NOT NULL DEFAULT 0,
                replies_received INTEGER NOT NULL DEFAULT 0,
                reactions_received INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, user_id, activity_date)
            );

            CREATE TABLE IF NOT EXISTS rank_message_authors (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS rank_reaction_state (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                reactor_user_id INTEGER NOT NULL,
                recipient_user_id INTEGER NOT NULL,
                reaction_date TEXT NOT NULL,
                reaction_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(chat_id, message_id, reactor_user_id)
            );

            CREATE TABLE IF NOT EXISTS rank_publications (
                chat_id INTEGER NOT NULL,
                publication_kind TEXT NOT NULL,
                period_key TEXT NOT NULL,
                message_id INTEGER,
                sent_at INTEGER NOT NULL,
                PRIMARY KEY(chat_id, publication_kind, period_key)
            );

            CREATE INDEX IF NOT EXISTS idx_rank_daily_period
            ON rank_daily(chat_id, activity_date);

            CREATE INDEX IF NOT EXISTS idx_rank_messages_created
            ON rank_message_authors(chat_id, created_at);
            """
        )
        # Explicit migration marker. INSERT OR IGNORE is safe on every restart.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
            """
        )
        already_applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version='4.1.0-rank'"
        ).fetchone()
        if not already_applied:
            # One-time safe backfill of historical message counts already kept by
            # V4.0. Media/reply/reaction details start from V4.1 because older
            # Telegram updates were not stored. INSERT OR IGNORE prevents any
            # duplication if a deployment is retried.
            conn.execute(
                """
                INSERT OR IGNORE INTO rank_daily(
                    chat_id,user_id,activity_date,messages,photos,videos,
                    replies_sent,replies_received,reactions_received
                )
                SELECT chat_id,user_id,activity_date,message_count,0,0,0,0,0
                FROM daily_activity
                """
            )
            conn.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES('4.1.0-rank',?)",
                (int(time.time()),),
            )


def _upsert_daily(
    conn: sqlite3.Connection,
    chat_id: int,
    user_id: int,
    activity_date: str,
    *,
    messages: int = 0,
    photos: int = 0,
    videos: int = 0,
    replies_sent: int = 0,
    replies_received: int = 0,
    reactions_received: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO rank_daily(
            chat_id,user_id,activity_date,messages,photos,videos,
            replies_sent,replies_received,reactions_received
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(chat_id,user_id,activity_date) DO UPDATE SET
            messages=MAX(0,rank_daily.messages+excluded.messages),
            photos=MAX(0,rank_daily.photos+excluded.photos),
            videos=MAX(0,rank_daily.videos+excluded.videos),
            replies_sent=MAX(0,rank_daily.replies_sent+excluded.replies_sent),
            replies_received=MAX(0,rank_daily.replies_received+excluded.replies_received),
            reactions_received=MAX(0,rank_daily.reactions_received+excluded.reactions_received)
        """,
        (
            chat_id,
            user_id,
            activity_date,
            messages,
            photos,
            videos,
            replies_sent,
            replies_received,
            reactions_received,
        ),
    )


def record_message_activity(chat_id: int, user, message) -> None:
    """Record every non-command group message with no score cooldown/cap."""
    if not user or getattr(user, "is_bot", False) or not message:
        return
    main_id = get_main_group_id()
    if not main_id or chat_id != main_id:
        return

    today = datetime.now(LOCAL_TZ).date().isoformat()
    photo = 1 if getattr(message, "photo", None) else 0
    video = 1 if (
        getattr(message, "video", None)
        or getattr(message, "animation", None)
        or getattr(message, "video_note", None)
    ) else 0

    reply_user = None
    replied = getattr(message, "reply_to_message", None)
    if replied is not None:
        reply_user = getattr(replied, "from_user", None)
        if reply_user and (getattr(reply_user, "is_bot", False) or reply_user.id == user.id):
            reply_user = None

    with db_connect() as conn:
        _upsert_daily(
            conn,
            chat_id,
            user.id,
            today,
            messages=1,
            photos=photo,
            videos=video,
            replies_sent=1 if reply_user else 0,
        )
        if reply_user:
            _upsert_daily(
                conn,
                chat_id,
                reply_user.id,
                today,
                replies_received=1,
            )
        conn.execute(
            """
            INSERT INTO rank_message_authors(chat_id,message_id,user_id,created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(chat_id,message_id) DO UPDATE SET
                user_id=excluded.user_id,
                created_at=excluded.created_at
            """,
            (chat_id, message.message_id, user.id, int(time.time())),
        )
        # Keep the reaction lookup table compact (older than 400 days is useless
        # for weekly/monthly rankings and can be safely pruned).
        conn.execute(
            "DELETE FROM rank_message_authors WHERE chat_id=? AND created_at<?",
            (chat_id, int(time.time()) - 400 * 86400),
        )


async def reaction_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reaction = update.message_reaction
    if reaction is None:
        return
    chat_id = reaction.chat.id
    if chat_id != get_main_group_id():
        return
    reactor = reaction.user
    if reactor is None or reactor.is_bot:
        return

    with db_connect() as conn:
        author = conn.execute(
            "SELECT user_id FROM rank_message_authors WHERE chat_id=? AND message_id=?",
            (chat_id, reaction.message_id),
        ).fetchone()
        if not author:
            return
        recipient_id = int(author["user_id"])
        if recipient_id == reactor.id:
            return

        state = conn.execute(
            """
            SELECT recipient_user_id,reaction_date,reaction_count
            FROM rank_reaction_state
            WHERE chat_id=? AND message_id=? AND reactor_user_id=?
            """,
            (chat_id, reaction.message_id, reactor.id),
        ).fetchone()
        new_count = len(reaction.new_reaction or ())
        today = datetime.now(LOCAL_TZ).date().isoformat()

        if state:
            old_count = int(state["reaction_count"] or 0)
            stored_date = str(state["reaction_date"])
            stored_recipient = int(state["recipient_user_id"])
            delta = new_count - old_count
            if delta:
                _upsert_daily(
                    conn,
                    chat_id,
                    stored_recipient,
                    stored_date,
                    reactions_received=delta,
                )
            if new_count <= 0:
                conn.execute(
                    "DELETE FROM rank_reaction_state WHERE chat_id=? AND message_id=? AND reactor_user_id=?",
                    (chat_id, reaction.message_id, reactor.id),
                )
            else:
                conn.execute(
                    """
                    UPDATE rank_reaction_state SET reaction_count=?
                    WHERE chat_id=? AND message_id=? AND reactor_user_id=?
                    """,
                    (new_count, chat_id, reaction.message_id, reactor.id),
                )
        elif new_count > 0:
            _upsert_daily(
                conn,
                chat_id,
                recipient_id,
                today,
                reactions_received=new_count,
            )
            conn.execute(
                """
                INSERT INTO rank_reaction_state(
                    chat_id,message_id,reactor_user_id,recipient_user_id,reaction_date,reaction_count
                ) VALUES(?,?,?,?,?,?)
                """,
                (chat_id, reaction.message_id, reactor.id, recipient_id, today, new_count),
            )


def period_bounds(kind: str, now: Optional[datetime] = None) -> tuple[date, date, str]:
    local_now = now.astimezone(LOCAL_TZ) if now and now.tzinfo else (now.replace(tzinfo=LOCAL_TZ) if now else datetime.now(LOCAL_TZ))
    end = local_now.date()
    if kind == "week":
        start = end - timedelta(days=6)
        key = end.isoformat()
    elif kind == "month":
        start = end.replace(day=1)
        key = end.strftime("%Y-%m")
    else:
        raise ValueError("kind must be week or month")
    return start, end, key


def _score_sql() -> str:
    return (
        f"messages + photos*{PHOTO_BONUS} + videos*{VIDEO_BONUS} + "
        f"replies_sent*{REPLY_SENT_POINTS} + replies_received*{REPLY_RECEIVED_POINTS} + "
        f"reactions_received*{REACTION_RECEIVED_POINTS}"
    )


def get_leaderboard(
    chat_id: int,
    start: date,
    end: date,
    *,
    limit: int,
    category: str = "overall",
) -> list[sqlite3.Row]:
    order_map = {
        "overall": "score",
        "messages": "messages",
        "interactions": "interactions",
        "hot": "hot_score",
    }
    order_by = order_map.get(category, "score")
    score_expr = _score_sql()
    query = f"""
        WITH totals AS (
            SELECT
                r.user_id,
                COALESCE(MAX(m.username),'') AS username,
                COALESCE(MAX(m.first_name),'Μέλος') AS first_name,
                SUM(r.messages) AS messages,
                SUM(r.photos) AS photos,
                SUM(r.videos) AS videos,
                SUM(r.replies_sent) AS replies_sent,
                SUM(r.replies_received) AS replies_received,
                SUM(r.reactions_received) AS reactions_received
            FROM rank_daily r
            LEFT JOIN members m ON m.chat_id=r.chat_id AND m.user_id=r.user_id
            WHERE r.chat_id=? AND r.activity_date BETWEEN ? AND ?
            GROUP BY r.user_id
        )
        SELECT *,
            (replies_sent + replies_received + reactions_received) AS interactions,
            (photos*5 + videos*8) AS hot_score,
            ({score_expr}) AS score
        FROM totals
        WHERE messages>0 OR photos>0 OR videos>0 OR replies_sent>0 OR replies_received>0 OR reactions_received>0
        ORDER BY {order_by} DESC, score DESC, messages DESC, user_id ASC
        LIMIT ?
    """
    with db_connect() as conn:
        return conn.execute(
            query,
            (chat_id, start.isoformat(), end.isoformat(), max(1, int(limit))),
        ).fetchall()


def spicy_title(position: int) -> str:
    if position in SPICY_POSITION_TITLES:
        return SPICY_POSITION_TITLES[position]
    if position <= 20:
        return "😏 Dirty Mind"
    if position <= 50:
        return "🌶️ Spicy Member"
    return "🕯️ Secret Watcher"


def _member_link(row: sqlite3.Row) -> str:
    user_id = int(row["user_id"])
    username = str(row["username"] or "").strip()
    first_name = html.escape(str(row["first_name"] or "Μέλος"))
    label = f"@{html.escape(username)}" if username else first_name
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def _range_label(start: date, end: date) -> str:
    return f"{start.strftime('%d/%m')}–{end.strftime('%d/%m/%Y')}"


def format_leaderboard(
    chat_id: int,
    kind: str,
    *,
    category: str = "overall",
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    start, end, _ = period_bounds(kind, now)
    if limit is None:
        limit = 5 if kind == "week" else 10
    rows = get_leaderboard(chat_id, start, end, limit=limit, category=category)

    period_title = "TOP 5 ΕΒΔΟΜΑΔΑΣ" if kind == "week" else "TOP 10 ΜΗΝΑ"
    category_titles = {
        "overall": period_title,
        "messages": "💬 TOP ΜΗΝΥΜΑΤΩΝ — ΜΗΝΑΣ",
        "interactions": "❤️ TOP ΑΛΛΗΛΕΠΙΔΡΑΣΕΩΝ — ΜΗΝΑΣ",
        "hot": "🔥 TOP ΚΑΥΤΟΥ ΥΛΙΚΟΥ — ΜΗΝΑΣ",
    }
    title = category_titles.get(category, period_title)
    lines = [f"🏆 <b>{title}</b>", f"📅 {_range_label(start, end)}", ""]
    if not rows:
        lines.append("Δεν υπάρχει ακόμη καταγεγραμμένη δραστηριότητα.")
        return "\n".join(lines)

    for position, row in enumerate(rows, 1):
        lines.append(
            f"<b>{position}.</b> {_member_link(row)} — <b>{html.escape(spicy_title(position))}</b>"
        )
        lines.append(
            f"   ⭐ {int(row['score'])}  •  💬 {int(row['messages'])}  •  "
            f"❤️ {int(row['interactions'])}  •  🔥 {int(row['photos'])}📷/{int(row['videos'])}🎥"
        )

    if category == "overall":
        message_leader = get_leaderboard(chat_id, start, end, limit=1, category="messages")
        interaction_leader = get_leaderboard(chat_id, start, end, limit=1, category="interactions")
        hot_leader = get_leaderboard(chat_id, start, end, limit=1, category="hot")
        lines.extend(["", "🏅 <b>ΠΡΩΤΑΘΛΗΤΕΣ ΚΑΤΗΓΟΡΙΩΝ</b>"])
        if message_leader:
            lines.append(f"💬 Message Machine: {_member_link(message_leader[0])}")
        if interaction_leader:
            lines.append(f"❤️ Interaction Beast: {_member_link(interaction_leader[0])}")
        if hot_leader:
            lines.append(f"🔥 Hot Content Star: {_member_link(hot_leader[0])}")

    lines.extend([
        "",
        "<i>Κάθε μήνυμα μετρά χωρίς ημερήσιο όριο. Φωτογραφίες, βίντεο, replies και reactions δίνουν επιπλέον πόντους.</i>",
    ])
    return "\n".join(lines)


def format_user_rank(chat_id: int, user_id: int, now: Optional[datetime] = None) -> str:
    month_start, month_end, _ = period_bounds("month", now)
    week_start, week_end, _ = period_bounds("week", now)
    month_rows = get_leaderboard(chat_id, month_start, month_end, limit=10000)
    week_rows = get_leaderboard(chat_id, week_start, week_end, limit=10000)

    month_position = next((i for i, row in enumerate(month_rows, 1) if int(row["user_id"]) == user_id), 0)
    week_position = next((i for i, row in enumerate(week_rows, 1) if int(row["user_id"]) == user_id), 0)
    row = next((row for row in month_rows if int(row["user_id"]) == user_id), None)
    if row is None:
        row = next((row for row in week_rows if int(row["user_id"]) == user_id), None)
    if row is None:
        return (
            "📊 <b>ΤΟ RANK ΜΟΥ</b>\n\n"
            "Δεν υπάρχει ακόμη δραστηριότητά σου στη νέα κατάταξη. "
            "Τα στατιστικά ξεκινούν να καταγράφονται μετά την εγκατάσταση της V4.1."
        )

    effective_position = month_position or week_position
    return (
        "📊 <b>ΤΟ RANK ΜΟΥ</b>\n\n"
        f"Τίτλος: <b>{html.escape(spicy_title(effective_position))}</b>\n"
        f"👑 Θέση μήνα: <b>{'#' + str(month_position) if month_position else '—'}</b>\n"
        f"📅 Θέση 7ημέρου: <b>{'#' + str(week_position) if week_position else '—'}</b>\n\n"
        f"⭐ Πόντοι: <b>{int(row['score'])}</b>\n"
        f"💬 Μηνύματα: <b>{int(row['messages'])}</b>\n"
        f"❤️ Αλληλεπιδράσεις: <b>{int(row['interactions'])}</b>\n"
        f"📷 Φωτογραφίες: <b>{int(row['photos'])}</b>\n"
        f"🎥 Βίντεο: <b>{int(row['videos'])}</b>"
    )


def rank_home_text() -> str:
    return (
        "🏆 <b>SECRET CLUB RANKINGS</b>\n\n"
        "Δες τους Top 5 της εβδομάδας, τους Top 10 του μήνα και τη δική σου θέση.\n\n"
        "💬 Μετράνε όλα τα μηνύματα χωρίς score cooldown ή ημερήσιο όριο.\n"
        "❤️ Οι αλληλεπιδράσεις περιλαμβάνουν replies και reactions που λαμβάνεις.\n"
        "🔥 Το καυτό υλικό μετρά φωτογραφίες και βίντεο.\n\n"
        "📣 Αυτόματη δημοσίευση: κάθε Παρασκευή και την τελευταία ημέρα του μήνα στις 22:00 ώρα Ελλάδας."
    )


def rank_home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Top 5 εβδομάδας", callback_data="v4:rank:week"),
            InlineKeyboardButton("👑 Top 10 μήνα", callback_data="v4:rank:month"),
        ],
        [InlineKeyboardButton("📊 Το rank μου", callback_data="v4:rank:me")],
        [
            InlineKeyboardButton("💬 Μηνύματα", callback_data="v4:rank:messages"),
            InlineKeyboardButton("❤️ Αλληλεπιδράσεις", callback_data="v4:rank:interactions"),
        ],
        [InlineKeyboardButton("🔥 Καυτό υλικό", callback_data="v4:rank:hot")],
        [InlineKeyboardButton("🏠 Αρχικό μενού", callback_data="v4:home")],
    ])


def rank_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Rankings", callback_data="v4:page:rank")],
        [InlineKeyboardButton("🏠 Αρχικό μενού", callback_data="v4:home")],
    ])


def publication_status_text(chat_id: int) -> str:
    now = datetime.now(LOCAL_TZ)
    week_start, week_end, _ = period_bounds("week", now)
    month_start, month_end, _ = period_bounds("month", now)
    week_count = len(get_leaderboard(chat_id, week_start, week_end, limit=5)) if chat_id else 0
    month_count = len(get_leaderboard(chat_id, month_start, month_end, limit=10)) if chat_id else 0
    return (
        "🏆 <b>RANKINGS CONTROL</b>\n\n"
        f"Timezone: <code>{html.escape(TIMEZONE_NAME)}</code>\n"
        "Εβδομαδιαία δημοσίευση: <b>Παρασκευή 22:00</b>\n"
        "Μηνιαία δημοσίευση: <b>Τελευταία ημέρα 22:00</b>\n"
        "Score limit: <b>Κανένα</b>\n\n"
        f"Top εβδομάδας διαθέσιμοι: <b>{week_count}</b>\n"
        f"Top μήνα διαθέσιμοι: <b>{month_count}</b>\n\n"
        "Η υπάρχουσα βάση verification δεν τροποποιείται από το ranking system."
    )


def _already_published(chat_id: int, kind: str, key: str) -> bool:
    with db_connect() as conn:
        return conn.execute(
            "SELECT 1 FROM rank_publications WHERE chat_id=? AND publication_kind=? AND period_key=?",
            (chat_id, kind, key),
        ).fetchone() is not None


def _record_publication(chat_id: int, kind: str, key: str, message_id: int) -> None:
    with db_connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rank_publications(chat_id,publication_kind,period_key,message_id,sent_at)
            VALUES(?,?,?,?,?)
            """,
            (chat_id, kind, key, message_id, int(time.time())),
        )


async def publish_leaderboard(
    context: ContextTypes.DEFAULT_TYPE,
    kind: str,
    *,
    force: bool = False,
) -> bool:
    chat_id = get_main_group_id()
    if not chat_id:
        return False
    _, _, key = period_bounds(kind)
    if not force and _already_published(chat_id, kind, key):
        return False
    text = format_leaderboard(chat_id, kind)
    if "Δεν υπάρχει ακόμη" in text:
        return False
    try:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError:
        logger.exception("Failed to publish %s leaderboard", kind)
        return False
    if not force:
        _record_publication(chat_id, kind, key, sent.message_id)
    return True


async def weekly_rank_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await publish_leaderboard(context, "week")


async def monthly_rank_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await publish_leaderboard(context, "month")


def register_rank_system(application: Application) -> None:
    application.add_handler(MessageReactionHandler(reaction_activity), group=21)
    if application.job_queue:
        run_at = dt_time(hour=22, minute=0, tzinfo=LOCAL_TZ)
        # python-telegram-bot maps Sunday=0 ... Friday=5.
        application.job_queue.run_daily(
            weekly_rank_job,
            time=run_at,
            days=(5,),
            name="v41-weekly-rank-friday-2200",
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
        )
        application.job_queue.run_monthly(
            monthly_rank_job,
            when=run_at,
            day=-1,
            name="v41-monthly-rank-lastday-2200",
            job_kwargs={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
        )
