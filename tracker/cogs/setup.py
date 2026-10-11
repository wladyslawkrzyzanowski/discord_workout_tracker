"""Slash command that creates a guild's workout category and channel."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tracker.bot import WorkoutBot
from tracker.db.guilds import get_guild_setup, save_guild_setup
from tracker.ui.embeds import build_admin_setup_embed
from tracker.ui.views import send_setup_panel

log = logging.getLogger(__name__)

CATEGORY_NAME = "Workout tracker"
CHANNEL_NAME = "setup"
MISSING_ADMINISTRATOR_PERMISSION = 'Missing "Administrator" permission.'


class SetupCog(commands.Cog):
    """Creates the category and channel where a guild's workouts live."""

    def __init__(self, bot: WorkoutBot) -> None:
        """Store the bot that owns the database.

        Args:
            bot: Running bot. Its database is already open.

        Returns:
            None.
        """
        self.bot = bot

    @app_commands.command(
        name="setup",
        description="Create the Workout tracker category and the setup channel.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        """Create this guild's workout category and setup channel.

        Args:
            interaction: Slash-command invocation. The caller must be a guild administrator.

        Returns:
            None.
        """
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        try:
            category, channel, created_channel = await self._ensure_setup_channel(guild)
        except discord.Forbidden:
            log.warning("Missing Manage Channels in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Channels" permission for the bot.',
                ephemeral=True,
            )
            return

        save_guild_setup(self.bot.database, guild.id, category.id, channel.id)
        log.info(
            "Saved workout location for guild %s category %s channel %s",
            guild.id,
            category.id,
            channel.id,
        )
        if not created_channel:
            await interaction.followup.send(
                f"Workout tracker is already set up in {channel.mention}.",
                ephemeral=True,
            )
            return

        try:
            await channel.send(embed=build_admin_setup_embed())
            await send_setup_panel(channel)
        except discord.Forbidden:
            log.warning("Cannot send in channel %s", channel.id)
            await interaction.followup.send(
                f"Created {channel.mention}, but the bot cannot send messages there.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Setup is ready in {channel.mention}.",
            ephemeral=True,
        )

    async def _ensure_setup_channel(
        self,
        guild: discord.Guild,
    ) -> tuple[discord.CategoryChannel, discord.TextChannel, bool]:
        """Reuse this guild's workout category and channel, or create the missing ones.

        Args:
            guild: Guild that invoked the setup command.

        Returns:
            The category, the setup channel, and whether that channel was created now.

        Raises:
            discord.Forbidden: The bot cannot create or see the channel.
        """
        stored = get_guild_setup(self.bot.database, guild.id)
        category = _existing_category(guild, None if stored is None else stored.category_id)
        if category is None:
            category = await guild.create_category(CATEGORY_NAME)
            log.info("Created category %s in guild %s", category.id, guild.id)

        channel = _existing_channel(
            category,
            None if stored is None else stored.channel_id,
        )
        if channel is not None:
            return category, channel, False

        overwrites = _bot_overwrites(guild)
        channel = await guild.create_text_channel(
            CHANNEL_NAME,
            category=category,
            overwrites=overwrites,
        )
        log.info("Created channel %s in guild %s", channel.id, guild.id)
        return category, channel, True

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Reply when the caller lacks the Administrator permission.

        Args:
            interaction: Slash-command invocation that failed.
            error: Error raised by the command or by one of its checks.

        Returns:
            None.

        Raises:
            app_commands.AppCommandError: The error is not a missing Administrator permission.
        """
        if isinstance(error, app_commands.MissingPermissions):
            await _send_missing_administrator(interaction)
            return
        raise error


def _existing_category(
    guild: discord.Guild,
    category_id: int | None,
) -> discord.CategoryChannel | None:
    """Find a previously saved category, or one already named Workout tracker.

    Args:
        guild: Guild to search.
        category_id: Saved category id, or None when this guild has no saved setup.

    Returns:
        The category, or None when it is not in the guild.
    """
    if category_id is not None:
        channel = guild.get_channel(category_id)
        if isinstance(channel, discord.CategoryChannel):
            return channel
    return discord.utils.get(guild.categories, name=CATEGORY_NAME)


def _existing_channel(
    category: discord.CategoryChannel,
    channel_id: int | None,
) -> discord.TextChannel | None:
    """Find a previously saved setup channel inside the category.

    Args:
        category: Workout tracker category.
        channel_id: Saved channel id, or None when this guild has no saved setup.

    Returns:
        The text channel, or None when it is not in this category.
    """
    if channel_id is not None:
        channel = category.guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) and channel.category_id == category.id:
            return channel
    return discord.utils.get(category.text_channels, name=CHANNEL_NAME)


def _bot_overwrites(guild: discord.Guild) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """Allow the bot to post the notice and the later member panel.

    Args:
        guild: Guild whose bot member receives the overwrite.

    Returns:
        Channel overwrites. Empty when the bot member is not cached.
    """
    me = guild.me
    if me is None:
        return {}
    return {
        me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            read_message_history=True,
        )
    }


async def _send_missing_administrator(interaction: discord.Interaction) -> None:
    """Tell the caller they need the Administrator permission.

    Args:
        interaction: Slash-command invocation that failed the permission check.

    Returns:
        None.
    """
    if interaction.response.is_done():
        await interaction.followup.send(MISSING_ADMINISTRATOR_PERMISSION, ephemeral=True)
        return
    await interaction.response.send_message(MISSING_ADMINISTRATOR_PERMISSION, ephemeral=True)


async def setup(bot: WorkoutBot) -> None:
    """Register the setup cog.

    Args:
        bot: Bot that is loading this extension.

    Returns:
        None.
    """
    await bot.add_cog(SetupCog(bot))
