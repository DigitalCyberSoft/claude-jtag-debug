"""Recursive descent GDB/MI output parser.

Handles the full MI grammar including nested tuples, lists, c-strings with
C-style escaping, and progressive buffering of incomplete lines.
"""

from __future__ import annotations

import logging
from typing import Any

from .types import MIAsyncRecord, MIRecord, MIResultRecord, MIStreamRecord

logger = logging.getLogger(__name__)

# MI prefix characters
_RESULT_PREFIX = "^"
_EXEC_PREFIX = "*"
_STATUS_PREFIX = "+"
_NOTIFY_PREFIX = "="
_CONSOLE_PREFIX = "~"
_TARGET_PREFIX = "@"
_LOG_PREFIX = "&"
_PROMPT = "(gdb)"

_ASYNC_PREFIXES = {_EXEC_PREFIX, _STATUS_PREFIX, _NOTIFY_PREFIX}
_STREAM_PREFIXES = {_CONSOLE_PREFIX, _TARGET_PREFIX, _LOG_PREFIX}
_ASYNC_TYPE_MAP = {
    _EXEC_PREFIX: "exec",
    _STATUS_PREFIX: "status",
    _NOTIFY_PREFIX: "notify",
}
_STREAM_TYPE_MAP = {
    _CONSOLE_PREFIX: "console",
    _TARGET_PREFIX: "target",
    _LOG_PREFIX: "log",
}

# C-style escape sequences used in MI c-strings
_ESCAPE_MAP = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


class MIParseError(Exception):
    """Raised on unrecoverable parse errors within a single line."""


