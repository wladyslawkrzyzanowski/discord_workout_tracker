"""Ephemeral panel for a member's exercise categories.

The setup message keeps a gear button. Everything else in this module is
sent as an ephemeral message, so it is not stored for a restart.
"""

import logging

import discord

from tracker.db.catalog import (
    CatalogExercise,
    CatalogRejected,
    ExerciseCategory,
    create_category,
    create_exercise,
    delete_category,
    ensure_user_catalog,
    get_category,
    list_categories,
    list_exercises,
    list_exercises_outside,
    move_exercise,
    place_exercises,
    remove_exercises,
)
from tracker.db.database import Database

log = logging.getLogger(__name__)

PAGE_SIZE = 25
CATALOG_TIMEOUT_SECONDS = 900


def catalog_database(interaction: discord.Interaction) -> Database | None:
    """Read the database from the running bot.

    Args:
        interaction: Button press or select change.

    Returns:
        The bot database, or None when the client does not own one.
    """
    database = getattr(interaction.client, "database", None)
    if isinstance(database, Database):
        return database
    return None


class CatalogView(discord.ui.View):
    """Category menu for the member who opened the gear."""

    def __init__(
        self,
        database: Database,
        guild_id: int,
        user_id: int,
        category_id: str | None = None,
    ) -> None:
        """Build the category menu.

        Args:
            database: Open database.
            guild_id: Discord guild that owns the catalog.
            user_id: Member who opened the panel.
            category_id: Category to show, or None when none is selected yet.

        Returns:
            None.
        """
        super().__init__(timeout=CATALOG_TIMEOUT_SECONDS)
        self.database = database
        self.guild_id = guild_id
        self.user_id = user_id
        self.category_id = category_id
        self.page = 0
        self.rebuild()

    def rebuild(self) -> None:
        """Replace the components so they match the selected category.

        Returns:
            None.
        """
        self.clear_items()
        categories = list_categories(self.database, self.guild_id, self.user_id)
        pages = _pages(categories)
        self.page = min(self.page, max(len(pages) - 1, 0))
        visible = pages[self.page] if pages else []
        self.add_item(CategorySelect(visible, self.category_id))
        self.add_item(
            CatalogActionButton(
                label="Add category",
                style=discord.ButtonStyle.primary,
                action="add-category",
                row=1,
            )
        )
        selected = self.selected()
        if selected is not None and not selected.is_default:
            self.add_item(
                CatalogActionButton(
                    label="Delete category",
                    style=discord.ButtonStyle.danger,
                    action="delete-category",
                    row=1,
                )
            )
            self.add_item(
                CatalogActionButton(
                    label="New",
                    style=discord.ButtonStyle.primary,
                    action="new-exercise",
                    row=2,
                )
            )
            self.add_item(
                CatalogActionButton(
                    label="Existing",
                    style=discord.ButtonStyle.secondary,
                    action="existing-exercise",
                    row=2,
                )
            )
            self.add_item(
                CatalogActionButton(
                    label="Remove",
                    style=discord.ButtonStyle.danger,
                    action="remove-exercise",
                    row=2,
                )
            )
            self.add_item(
                CatalogActionButton(
                    label="Move",
                    style=discord.ButtonStyle.secondary,
                    action="move-exercise",
                    row=2,
                )
            )
        if len(pages) > 1:
            self.add_item(
                CatalogActionButton(
                    label="Previous",
                    style=discord.ButtonStyle.secondary,
                    action="previous-page",
                    row=3,
                    disabled=self.page == 0,
                )
            )
            self.add_item(
                CatalogActionButton(
                    label="Next",
                    style=discord.ButtonStyle.secondary,
                    action="next-page",
                    row=3,
                    disabled=self.page >= len(pages) - 1,
                )
            )

    def selected(self) -> ExerciseCategory | None:
        """Return the category currently shown.

        Returns:
            The selected category, or None when the id is missing.
        """
        if self.category_id is None:
            return None
        return get_category(self.database, self.guild_id, self.user_id, self.category_id)

    def render(self, notice: str | None = None) -> str:
        """Build the message text for this menu.

        Args:
            notice: Optional sentence placed above the menu, such as a deletion.

        Returns:
            The ephemeral message content.
        """
        lines: list[str] = []
        if notice:
            lines.append(notice)
        selected = self.selected()
        if selected is None:
            lines.append(
                "Choose a category. Default categories stay as they are. "
                "Add your own to organize exercises."
            )
        else:
            names = [
                discord.utils.escape_markdown(exercise.name)
                for exercise in list_exercises(
                    self.database,
                    self.guild_id,
                    self.user_id,
                    selected.category_id,
                )
            ]
            listing = ", ".join(names) if names else "none"
            title = discord.utils.escape_markdown(selected.name)
            if selected.is_default:
                lines.append(
                    f"**{title}** is a default category, so it cannot be edited.\n"
                    f"Exercises: {listing}"
                )
            else:
                lines.append(
                    f"**{title}**\nExercises: {listing}\n"
                    "New creates an exercise. Existing copies one you already have and keeps its id."
                )
        text = "\n\n".join(lines)
        if len(text) > 1900:
            return text[:1900] + "…"
        return text

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore a click from anyone other than the member who opened the menu.

        Args:
            interaction: Component click on this ephemeral message.

        Returns:
            True when the click belongs to the catalog owner.
        """
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            "This catalog belongs to another member.",
            ephemeral=True,
        )
        return False


class CategorySelect(discord.ui.Select):
    """Menu of the member's categories."""

    def __init__(self, categories: list[ExerciseCategory], selected_id: str | None) -> None:
        """Fill the menu with one page of categories.

        Args:
            categories: Categories shown on this page.
            selected_id: Category to mark as the current choice, or None.

        Returns:
            None.
        """
        options = [
            discord.SelectOption(
                label=category.name,
                value=category.category_id,
                description="Default" if category.is_default else "Custom",
                default=category.category_id == selected_id,
            )
            for category in categories
        ]
        if not options:
            options = [discord.SelectOption(label="No categories", value="missing")]
        super().__init__(
            placeholder="Choose a category",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the category that was chosen.

        Args:
            interaction: Select change on the catalog message.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, CatalogView):
            return
        chosen = self.values[0]
        if chosen == "missing":
            await interaction.response.edit_message(content=view.render(), view=view)
            return
        view.category_id = chosen
        view.rebuild()
        await interaction.response.edit_message(content=view.render(), view=view)


