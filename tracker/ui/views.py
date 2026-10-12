"""Persistent Discord views.

A view that must survive a restart uses ``timeout=None`` and stable
``custom_id`` values on its components. The process stores the message,
channel, guild, view name, and state, then calls ``Bot.add_view`` on startup.
The Interaction object itself is not stored.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime

import discord

from tracker.db.database import Database
from tracker.db.forums import get_member_forum, save_member_forum
from tracker.db.guilds import GuildSetup, get_guild_setup
from tracker.ui.embeds import build_setup_panel_embed, build_start_workout_embed

log = logging.getLogger(__name__)

SETUP_PANEL_VIEW = "setup_panel"
START_WORKOUT_VIEW = "start_workout"
CREATE_FORUM_BUTTON = "workout:create-forum"
START_WORKOUT_BUTTON = "workout:start"

ViewBuilder = Callable[[str], discord.ui.View]
_BUILDERS: dict[str, ViewBuilder] = {}


class PersistentView(discord.ui.View):
    """Base view that stays attached to its message across restarts."""

    def __init__(self) -> None:
        """Create a view that does not time out.

        Returns:
            None.
        """
        super().__init__(timeout=None)


def register_view(name: str) -> Callable[[ViewBuilder], ViewBuilder]:
    """Register the builder used to restore one kind of persistent view.

    Args:
        name: Stable name stored in the database. It must not change between releases.

    Returns:
        A decorator that records the builder and returns it unchanged.
    """

    def decorator(builder: ViewBuilder) -> ViewBuilder:
        """Record ``builder`` under the name closed over by ``register_view``.

        Args:
            builder: Callable that accepts the stored state and returns a view.

        Returns:
            The same builder.
        """
        _BUILDERS[name] = builder
        return builder

    return decorator


def build_view(view_name: str, state: str) -> discord.ui.View | None:
    """Rebuild a persistent view from its stored name and state.

    Args:
        view_name: Name passed to ``register_view``.
        state: Text saved with the view.

    Returns:
        The view, or None when no builder is registered under that name.
    """
    builder = _BUILDERS.get(view_name)
    if builder is None:
        return None
    return builder(state)


@register_view(SETUP_PANEL_VIEW)
def build_setup_panel(state: str) -> discord.ui.View:
    """Rebuild the setup panel after a restart.

    Args:
        state: Stored text. The panel reads the guild from the button press instead.

    Returns:
        The persistent setup panel.
    """
    del state
    return SetupPanelView()


class SetupPanelView(PersistentView):
    """Button that creates one private workout forum for the member who clicks it."""

    @discord.ui.button(
        label="Create my forum",
        style=discord.ButtonStyle.primary,
        custom_id=CREATE_FORUM_BUTTON,
    )
    async def create_forum(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Create this member's private forum, once per server.

        Args:
            interaction: Button press on the setup panel.
            button: The button that was pressed.

        Returns:
            None.
        """
        del button
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        database = _database(interaction)
        if guild is None or not isinstance(member, discord.Member) or database is None:
            await interaction.followup.send(
                "This panel only works in a server.",
                ephemeral=True,
            )
            return

        existing = await _existing_forum(guild, database, member.id)
        setup = get_guild_setup(database, guild.id)
        if existing is not None:
            start_workout = await _start_workout_channel(guild, setup)
            if start_workout is None:
                await interaction.followup.send(
                    f"You already have a private forum: {existing.mention}.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"You already have a private forum: {existing.mention}. Use the panel in {start_workout.mention} to start a workout.",
                ephemeral=True,
            )
            return

        category = None if setup is None else guild.get_channel(setup.category_id)
        if setup is None or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                "The Workout tracker category is missing. An administrator has to run /setup again.",
                ephemeral=True,
            )
            return

        role = None if setup.moderator_role_id is None else guild.get_role(setup.moderator_role_id)
        try:
            forum = await guild.create_forum(
                forum_channel_name(member.display_name, member.id),
                category=category,
                overwrites=private_forum_overwrites(guild, member, role),
                reason=f"Private workout forum for {member.id}",
            )
        except discord.Forbidden:
            log.warning("Missing Manage Channels while creating a forum in guild %s", guild.id)
            await interaction.followup.send(
                'Missing "Manage Channels" permission for the bot.',
                ephemeral=True,
            )
            return

        save_member_forum(database, guild.id, member.id, forum.id)
        log.info("Created forum %s for user %s in guild %s", forum.id, member.id, guild.id)
        role_note = await _grant_tracking_role(guild, member, setup.tracking_role_id)
        await interaction.followup.send(
            f"Your private forum is ready: {forum.mention}.{role_note}",
            ephemeral=True,
        )


