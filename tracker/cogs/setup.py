"""Slash command that creates a guild's workout category and channel."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from tracker.bot import WorkoutBot
from tracker.db.database import Database
from tracker.db.forums import list_member_forums
from tracker.db.guilds import get_guild_setup, save_guild_setup
from tracker.db.views import channel_has_view, find_channel_view, save_view
from tracker.ui.embeds import build_admin_setup_embed, build_setup_panel_embed
from tracker.ui.views import (
    SETUP_PANEL_VIEW,
    START_WORKOUT_VIEW,
    SetupPanelView,
    bot_forum_permissions,
    read_only_forum_permissions,
    send_setup_panel,
    send_start_workout_panel,
)

log = logging.getLogger(__name__)

CATEGORY_NAME = "Workout tracker"
CHANNEL_NAME = "setup"
START_CHANNEL_NAME = "start-workout"
ROLE_NAME = "WorkoutModerator"
TRACKING_ROLE_NAME = "TrackingWorkout"
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
        description="Create the workout channels and the moderator and tracking roles.",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        """Create this guild's workout channels and roles.

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

        stored = get_guild_setup(self.bot.database, guild.id)
        try:
            category, channel, created_channel = await self._ensure_setup_channel(guild)
            start_channel = await self._ensure_start_channel(
                guild,
                category,
                None if stored is None else stored.start_channel_id,
            )
        except discord.Forbidden:
            log.warning("Missing Manage Channels in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Channels" permission for the bot.',
                ephemeral=True,
            )
            return

        try:
            moderator = await _ensure_role(
                guild,
                None if stored is None else stored.moderator_role_id,
                ROLE_NAME,
                "Sees every Workout tracker channel",
            )
            tracking = await _ensure_role(
                guild,
                None if stored is None else stored.tracking_role_id,
                TRACKING_ROLE_NAME,
                "Sees the start-workout channel",
            )
        except discord.Forbidden:
            log.warning("Missing Manage Roles in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Roles" permission for the bot.',
                ephemeral=True,
            )
            return

        try:
            await _apply_setup_channel_permissions(guild, channel)
            await _apply_start_channel_permissions(guild, start_channel, tracking, moderator)
            await _grant_moderator_visibility(self.bot.database, guild, moderator, category)
        except discord.Forbidden:
            log.warning("Cannot edit channel permissions in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Channels" permission for the bot.',
                ephemeral=True,
            )
            return

        try:
            await _grant_tracking_role_to_owners(self.bot.database, guild, tracking)
        except discord.Forbidden:
            log.warning("Cannot assign TrackingWorkout in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Roles" permission for the bot.',
                ephemeral=True,
            )
            return

        save_guild_setup(
            self.bot.database,
            guild.id,
            category.id,
            channel.id,
            moderator.id,
            tracking.id,
            start_channel.id,
        )
        log.info(
            "Saved workout location for guild %s category %s setup %s start %s",
            guild.id,
            category.id,
            channel.id,
            start_channel.id,
        )

        try:
            await self._post_setup_messages(guild, channel, created_channel)
            await self._post_start_panel(guild, start_channel)
        except discord.Forbidden:
            log.warning("Cannot send in a setup channel in guild %s", guild.id)
            await interaction.followup.send(
                "Channels were created, but the bot cannot send messages there.",
                ephemeral=True,
            )
            return

        ready = "Setup is ready" if created_channel else "Workout tracker is already set up"
        await interaction.followup.send(
            f"{ready} in {channel.mention}. {moderator.mention} can see the category. "
            f"{tracking.mention} can see {start_channel.mention}.",
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

        channel = _existing_text_channel(
            category,
            None if stored is None else stored.channel_id,
            CHANNEL_NAME,
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

    async def _ensure_start_channel(
        self,
        guild: discord.Guild,
        category: discord.CategoryChannel,
        channel_id: int | None,
    ) -> discord.TextChannel:
        """Reuse the start-workout channel, or create it in the workout category.

        Args:
            guild: Guild that invoked the setup command.
            category: Workout tracker category.
            channel_id: Saved channel id, or None when setup has not stored one.

        Returns:
            The start-workout text channel.

        Raises:
            discord.Forbidden: The bot cannot create the channel.
        """
        channel = _existing_text_channel(category, channel_id, START_CHANNEL_NAME)
        if channel is not None:
            return channel
        channel = await guild.create_text_channel(
            START_CHANNEL_NAME,
            category=category,
            overwrites=_bot_overwrites(guild),
        )
        log.info("Created channel %s in guild %s", channel.id, guild.id)
        return channel

    async def _post_start_panel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        """Post the start-workout panel when that channel does not have one yet.

        Args:
            guild: Guild that invoked setup.
            channel: Read-only start-workout channel.

        Returns:
            None.

        Raises:
            discord.Forbidden: The bot cannot send messages in the channel.
        """
        if channel_has_view(self.bot.database, channel.id, START_WORKOUT_VIEW):
            return
        message = await send_start_workout_panel(channel)
        save_view(
            self.bot.database,
            message.id,
            channel.id,
            guild.id,
            START_WORKOUT_VIEW,
            "",
        )

    async def _post_setup_messages(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        created_channel: bool,
    ) -> None:
        """Post the administrator notice and keep the member panel current.

        An existing panel is edited so newly added buttons, such as the gear,
        appear without a second message.

        Args:
            guild: Guild that invoked setup.
            channel: Setup channel in the Workout tracker category.
            created_channel: True when this call created the channel.

        Returns:
            None.

        Raises:
            discord.Forbidden: The bot cannot send messages in the channel.
        """
        if created_channel:
            await channel.send(embed=build_admin_setup_embed())
        stored = find_channel_view(self.bot.database, channel.id, SETUP_PANEL_VIEW)
        if stored is not None:
            try:
                message = await channel.fetch_message(stored.message_id)
            except discord.NotFound:
                message = None
            if message is not None:
                await message.edit(embed=build_setup_panel_embed(), view=SetupPanelView())
                return
        message = await send_setup_panel(channel)
        save_view(
            self.bot.database,
            message.id,
            channel.id,
            guild.id,
            SETUP_PANEL_VIEW,
            "",
        )

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


async def _ensure_role(
    guild: discord.Guild,
    role_id: int | None,
    name: str,
    reason: str,
) -> discord.Role:
    """Reuse a role by its saved id or name, or create it.

    Args:
        guild: Guild that invoked setup.
        role_id: Saved role id, or None when setup has not stored one.
        name: Role name used when the saved id is missing.
        reason: Audit-log reason used when the role is created.

    Returns:
        The existing or new role.

    Raises:
        discord.Forbidden: The bot cannot create the role.
    """
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is not None:
            return role
    found = discord.utils.get(guild.roles, name=name)
    if found is not None:
        return found
    role = await guild.create_role(name=name, reason=reason)
    log.info("Created role %s in guild %s", role.id, guild.id)
    return role


async def _grant_moderator_visibility(
    database: Database,
    guild: discord.Guild,
    role: discord.Role,
    category: discord.CategoryChannel,
) -> None:
    """Let the moderator role see the category and every private forum.

    A private forum denies view access for everyone on the channel itself.
    That deny is applied after the category allow, so the role also needs an
    allow on each forum. Owners and moderators stay read-only: the bot creates
    the posts.

    Args:
        database: Open database.
        guild: Guild that contains the category.
        role: WorkoutModerator role.
        category: Workout tracker category.

    Returns:
        None.

    Raises:
        discord.Forbidden: The bot cannot edit these overwrites.
    """
    await category.set_permissions(
        role,
        view_channel=True,
        reason="WorkoutModerator sees this category",
    )
    me = guild.me
    for record in list_member_forums(database, guild.id):
        channel = guild.get_channel(record.channel_id)
        if not isinstance(channel, discord.ForumChannel):
            continue
        await channel.set_permissions(
            role,
            overwrite=read_only_forum_permissions(),
            reason="WorkoutModerator sees private forums",
        )
        member = guild.get_member(record.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(record.user_id)
            except discord.NotFound:
                member = None
        if member is not None:
            await channel.set_permissions(
                member,
                overwrite=read_only_forum_permissions(),
                reason="Members read posts the bot creates",
            )
        if me is not None:
            await channel.set_permissions(
                me,
                overwrite=bot_forum_permissions(),
                reason="Bot creates workout posts and later attaches their tile image",
            )


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


def _existing_text_channel(
    category: discord.CategoryChannel,
    channel_id: int | None,
    name: str,
) -> discord.TextChannel | None:
    """Find a previously saved text channel inside the category, or one with this name.

    Args:
        category: Workout tracker category.
        channel_id: Saved channel id, or None when setup has not stored one.
        name: Channel name used when the saved id is missing.

    Returns:
        The text channel, or None when it is not in this category.
    """
    if channel_id is not None:
        channel = category.guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel) and channel.category_id == category.id:
            return channel
    return discord.utils.get(category.text_channels, name=name)


def nobody_can_type(*, hide: bool) -> discord.PermissionOverwrite:
    """Block messages from @everyone in a panel channel.

    Args:
        hide: When True, also deny View Channel. The start-workout channel uses
            this. The setup channel stays visible.

    Returns:
        The overwrite for the @everyone role.
    """
    return discord.PermissionOverwrite(
        view_channel=False if hide else None,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
    )


def can_see_but_not_type() -> discord.PermissionOverwrite:
    """Let a role open a panel channel without sending messages.

    Returns:
        The overwrite for TrackingWorkout or WorkoutModerator on a read-only channel.
    """
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
    )


def bot_text_permissions() -> discord.PermissionOverwrite:
    """Let the bot post a panel in a channel where everyone else cannot type.

    Returns:
        The overwrite for the bot member.
    """
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
        attach_files=True,
        add_reactions=True,
        use_external_emojis=True,
    )