class CatalogActionButton(discord.ui.Button):
    """One action on the category menu."""

    def __init__(
        self,
        *,
        label: str,
        style: discord.ButtonStyle,
        action: str,
        row: int,
        disabled: bool = False,
    ) -> None:
        """Create a button that stores which catalog action it runs.

        Args:
            label: Text on the button.
            style: Discord button colour.
            action: ``add-category``, ``delete-category``, ``new-exercise``,
            ``existing-exercise``, ``remove-exercise``, ``move-exercise``,
            ``previous-page``, or ``next-page``.
            row: Action row on the message.
            disabled: True when the button should not be clickable.

        Returns:
            None.
        """
        super().__init__(label=label, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run this button's catalog action.

        Args:
            interaction: Button press on the catalog message.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, CatalogView):
            return
        if self.action == "add-category":
            await interaction.response.send_modal(CategoryNameModal(view))
            return
        if self.action == "new-exercise":
            await interaction.response.send_modal(ExerciseModal(view))
            return
        if self.action == "existing-exercise":
            picker = ExistingExerciseView(view)
            await interaction.response.edit_message(content=picker.render(), view=picker)
            return
        if self.action == "remove-exercise":
            picker = RemoveExerciseView(view)
            await interaction.response.edit_message(content=picker.render(), view=picker)
            return
        if self.action == "move-exercise":
            picker = MoveExerciseView(view)
            await interaction.response.edit_message(content=picker.render(), view=picker)
            return
        if self.action == "previous-page":
            view.page -= 1
            view.rebuild()
            await interaction.response.edit_message(content=view.render(), view=view)
            return
        if self.action == "next-page":
            view.page += 1
            view.rebuild()
            await interaction.response.edit_message(content=view.render(), view=view)
            return
        if self.action == "delete-category":
            await _start_delete(interaction, view)


