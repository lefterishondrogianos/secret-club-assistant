"""Static and database migration self-test for Secret Club Assistant v3."""
from __future__ import annotations

import ast
import importlib
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def static_parse() -> None:
    for path in ROOT.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=path.name)


def install_telegram_stubs() -> None:
    class Dummy:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class Filter:
        def __and__(self, other): return self
        def __or__(self, other): return self
        def __invert__(self): return self

    class FilterNamespace:
        def __init__(self):
            self.TEXT = self.PHOTO = self.VIDEO = self.VOICE = self.REPLY = Filter()
            self.COMMAND = self.ALL = Filter()
            self.Document = types.SimpleNamespace(ALL=Filter())
            self.ChatType = types.SimpleNamespace(GROUPS=Filter(), PRIVATE=Filter())
            self.StatusUpdate = types.SimpleNamespace(ALL=Filter())

    telegram = types.ModuleType("telegram")
    for name in (
        "ChatPermissions", "Update", "User", "InlineKeyboardButton",
        "InlineKeyboardMarkup", "Message",
    ):
        setattr(telegram, name, type(name, (Dummy,), {
            "no_permissions": classmethod(lambda cls: cls()),
            "all_permissions": classmethod(lambda cls: cls()),
        }))

    constants = types.ModuleType("telegram.constants")
    constants.ChatMemberStatus = types.SimpleNamespace(
        ADMINISTRATOR="administrator", OWNER="creator", LEFT="left", BANNED="kicked"
    )
    constants.ChatType = types.SimpleNamespace(
        GROUP="group", SUPERGROUP="supergroup", PRIVATE="private"
    )
    constants.ParseMode = types.SimpleNamespace(HTML="HTML")

    errors = types.ModuleType("telegram.error")
    class TelegramError(Exception): pass
    class Forbidden(TelegramError): pass
    errors.TelegramError = TelegramError
    errors.Forbidden = Forbidden

    ext = types.ModuleType("telegram.ext")
    for name in (
        "Application", "CommandHandler", "ContextTypes", "MessageHandler",
        "CallbackQueryHandler", "ConversationHandler", "ChatJoinRequestHandler",
        "MessageReactionHandler",
    ):
        setattr(ext, name, type(name, (Dummy,), {}))
    ext.ConversationHandler.END = -1
    ext.filters = FilterNamespace()

    sys.modules.update({
        "telegram": telegram,
        "telegram.constants": constants,
        "telegram.error": errors,
        "telegram.ext": ext,
    })


