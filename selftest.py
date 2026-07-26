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
    for name in ("bot.py", "v3_core.py", "v3_flows.py", "v3_features.py"):
        ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)


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
                    rules_accepted INTEGER NOT NULL DEFAULT 0,
                    warnings INTEGER NOT NULL DEFAULT 0,
                    inactive_warned_at INTEGER,
                    is_exempt INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(chat_id, user_id)
                )
                """
            )

        core = importlib.import_module("v3_core")
        core.initialize_v3()
        flows = importlib.import_module("v3_flows")
        features = importlib.import_module("v3_features")

        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            required_tables = {
                "settings", "daily_activity", "moderation_log", "verifications",
                "tickets", "ticket_message_map", "presentations", "schedules",
                "ai_usage", "verified_users",
            }
            missing = required_tables - tables
            if missing:
                raise AssertionError(f"Missing tables: {sorted(missing)}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(members)")}
            for column in ("verified", "xp", "level", "message_count", "last_xp_at"):
                if column not in columns:
                    raise AssertionError(f"Missing members column: {column}")

        assert core.level_from_xp(0) == 1
        assert core.level_from_xp(80) >= 3
        assert flows.local_answer("Πώς κάνω verification;")
        assert len(features.v3_menu_rows()) == 3

        class FakeJobQueue:
            def __init__(self): self.jobs = []
            def run_repeating(self, *args, **kwargs): self.jobs.append((args, kwargs))

        class FakeApplication:
            def __init__(self):
                self.handlers = []
                self.job_queue = FakeJobQueue()
            def add_handler(self, handler, group=0):
                self.handlers.append((group, handler))

        app = FakeApplication()
        features.register_v3_handlers(app)
        assert len(app.handlers) >= 20
        assert len(app.job_queue.jobs) == 2


def main() -> None:
    static_parse()
    migration_test()
    print("Secret Club Assistant v3 self-test: OK")


if __name__ == "__main__":
    main()
