"""Private workout forums, one per member per guild."""

from dataclasses import dataclass

from tracker.db.database import Database


@dataclass(frozen=True)
class MemberForum:
    """Forum channel owned by one member.

    Attributes:
        guild_id: Discord guild that contains the forum.
        user_id: Member who owns the forum.
        channel_id: Forum channel id.
    """

    guild_id: int
    user_id: int
    channel_id: int


def get_member_forum(database: Database, guild_id: int, user_id: int) -> MemberForum | None:
    """Load the private forum saved for one member.

    Args:
        database: Open database.
        guild_id: Discord guild to look up.
        user_id: Member who would own the forum.

    Returns:
        The saved forum, or None when this member has not created one here.
    """
    row = database.connection.execute(
        """
        SELECT guild_id, user_id, channel_id
        FROM member_forums
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return MemberForum(
        guild_id=row["guild_id"],
        user_id=row["user_id"],
        channel_id=row["channel_id"],
    )


def save_member_forum(
    database: Database,
    guild_id: int,
    user_id: int,
    channel_id: int,
) -> None:
    """Store the private forum for one member, replacing an older channel id.

    Args:
        database: Open database.
        guild_id: Discord guild that contains the forum.
        user_id: Member who owns the forum.
        channel_id: Forum channel id.

    Returns:
        None.
    """
    database.connection.execute(
        """
        INSERT INTO member_forums (guild_id, user_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            channel_id = excluded.channel_id
        """,
        (guild_id, user_id, channel_id),
    )
    database.connection.commit()


def list_member_forums(database: Database, guild_id: int) -> list[MemberForum]:
    """List private forums in one guild.

    Args:
        database: Open database.
        guild_id: Discord guild to list.

    Returns:
        Forums in that guild, ordered by user id.
    """
    rows = database.connection.execute(
        """
        SELECT guild_id, user_id, channel_id
        FROM member_forums
        WHERE guild_id = ?
        ORDER BY user_id
        """,
        (guild_id,),
    )
    return [
        MemberForum(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            channel_id=row["channel_id"],
        )
        for row in rows
    ]
