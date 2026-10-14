"""Exercise menu posted on a workout thread, and the ephemeral set editor.

The menu stays on the starter message. Each added set updates a second
message below it. Weight and reps are chosen in an ephemeral message.
"""

import json
import logging
import re
from datetime import datetime

import discord

from tracker.db.catalog import CatalogExercise, ensure_user_catalog, list_categories, list_exercises
from tracker.db.database import Database
from tracker.db.member_views import replace_member_view_payload
from tracker.db.sets import add_set, next_set_number, session_log, suggestion_for_set
from tracker.ui.embeds import build_workout_log_embed

log = logging.getLogger(__name__)

PAGE_SIZE = 25
EMPTY_EXERCISE = "__empty__"
CATEGORY_SELECT = "workout:session-category"
EXERCISE_SELECT = "workout:session-exercise"
CATEGORY_PREVIOUS = "workout:session-category-previous"
CATEGORY_NEXT = "workout:session-category-next"
EXERCISE_PREVIOUS = "workout:session-exercise-previous"
EXERCISE_NEXT = "workout:session-exercise-next"
_database: Database | None = None


def bind_database(database: Database) -> None:
    """Remember the database used to rebuild workout menus after a restart.

    Args:
        database: Open database.

    Returns:
        None.
    """
    global _database
    _database = database


def menu_from_payload(payload: str) -> discord.ui.View | None:
    """Rebuild a workout menu from the JSON stored with its message.

    Args:
        payload: JSON saved when the workout post was created or its page changed.

    Returns:
        The menu, or None when the payload belongs to an older post that only
        had an acknowledgement button. None is also returned before the
        database is bound.
    """
    if _database is None:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "category_id" not in data:
        return None
    try:
        guild_id = int(data["guild_id"])
        user_id = int(data["user_id"])
        exercise_page = int(data.get("exercise_page", 0))
        category_page = int(data.get("category_page", 0))
    except (KeyError, TypeError, ValueError):
        return None
    log_message_id = data.get("log_message_id")
    if log_message_id is not None:
        try:
            log_message_id = int(log_message_id)
        except (TypeError, ValueError):
            log_message_id = None
    started_at = data.get("started_at")
    if not isinstance(started_at, str):
        started_at = datetime.now().isoformat()
    return ExerciseMenuView(
        _database,
        guild_id,
        user_id,
        str(data["category_id"]),
        exercise_page,
        category_page,
        log_message_id,
        started_at,
    )


