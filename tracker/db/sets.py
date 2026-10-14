"""Sets logged inside a workout thread."""

from dataclasses import dataclass
from datetime import datetime, timezone

from tracker.db.database import Database


@dataclass(frozen=True)
class SetSuggestion:
    """Weight and reps proposed for the next set.

    Attributes:
        weight: Kilograms, without a unit suffix.
        reps: Repetitions.
    """

    weight: str
    reps: int


def next_set_number(
    database: Database,
    guild_id: int,
    user_id: int,
    exercise_id: str,
    thread_id: int,
) -> int:
    """Return the set number about to be logged for an exercise.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the workout.
        user_id: Member who owns the workout.
        exercise_id: Exercise being performed.
        thread_id: Forum thread for this workout.

    Returns:
        One past the highest set number already stored in this thread.
    """
    row = database.connection.execute(
        """
        SELECT COALESCE(MAX(set_number), 0) + 1 AS next_number
        FROM exercise_sets
        WHERE guild_id = ? AND user_id = ? AND exercise_id = ? AND thread_id = ?
        """,
        (guild_id, user_id, exercise_id, thread_id),
    ).fetchone()
    return int(row["next_number"])


def suggestion_for_set(
    database: Database,
    guild_id: int,
    user_id: int,
    exercise_id: str,
    thread_id: int,
    set_number: int,
) -> SetSuggestion | None:
    """Find the weight and reps to propose for one set number.

    The proposal comes from the member's previous thread that used this
    exercise. The same set number is preferred. When that thread had fewer
    sets, the last set in it is used.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the workout.
        user_id: Member who owns the workout.
        exercise_id: Exercise being performed.
        thread_id: Forum thread for the workout being logged. It is excluded.
        set_number: Set about to be logged, starting at 1.

    Returns:
        The proposed weight and reps, or None when this exercise has no history.
    """
    previous = database.connection.execute(
        """
        SELECT thread_id
        FROM exercise_sets
        WHERE guild_id = ? AND user_id = ? AND exercise_id = ? AND thread_id != ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (guild_id, user_id, exercise_id, thread_id),
    ).fetchone()
    if previous is None:
        return None
    previous_thread = previous["thread_id"]
    exact = database.connection.execute(
        """
        SELECT weight, reps
        FROM exercise_sets
        WHERE guild_id = ? AND user_id = ? AND exercise_id = ? AND thread_id = ? AND set_number = ?
        """,
        (guild_id, user_id, exercise_id, previous_thread, set_number),
    ).fetchone()
    if exact is not None:
        return SetSuggestion(weight=exact["weight"], reps=exact["reps"])
    fallback = database.connection.execute(
        """
        SELECT weight, reps
        FROM exercise_sets
        WHERE guild_id = ? AND user_id = ? AND exercise_id = ? AND thread_id = ?
        ORDER BY set_number DESC
        LIMIT 1
        """,
        (guild_id, user_id, exercise_id, previous_thread),
    ).fetchone()
    if fallback is None:
        return None
    return SetSuggestion(weight=fallback["weight"], reps=fallback["reps"])


def add_set(
    database: Database,
    guild_id: int,
    user_id: int,
    exercise_id: str,
    thread_id: int,
    set_number: int,
    weight: str,
    reps: int,
) -> None:
    """Store one completed set.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the workout.
        user_id: Member who performed the set.
        exercise_id: Exercise that was performed.
        thread_id: Forum thread for this workout.
        set_number: Position of this set for that exercise in the thread.
        weight: Kilograms, without a unit suffix.
        reps: Repetitions in the set.

    Returns:
        None.
    """
    database.connection.execute(
        """
        INSERT INTO exercise_sets (
            guild_id, user_id, exercise_id, thread_id, set_number, weight, reps, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            user_id,
            exercise_id,
            thread_id,
            set_number,
            weight,
            reps,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    database.connection.commit()


def session_log(
    database: Database,
    guild_id: int,
    user_id: int,
    thread_id: int,
) -> list[tuple[str, list[str]]]:
    """Group the sets in one workout by exercise.

    Args:
        database: Open database.
        guild_id: Discord guild that owns the workout.
        user_id: Member who owns the workout.
        thread_id: Forum thread for this workout.

    Returns:
        Pairs of exercise name and lines such as ``1. 60 kg × 8``. The groups
        follow the order the exercises were first logged.
    """
    rows = database.connection.execute(
        """
        SELECT exercise_sets.exercise_id, exercise_sets.set_number,
               exercise_sets.weight, exercise_sets.reps, exercises.name
        FROM exercise_sets
        LEFT JOIN exercises
          ON exercises.guild_id = exercise_sets.guild_id
         AND exercises.user_id = exercise_sets.user_id
         AND exercises.exercise_id = exercise_sets.exercise_id
        WHERE exercise_sets.guild_id = ?
          AND exercise_sets.user_id = ?
          AND exercise_sets.thread_id = ?
        ORDER BY exercise_sets.set_id
        """,
        (guild_id, user_id, thread_id),
    )
    grouped: dict[str, tuple[str, list[str]]] = {}
    order: list[str] = []
    for row in rows:
        exercise_id = row["exercise_id"]
        if exercise_id not in grouped:
            name = row["name"] if row["name"] else exercise_id
            grouped[exercise_id] = (name, [])
            order.append(exercise_id)
        grouped[exercise_id][1].append(
            f"{row['set_number']}. {row['weight']} kg × {row['reps']}"
        )
    return [(grouped[exercise_id][0], grouped[exercise_id][1]) for exercise_id in order]
