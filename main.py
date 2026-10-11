"""Process entry point for the workout tracker bot."""

import logging

from tracker.config import load_config, read_token
from tracker.db.database import Database
from tracker.log_setup import setup_logging


def main() -> None:
    """Load configuration, start logging, and connect the bot.

    Returns:
        None. The call blocks until the bot disconnects.
    """
    config = load_config()
    setup_logging(config.log_directory, config.log_max_megabytes)
    log = logging.getLogger(__name__)

    # Imported after logging is configured so library warnings use that setup.
    from tracker.bot import WorkoutBot

    database = Database(config.database_path)
    database.initialize()
    token = read_token()
    bot = WorkoutBot(database)
    log.info("Starting bot")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
