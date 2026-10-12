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
        title = "Workout tracker",
        description = "This channel is where members will use the workout panel.\n\n"
        "You can delete this message. Leave the panel below it.",
    )


def build_setup_panel_embed() -> discord.Embed:
    """Build the member panel posted under the administrator notice.

    Returns:
        The embed. It says each member can create one private workout forum.
    """
    return build_embed(
        title = "Workout panel",
        description = "This panel is where you create your own place to track workouts.\n\n"
        "You can do this once. The bot creates a private forum that only you can see.",
    )


def build_start_workout_embed() -> discord.Embed:
    """Build the panel posted in the start-workout channel.

    Returns:
        The embed. It says the bot creates the workout post.
    """
    return build_embed(
        title = "Start workout",
        description = "Use this panel to start a workout in your private forum."
    )