class ExerciseMenuView(discord.ui.View):
    """Category and exercise menus on an open workout post."""

    def __init__(
        self,
        database: Database,
        guild_id: int,
        user_id: int,
        category_id: str,
        exercise_page: int,
        category_page: int,
        log_message_id: int | None,
        started_at: str,
    ) -> None:
        """Build the menus for one member's open workout.

        Args:
            database: Open database.
            guild_id: Discord guild that owns the workout.
            user_id: Member who owns the workout.
            category_id: Category whose exercises are shown.
            exercise_page: Zero-based page of exercises.
            category_page: Zero-based page of categories.
            log_message_id: Message that lists the sets, or None when it is not sent yet.
            started_at: ISO timestamp stored so the 24 hour sweep still finds it.

        Returns:
            None.
        """
        super().__init__(timeout=None)
        self.database = database
        self.guild_id = guild_id
        self.user_id = user_id
        self.category_id = category_id
        self.exercise_page = exercise_page
        self.category_page = category_page
        self.log_message_id = log_message_id
        self.started_at = started_at
        self.rebuild()

    def rebuild(self) -> None:
        """Replace the menus so they match the current category and pages.

        Returns:
            None.
        """
        self.clear_items()
        ensure_user_catalog(self.database, self.guild_id, self.user_id)
        categories = list_categories(self.database, self.guild_id, self.user_id)
        if not any(category.category_id == self.category_id for category in categories) and categories:
            self.category_id = categories[0].category_id
        category_pages = _chunks(categories)
        self.category_page = _clamp_page(self.category_page, len(category_pages))
        visible_categories = category_pages[self.category_page] if category_pages else []
        self.add_item(WorkoutCategorySelect(visible_categories, self.category_id))
        exercises = list_exercises(self.database, self.guild_id, self.user_id, self.category_id)
        exercise_pages = _chunks(exercises)
        self.exercise_page = _clamp_page(self.exercise_page, len(exercise_pages))
        visible_exercises = exercise_pages[self.exercise_page] if exercise_pages else []
        self.add_item(WorkoutExerciseSelect(visible_exercises))
        if len(category_pages) > 1:
            self._add_page_buttons(
                "Categories",
                CATEGORY_PREVIOUS,
                CATEGORY_NEXT,
                self.category_page,
                len(category_pages),
                row=2,
            )
        if len(exercise_pages) > 1:
            self._add_page_buttons(
                "Exercises",
                EXERCISE_PREVIOUS,
                EXERCISE_NEXT,
                self.exercise_page,
                len(exercise_pages),
                row=3,
            )

    def payload(self) -> str:
        """Encode the menu state stored for a restart.

        Returns:
            A JSON object.
        """
        return json.dumps(
            {
                "started_at": self.started_at,
                "guild_id": self.guild_id,
                "user_id": self.user_id,
                "log_message_id": self.log_message_id,
                "category_id": self.category_id,
                "exercise_page": self.exercise_page,
                "category_page": self.category_page,
            }
        )

    def persist(self, message_id: int) -> None:
        """Write the current menu state over the stored payload.

        Args:
            message_id: Starter message that holds this menu.

        Returns:
            None.
        """
        replace_member_view_payload(self.database, message_id, self.payload())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore a click from anyone other than the workout owner.

        Args:
            interaction: Component click on the workout post.

        Returns:
            True when the click belongs to the owner.
        """
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "This workout belongs to another member.",
            ephemeral=True,
        )
        return False

    def _add_page_buttons(
        self,
        label: str,
        previous_id: str,
        next_id: str,
        page: int,
        page_count: int,
        row: int,
    ) -> None:
        """Add previous and next buttons for one menu.

        Args:
            label: Word used on the buttons, such as ``Exercises``.
            previous_id: Stable custom id for the previous button.
            next_id: Stable custom id for the next button.
            page: Zero-based page currently shown.
            page_count: Number of pages.
            row: Action row on the message.

        Returns:
            None.
        """
        self.add_item(
            WorkoutPageButton(
                label=f"{label} previous",
                custom_id=previous_id,
                row=row,
                disabled=page == 0,
            )
        )
        self.add_item(
            WorkoutPageButton(
                label=f"{label} next",
                custom_id=next_id,
                row=row,
                disabled=page >= page_count - 1,
            )
        )


class WorkoutCategorySelect(discord.ui.Select):
    """Menu of categories on a workout post."""

    def __init__(self, categories: list, selected_id: str) -> None:
        """Fill one page of categories.

        Args:
            categories: Categories shown on this page.
            selected_id: Category to mark as the current choice.

        Returns:
            None.
        """
        options = [
            discord.SelectOption(
                label=category.name,
                value=category.category_id,
                default=category.category_id == selected_id,
            )
            for category in categories
        ]
        if not options:
            options = [discord.SelectOption(label="No categories", value=EMPTY_EXERCISE)]
        super().__init__(
            custom_id=CATEGORY_SELECT,
            placeholder="Category",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show exercises from the chosen category.

        Args:
            interaction: Select change on the workout post.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, ExerciseMenuView):
            return
        if self.values[0] == EMPTY_EXERCISE:
            await interaction.response.defer()
            return
        view.category_id = self.values[0]
        view.exercise_page = 0
        view.rebuild()
        if interaction.message is not None:
            view.persist(interaction.message.id)
        await interaction.response.edit_message(view=view)


class WorkoutExerciseSelect(discord.ui.Select):
    """Menu of exercises in the open category."""

    def __init__(self, exercises: list) -> None:
        """Fill one page of exercises.

        Args:
            exercises: Exercises shown on this page.

        Returns:
            None.
        """
        if exercises:
            options = [
                discord.SelectOption(label=exercise.name, value=exercise.exercise_id)
                for exercise in exercises
            ]
        else:
            options = [discord.SelectOption(label="No exercises in this category", value=EMPTY_EXERCISE)]
        super().__init__(
            custom_id=EXERCISE_SELECT,
            placeholder="Exercise",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral editor for the chosen exercise.

        Args:
            interaction: Select change on the workout post.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, ExerciseMenuView):
            return
        chosen = self.values[0]
        if chosen == EMPTY_EXERCISE:
            await interaction.response.send_message(
                "This category has no exercises.",
                ephemeral=True,
            )
            return
        exercise = next(
            (
                item
                for item in list_exercises(view.database, view.guild_id, view.user_id, view.category_id)
                if item.exercise_id == chosen
            ),
            None,
        )
        if exercise is None or interaction.channel is None or interaction.message is None:
            await interaction.response.send_message(
                "That exercise is no longer in this category.",
                ephemeral=True,
            )
            return
        editor = SetEntryView(view, exercise, interaction.channel.id, interaction.message.id)
        await interaction.response.send_message(
            embed=editor.render_embed(),
            view=editor,
            ephemeral=True,
        )
        view.rebuild()
        await interaction.message.edit(view=view)


class WorkoutPageButton(discord.ui.Button):
    """Previous or next page for the category or exercise menu."""

    def __init__(self, *, label: str, custom_id: str, row: int, disabled: bool) -> None:
        """Create a page button with a stable custom id.

        Args:
            label: Text on the button.
            custom_id: Id stored with the persistent view.
            row: Action row on the message.
            disabled: True on the first or last page.

        Returns:
            None.
        """
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
            row=row,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Turn one page and store it for the next restart.

        Args:
            interaction: Button press on the workout post.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, ExerciseMenuView) or self.custom_id is None:
            return
        if self.custom_id == CATEGORY_PREVIOUS:
            view.category_page -= 1
        elif self.custom_id == CATEGORY_NEXT:
            view.category_page += 1
        elif self.custom_id == EXERCISE_PREVIOUS:
            view.exercise_page -= 1
        elif self.custom_id == EXERCISE_NEXT:
            view.exercise_page += 1
        view.rebuild()
        if interaction.message is not None:
            view.persist(interaction.message.id)
        await interaction.response.edit_message(view=view)


class SetEntryView(discord.ui.View):
    """Ephemeral editor for the weight and reps of one set."""

    def __init__(
        self,
        menu: ExerciseMenuView,
        exercise: CatalogExercise,
        thread_id: int,
        starter_message_id: int,
    ) -> None:
        """Prepare the next set for one exercise.

        Args:
            menu: Workout menu that owns the set list.
            exercise: Catalog exercise being logged.
            thread_id: Forum thread for this workout.
            starter_message_id: Message that holds the workout menu.

        Returns:
            None.
        """
        super().__init__(timeout=900)
        self.menu = menu
        self.exercise = exercise
        self.thread_id = thread_id
        self.starter_message_id = starter_message_id
        self.weight: str | None = None
        self.reps: int | None = None
        self._load_set()

    def _load_set(self) -> None:
        """Read the set number and the previous performance.

        Returns:
            None.
        """
        exercise_id = self.exercise.exercise_id
        self.set_number = next_set_number(
            self.menu.database,
            self.menu.guild_id,
            self.menu.user_id,
            exercise_id,
            self.thread_id,
        )
        self.suggestion = suggestion_for_set(
            self.menu.database,
            self.menu.guild_id,
            self.menu.user_id,
            exercise_id,
            self.thread_id,
            self.set_number,
        )
        self.weight = None
        self.reps = None

    def render_embed(self) -> discord.Embed:
        """Build the ephemeral embed for the set being edited.

        Returns:
            The embed, including the exercise image when one was stored.
        """
        lines = [
            f"**{discord.utils.escape_markdown(self.exercise.name)}**",
            f"Set {self.set_number}",
        ]
        if self.suggestion is None:
            lines.append("No previous sets for this exercise.")
        else:
            lines.append(f"Last time: {self.suggestion.weight} kg × {self.suggestion.reps}")
        if self.weight is None or self.reps is None:
            lines.append("Set the weight and reps, then add the set.")
        else:
            lines.append(f"Ready: {self.weight} kg × {self.reps}")
        embed = discord.Embed(description="\n".join(lines), colour=discord.Colour.blurple())
        if self.exercise.image_url:
            embed.set_thumbnail(url=self.exercise.image_url)
        return embed

    @discord.ui.button(label="Set weight and reps", style=discord.ButtonStyle.primary)
    async def edit_values(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Open the modal that sets weight and reps.

        Args:
            interaction: Button press on the ephemeral editor.
            button: The button that was pressed.

        Returns:
            None.
        """
        del button
        weight = "" if self.suggestion is None else self.suggestion.weight
        reps = "" if self.suggestion is None else str(self.suggestion.reps)
        if self.weight is not None and self.reps is not None:
            weight = self.weight
            reps = str(self.reps)
        await interaction.response.send_modal(SetValueModal(self, weight, reps))

    @discord.ui.button(label="Add set", style=discord.ButtonStyle.success)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Append the chosen weight and reps to the workout list.

        Args:
            interaction: Button press on the ephemeral editor.
            button: The button that was pressed.

        Returns:
            None.
        """
        del button
        if self.weight is None or self.reps is None or interaction.channel is None:
            await interaction.response.send_message(
                "Set the weight and reps first.",
                ephemeral=True,
            )
            return
        add_set(
            self.menu.database,
            self.menu.guild_id,
            self.menu.user_id,
            self.exercise.exercise_id,
            self.thread_id,
            self.set_number,
            self.weight,
            self.reps,
        )
        try:
            await _refresh_log(self.menu, interaction.channel, self.thread_id)
        except discord.HTTPException:
            log.warning("Could not update the set list in thread %s", self.thread_id)
            await interaction.response.send_message(
                "The set was saved, but the list on the post could not be updated.",
                ephemeral=True,
            )
            return
        self.menu.persist(self.starter_message_id)
        self._load_set()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)


class SetValueModal(discord.ui.Modal):
    """Asks for the kilograms and repetitions of one set."""

    def __init__(self, editor: SetEntryView, weight: str, reps: str) -> None:
        """Prefill the fields with the proposed or current values.

        Args:
            editor: Ephemeral editor this modal updates.
            weight: Text placed in the weight field. Empty when there is no history.
            reps: Text placed in the reps field. Empty when there is no history.

        Returns:
            None.
        """
        super().__init__(title="Weight and reps")
        self.editor = editor
        self.weight_input = discord.ui.TextInput(
            label="Weight (kg)",
            max_length=10,
            required=True,
            **({} if not weight else {"default": weight}),
        )
        self.reps_input = discord.ui.TextInput(
            label="Reps",
            max_length=4,
            required=True,
            **({} if not reps else {"default": reps}),
        )
        self.add_item(self.weight_input)
        self.add_item(self.reps_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the parsed values on the ephemeral editor.

        Args:
            interaction: Modal submission.

        Returns:
            None.
        """
        weight = parse_weight(self.weight_input.value)
        reps = parse_reps(self.reps_input.value)
        if weight is None or reps is None:
            await interaction.response.send_message(
                "Enter a weight in kg and a positive number of reps.",
                ephemeral=True,
            )
            return
        self.editor.weight = weight
        self.editor.reps = reps
        await interaction.response.edit_message(embed=self.editor.render_embed(), view=self.editor)


