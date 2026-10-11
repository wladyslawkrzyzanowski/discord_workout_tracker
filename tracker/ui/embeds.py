"""Builders for Discord embeds."""

import discord


def build_embed(title: str, description: str) -> discord.Embed:
    """Build an embed with the tracker's default colour.

    Args:
        title: Embed title.
        description: Embed body.

    Returns:
        The embed, ready to send.
    """
    return discord.Embed(
        title=title,
        description=description,
        colour=discord.Colour.blurple(),
    )
