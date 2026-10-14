"""Stored state used to rebuild persistent Discord views."""

from dataclasses import dataclass

from tracker.db.database import Database


@dataclass(frozen=True)
class StoredView:
    """One persistent view attached to a message.

    Attributes:
        message_id: Discord message that holds the components.
        channel_id: Channel that contains the message.
        guild_id: Guild that contains the channel.
        view_name: Registry name of the view class.
        state: Text the view needs in order to rebuild itself.
    """

    message_id: int
    channel_id: int
    guild_id: int
    view_name: str
    state: str


def save_view(
    database: Database,
    message_id: int,
    channel_id: int,
    guild_id: int,
    view_name: str,
    state: str,
) -> None:
    """Insert or replace the stored state of one message's view.

    Args:
        database: Open database.
        message_id: Discord message that holds the components.
        channel_id: Channel that contains the message.
        guild_id: Guild that contains the channel.
        view_name: Registry name of the view class.
        state: Text the view needs in order to rebuild itself.

    Returns:
        None.
    """
    database.connection.execute(
        """
        INSERT INTO persistent_views (
            message_id, channel_id, guild_id, view_name, state
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            guild_id = excluded.guild_id,
            view_name = excluded.view_name,
            state = excluded.state
        """,
        (message_id, channel_id, guild_id, view_name, state),
    )
    database.connection.commit()


def delete_view(database: Database, message_id: int) -> None:
    """Remove the stored view for a message.

    Args:
        database: Open database.
        message_id: Discord message whose view should be forgotten.

    Returns:
        None.
    """
    database.connection.execute(
        "DELETE FROM persistent_views WHERE message_id = ?",
        (message_id,),
    )
    database.connection.commit()


def find_channel_view(
    database: Database,
    channel_id: int,
    view_name: str,
) -> StoredView | None:
    """Load one stored view of this kind in a channel.

    Args:
        database: Open database.
        channel_id: Channel to search.
        view_name: Registry name of the view class.

    Returns:
        The stored view, or None when the channel has none of this kind.
    """
    row = database.connection.execute(
        """
        SELECT message_id, channel_id, guild_id, view_name, state
        FROM persistent_views
        WHERE channel_id = ? AND view_name = ?
        LIMIT 1
        """,
        (channel_id, view_name),
    ).fetchone()
    if row is None:
        return None
    return StoredView(
        message_id=row["message_id"],
        channel_id=row["channel_id"],
        guild_id=row["guild_id"],
        view_name=row["view_name"],
        state=row["state"],
    )


def channel_has_view(database: Database, channel_id: int, view_name: str) -> bool:
    """Report whether a channel already has a stored view of this kind.

    Args:
        database: Open database.
        channel_id: Channel to search.
        view_name: Registry name of the view class.

    Returns:
        True when at least one stored view matches.
    """
    row = database.connection.execute(
        """
        SELECT 1
        FROM persistent_views
        WHERE channel_id = ? AND view_name = ?
        LIMIT 1
        """,
        (channel_id, view_name),
    ).fetchone()
    return row is not None


def list_views(database: Database) -> list[StoredView]:
    """List every stored view across all guilds.

    Args:
        database: Open database.

    Returns:
        Stored views ordered by message id.
    """
    rows = database.connection.execute(
        """
        SELECT message_id, channel_id, guild_id, view_name, state
        FROM persistent_views
        ORDER BY message_id
        """
    )
    return [
        StoredView(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            view_name=row["view_name"],
            state=row["state"],
        )
        for row in rows
    ]
