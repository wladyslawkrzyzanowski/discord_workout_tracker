"""Load runtime settings and the bot token."""

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.txt"
TOKEN_PATH = ROOT / "token.txt"


@dataclass(frozen=True)
class Config:
    """Settings read from config.txt.

    Attributes:
        log_directory: Absolute path of the folder that stores log files.
        log_max_megabytes: Maximum size, in MB, of one log file and of the whole log folder.
        database_path: Absolute path of the SQLite database file.
    """

    log_directory: Path
    log_max_megabytes: int
    database_path: Path


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read runtime settings from the config file.

    Args:
        path: Path to the INI config file. Defaults to config.txt in the project root.

    Returns:
        The parsed settings. Relative paths are resolved from the project root.

    Raises:
        FileNotFoundError: The config file does not exist.
        ValueError: A required setting is missing or not a positive integer.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    parser = ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    directory = _require_text(parser, path, "logging", "directory")
    max_megabytes = _require_positive_int(parser, path, "logging", "max_megabytes")
    database_file = _require_text(parser, path, "database", "file")

    return Config(
        log_directory=_resolve(directory),
        log_max_megabytes=max_megabytes,
        database_path=_resolve(database_file),
    )


def _resolve(value: str) -> Path:
    """Resolve a config path from the project root when it is relative.

    Args:
        value: Path text from the config file.

    Returns:
        The absolute path.
    """
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def _require_text(parser: ConfigParser, path: Path, section: str, option: str) -> str:
    """Read a non-empty config option.

    Args:
        parser: Parser that has already read the config file.
        path: Config file path, used in the error message.
        section: INI section name.
        option: Option name inside the section.

    Returns:
        The option value without surrounding whitespace.

    Raises:
        ValueError: The option is missing or blank.
    """
    if not parser.has_option(section, option):
        raise ValueError(f"{path.name} is missing [{section}] {option}")
    value = parser.get(section, option).strip()
    if not value:
        raise ValueError(f"{path.name} [{section}] {option} is empty")
    return value


def _require_positive_int(parser: ConfigParser, path: Path, section: str, option: str) -> int:
    """Read a config option that must be an integer greater than zero.

    Args:
        parser: Parser that has already read the config file.
        path: Config file path, used in the error message.
        section: INI section name.
        option: Option name inside the section.

    Returns:
        The parsed integer.

    Raises:
        ValueError: The option is missing, not an integer, or less than 1.
    """
    raw = _require_text(parser, path, section, option)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{path.name} [{section}] {option} must be an integer") from error
    if value < 1:
        raise ValueError(f"{path.name} [{section}] {option} must be at least 1")
    return value


def read_token(path: Path = TOKEN_PATH) -> str:
    """Read the bot token from a local file.

    Args:
        path: Path to the token file. Defaults to token.txt in the project root.

    Returns:
        The token, without surrounding whitespace.

    Raises:
        FileNotFoundError: The token file does not exist.
        ValueError: The token file is empty.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Token file not found: {path}")

    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"Token file is empty: {path}")
    return token
