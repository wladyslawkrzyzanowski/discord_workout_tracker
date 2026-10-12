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
        moderator_role_id: Role that can see every channel in the category, or None
            when setup has not stored it yet.
        tracking_role_id: Role that can see the start-workout channel, or None
            when setup has not stored it yet.
        start_channel_id: Channel that holds the start-workout panel, or None
            when setup has not stored it yet.
    """

    guild_id: int
    category_id: int
    channel_id: int
    moderator_role_id: int | None
    tracking_role_id: int | None
    start_channel_id: int | None


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
        SELECT guild_id, category_id, channel_id, moderator_role_id,
               tracking_role_id, start_channel_id
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
        moderator_role_id=row["moderator_role_id"],
        tracking_role_id=row["tracking_role_id"],
        start_channel_id=row["start_channel_id"],
    )


def save_guild_setup(
    database: Database,
    guild_id: int,
    category_id: int,
    channel_id: int,
    moderator_role_id: int,
    tracking_role_id: int,
    start_channel_id: int,
) -> None:
    """Store the channels and roles created for this guild.

    Args:
        database: Open database.
        guild_id: Discord guild being set up.
        category_id: Category that contains the workout channels.
        channel_id: Channel that contains the member panel.
        moderator_role_id: Role that can see every channel in the category.
        tracking_role_id: Role that can see the start-workout channel.
        start_channel_id: Channel that holds the start-workout panel.

    Returns:
        None.
    """
    database.connection.execute(
        """
        INSERT INTO guild_setups (
            guild_id, category_id, channel_id, moderator_role_id,
            tracking_role_id, start_channel_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            category_id = excluded.category_id,
            channel_id = excluded.channel_id,
            moderator_role_id = excluded.moderator_role_id,
            tracking_role_id = excluded.tracking_role_id,
            start_channel_id = excluded.start_channel_id
        """,
        (
            guild_id,
            category_id,
            channel_id,
            moderator_role_id,
            tracking_role_id,
            start_channel_id,
        ),
    )
    database.connection.commit()