async def send_setup_panel(channel: discord.TextChannel) -> discord.Message:
    """Send the member panel into the setup channel.

    The caller posts the administrator notice first, then calls this, so the
    panel is the next message in the channel.

    Args:
        channel: Channel created for this guild's workout panel.

    Returns:
        The panel message. The caller stores it so the button survives a restart.
    """
    return await channel.send(embed=build_setup_panel_embed(), view=SetupPanelView())


@register_view(START_WORKOUT_VIEW)
def build_start_workout(state: str) -> discord.ui.View:
    """Rebuild the start-workout panel after a restart.

    Args:
        state: Stored text. The panel reads the member from the button press instead.

    Returns:
        The persistent start-workout panel.
    """
    del state
    return StartWorkoutView()


class StartWorkoutView(PersistentView):
    """Button that asks the bot to open a workout post in the member's forum."""

    @discord.ui.button(
        label="Start workout",
        style=discord.ButtonStyle.primary,
        custom_id=START_WORKOUT_BUTTON,
    )
    async def start_workout(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Create a workout post in this member's private forum.

        The post has no image yet. The tile is attached when the workout ends,
        so it can show the type of workout that was done.

        Args:
            interaction: Button press on the start-workout panel.
            button: The button that was pressed.

        Returns:
            None.
        """
        del button
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        database = _database(interaction)
        if guild is None or not isinstance(member, discord.Member) or database is None:
            await interaction.followup.send(
                "This panel only works in a server.",
                ephemeral=True,
            )
            return

        forum = await _existing_forum(guild, database, member.id)
        if forum is None:
            await interaction.followup.send(
                "Create your private forum from the setup channel first.",
                ephemeral=True,
            )
            return

        try:
            created = await forum.create_thread(
                name=workout_post_name(),
                content=f"Workout started for {member.mention}.",
                reason=f"Workout post for {member.id}",
            )
        except discord.Forbidden:
            log.warning("Cannot create a workout post in forum %s", forum.id)
            await interaction.followup.send(
                'Missing permission for the bot to create a post in your forum.',
                ephemeral=True,
            )
            return

        log.info("Created workout post %s for user %s", created.thread.id, member.id)
        await interaction.followup.send(
            f"Your workout post is ready: {created.thread.mention}",
            ephemeral=True,
        )


async def send_start_workout_panel(channel: discord.TextChannel) -> discord.Message:
    """Send the start-workout panel into its channel.

    Args:
        channel: Read-only channel visible to the TrackingWorkout role.

    Returns:
        The panel message. The caller stores it so the button survives a restart.
    """
    return await channel.send(embed=build_start_workout_embed(), view=StartWorkoutView())


def workout_post_name() -> str:
    """Build a forum post title from the local date and time.

    Returns:
        A title such as "Workout 2026-10-11 16:58".
    """
    return datetime.now().strftime("Workout %Y-%m-%d %H:%M")


def forum_channel_name(display_name: str, user_id: int) -> str:
    """Build a unique forum name from a member's display name.

    Args:
        display_name: Name shown for the member in the guild.
        user_id: Discord user id, appended so two members can share a name.

    Returns:
        A lowercase channel name of at most 100 characters.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    suffix = f"-{user_id}"
    if not cleaned:
        return f"workout{suffix}"[:100]
    trimmed = cleaned[: 100 - len(suffix)].strip("-")
    if not trimmed:
        return f"workout{suffix}"[:100]
    return f"{trimmed}{suffix}"


def private_forum_overwrites(
    guild: discord.Guild,
    member: discord.Member,
    moderator: discord.Role | None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """Hide a forum from everyone except its owner and the moderator role.

    Both can read the forum. Neither can post, reply, or attach files.
    The bot creates posts from the start-workout button. A channel deny for
    everyone is applied after category allows, so the moderator role needs
    its own overwrite on this channel.

    Args:
        guild: Guild that will contain the forum.
        member: Member who owns the forum.
        moderator: Role that should see the forum, or None when setup has not stored one.

    Returns:
        Overwrites passed to forum creation.
    """
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            send_messages_in_threads=False,
            create_public_threads=False,
            create_private_threads=False,
        ),
        member: read_only_forum_permissions(),
    }
    if moderator is not None:
        overwrites[moderator] = read_only_forum_permissions()
    me = guild.me
    if me is not None:
        overwrites[me] = bot_forum_permissions()
    return overwrites


