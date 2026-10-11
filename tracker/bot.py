"""Discord bot that owns the slash-command tree."""

import logging
import pkgutil

import discord
from discord.ext import commands

import tracker.cogs as cogs
from tracker.db.database import Database
from tracker.db.views import list_views
from tracker.ui.views import build_view

log = logging.getLogger(__name__)


class WorkoutBot(commands.Bot):
    """Gateway client for every guild the bot has joined.

    Slash commands live in ``tracker.cogs``. Persistent views are restored
    from SQLite during startup.
    """

    def __init__(self, database: Database) -> None:
        """Create the bot with the gateway intents this process uses.

        Message content is requested because the developer portal enables that
        privileged intent. Slash commands are registered on ``self.tree``.

        Args:
            database: Open database shared by every guild.

        Returns:
            None.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.database = database

    async def setup_hook(self) -> None:
        """Load cogs, restore views from every guild, then publish commands.

        Discord rate-limits global command sync, so this runs once per process
        start rather than on every reconnect. Global sync publishes the same
        commands to every guild.

        Returns:
            None.
        """
        await self._load_cogs()
        self._restore_views()
        synced = await self.tree.sync()
        log.info("Synced %s slash command(s)", len(synced))

    async def _load_cogs(self) -> None:
        """Load every slash-command cog from the cogs package.

        A cog is a module in that package. Each module must define
        ``async def setup(bot)``. Loading a cog also registers its views.

        Returns:
            None.

        Raises:
            discord.ext.commands.ExtensionError: A cog module failed to load.
        """
        names = sorted(
            module.name
            for module in pkgutil.iter_modules(cogs.__path__, prefix="tracker.cogs.")
        )
        for name in names:
            await self.load_extension(name)
            log.info("Loaded cog %s", name)

    def _restore_views(self) -> None:
        """Reattach every stored persistent view, from all guilds.

        Returns:
            None.
        """
        for stored in list_views(self.database):
            view = build_view(stored.view_name, stored.state)
            if view is None:
                log.warning(
                    "No view registered for %s on message %s",
                    stored.view_name,
                    stored.message_id,
                )
                continue
            self.add_view(view, message_id=stored.message_id)
            log.info(
                "Restored view %s for guild %s",
                stored.view_name,
                stored.guild_id,
            )

    async def on_ready(self) -> None:
        """Log the connected bot user.

        Discord can call this again after a reconnect.

        Returns:
            None.
        """
        log.info("Logged in as %s", self.user)

    async def close(self) -> None:
        """Close the database, then disconnect the gateway.

        Returns:
            None.
        """
        self.database.close()
        await super().close()