def parse_weight(raw: str) -> str | None:
    """Parse a kilogram value typed with a dot or a comma.

    Args:
        raw: Text from the weight field.

    Returns:
        A normalized number such as ``72.5``, or None when it is not a weight
        from 0 through 2000.
    """
    text = raw.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    value = float(text)
    if value < 0 or value > 2000:
        return None
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def parse_reps(raw: str) -> int | None:
    """Parse a repetition count.

    Args:
        raw: Text from the reps field.

    Returns:
        The count, or None when it is not an integer from 1 through 999.
    """
    text = raw.strip()
    if not text.isdigit():
        return None
    value = int(text)
    if value < 1 or value > 999:
        return None
    return value


async def _refresh_log(
    menu: ExerciseMenuView,
    channel: discord.abc.Messageable,
    thread_id: int,
) -> None:
    """Rewrite the set list on the workout post, creating it when missing.

    Args:
        menu: Workout menu that remembers the list message.
        channel: Thread that contains the workout post.
        thread_id: Forum thread id stored on the sets.

    Returns:
        None.

    Raises:
        discord.HTTPException: Discord rejected the edit or the send.
    """
    embed = build_workout_log_embed(
        session_log(menu.database, menu.guild_id, menu.user_id, thread_id)
    )
    if menu.log_message_id is None:
        message = await channel.send(embed=embed)
        menu.log_message_id = message.id
        return
    message = await channel.fetch_message(menu.log_message_id)
    await message.edit(embed=embed)


def _chunks(items: list) -> list[list]:
    """Split a list into pages of at most 25 entries.

    Args:
        items: Values to page.

    Returns:
        Pages. Empty when ``items`` is empty.
    """
    return [items[index : index + PAGE_SIZE] for index in range(0, len(items), PAGE_SIZE)]


def _clamp_page(page: int, page_count: int) -> int:
    """Keep a page index inside the available pages.

    Args:
        page: Requested zero-based page.
        page_count: Number of pages. Zero when there is nothing to show.

    Returns:
        A usable page index, or 0 when there are no pages.
    """
    if page_count <= 0:
        return 0
    return min(max(page, 0), page_count - 1)
