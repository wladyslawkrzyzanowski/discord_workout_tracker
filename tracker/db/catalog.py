"""Per-member exercise categories and the exercises placed in them.

Default categories are copied once for each member and then left unchanged.
A custom category can hold a new exercise or another placement of an existing
one. Placements share ``exercise_id``, so logged sets stay on that id.
"""

from dataclasses import dataclass

from tracker.db.database import Database

# Stable ids. The same default exercise keeps one id for every member.
DEFAULT_CATEGORIES: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "default:upper",
        "Upper",
        (
            ("default:bench-press", "Bench press"),
            ("default:overhead-press", "Overhead press"),
            ("default:bent-over-row", "Bent-over row"),
            ("default:pull-up", "Pull-up"),
            ("default:lateral-raise", "Lateral raise"),
        ),
    ),
    (
        "default:lower",
        "Lower",
        (
            ("default:squat", "Squat"),
            ("default:romanian-deadlift", "Romanian deadlift"),
            ("default:leg-press", "Leg press"),
            ("default:lunge", "Lunge"),
            ("default:calf-raise", "Calf raise"),
        ),
    ),
    (
        "default:core",
        "Core",
        (
            ("default:crunch", "Crunch"),
            ("default:hanging-leg-raise", "Hanging leg raise"),
            ("default:cable-crunch", "Cable crunch"),
        ),
    ),
)


class CatalogRejected(Exception):
    """A catalog change was rejected.

    Attributes:
        reason: Short machine code the UI turns into a sentence.
    """

    def __init__(self, reason: str) -> None:
        """Store the rejection code.

        Args:
            reason: ``empty-name``, ``duplicate-name``, ``default-locked``, or ``missing``.

        Returns:
            None.
        """
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExerciseCategory:
    """One category in a member's catalog.

    Attributes:
        category_id: Stable id. Defaults start with ``default:``.
        name: Name shown in menus.
        is_default: True when this category was seeded and cannot be edited.
        position: Sort order, lowest first.
    """

    category_id: str
    name: str
    is_default: bool
    position: int


@dataclass(frozen=True)
class CatalogExercise:
    """One exercise that can be placed in a category.

    Attributes:
        exercise_id: Id stored on logged sets. Custom ids look like ``123-1``.
        name: Name shown in menus.
        image_url: Optional image address, or None.
        is_default: True when this exercise was seeded.
    """

    exercise_id: str
    name: str
    image_url: str | None
    is_default: bool


