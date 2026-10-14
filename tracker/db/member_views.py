"""Persistent views that belong to one member.

Panel views that are shared by a whole channel stay in ``persistent_views``.
A row here is rebuilt after a restart and can carry a JSON object of extra
data, such as when a workout started.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tracker.db.database import Database

WORKOUT_LIMIT = timedelta(hours=24)


@dataclass(frozen=True)
class MemberView:
    """One persistent view owned by a member.

    Attributes:
        message_id: Discord message that holds the components.
        channel_id: Channel or thread that contains the message.
        guild_id: Guild that contains the channel.
        user_id: Member the view belongs to.
        view_name: Registry name of the view class.
        payload: JSON object with the extra data this view needs.
    """

    message_id: int
    channel_id: int
    guild_id: int
    user_id: int
    view_name: str
    payload: str


def payload_started_at(payload: str) -> datetime | None:
    """Read the workout start time from a view payload.

    Args:
        payload: JSON object stored with the view.

    Returns:
        The UTC start time, or None when the payload has no usable timestamp.
    """
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("started_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_abandoned(payload: str, now: datetime, limit: timedelta = WORKOUT_LIMIT) -> bool:
    """Report whether a workout has been open longer than the allowed time.

    Args:
        payload: JSON object stored with the workout view.
        now: Current time. Naive values are treated as UTC.
        limit: How long a workout may stay open before it is abandoned.

    Returns:
        True when ``started_at`` is present and is at least ``limit`` old.
    """
    started = payload_started_at(payload)
    if started is None:
        return False
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - started >= limit


def save_member_view(
    database: Database,
    message_id: int,
    channel_id: int,
    guild_id: int,
    user_id: int,
    view_name: str,
    payload: str,
) -> None:
    """Insert or replace one member view.

    Args:
        database: Open database.
        message_id: Discord message that holds the components.
        channel_id: Channel or thread that contains the message.
        guild_id: Guild that contains the channel.
        user_id: Member the view belongs to.
        view_name: Registry name of the view class.
        payload: JSON object with the extra data this view needs.

    Returns:
        None.

    Raises:
        ValueError: ``payload`` is not a JSON object.
    """
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    database.connection.execute(
        """
        INSERT INTO member_views (
            message_id, channel_id, guild_id, user_id, view_name, payload
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            guild_id = excluded.guild_id,
            user_id = excluded.user_id,
            view_name = excluded.view_name,
            payload = excluded.payload
        """,
        (message_id, channel_id, guild_id, user_id, view_name, payload),
    )
    database.connection.commit()


def replace_member_view_payload(database: Database, message_id: int, payload: str) -> None:
    """Replace the JSON stored for one member view.

    Args:
        database: Open database.
        message_id: Discord message whose payload changes.
        payload: JSON object with the extra data this view needs.

    Returns:
        None.

    Raises:
        ValueError: ``payload`` is not a JSON object.
    """
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    database.connection.execute(
        """
        UPDATE member_views
        SET payload = ?
        WHERE message_id = ?
        """,
        (payload, message_id),
    )
    database.connection.commit()


def delete_member_view(database: Database, message_id: int) -> None:
    """Remove the stored view for a message.

    Args:
        database: Open database.
        message_id: Discord message whose view should be forgotten.

    Returns:
        None.
    """
    database.connection.execute(
        "DELETE FROM member_views WHERE message_id = ?",
        (message_id,),
    )
    database.connection.commit()


def list_member_views(database: Database) -> list[MemberView]:
    """List every stored member view across all guilds.

    Args:
        database: Open database.

    Returns:
        Stored views ordered by message id.
    """
    rows = database.connection.execute(
        """
        SELECT message_id, channel_id, guild_id, user_id, view_name, payload
        FROM member_views
        ORDER BY message_id
        """
    )
    return [
        MemberView(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            view_name=row["view_name"],
            payload=row["payload"],
        )
        for row in rows
    ]