async def _apply_setup_channel_permissions(
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> None:
    """Stop everyone except the bot from typing in the setup channel.

    Args:
        guild: Guild that contains the channel.
        channel: Setup channel.

    Returns:
        None.

    Raises:
        discord.Forbidden: The bot cannot edit these overwrites.
    """
    await channel.set_permissions(
        guild.default_role,
        overwrite=nobody_can_type(hide=False),
        reason="Members use the panel instead of typing",
    )
    me = guild.me
    if me is not None:
        await channel.set_permissions(
            me,
            overwrite=bot_text_permissions(),
            reason="Bot posts the setup panel",
        )


async def _apply_start_channel_permissions(
    guild: discord.Guild,
    channel: discord.TextChannel,
    tracking: discord.Role,
    moderator: discord.Role,
) -> None:
    """Hide start-workout from everyone and show it to the tracking role.

    Nobody can type in the channel. The view creates posts instead.

    Args:
        guild: Guild that contains the channel.
        channel: Start-workout channel.
        tracking: Role that can see the channel.
        moderator: Role that can see every workout channel.

    Returns:
        None.

    Raises:
        discord.Forbidden: The bot cannot edit these overwrites.
    """
    await channel.set_permissions(
        guild.default_role,
        overwrite=nobody_can_type(hide=True),
        reason="Only TrackingWorkout can see this channel",
    )
    visible = can_see_but_not_type()
    await channel.set_permissions(
        tracking,
        overwrite=visible,
        reason="TrackingWorkout can see start-workout",
    )
    await channel.set_permissions(
        moderator,
        overwrite=visible,
        reason="WorkoutModerator can see start-workout",
    )
    me = guild.me
    if me is not None:
        await channel.set_permissions(
            me,
            overwrite=bot_text_permissions(),
            reason="Bot posts the start-workout panel",
        )


async def _grant_tracking_role_to_owners(
    database: Database,
    guild: discord.Guild,
    role: discord.Role,
) -> None:
    """Give TrackingWorkout to members who already have a private forum.

    Args:
        database: Open database.
        guild: Guild that contains the role.
        role: TrackingWorkout role.

    Returns:
        None.

    Raises:
        discord.Forbidden: The bot cannot assign the role.
    """
    for record in list_member_forums(database, guild.id):
        member = guild.get_member(record.user_id)
        if member is None:
            try:
                member = await guild.fetch_member(record.user_id)
            except discord.NotFound:
                continue
        if role not in member.roles:
            await member.add_roles(role, reason="Can see start-workout")


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
