"""GDB/MI record types and state enums."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class GDBState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    RUNNING = "running"
    STOPPED = "stopped"
    EXITED = "exited"


class StopReason(enum.Enum):
    BREAKPOINT_HIT = "breakpoint-hit"
    SIGNAL_RECEIVED = "signal-received"
    END_STEPPING_RANGE = "end-stepping-range"
    WATCHPOINT_TRIGGER = "watchpoint-trigger"
    FUNCTION_FINISHED = "function-finished"
    EXITED = "exited"
    EXITED_NORMALLY = "exited-normally"
    EXITED_SIGNALLED = "exited-signalled"
    UNKNOWN = "unknown"

    @classmethod
    def from_string(cls, s: str) -> StopReason:
        for member in cls:
            if member.value == s:
                return member
        return cls.UNKNOWN


@dataclass(frozen=True)
class MIResultRecord:
    """Result record: ^done, ^running, ^error, ^connected, ^exit."""
    token: int | None
    result_class: str
    results: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MIAsyncRecord:
    """Async record: *stopped, *running, =thread-created, +download, etc."""
    record_type: str  # 'exec' (*), 'status' (+), 'notify' (=)
    async_class: str
    results: dict[str, Any] = field(default_factory=dict)
    token: int | None = None


@dataclass(frozen=True)
class MIStreamRecord:
    """Stream record: ~console, @target, &log."""
    stream_type: str  # 'console' (~), 'target' (@), 'log' (&)
    content: str = ""


MIRecord = MIResultRecord | MIAsyncRecord | MIStreamRecord
