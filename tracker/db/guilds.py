"""Where each guild keeps its workout channels."""

from dataclasses import dataclass

from tracker.db.database import Database


@dataclass(frozen=True)
class GuildSetup:
    """Category and channel that hold workouts for one guild.

    Attributes:
        guild_id: Discord guild these workouts belong to.
        category_id: Category that contains the setup channel.
        channel_id: Channel that contains the member panel.
    """

    guild_id: int
    category_id: int
    channel_id: int


def get_guild_setup(database: Database, guild_id: int) -> GuildSetup | None:
    """Load the workout location saved for one guild.

    Args:
        database: Open database.
        guild_id: Discord guild to look up.

    Returns:
        The saved location, or None when this guild has not been set up.
    """
    row = database.connection.execute(
        """
        SELECT guild_id, category_id, channel_id
        FROM guild_setups
        WHERE guild_id = ?
        """,
        (guild_id,),
    ).fetchone()
    if row is None:
        return None
    return GuildSetup(
        guild_id=row["guild_id"],
        category_id=row["category_id"],
        channel_id=row["channel_id"],
    )


def save_guild_setup(
    database: Database,
    guild_id: int,
    category_id: int,
    channel_id: int,
) -> None:
    """Store the category and channel where this guild's workouts live.

    Args:
        database: Open database.
        guild_id: Discord guild being set up.
        category_id: Category that contains the setup channel.
        channel_id: Channel that contains the member panel.

    Returns:
        None.
    """
    database.connection.execute(
        """
        INSERT INTO guild_setups (guild_id, category_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id = excluded.category_id,
            channel_id = excluded.channel_id
        """,
        (guild_id, category_id, channel_id),
    )
    database.connection.commit()