def migration_test() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / "test.db")
        os.environ["DATABASE_PATH"] = db_path
        install_telegram_stubs()
        sys.path.insert(0, str(ROOT))

        # Simulate the essential v2 members table before importing v3_core.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE members (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    joined_at INTEGER,
                    last_active INTEGER,
                    verified INTEGER NOT NULL DEFAULT 0,
                    rules_accepted INTEGER NOT NULL DEFAULT 0,
                    warnings INTEGER NOT NULL DEFAULT 0,
                    inactive_warned_at INTEGER,
                    is_exempt INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(chat_id, user_id)
                )
                """
            )
            conn.execute(
                "INSERT INTO members(chat_id,user_id,username,first_name,verified) VALUES(?,?,?,?,?)",
                (-100123, 424242, "legacy", "Legacy", 1),
            )

        core = importlib.import_module("v3_core")
        core.initialize_v3()
        rank = importlib.import_module("rank_v41")
        rank.initialize_rank_system()
        flows = importlib.import_module("v3_flows")
        features = importlib.import_module("v3_features")

        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required_tables = {
                "settings", "daily_activity", "moderation_log", "verifications",
                "tickets", "ticket_message_map", "presentations", "schedules",
                "ai_usage", "verified_users", "rank_daily",
                "rank_message_authors", "rank_reaction_state", "rank_publications",
            }
            missing = required_tables - tables
            if missing:
                raise AssertionError(f"Missing tables: {sorted(missing)}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
            for column in ("verified", "xp", "level", "message_count", "last_xp_at"):
                if column not in columns:
                    raise AssertionError(f"Missing members column: {column}")
            migrated = conn.execute(
                "SELECT verified, source FROM verified_users WHERE user_id=?", (424242,)
            ).fetchone()
            if not migrated or migrated[0] != 1 or migrated[1] != "legacy_members_migration":
                raise AssertionError("Legacy verification was not migrated safely")

        # V4.1 migration must preserve the legacy verified decision.
        rank.initialize_rank_system()
        with sqlite3.connect(db_path) as conn:
            preserved = conn.execute(
                "SELECT verified,source FROM verified_users WHERE user_id=?", (424242,)
            ).fetchone()
            if preserved != (1, "legacy_members_migration"):
                raise AssertionError("V4.1 rank migration modified verified_users")
            conn.execute(
                "INSERT OR REPLACE INTO settings(chat_id,key,value) VALUES(0,'main_group_id',?)",
                (str(-100123),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO members(chat_id,user_id,username,first_name) VALUES(?,?,?,?)",
                (-100123, 777, "second", "Second"),
            )

        # Ranking counts every message and media without cooldown/cap.
        fake_user = types.SimpleNamespace(id=424242, username="legacy", first_name="Legacy", is_bot=False)
        second_user = types.SimpleNamespace(id=777, username="second", first_name="Second", is_bot=False)
        base = types.SimpleNamespace(
            message_id=1, photo=None, video=None, animation=None, video_note=None, reply_to_message=None
        )
        rank.record_message_activity(-100123, fake_user, base)
        photo_reply = types.SimpleNamespace(
            message_id=2, photo=[object()], video=None, animation=None, video_note=None,
            reply_to_message=types.SimpleNamespace(from_user=second_user),
        )
        rank.record_message_activity(-100123, fake_user, photo_reply)
        week_start, week_end, _ = rank.period_bounds("week")
        rows = rank.get_leaderboard(-100123, week_start, week_end, limit=5)
        if not rows or int(rows[0]["user_id"]) != 424242:
            raise AssertionError("Rank leaderboard did not record activity")
        if int(rows[0]["messages"]) != 2 or int(rows[0]["photos"]) != 1:
            raise AssertionError("Rank message/media counters are incorrect")

        assert core.level_from_xp(0) == 1
        assert core.level_from_xp(80) >= 3
        assert flows.local_answer("Πώς κάνω verification;")
        assert len(features.v3_menu_rows()) == 3

        class FakeJobQueue:
            def __init__(self): self.jobs = []
            def run_repeating(self, *args, **kwargs): self.jobs.append(("repeating", args, kwargs))
            def run_daily(self, *args, **kwargs): self.jobs.append(("daily", args, kwargs))
            def run_monthly(self, *args, **kwargs): self.jobs.append(("monthly", args, kwargs))

        class FakeApplication:
            def __init__(self):
                self.handlers = []
                self.job_queue = FakeJobQueue()
            def add_handler(self, handler, group=0):
                self.handlers.append((group, handler))

        app = FakeApplication()
        features.register_v3_handlers(app)
        rank.register_rank_system(app)
        assert len(app.handlers) >= 21
        kinds = [job[0] for job in app.job_queue.jobs]
        assert kinds.count("repeating") == 2
        assert kinds.count("daily") == 1
        assert kinds.count("monthly") == 1
        daily_job = next(job for job in app.job_queue.jobs if job[0] == "daily")
        monthly_job = next(job for job in app.job_queue.jobs if job[0] == "monthly")
        if daily_job[2].get("days") != (5,):
            raise AssertionError("Weekly ranking is not scheduled for Friday")
        if daily_job[2].get("time").hour != 22 or daily_job[2].get("time").tzinfo is None:
            raise AssertionError("Weekly ranking is not scheduled at 22:00 local time")
        if monthly_job[2].get("day") != -1:
            raise AssertionError("Monthly ranking is not scheduled for the last day")
        if monthly_job[2].get("when").hour != 22 or monthly_job[2].get("when").tzinfo is None:
            raise AssertionError("Monthly ranking is not scheduled at 22:00 local time")


def main() -> None:
    static_parse()
    migration_test()
    print("Secret Club Assistant v4.1 self-test: OK")


if __name__ == "__main__":
    main()
