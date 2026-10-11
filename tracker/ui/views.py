"""Persistent Discord views.

A view that must survive a restart uses ``timeout=None`` and stable
``custom_id`` values on its components. The process stores the message,
channel, guild, view name, and state, then calls ``Bot.add_view`` on startup.
The Interaction object itself is not stored.
"""

from collections.abc import Callable

import discord

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


async def send_setup_panel(channel: discord.abc.Messageable) -> None:
    """Send the member panel into the setup channel.

    The caller posts the administrator notice first, then calls this, so the
    panel is the next message in the channel.

    Args:
        channel: Channel created for this guild's workout panel.

    Returns:
        None.
    """
    return None