def ensure_user_catalog(database: Database, guild_id: int, user_id: int) -> None:
    """Insert any missing default categories and exercises for one member.

    Existing rows are left as they are, including custom categories and extra
    placements of a default exercise.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose catalog is being prepared.

    Returns:
        None.
    """
    for position, (category_id, name, exercises) in enumerate(DEFAULT_CATEGORIES):
        database.connection.execute(
            """
            INSERT OR IGNORE INTO exercise_categories (
                guild_id, user_id, category_id, name, is_default, position
            ) VALUES (?, ?, ?, ?, 1, ?)
            """,
            (guild_id, user_id, category_id, name, position),
        )
        for exercise_position, (exercise_id, exercise_name) in enumerate(exercises):
            database.connection.execute(
                """
                INSERT OR IGNORE INTO exercises (
                    guild_id, user_id, exercise_id, name, image_url, is_default
                ) VALUES (?, ?, ?, ?, NULL, 1)
                """,
                (guild_id, user_id, exercise_id, exercise_name),
            )
            database.connection.execute(
                """
                INSERT OR IGNORE INTO exercise_placements (
                    guild_id, user_id, category_id, exercise_id, position
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, category_id, exercise_id, exercise_position),
            )
    database.connection.commit()


def list_categories(database: Database, guild_id: int, user_id: int) -> list[ExerciseCategory]:
    """List a member's categories in display order.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose categories are listed.

    Returns:
        Categories ordered by position, then name.
    """
    rows = database.connection.execute(
        """
        SELECT category_id, name, is_default, position
        FROM exercise_categories
        WHERE guild_id = ? AND user_id = ?
        ORDER BY position, name
        """,
        (guild_id, user_id),
    )
    return [_category_from_row(row) for row in rows]


def get_category(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
) -> ExerciseCategory | None:
    """Load one category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the category.
        category_id: Category to load.

    Returns:
        The category, or None when this member does not have it.
    """
    row = database.connection.execute(
        """
        SELECT category_id, name, is_default, position
        FROM exercise_categories
        WHERE guild_id = ? AND user_id = ? AND category_id = ?
        """,
        (guild_id, user_id, category_id),
    ).fetchone()
    if row is None:
        return None
    return _category_from_row(row)


def list_exercises(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
) -> list[CatalogExercise]:
    """List exercises placed in one category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the category.
        category_id: Category whose exercises are listed.

    Returns:
        Exercises in placement order.
    """
    rows = database.connection.execute(
        """
        SELECT exercises.exercise_id, exercises.name, exercises.image_url, exercises.is_default
        FROM exercise_placements
        JOIN exercises
          ON exercises.guild_id = exercise_placements.guild_id
         AND exercises.user_id = exercise_placements.user_id
         AND exercises.exercise_id = exercise_placements.exercise_id
        WHERE exercise_placements.guild_id = ?
          AND exercise_placements.user_id = ?
          AND exercise_placements.category_id = ?
        ORDER BY exercise_placements.position, exercises.name
        """,
        (guild_id, user_id, category_id),
    )
    return [_exercise_from_row(row) for row in rows]


def create_category(database: Database, guild_id: int, user_id: int, name: str) -> ExerciseCategory:
    """Add a custom category for one member.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who is adding the category.
        name: Requested name. Leading and trailing space is removed.

    Returns:
        The stored category.

    Raises:
        CatalogRejected: The name is empty or this member already uses it.
    """
    cleaned = _clean_name(name)
    _reject_duplicate_category(database, guild_id, user_id, cleaned)
    position = _next_position(database, guild_id, user_id)
    category_id = f"{user_id}-c-{_next_category_number(database, guild_id, user_id)}"
    database.connection.execute(
        """
        INSERT INTO exercise_categories (
            guild_id, user_id, category_id, name, is_default, position
        ) VALUES (?, ?, ?, ?, 0, ?)
        """,
        (guild_id, user_id, category_id, cleaned, position),
    )
    database.connection.commit()
    return ExerciseCategory(
        category_id=category_id,
        name=cleaned,
        is_default=False,
        position=position,
    )


def list_exercises_outside(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
) -> list[CatalogExercise]:
    """List this member's exercises that are not in one category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose exercises are listed.
        category_id: Category to exclude.

    Returns:
        Exercises ordered by name. Choosing one copies it into the category
        without creating a new id.
    """
    rows = database.connection.execute(
        """
        SELECT exercise_id, name, image_url, is_default
        FROM exercises
        WHERE guild_id = ? AND user_id = ?
          AND exercise_id NOT IN (
              SELECT exercise_id
              FROM exercise_placements
              WHERE guild_id = ? AND user_id = ? AND category_id = ?
          )
        ORDER BY name
        """,
        (guild_id, user_id, guild_id, user_id, category_id),
    )
    return [_exercise_from_row(row) for row in rows]


def create_exercise(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
    name: str,
    image_url: str | None,
) -> CatalogExercise:
    """Create an exercise and place it in a custom category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who is adding the exercise.
        category_id: Custom category that receives the exercise.
        name: Requested name. Leading and trailing space is removed.
        image_url: Optional http or https address, or None.

    Returns:
        The stored exercise. Its id looks like ``123-1``.

    Raises:
        CatalogRejected: The category cannot take exercises, the name is
        empty or already used, or the image address is not http(s).
    """
    _require_custom_category(database, guild_id, user_id, category_id)
    cleaned = _clean_name(name)
    _reject_duplicate_exercise(database, guild_id, user_id, cleaned)
    stored_image = _clean_image_url(image_url)
    exercise_id = f"{user_id}-{_next_exercise_number(database, guild_id, user_id)}"
    position = _next_placement_position(database, guild_id, user_id, category_id)
    database.connection.execute(
        """
        INSERT INTO exercises (
            guild_id, user_id, exercise_id, name, image_url, is_default
        ) VALUES (?, ?, ?, ?, ?, 0)
        """,
        (guild_id, user_id, exercise_id, cleaned, stored_image),
    )
    database.connection.execute(
        """
        INSERT INTO exercise_placements (
            guild_id, user_id, category_id, exercise_id, position
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (guild_id, user_id, category_id, exercise_id, position),
    )
    database.connection.commit()
    return CatalogExercise(
        exercise_id=exercise_id,
        name=cleaned,
        image_url=stored_image,
        is_default=False,
    )


def place_exercises(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
    exercise_ids: list[str],
) -> list[str]:
    """Copy existing exercises into a custom category, keeping their ids.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the exercises.
        category_id: Custom category that receives the placements.
        exercise_ids: Exercises to copy. Unknown ids and exercises already
            in the category are skipped.

    Returns:
        Names of the exercises that were added, in the request order.

    Raises:
        CatalogRejected: The category is missing or it is a default category.
    """
    _require_custom_category(database, guild_id, user_id, category_id)
    added: list[str] = []
    for exercise_id in exercise_ids:
        exercise = _get_exercise(database, guild_id, user_id, exercise_id)
        if exercise is None or _placement_exists(database, guild_id, user_id, category_id, exercise_id):
            continue
        position = _next_placement_position(database, guild_id, user_id, category_id)
        database.connection.execute(
            """
            INSERT INTO exercise_placements (
                guild_id, user_id, category_id, exercise_id, position
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (guild_id, user_id, category_id, exercise_id, position),
        )
        added.append(exercise.name)
    database.connection.commit()
    return added


def delete_category(database: Database, guild_id: int, user_id: int, category_id: str) -> None:
    """Delete a custom category and placements that exist only there.

    A custom exercise with no category left is removed from the menu. Its
    logged sets are not touched. Default exercises stay, because they remain
    in their default category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the category.
        category_id: Custom category to delete.

    Returns:
        None.

    Raises:
        CatalogRejected: The category is missing or it is a default category.
    """
    category = get_category(database, guild_id, user_id, category_id)
    if category is None:
        raise CatalogRejected("missing")
    if category.is_default:
        raise CatalogRejected("default-locked")
    placed = list_exercises(database, guild_id, user_id, category_id)
    database.connection.execute(
        """
        DELETE FROM exercise_categories
        WHERE guild_id = ? AND user_id = ? AND category_id = ?
        """,
        (guild_id, user_id, category_id),
    )
    for exercise in placed:
        if exercise.is_default:
            continue
        remaining = database.connection.execute(
            """
            SELECT 1
            FROM exercise_placements
            WHERE guild_id = ? AND user_id = ? AND exercise_id = ?
            LIMIT 1
            """,
            (guild_id, user_id, exercise.exercise_id),
        ).fetchone()
        if remaining is None:
            database.connection.execute(
                """
                DELETE FROM exercises
                WHERE guild_id = ? AND user_id = ? AND exercise_id = ?
                """,
                (guild_id, user_id, exercise.exercise_id),
            )
    database.connection.commit()


def _category_from_row(row: object) -> ExerciseCategory:
    """Map a category query row to a category.

    Args:
        row: sqlite3 row with category columns.

    Returns:
        The category.
    """
    return ExerciseCategory(
        category_id=row["category_id"],
        name=row["name"],
        is_default=bool(row["is_default"]),
        position=row["position"],
    )


def _exercise_from_row(row: object) -> CatalogExercise:
    """Map an exercise query row to an exercise.

    Args:
        row: sqlite3 row with exercise columns.

    Returns:
        The exercise.
    """
    return CatalogExercise(
        exercise_id=row["exercise_id"],
        name=row["name"],
        image_url=row["image_url"],
        is_default=bool(row["is_default"]),
    )


def _clean_name(name: str) -> str:
    """Normalize a catalog name.

    Args:
        name: Raw name from a modal.

    Returns:
        The stripped name.

    Raises:
        CatalogRejected: The name is empty or longer than 100 characters.
    """
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 100:
        raise CatalogRejected("empty-name")
    return cleaned


def _reject_duplicate_category(
    database: Database,
    guild_id: int,
    user_id: int,
    name: str,
) -> None:
    """Reject a category name this member already has.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose names are checked.
        name: Cleaned name.

    Returns:
        None.

    Raises:
        CatalogRejected: A category with this name already exists, ignoring case.
    """
    existing = list_categories(database, guild_id, user_id)
    folded = name.casefold()
    if any(category.name.casefold() == folded for category in existing):
        raise CatalogRejected("duplicate-name")


def _next_position(database: Database, guild_id: int, user_id: int) -> int:
    """Return the next category sort position.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose categories are counted.

    Returns:
        One past the current highest position, or 0 when the member has none.
    """
    row = database.connection.execute(
        """
        SELECT COALESCE(MAX(position), -1) + 1 AS next_position
        FROM exercise_categories
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    ).fetchone()
    return int(row["next_position"])


def _require_custom_category(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
) -> ExerciseCategory:
    """Load a category and reject a missing or default one.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the category.
        category_id: Category that must accept edits.

    Returns:
        The custom category.

    Raises:
        CatalogRejected: The category is missing or it is a default category.
    """
    category = get_category(database, guild_id, user_id, category_id)
    if category is None:
        raise CatalogRejected("missing")
    if category.is_default:
        raise CatalogRejected("default-locked")
    return category


def _get_exercise(
    database: Database,
    guild_id: int,
    user_id: int,
    exercise_id: str,
) -> CatalogExercise | None:
    """Load one exercise.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the exercise.
        exercise_id: Exercise to load.

    Returns:
        The exercise, or None when this member does not have it.
    """
    row = database.connection.execute(
        """
        SELECT exercise_id, name, image_url, is_default
        FROM exercises
        WHERE guild_id = ? AND user_id = ? AND exercise_id = ?
        """,
        (guild_id, user_id, exercise_id),
    ).fetchone()
    if row is None:
        return None
    return _exercise_from_row(row)


def _placement_exists(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
    exercise_id: str,
) -> bool:
    """Report whether an exercise is already in a category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the placement.
        category_id: Category to check.
        exercise_id: Exercise to check.

    Returns:
        True when that placement exists.
    """
    row = database.connection.execute(
        """
        SELECT 1
        FROM exercise_placements
        WHERE guild_id = ? AND user_id = ? AND category_id = ? AND exercise_id = ?
        LIMIT 1
        """,
        (guild_id, user_id, category_id, exercise_id),
    ).fetchone()
    return row is not None


def _reject_duplicate_exercise(
    database: Database,
    guild_id: int,
    user_id: int,
    name: str,
) -> None:
    """Reject an exercise name this member already has.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose names are checked.
        name: Cleaned name.

    Returns:
        None.

    Raises:
        CatalogRejected: An exercise with this name already exists, ignoring case.
    """
    rows = database.connection.execute(
        """
        SELECT name
        FROM exercises
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    folded = name.casefold()
    if any(row["name"].casefold() == folded for row in rows):
        raise CatalogRejected("duplicate-name")


def _clean_image_url(image_url: str | None) -> str | None:
    """Normalize an optional exercise image address.

    Args:
        image_url: Raw address from a modal, or None.

    Returns:
        The stripped address, or None when it was empty.

    Raises:
        CatalogRejected: The address is not an http or https URL.
    """
    if image_url is None:
        return None
    cleaned = image_url.strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if " " in cleaned or len(cleaned) > 500 or not (lowered.startswith("https://") or lowered.startswith("http://")):
        raise CatalogRejected("bad-image")
    return cleaned


def _next_placement_position(
    database: Database,
    guild_id: int,
    user_id: int,
    category_id: str,
) -> int:
    """Return the next placement sort position in a category.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member who owns the category.
        category_id: Category that receives a placement.

    Returns:
        One past the current highest position, or 0 when the category is empty.
    """
    row = database.connection.execute(
        """
        SELECT COALESCE(MAX(position), -1) + 1 AS next_position
        FROM exercise_placements
        WHERE guild_id = ? AND user_id = ? AND category_id = ?
        """,
        (guild_id, user_id, category_id),
    ).fetchone()
    return int(row["next_position"])


def _next_exercise_number(database: Database, guild_id: int, user_id: int) -> int:
    """Return the next custom exercise number for this member.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose exercise ids are scanned.

    Returns:
        The next integer used in ids such as ``123-4``.
    """
    prefix = f"{user_id}-"
    rows = database.connection.execute(
        """
        SELECT exercise_id
        FROM exercises
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    highest = 0
    for row in rows:
        exercise_id = row["exercise_id"]
        if not exercise_id.startswith(prefix):
            continue
        tail = exercise_id[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


def _next_category_number(database: Database, guild_id: int, user_id: int) -> int:
    """Return the next custom category number for this member.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the catalog.
        user_id: Member whose category ids are scanned.

    Returns:
        The next integer used in ids such as ``123-c-4``.
    """
    prefix = f"{user_id}-c-"
    rows = database.connection.execute(
        """
        SELECT category_id
        FROM exercise_categories
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    highest = 0
    for row in rows:
        category_id = row["category_id"]
        if not category_id.startswith(prefix):
            continue
        tail = category_id[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1
