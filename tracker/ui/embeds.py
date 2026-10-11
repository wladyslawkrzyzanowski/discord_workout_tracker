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


def build_admin_setup_embed() -> discord.Embed:
    """Build the notice posted for administrators in the setup channel.

    Returns:
        The embed. It says this message can be deleted and the member panel below it should stay.
    """
    return build_embed(
        "Workout tracker",
        "This channel is where members will use the workout panel.\n\n"
        "You can delete this message. Leave the panel below it.",
    )