def read_only_forum_permissions() -> discord.PermissionOverwrite:
    """Let someone open a private forum without posting in it.

    Returns:
        The overwrite for the forum owner and the WorkoutModerator role.
    """
    return discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        send_messages=False,
        send_messages_in_threads=False,
        create_public_threads=False,
        create_private_threads=False,
        attach_files=False,
        embed_links=False,
        add_reactions=False,
    )


def bot_forum_permissions() -> discord.PermissionOverwrite:
    """Permissions the bot needs to create a post and later attach its tile.

    Returns:
        The overwrite applied to the bot member on a private forum.
    """
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        send_messages_in_threads=True,
        create_public_threads=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        add_reactions=True,
        use_external_emojis=True,
        manage_messages=True,
        manage_threads=True,
        manage_channels=True,
    )


def _database(interaction: discord.Interaction) -> Database | None:
    """Read the database from the running bot.

    Args:
        interaction: Button press or slash command.

    Returns:
        The bot database, or None when the client does not own one.
    """
    database = getattr(interaction.client, "database", None)
    if isinstance(database, Database):
        return database
    return None


async def _start_workout_channel(
    guild: discord.Guild,
    setup: GuildSetup | None,
) -> discord.TextChannel | None:
    """Return the start-workout channel saved for this guild.

    Args:
        guild: Guild the button was pressed in.
        setup: Saved setup, or None when this guild has not been set up.

    Returns:
        The text channel, or None when setup has no start-workout channel.
    """
    if setup is None or setup.start_channel_id is None:
        return None
    channel = guild.get_channel(setup.start_channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel
    try:
        fetched = await guild.fetch_channel(setup.start_channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None
    if isinstance(fetched, discord.TextChannel):
        return fetched
    return None


async def _existing_forum(
    guild: discord.Guild,
    database: Database,
    user_id: int,
) -> discord.ForumChannel | None:
    """Return the member's forum when it is still in the guild.

    Args:
        guild: Guild the button was pressed in.
        database: Open database.
        user_id: Member who pressed the button.

    Returns:
        The forum channel, or None when the member has none or it was deleted.
    """
    stored = get_member_forum(database, guild.id, user_id)
    if stored is None:
        return None
    channel = guild.get_channel(stored.channel_id)
    if isinstance(channel, discord.ForumChannel):
        return channel
    return None


async def _grant_tracking_role(
    guild: discord.Guild,
    member: discord.Member,
    role_id: int | None,
) -> str:
    """Give the member the role that reveals the start-workout channel.

    Args:
        guild: Guild that contains the role.
        member: Member who just received a private forum.
        role_id: TrackingWorkout role id, or None when setup has not stored one.

    Returns:
        An empty string when the role was assigned or was not needed, otherwise
        a sentence explaining that the assignment failed.
    """
    if role_id is None:
        return ""
    role = guild.get_role(role_id)
    if role is None or role in member.roles:
        return ""
    try:
        await member.add_roles(role, reason="Can see start-workout")
    except discord.Forbidden:
        log.warning("Cannot assign TrackingWorkout to user %s in guild %s", member.id, guild.id)
        return " The bot could not assign the TrackingWorkout role."
    return ""
