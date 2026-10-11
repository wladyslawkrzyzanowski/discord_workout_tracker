"""Configure process-wide logging."""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TextIO

_LOG_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:_\d+)?\.log$")
_BYTES_PER_MEGABYTE = 1000 * 1000


class StartupSizeFileHandler(logging.Handler):
    """Write logs to a new file on startup and after a size limit.

    The same limit caps the whole folder. Older files are deleted until the
    folder fits. The file currently being written is kept.
    """

    def __init__(self, directory: Path, max_bytes: int) -> None:
        """Open the first log file for this process.

        Args:
            directory: Folder that stores log files. It is created when missing.
            max_bytes: Maximum size of one log file and of all log files together.

        Returns:
            None.
        """
        super().__init__()
        self._directory = directory
        self._max_bytes = max_bytes
        self._directory.mkdir(parents=True, exist_ok=True)
        self._stream: TextIO | None = None
        self._path: Path | None = None
        self._size = 0
        self._open_new_file()
        self._prune()

    def emit(self, record: logging.LogRecord) -> None:
        """Write one log record, opening a new file when the size limit is hit.

        Args:
            record: Log record to format and store.

        Returns:
            None.
        """
        try:
            message = self.format(record) + "\n"
            encoded_size = len(message.encode("utf-8"))
            if self._size > 0 and self._size + encoded_size > self._max_bytes:
                self._open_new_file()
            stream = self._stream
            if stream is None:
                raise RuntimeError("Log file is not open")
            stream.write(message)
            stream.flush()
            self._size += encoded_size
            self._prune()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Close the open log file.

        Returns:
            None.
        """
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        finally:
            super().close()

    def _open_new_file(self) -> None:
        """Close the current file, if any, and open one named with the current time.

        Returns:
            None.
        """
        if self._stream is not None:
            self._stream.close()
        self._path = self._new_path()
        self._stream = self._path.open("a", encoding="utf-8")
        self._size = 0

    def _new_path(self) -> Path:
        """Build a log path that starts with the full local date and time.

        Returns:
            A path that does not already exist. A numeric suffix is added when
            a file from the same second is already present.
        """
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        candidate = self._directory / f"{stamp}.log"
        suffix = 2
        while candidate.exists():
            candidate = self._directory / f"{stamp}_{suffix:04d}.log"
            suffix += 1
        return candidate

    def _prune(self) -> None:
        """Delete the oldest log files until the folder is within the size cap.

        Returns:
            None.
        """
        files = self._log_files()
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self._max_bytes or path == self._path:
                continue
            size = path.stat().st_size
            path.unlink()
            total -= size

    def _log_files(self) -> list[Path]:
        """List this handler's log files, oldest first.

        Returns:
            Files whose names start with a full date and time, sorted by name.
        """
        files = [
            path
            for path in self._directory.iterdir()
            if path.is_file() and _LOG_NAME.match(path.name)
        ]
        return sorted(files, key=lambda path: path.name)


def setup_logging(directory: Path, max_megabytes: int) -> None:
    """Send logs to a new timestamped file and to the terminal.

    Args:
        directory: Folder that stores log files.
        max_megabytes: Maximum size, in MB, of one log file and of the whole folder.

    Returns:
        None.
    """
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    file_handler = StartupSizeFileHandler(
        directory,
        max_bytes=max_megabytes * _BYTES_PER_MEGABYTE,
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
