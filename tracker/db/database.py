"""Open the SQLite database and create its tables."""

import sqlite3
from pathlib import Path


class Database:
    """One SQLite file for every guild the bot is in."""

    def __init__(self, path: Path) -> None:
        """Store the database path. The file is opened by ``initialize``.

        Args:
            path: Path of the SQLite file. Parent directories are created later.

        Returns:
            None.
        """
        self.path = path
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open SQLite connection.

        Returns:
            The connection created by ``initialize``.

        Raises:
            RuntimeError: ``initialize`` has not been called.
        """
        if self._connection is None:
            raise RuntimeError("Database is not open")
        return self._connection

    def initialize(self) -> None:
        """Create the database file and tables.

        Call this from the bot's event-loop thread. Later calls on this
        instance use the same connection.

        Returns:
            None.
        """
        if self._connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS persistent_views (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                view_name TEXT NOT NULL,
                state TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_setups (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                moderator_role_id INTEGER,
                tracking_role_id INTEGER,
                start_channel_id INTEGER
            )
            """
        )
        _add_column_if_missing(connection, "guild_setups", "moderator_role_id", "INTEGER")
        _add_column_if_missing(connection, "guild_setups", "tracking_role_id", "INTEGER")
        _add_column_if_missing(connection, "guild_setups", "start_channel_id", "INTEGER")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_forums (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_views (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                view_name TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exercise_categories (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category_id TEXT NOT NULL,
                name TEXT NOT NULL,
                is_default INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, category_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exercises (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                exercise_id TEXT NOT NULL,
                name TEXT NOT NULL,
                image_url TEXT,
                is_default INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, exercise_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exercise_sets (
                set_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                exercise_id TEXT NOT NULL,
                thread_id INTEGER NOT NULL,
                set_number INTEGER NOT NULL,
                weight TEXT NOT NULL,
                reps INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS exercise_placements (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                category_id TEXT NOT NULL,
                exercise_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, category_id, exercise_id),
                FOREIGN KEY (guild_id, user_id, category_id)
                    REFERENCES exercise_categories (guild_id, user_id, category_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (guild_id, user_id, exercise_id)
                    REFERENCES exercises (guild_id, user_id, exercise_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.commit()
        self._connection = connection

    def close(self) -> None:
        """Close the SQLite connection.

        Returns:
            None.
        """
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _add_column_if_missing(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """Add a column when an older database file does not have it yet.

    Args:
        connection: Open SQLite connection.
        table: Table to alter. This is a fixed name from this module, not user input.
        column: Column to add when it is absent.
        definition: SQL type clause for the new column.

    Returns:
        None.
    """
    rows = connection.execute(f"PRAGMA table_info({table})")
    names = {row["name"] for row in rows}
    if column not in names:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
