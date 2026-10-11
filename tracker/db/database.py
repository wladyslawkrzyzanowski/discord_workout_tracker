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
                channel_id INTEGER NOT NULL
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