class CategoryNameModal(discord.ui.Modal):
    """Asks for the name of a new custom category."""

    name = discord.ui.TextInput(
        label="Category name",
        max_length=100,
        required=True,
    )

    def __init__(self, catalog: CatalogView) -> None:
        """Remember the catalog message this modal edits.

        Args:
            catalog: Open category menu.

        Returns:
            None.
        """
        super().__init__(title="New category")
        self.catalog = catalog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the category and show it in the menu.

        Args:
            interaction: Modal submission.

        Returns:
            None.
        """
        try:
            category = create_category(
                self.catalog.database,
                self.catalog.guild_id,
                self.catalog.user_id,
                self.name.value,
            )
        except CatalogRejected as rejected:
            await interaction.response.send_message(_rejection_text(rejected), ephemeral=True)
            return
        log.info(
            "Created category %s for user %s in guild %s",
            category.category_id,
            self.catalog.user_id,
            self.catalog.guild_id,
        )
        self.catalog.category_id = category.category_id
        categories = list_categories(self.catalog.database, self.catalog.guild_id, self.catalog.user_id)
        index = next(
            (position for position, item in enumerate(categories) if item.category_id == category.category_id),
            0,
        )
        self.catalog.page = index // PAGE_SIZE
        self.catalog.rebuild()
        await interaction.response.edit_message(
            content=self.catalog.render(f"Added **{discord.utils.escape_markdown(category.name)}**."),
            view=self.catalog,
        )


class DeleteCategoryView(discord.ui.View):
    """Confirmation before a custom category with exercises is deleted."""

    def __init__(self, catalog: CatalogView, category: ExerciseCategory, exercise_count: int) -> None:
        """Store the category waiting for confirmation.

        Args:
            catalog: Menu to return to when the member cancels.
            category: Custom category that would be deleted.
            exercise_count: Placements currently in that category.

        Returns:
            None.
        """
        super().__init__(timeout=CATALOG_TIMEOUT_SECONDS)
        self.catalog = catalog
        self.category = category
        self.exercise_count = exercise_count

    def render(self) -> str:
        """Build the warning text.

        Returns:
            The ephemeral message content.
        """
        name = discord.utils.escape_markdown(self.category.name)
        noun = "exercise" if self.exercise_count == 1 else "exercises"
        return (
            f"Delete **{name}**? It still has {self.exercise_count} {noun}. "
            "They disappear from this category. An exercise with no category left "
            "leaves the menu. Logged sets stay saved."
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Delete the category and return to the menu.

        Args:
            interaction: Confirmation click.
            button: The Delete button.

        Returns:
            None.
        """
        del button
        await _finish_delete(interaction, self.catalog, self.category)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Return to the category menu without deleting.

        Args:
            interaction: Cancel click.
            button: The Cancel button.

        Returns:
            None.
        """
        del button
        self.catalog.rebuild()
        await interaction.response.edit_message(content=self.catalog.render(), view=self.catalog)


class ExerciseModal(discord.ui.Modal):
    """Asks for a new exercise name and an optional image address."""

    name = discord.ui.TextInput(
        label="Exercise name",
        max_length=100,
        required=True,
    )
    image = discord.ui.TextInput(
        label="Image URL",
        max_length=500,
        required=False,
        placeholder="https://example.com/bench.png",
    )

    def __init__(self, catalog: CatalogView) -> None:
        """Remember the category that receives the exercise.

        Args:
            catalog: Open category menu. A custom category must be selected.

        Returns:
            None.
        """
        super().__init__(title="New exercise")
        self.catalog = catalog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Store the exercise in the selected category.

        Args:
            interaction: Modal submission.

        Returns:
            None.
        """
        category = self.catalog.selected()
        if category is None:
            await interaction.response.send_message("Choose a category first.", ephemeral=True)
            return
        try:
            exercise = create_exercise(
                self.catalog.database,
                self.catalog.guild_id,
                self.catalog.user_id,
                category.category_id,
                self.name.value,
                self.image.value,
            )
        except CatalogRejected as rejected:
            await interaction.response.send_message(_exercise_rejection_text(rejected), ephemeral=True)
            return
        log.info(
            "Created exercise %s for user %s in guild %s",
            exercise.exercise_id,
            self.catalog.user_id,
            self.catalog.guild_id,
        )
        self.catalog.rebuild()
        await interaction.response.edit_message(
            content=self.catalog.render(
                f"Added **{discord.utils.escape_markdown(exercise.name)}** as `{exercise.exercise_id}`."
            ),
            view=self.catalog,
        )


