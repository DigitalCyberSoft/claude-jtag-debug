"""Session persistence to disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path.home() / ".claude" / "jtag-debug-sessions"


class SessionStore:
    """Persists debug session metadata to disk for recovery."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _DEFAULT_DIR
        self._base.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        session_dir = self._base / session_id
        session_dir.mkdir(exist_ok=True)
        state_file = session_dir / "session.json"
        state_file.write_text(json.dumps(data, indent=2, default=str))

    def load(self, session_id: str) -> dict[str, Any] | None:
        state_file = self._base / session_id / "session.json"
        if not state_file.is_file():
            return None
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load session %s: %s", session_id, exc)
            return None

    def delete(self, session_id: str) -> None:
        session_dir = self._base / session_id
        if session_dir.is_dir():
            for f in session_dir.iterdir():
                f.unlink(missing_ok=True)
            session_dir.rmdir()

    def list_sessions(self) -> list[str]:
        if not self._base.is_dir():
            return []
        return [
            d.name
            for d in self._base.iterdir()
            if d.is_dir() and (d / "session.json").is_file()
        ]

    def save_channel_map(
        self, session_id: str, channel_map: dict[str, int]
    ) -> None:
        session_dir = self._base / session_id
        session_dir.mkdir(exist_ok=True)
        channels_file = session_dir / "channels.json"
        channels_file.write_text(json.dumps(channel_map, indent=2))

    def load_channel_map(self, session_id: str) -> dict[str, int] | None:
        channels_file = self._base / session_id / "channels.json"
        if not channels_file.is_file():
            return None
        try:
            return json.loads(channels_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None
