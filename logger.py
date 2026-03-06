"""
logger.py
---------
Thread-safe logger that writes timestamped messages to a log file
AND echoes them to stdout simultaneously.
"""

import threading
from datetime import datetime


class Logger:
    """
    Concurrent-safe logger.

    Usage
    -----
    log = Logger("log_peer_1001.log")
    log.create_log("Peer connected to 1002")
    log.close()
    """

    def __init__(self, file_path: str):
        self._lock = threading.Lock()
        self._file = None
        try:
            # Line-buffered so every write flushes immediately (buffering=1)
            self._file = open(file_path, "a", buffering=1, encoding="utf-8")
        except OSError as exc:
            print(f"[Logger] Cannot open {file_path!r}: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_log(self, msg: str) -> None:
        """Write a timestamped log entry to the file and to stdout."""
        timestamp = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        entry = f"[{timestamp}]: {msg}"
        with self._lock:
            if self._file:
                self._file.write(entry + "\n")
            print(entry)

    def close(self) -> None:
        """Flush and close the underlying log file."""
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