class ExistingExerciseView(discord.ui.View):
    """Paginated menu of exercises that can be copied into a category."""

    def __init__(self, catalog: CatalogView) -> None:
        """Build the first page of exercises outside the selected category.

        Args:
            catalog: Category menu to return to.

        Returns:
            None.
        """
        super().__init__(timeout=CATALOG_TIMEOUT_SECONDS)
        self.catalog = catalog
        self.page = 0
        self.rebuild()

    def rebuild(self) -> None:
        """Replace the components for the current page.

        Returns:
            None.
        """
        self.clear_items()
        category = self.catalog.selected()
        exercises: list[CatalogExercise] = []
        if category is not None:
            exercises = list_exercises_outside(
                self.catalog.database,
                self.catalog.guild_id,
                self.catalog.user_id,
                category.category_id,
            )
        pages = _exercise_pages(exercises)
        self.page = min(self.page, max(len(pages) - 1, 0))
        visible = pages[self.page] if pages else []
        if visible:
            self.add_item(ExistingExerciseSelect(visible))
        self.add_item(ExistingActionButton(label="Back", action="back", row=1))
        if len(pages) > 1:
            self.add_item(
                ExistingActionButton(
                    label="Previous",
                    action="previous-page",
                    row=2,
                    disabled=self.page == 0,
                )
            )
            self.add_item(
                ExistingActionButton(
                    label="Next",
                    action="next-page",
                    row=2,
                    disabled=self.page >= len(pages) - 1,
                )
            )

    def render(self, notice: str | None = None) -> str:
        """Build the message text for the exercise picker.

        Args:
            notice: Optional sentence placed above the menu.

        Returns:
            The ephemeral message content.
        """
        category = self.catalog.selected()
        name = "this category" if category is None else discord.utils.escape_markdown(category.name)
        lines: list[str] = []
        if notice:
            lines.append(notice)
        outside = []
        if category is not None:
            outside = list_exercises_outside(
                self.catalog.database,
                self.catalog.guild_id,
                self.catalog.user_id,
                category.category_id,
            )
        if not outside:
            lines.append(f"Every exercise is already in **{name}**.")
        else:
            lines.append(
                f"Select exercises to copy into **{name}**. "
                "They keep the same id, so logged weight and reps stay shared."
            )
        return "\n\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore a click from anyone other than the catalog owner.

        Args:
            interaction: Component click on this ephemeral message.

        Returns:
            True when the click belongs to the catalog owner.
        """
        return await self.catalog.interaction_check(interaction)


class ExistingExerciseSelect(discord.ui.Select):
    """Multi-select of exercises that can be copied into the category."""

    def __init__(self, exercises: list[CatalogExercise]) -> None:
        """Fill one page of exercises.

        Args:
            exercises: Exercises shown on this page.

        Returns:
            None.
        """
        options = [
            discord.SelectOption(
                label=exercise.name,
                value=exercise.exercise_id,
                description="Default" if exercise.is_default else "Custom",
            )
            for exercise in exercises
        ]
        super().__init__(
            placeholder="Choose exercises",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Copy the chosen exercises into the open category.

        Args:
            interaction: Select change on the picker.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, ExistingExerciseView):
            return
        category = view.catalog.selected()
        if category is None:
            await interaction.response.send_message("Choose a category first.", ephemeral=True)
            return
        try:
            added = place_exercises(
                view.catalog.database,
                view.catalog.guild_id,
                view.catalog.user_id,
                category.category_id,
                list(self.values),
            )
        except CatalogRejected as rejected:
            await interaction.response.send_message(_exercise_rejection_text(rejected), ephemeral=True)
            return
        notice = _added_notice(added)
        view.rebuild()
        await interaction.response.edit_message(content=view.render(notice), view=view)


class ExistingActionButton(discord.ui.Button):
    """Back or page button on the existing-exercise picker."""

    def __init__(self, *, label: str, action: str, row: int, disabled: bool = False) -> None:
        """Create a picker button.

        Args:
            label: Text on the button.
            action: ``back``, ``previous-page``, or ``next-page``.
            row: Action row on the message.
            disabled: True when the button should not be clickable.

        Returns:
            None.
        """
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
            disabled=disabled,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        """Turn the page or return to the category menu.

        Args:
            interaction: Button press on the picker.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, ExistingExerciseView):
            return
        if self.action == "back":
            view.catalog.rebuild()
            await interaction.response.edit_message(content=view.catalog.render(), view=view.catalog)
            return
        if self.action == "previous-page":
            view.page -= 1
        elif self.action == "next-page":
            view.page += 1
        view.rebuild()
        await interaction.response.edit_message(content=view.render(), view=view)


