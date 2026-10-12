"""Background sweep that closes workouts left open for 24 hours."""

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from tracker.bot import WorkoutBot
from tracker.db.member_views import (
    WORKOUT_LIMIT,
    MemberView,
    delete_member_view,
    is_abandoned,
    list_member_views,
    payload_started_at,
)
from tracker.ui.views import WORKOUT_SESSION_VIEW

log = logging.getLogger(__name__)

_sweep_lock = asyncio.Lock()


class WorkoutCog(commands.Cog):
    """Closes abandoned workout posts on a timer."""

    def __init__(self, bot: WorkoutBot) -> None:
        """Start the sweep that closes workouts left unfinished.

        Args:
            bot: Running bot. Its database is already open.

        Returns:
            None.
        """
        self.bot = bot
        self.close_abandoned.start()

    def cog_unload(self) -> None:
        """Stop the sweep when this cog is unloaded.

        Returns:
            None.
        """
        self.close_abandoned.cancel()

    @tasks.loop(minutes=10)
    async def close_abandoned(self) -> None:
        """Close workouts that are still open after 24 hours.

        Returns:
            None.
        """
        await close_abandoned_workouts(self.bot)

    @close_abandoned.before_loop
    async def before_close_abandoned(self) -> None:
        """Wait until the gateway is connected before the first sweep.

        The loop body then runs immediately and repeats every ten minutes.

        Returns:
            None.
        """
        await self.bot.wait_until_ready()


async def close_abandoned_workouts(bot: WorkoutBot) -> None:
    """Close every unfinished workout that has been open for 24 hours.

    Args:
        bot: Running bot. The sweep reads and updates its database.

    Returns:
        None.
    """
    async with _sweep_lock:
        now = datetime.now(timezone.utc)
        closed = 0
        for stored in list_member_views(bot.database):
            if stored.view_name != WORKOUT_SESSION_VIEW:
                continue
            if payload_started_at(stored.payload) is None:
                log.error("Workout view %s has no started_at", stored.message_id)
                continue
            if not is_abandoned(stored.payload, now, WORKOUT_LIMIT):
                continue
            try:
                finished = await _close_workout(bot, stored)
            except Exception:
                log.exception("Failed to close workout message %s", stored.message_id)
                continue
            if not finished:
                continue
            delete_member_view(bot.database, stored.message_id)
            closed += 1
            log.info(
                "Closed abandoned workout message %s for user %s in guild %s",
                stored.message_id,
                stored.user_id,
                stored.guild_id,
            )
        if closed:
            log.info("Closed %s abandoned workout(s)", closed)


async def _close_workout(bot: WorkoutBot, stored: MemberView) -> bool:
    """Lock one unfinished workout thread and remove its button.

    Args:
        bot: Running bot.
        stored: Open workout view saved for this post.

    Returns:
        True when the row can be deleted. False when Discord should be
        tried again on the next pass.
    """
    try:
        channel = await _workout_thread(bot, stored.channel_id)
    except discord.NotFound:
        log.info("Workout thread %s is already gone", stored.channel_id)
        return True
    except discord.HTTPException:
        log.warning("Cannot fetch workout thread %s", stored.channel_id)
        return False
    if not isinstance(channel, discord.Thread):
        log.error("Workout message %s is not in a thread", stored.message_id)
        return False

    try:
        message = await channel.fetch_message(stored.message_id)
    except discord.NotFound:
        log.info("Workout message %s is already gone", stored.message_id)
        return True
    except discord.HTTPException:
        log.warning("Cannot fetch workout message %s", stored.message_id)
        return False

    hours = int(WORKOUT_LIMIT.total_seconds() // 3600)
    had_button = bool(message.components)
    if not had_button and channel.archived and channel.locked:
        return True
    try:
        if channel.archived or channel.locked:
            channel = await channel.edit(
                archived=False,
                locked=False,
                reason="Reopen an unfinished workout to close it",
            )
        if had_button:
            await message.edit(view=None)
            await channel.send(
                f"This workout was closed after {hours} hours because it was not marked finished."
            )
        await channel.edit(
            archived=True,
            locked=True,
            reason=f"Workout left unfinished for {hours} hours",
        )
    except discord.HTTPException:
        log.warning("Cannot close workout message %s", stored.message_id)
        return False
    return True


async def _workout_thread(bot: WorkoutBot, channel_id: int) -> discord.abc.GuildChannel | discord.Thread:
    """Return the channel that holds a workout post.

    Args:
        bot: Running bot.
        channel_id: Thread id saved with the workout view.

    Returns:
        The cached or fetched channel.

    Raises:
        discord.NotFound: Discord no longer has this channel.
        discord.HTTPException: The lookup failed for another reason.
    """
    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.Thread):
        return channel
    return await bot.fetch_channel(channel_id)


async def setup(bot: WorkoutBot) -> None:
    """Register the workout sweep cog.

    Args:
        bot: Bot that is loading this extension.

    Returns:
        None.
    """
    await bot.add_cog(WorkoutCog(bot))