class MIParser:
    """Progressive GDB/MI output parser.

    Feed raw data from GDB's stdout. Complete lines are parsed into typed
    records. Incomplete trailing data is buffered for the next feed() call.
    """

    def __init__(self) -> None:
        self._buffer: str = ""

    def feed(self, data: str) -> list[MIRecord]:
        """Feed raw data, return list of fully parsed records."""
        self._buffer += data
        records: list[MIRecord] = []

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                continue
            record = self._parse_line(line)
            if record is not None:
                records.append(record)

        return records

    def reset(self) -> None:
        self._buffer = ""

    # ── Line-level dispatch ──────────────────────────────────────────

    def _parse_line(self, line: str) -> MIRecord | None:
        if line.startswith(_PROMPT):
            return None  # GDB prompt marker, not a record

        # Consume optional leading token (digits)
        pos = 0
        token: int | None = None
        while pos < len(line) and line[pos].isdigit():
            pos += 1
        if pos > 0:
            token = int(line[:pos])

        if pos >= len(line):
            logger.debug("Unclassified output (token only): %r", line)
            return None

        prefix = line[pos]

        if prefix == _RESULT_PREFIX:
            return self._parse_result_record(line, pos + 1, token)
        if prefix in _ASYNC_PREFIXES:
            return self._parse_async_record(line, pos, token)
        if prefix in _STREAM_PREFIXES:
            return self._parse_stream_record(line, pos)

        # Unrecognised line -- target stdout via semihosting or similar
        logger.debug("Unclassified target output: %r", line)
        return None

    # ── Result record: [token] "^" result-class ("," result)* ────────

    def _parse_result_record(self, line: str, pos: int, token: int | None) -> MIResultRecord:
        result_class, pos = self._parse_identifier(line, pos)
        results = self._parse_results_tail(line, pos)
        return MIResultRecord(token=token, result_class=result_class, results=results)

    # ── Async record: [token] ("*"|"+"|"=") async-class ("," result)* ─

    def _parse_async_record(self, line: str, pos: int, token: int | None) -> MIAsyncRecord:
        prefix = line[pos]
        pos += 1
        async_class, pos = self._parse_identifier(line, pos)
        results = self._parse_results_tail(line, pos)
        return MIAsyncRecord(
            record_type=_ASYNC_TYPE_MAP[prefix],
            async_class=async_class,
            results=results,
            token=token,
        )

    # ── Stream record: ("~"|"@"|"&") c-string ───────────────────────

    def _parse_stream_record(self, line: str, pos: int) -> MIStreamRecord:
        prefix = line[pos]
        pos += 1
        # Skip whitespace between prefix and string
        while pos < len(line) and line[pos] == " ":
            pos += 1
        if pos < len(line) and line[pos] == '"':
            content, _ = self._parse_cstring(line, pos)
        else:
            content = line[pos:]
        return MIStreamRecord(stream_type=_STREAM_TYPE_MAP[prefix], content=content)

    # ── Shared helpers ───────────────────────────────────────────────

    def _parse_identifier(self, s: str, pos: int) -> tuple[str, int]:
        start = pos
        while pos < len(s) and (s[pos].isalnum() or s[pos] in "-_"):
            pos += 1
        return s[start:pos], pos

    def _parse_results_tail(self, s: str, pos: int) -> dict[str, Any]:
        """Parse comma-separated key=value pairs from current position."""
        results: dict[str, Any] = {}
        while pos < len(s) and s[pos] == ",":
            pos += 1  # skip comma
            key, pos = self._parse_identifier(s, pos)
            if not key:
                break
            if pos < len(s) and s[pos] == "=":
                pos += 1
                val, pos = self._parse_value(s, pos)
            else:
                val = ""
            results[key] = val
        return results

    # ── Value grammar ────────────────────────────────────────────────

    def _parse_value(self, s: str, pos: int) -> tuple[Any, int]:
        if pos >= len(s):
            return "", pos
        ch = s[pos]
        if ch == '"':
            return self._parse_cstring(s, pos)
        if ch == "{":
            return self._parse_tuple(s, pos)
        if ch == "[":
            return self._parse_list(s, pos)
        # Bare value (shouldn't happen per spec, but be defensive)
        start = pos
        while pos < len(s) and s[pos] not in ",}]":
            pos += 1
        return s[start:pos], pos

    def _parse_cstring(self, s: str, pos: int) -> tuple[str, int]:
        """Parse a C-style quoted string: "..." with escape sequences."""
        if s[pos] != '"':
            raise MIParseError(f"Expected '\"' at pos {pos}, got {s[pos]!r}")
        pos += 1
        chars: list[str] = []
        while pos < len(s):
            ch = s[pos]
            if ch == '"':
                return "".join(chars), pos + 1
            if ch == "\\":
                pos += 1
                if pos >= len(s):
                    break
                esc = s[pos]
                if esc in _ESCAPE_MAP:
                    chars.append(_ESCAPE_MAP[esc])
                    pos += 1
                elif esc.isdigit():
                    # Octal: up to 3 digits
                    octal = esc
                    pos += 1
                    for _ in range(2):
                        if pos < len(s) and s[pos].isdigit() and s[pos] < "8":
                            octal += s[pos]
                            pos += 1
                        else:
                            break
                    chars.append(chr(int(octal, 8)))
                else:
                    # Unknown escape, keep literal
                    chars.append(esc)
                    pos += 1
            else:
                chars.append(ch)
                pos += 1
        # Unterminated string -- return what we have
        return "".join(chars), pos

    def _parse_tuple(self, s: str, pos: int) -> tuple[dict[str, Any], int]:
        """Parse tuple: {} | { result ("," result)* }."""
        if s[pos] != "{":
            raise MIParseError(f"Expected '{{' at pos {pos}")
        pos += 1
        if pos < len(s) and s[pos] == "}":
            return {}, pos + 1

        result: dict[str, Any] = {}
        while pos < len(s):
            key, pos = self._parse_identifier(s, pos)
            if pos < len(s) and s[pos] == "=":
                pos += 1
                val, pos = self._parse_value(s, pos)
            else:
                val = ""
            result[key] = val

            if pos < len(s) and s[pos] == ",":
                pos += 1
                continue
            if pos < len(s) and s[pos] == "}":
                return result, pos + 1
            break

        return result, pos

    def _parse_list(self, s: str, pos: int) -> tuple[list[Any], int]:
        """Parse list: [] | [ value ("," value)* ] | [ result ("," result)* ]."""
        if s[pos] != "[":
            raise MIParseError(f"Expected '[' at pos {pos}")
        pos += 1
        if pos < len(s) and s[pos] == "]":
            return [], pos + 1

        items: list[Any] = []

        # Peek: if next is identifier followed by '=', it's a result-list
        peek_pos = pos
        while peek_pos < len(s) and (s[peek_pos].isalnum() or s[peek_pos] in "-_"):
            peek_pos += 1
        is_result_list = peek_pos < len(s) and s[peek_pos] == "="

        while pos < len(s):
            if is_result_list:
                key, pos = self._parse_identifier(s, pos)
                if pos < len(s) and s[pos] == "=":
                    pos += 1
                val, pos = self._parse_value(s, pos)
                items.append({key: val})
            else:
                val, pos = self._parse_value(s, pos)
                items.append(val)

            if pos < len(s) and s[pos] == ",":
                pos += 1
                continue
            if pos < len(s) and s[pos] == "]":
                return items, pos + 1
            break

        return items, pos