class RemoveExerciseView(discord.ui.View):
    """Paginated menu of exercises that can be removed from a custom category."""

    def __init__(self, catalog: CatalogView) -> None:
        """Build the first page of exercises in the selected category.

        Args:
            catalog: Category menu to return to.

        Returns:
            None.
        """
        super().__init__(timeout=CATALOG_TIMEOUT_SECONDS)
        self.catalog = catalog
        self.page = 0
        self.rebuild()

    def rebuild(self) -> None:
        """Replace the components for the current page.

        Returns:
            None.
        """
        self.clear_items()
        exercises = _exercises_in_selected(self.catalog)
        pages = _exercise_pages(exercises)
        self.page = min(self.page, max(len(pages) - 1, 0))
        visible = pages[self.page] if pages else []
        if visible:
            self.add_item(RemoveExerciseSelect(visible))
        self.add_item(PlacementActionButton(owner=self, label="Back", action="back", row=1))
        if len(pages) > 1:
            self.add_item(
                PlacementActionButton(
                    owner=self,
                    label="Previous",
                    action="previous-page",
                    row=2,
                    disabled=self.page == 0,
                )
            )
            self.add_item(
                PlacementActionButton(
                    owner=self,
                    label="Next",
                    action="next-page",
                    row=2,
                    disabled=self.page >= len(pages) - 1,
                )
            )

    def render(self, notice: str | None = None) -> str:
        """Build the message text for the removal menu.

        Args:
            notice: Optional sentence placed above the menu.

        Returns:
            The ephemeral message content.
        """
        category = self.catalog.selected()
        name = "this category" if category is None else discord.utils.escape_markdown(category.name)
        lines: list[str] = []
        if notice:
            lines.append(notice)
        if not _exercises_in_selected(self.catalog):
            lines.append(f"**{name}** has no exercises.")
        else:
            lines.append(
                f"Select exercises to remove from **{name}**. "
                "An exercise with no category left leaves the menu. Logged sets stay saved."
            )
        return "\n\n".join(lines)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore a click from anyone other than the catalog owner.

        Args:
            interaction: Component click on this ephemeral message.

        Returns:
            True when the click belongs to the catalog owner.
        """
        return await self.catalog.interaction_check(interaction)


class RemoveExerciseSelect(discord.ui.Select):
    """Multi-select of exercises to remove from the open category."""

    def __init__(self, exercises: list[CatalogExercise]) -> None:
        """Fill one page of exercises.

        Args:
            exercises: Exercises shown on this page.

        Returns:
            None.
        """
        options = [
            discord.SelectOption(label=exercise.name, value=exercise.exercise_id)
            for exercise in exercises
        ]
        super().__init__(
            placeholder="Choose exercises",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Remove the chosen exercises from the open category.

        Args:
            interaction: Select change on the removal menu.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, RemoveExerciseView):
            return
        category = view.catalog.selected()
        if category is None:
            await interaction.response.send_message("Choose a category first.", ephemeral=True)
            return
        try:
            removed = remove_exercises(
                view.catalog.database,
                view.catalog.guild_id,
                view.catalog.user_id,
                category.category_id,
                list(self.values),
            )
        except CatalogRejected as rejected:
            await interaction.response.send_message(_exercise_rejection_text(rejected), ephemeral=True)
            return
        view.rebuild()
        await interaction.response.edit_message(content=view.render(_removed_notice(removed)), view=view)


class MoveExerciseView(discord.ui.View):
    """Picks an exercise, then a custom category to move it into."""

    def __init__(self, catalog: CatalogView) -> None:
        """Start on the exercise list for the selected category.

        Args:
            catalog: Category menu to return to.

        Returns:
            None.
        """
        super().__init__(timeout=CATALOG_TIMEOUT_SECONDS)
        self.catalog = catalog
        self.page = 0
        self.exercise_id: str | None = None
        self.rebuild()

    def rebuild(self) -> None:
        """Replace the components for the current step.

        Returns:
            None.
        """
        self.clear_items()
        if self.exercise_id is None:
            exercises = _exercises_in_selected(self.catalog)
            pages = _exercise_pages(exercises)
            self.page = min(self.page, max(len(pages) - 1, 0))
            visible = pages[self.page] if pages else []
            if visible:
                self.add_item(MoveExerciseSelect(visible))
        else:
            destinations = _move_destinations(self.catalog)
            pages = _pages(destinations)
            self.page = min(self.page, max(len(pages) - 1, 0))
            visible = pages[self.page] if pages else []
            if visible:
                self.add_item(MoveDestinationSelect(visible))
        self.add_item(PlacementActionButton(owner=self, label="Back", action="back", row=1))
        page_count = self._page_count()
        if page_count > 1:
            self.add_item(
                PlacementActionButton(
                    owner=self,
                    label="Previous",
                    action="previous-page",
                    row=2,
                    disabled=self.page == 0,
                )
            )
            self.add_item(
                PlacementActionButton(
                    owner=self,
                    label="Next",
                    action="next-page",
                    row=2,
                    disabled=self.page >= page_count - 1,
                )
            )

    def render(self) -> str:
        """Build the message text for the current move step.

        Returns:
            The ephemeral message content.
        """
        category = self.catalog.selected()
        source = "this category" if category is None else discord.utils.escape_markdown(category.name)
        if self.exercise_id is None:
            if not _exercises_in_selected(self.catalog):
                return f"**{source}** has no exercises to move."
            return f"Select an exercise to move out of **{source}**."
        exercise = _selected_move_exercise(self)
        exercise_name = "that exercise" if exercise is None else discord.utils.escape_markdown(exercise.name)
        if not _move_destinations(self.catalog):
            return f"Create another category before moving **{exercise_name}**."
        return f"Move **{exercise_name}** to another category. It keeps the same id."

    def _page_count(self) -> int:
        """Count pages for the step that is on screen.

        Returns:
            The number of pages. Zero when the step has nothing to list.
        """
        if self.exercise_id is None:
            return len(_exercise_pages(_exercises_in_selected(self.catalog)))
        return len(_pages(_move_destinations(self.catalog)))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Ignore a click from anyone other than the catalog owner.

        Args:
            interaction: Component click on this ephemeral message.

        Returns:
            True when the click belongs to the catalog owner.
        """
        return await self.catalog.interaction_check(interaction)


class MoveExerciseSelect(discord.ui.Select):
    """Single-select of an exercise to move."""

    def __init__(self, exercises: list[CatalogExercise]) -> None:
        """Fill one page of exercises.

        Args:
            exercises: Exercises shown on this page.

        Returns:
            None.
        """
        super().__init__(
            placeholder="Choose an exercise",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=exercise.name, value=exercise.exercise_id)
                for exercise in exercises
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Ask which custom category should receive the exercise.

        Args:
            interaction: Select change on the move menu.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, MoveExerciseView):
            return
        view.exercise_id = self.values[0]
        view.page = 0
        view.rebuild()
        await interaction.response.edit_message(content=view.render(), view=view)


class MoveDestinationSelect(discord.ui.Select):
    """Single-select of a custom category that can receive an exercise."""

    def __init__(self, categories: list[ExerciseCategory]) -> None:
        """Fill one page of destination categories.

        Args:
            categories: Custom categories other than the source.

        Returns:
            None.
        """
        super().__init__(
            placeholder="Choose a category",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=category.name, value=category.category_id)
                for category in categories
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Move the chosen exercise and return to the category menu.

        Args:
            interaction: Select change on the destination menu.

        Returns:
            None.
        """
        view = self.view
        if not isinstance(view, MoveExerciseView) or view.exercise_id is None:
            return
        source = view.catalog.selected()
        if source is None:
            await interaction.response.send_message("Choose a category first.", ephemeral=True)
            return
        destination_id = self.values[0]
        try:
            exercise = move_exercise(
                view.catalog.database,
                view.catalog.guild_id,
                view.catalog.user_id,
                view.exercise_id,
                source.category_id,
                destination_id,
            )
        except CatalogRejected as rejected:
            await interaction.response.send_message(_move_rejection_text(rejected), ephemeral=True)
            return
        destination = get_category(
            view.catalog.database,
            view.catalog.guild_id,
            view.catalog.user_id,
            destination_id,
        )
        destination_name = destination_id if destination is None else destination.name
        view.catalog.category_id = destination_id
        view.catalog.rebuild()
        notice = (
            f"Moved **{discord.utils.escape_markdown(exercise.name)}** "
            f"to **{discord.utils.escape_markdown(destination_name)}**."
        )
        await interaction.response.edit_message(content=view.catalog.render(notice), view=view.catalog)


class PlacementActionButton(discord.ui.Button):
    """Back or page button shared by the remove and move menus."""

    def __init__(
        self,
        *,
        owner: RemoveExerciseView | MoveExerciseView,
        label: str,
        action: str,
        row: int,
        disabled: bool = False,
    ) -> None:
        """Create a button for one placement menu.

        Args:
            owner: Menu that owns the button. Stored so Back can reach it
                after the view rebuilds.
            label: Text on the button.
            action: ``back``, ``previous-page``, or ``next-page``.
            row: Action row on the message.
            disabled: True when the button should not be clickable.

        Returns:
            None.
        """
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=row,
            disabled=disabled,
        )
        self.owner = owner
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        """Turn the page or step back.

        Args:
            interaction: Button press on the remove or move menu.

        Returns:
            None.
        """
        owner = self.owner
        if self.action == "back":
            if isinstance(owner, MoveExerciseView) and owner.exercise_id is not None:
                owner.exercise_id = None
                owner.page = 0
                owner.rebuild()
                await interaction.response.edit_message(content=owner.render(), view=owner)
                return
            owner.catalog.rebuild()
            await interaction.response.edit_message(content=owner.catalog.render(), view=owner.catalog)
            return
        if self.action == "previous-page":
            owner.page -= 1
        elif self.action == "next-page":
            owner.page += 1
        owner.rebuild()
        await interaction.response.edit_message(content=owner.render(), view=owner)


async def send_catalog(interaction: discord.Interaction) -> None:
    """Open the category menu for the member who clicked the gear.

    Args:
        interaction: Gear press on the setup panel.

    Returns:
        None.
    """
    guild = interaction.guild
    member = interaction.user
    database = catalog_database(interaction)
    if guild is None or database is None or not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "This panel only works in a server.",
            ephemeral=True,
        )
        return
    ensure_user_catalog(database, guild.id, member.id)
    view = CatalogView(database, guild.id, member.id)
    await interaction.response.send_message(view.render(), view=view, ephemeral=True)


async def _start_delete(interaction: discord.Interaction, catalog: CatalogView) -> None:
    """Delete an empty custom category, or ask when it still has exercises.

    Args:
        interaction: Delete button press.
        catalog: Open category menu.

    Returns:
        None.
    """
    category = catalog.selected()
    if category is None or category.is_default:
        await interaction.response.send_message(
            "Default categories cannot be edited.",
            ephemeral=True,
        )
        return
    exercises = list_exercises(catalog.database, catalog.guild_id, catalog.user_id, category.category_id)
    if exercises:
        warning = DeleteCategoryView(catalog, category, len(exercises))
        await interaction.response.edit_message(content=warning.render(), view=warning)
        return
    await _finish_delete(interaction, catalog, category)


async def _finish_delete(
    interaction: discord.Interaction,
    catalog: CatalogView,
    category: ExerciseCategory,
) -> None:
    """Delete a custom category and show the menu again.

    Args:
        interaction: Click that confirmed the deletion.
        catalog: Menu to show afterwards.
        category: Custom category to delete.

    Returns:
        None.
    """
    name = category.name
    try:
        delete_category(catalog.database, catalog.guild_id, catalog.user_id, category.category_id)
    except CatalogRejected as rejected:
        await interaction.response.send_message(_rejection_text(rejected), ephemeral=True)
        return
    log.info(
        "Deleted category %s for user %s in guild %s",
        category.category_id,
        catalog.user_id,
        catalog.guild_id,
    )
    catalog.category_id = None
    catalog.rebuild()
    await interaction.response.edit_message(
        content=catalog.render(f"Deleted **{discord.utils.escape_markdown(name)}**."),
        view=catalog,
    )


def _exercises_in_selected(catalog: CatalogView) -> list[CatalogExercise]:
    """List exercises in the category open on a catalog menu.

    Args:
        catalog: Category menu.

    Returns:
        Exercises in that category, or an empty list when none is selected.
    """
    category = catalog.selected()
    if category is None:
        return []
    return list_exercises(catalog.database, catalog.guild_id, catalog.user_id, category.category_id)


def _move_destinations(catalog: CatalogView) -> list[ExerciseCategory]:
    """List custom categories that can receive a moved exercise.

    Args:
        catalog: Category menu. Its selected category is excluded.

    Returns:
        Custom categories other than the source, in display order.
    """
    return [
        category
        for category in list_categories(catalog.database, catalog.guild_id, catalog.user_id)
        if not category.is_default and category.category_id != catalog.category_id
    ]


def _selected_move_exercise(view: MoveExerciseView) -> CatalogExercise | None:
    """Load the exercise chosen on the move menu.

    Args:
        view: Move menu that may already have an exercise id.

    Returns:
        The exercise, or None when it is no longer in the catalog.
    """
    if view.exercise_id is None:
        return None
    for exercise in _exercises_in_selected(view.catalog):
        if exercise.exercise_id == view.exercise_id:
            return exercise
    return None


def _removed_notice(names: list[str]) -> str:
    """Describe exercises removed from a category.

    Args:
        names: Exercise names that were removed.

    Returns:
        A sentence for the ephemeral message.
    """
    if not names:
        return "Those exercises are not in this category."
    escaped = [discord.utils.escape_markdown(name) for name in names]
    if len(escaped) == 1:
        return f"Removed **{escaped[0]}**."
    return f"Removed {', '.join(f'**{name}**' for name in escaped)}."


def _move_rejection_text(rejected: CatalogRejected) -> str:
    """Turn a move rejection into a sentence.

    Args:
        rejected: Error raised by the catalog storage.

    Returns:
        Text safe to show to the member.
    """
    if rejected.reason == "already-placed":
        return "That exercise is already in the destination category."
    if rejected.reason == "default-locked":
        return "Default categories cannot be edited."
    return "That exercise or category no longer exists."


def _exercise_pages(exercises: list[CatalogExercise]) -> list[list[CatalogExercise]]:
    """Split exercises into menus of at most 25 options.

    Args:
        exercises: Full exercise list.

    Returns:
        Pages. Empty when there is nothing to copy.
    """
    return [exercises[index : index + PAGE_SIZE] for index in range(0, len(exercises), PAGE_SIZE)]


def _added_notice(names: list[str]) -> str:
    """Describe exercises that were copied into a category.

    Args:
        names: Exercise names that were added.

    Returns:
        A sentence for the ephemeral message.
    """
    if not names:
        return "Those exercises are already in this category."
    escaped = [discord.utils.escape_markdown(name) for name in names]
    if len(escaped) == 1:
        return f"Added **{escaped[0]}**."
    return f"Added {', '.join(f'**{name}**' for name in escaped)}."


def _exercise_rejection_text(rejected: CatalogRejected) -> str:
    """Turn an exercise-catalog rejection into a sentence.

    Args:
        rejected: Error raised by the catalog storage.

    Returns:
        Text safe to show to the member.
    """
    if rejected.reason == "duplicate-name":
        return "You already have an exercise with that name."
    if rejected.reason == "bad-image":
        return "Enter an http or https image address, or leave it empty."
    if rejected.reason == "default-locked":
        return "Default categories cannot be edited."
    if rejected.reason == "missing":
        return "That category no longer exists."
    return "Enter an exercise name."


def _pages(categories: list[ExerciseCategory]) -> list[list[ExerciseCategory]]:
    """Split categories into menus of at most 25 options.

    Args:
        categories: Full category list.

    Returns:
        Pages. Empty when the member has no categories.
    """
    return [categories[index : index + PAGE_SIZE] for index in range(0, len(categories), PAGE_SIZE)]


def _rejection_text(rejected: CatalogRejected) -> str:
    """Turn a catalog rejection into a sentence.

    Args:
        rejected: Error raised by the catalog storage.

    Returns:
        Text safe to show to the member.
    """
    if rejected.reason == "duplicate-name":
        return "You already have a category with that name."
    if rejected.reason == "default-locked":
        return "Default categories cannot be edited."
    if rejected.reason == "missing":
        return "That category no longer exists."
    return "Enter a category name."
