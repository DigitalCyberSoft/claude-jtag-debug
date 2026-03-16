"""Async GDB/MI subprocess connection with command queue and token matching."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from .mi_parser import MIParser
from .types import (
    GDBState,
    MIAsyncRecord,
    MIRecord,
    MIResultRecord,
    MIStreamRecord,
)

logger = logging.getLogger(__name__)

# Timeout defaults per operation class (seconds)
TIMEOUT_MEMORY = 5.0
TIMEOUT_DEFAULT = 30.0
TIMEOUT_FLASH = 120.0

# GDB binary fallback chain
_GDB_CANDIDATES = ["arm-none-eabi-gdb", "gdb-multiarch", "gdb"]


def _find_gdb(preferred: str | None = None) -> str:
    """Find a usable GDB binary."""
    candidates = [preferred] if preferred else []
    candidates.extend(_GDB_CANDIDATES)
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        f"No GDB binary found. Searched: {', '.join(c for c in candidates if c)}. "
        "Install arm-none-eabi-gdb or gdb-multiarch."
    )


class MIConnection:
    """Manages an async GDB/MI subprocess with token-based command/response matching."""

    def __init__(self, gdb_path: str | None = None) -> None:
        self._gdb_path_preference = gdb_path
        self._gdb_path: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._parser: MIParser = MIParser()
        self._token_counter: int = 1
        self._pending: dict[int, asyncio.Future[MIResultRecord]] = {}
        self._pending_order: list[int] = []  # insertion order for tokenless fallback
        self._event_queue: asyncio.Queue[MIAsyncRecord] = asyncio.Queue(maxsize=1000)
        self._console_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self._state: GDBState = GDBState.DISCONNECTED
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._mi_version: str = "mi3"

    @property
    def state(self) -> GDBState:
        return self._state

    @property
    def gdb_path(self) -> str | None:
        return self._gdb_path

    @property
    def mi_version(self) -> str:
        return self._mi_version

    @property
    def pid(self) -> int | None:
        if self._process:
            return self._process.pid
        return None

    def get_event_queue(self) -> asyncio.Queue[MIAsyncRecord]:
        return self._event_queue

    def get_console_queue(self) -> asyncio.Queue[str]:
        return self._console_queue

    async def start(self) -> None:
        """Spawn GDB subprocess in MI mode."""
        if self._state != GDBState.DISCONNECTED:
            raise RuntimeError(f"Cannot start: state is {self._state.value}")

        self._gdb_path = _find_gdb(self._gdb_path_preference)
        logger.info("Starting GDB: %s", self._gdb_path)

        # Try MI3 first, fall back to MI2
        for mi in ("mi3", "mi2"):
            try:
                self._process = await asyncio.create_subprocess_exec(
                    self._gdb_path,
                    f"--interpreter={mi}",
                    "--quiet",
                    "--nx",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._mi_version = mi
                break
            except Exception:
                if mi == "mi2":
                    raise
                continue

        self._parser.reset()
        self._state = GDBState.CONNECTED
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        """Shut down GDB gracefully."""
        if self._process is None:
            self._state = GDBState.DISCONNECTED
            return

        try:
            await self.send_raw("-gdb-exit")
        except Exception:
            pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()

        self._process = None
        self._state = GDBState.DISCONNECTED
        self._reject_all_pending("GDB connection closed")

    async def send(self, command: str, timeout: float = TIMEOUT_DEFAULT) -> MIResultRecord:
        """Send an MI command and wait for its result record."""
        if self._process is None or self._state == GDBState.DISCONNECTED:
            raise RuntimeError("GDB not connected")

        token = self._token_counter
        self._token_counter += 1

        future: asyncio.Future[MIResultRecord] = asyncio.get_event_loop().create_future()
        self._pending[token] = future
        self._pending_order.append(token)

        mi_command = f"{token}{command}\n"
        await self._write(mi_command)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(token, None)
            if token in self._pending_order:
                self._pending_order.remove(token)
            # Try to interrupt GDB
            try:
                await self.send_raw("-exec-interrupt")
            except Exception:
                pass
            raise TimeoutError(f"GDB command timed out after {timeout}s: {command}")

        if result.result_class == "error":
            msg = result.results.get("msg", "Unknown GDB error")
            raise GDBError(msg, result)

        return result

    async def send_raw(self, data: str) -> None:
        """Send raw text to GDB stdin without token tracking."""
        await self._write(data + "\n")

    async def _write(self, data: str) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("GDB not connected")
        async with self._write_lock:
            self._process.stdin.write(data.encode())
            await self._process.stdin.drain()

    async def _reader_loop(self) -> None:
        """Background task reading GDB stdout and dispatching records."""
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                data = await self._process.stdout.readline()
                if not data:
                    # EOF -- GDB exited
                    logger.warning("GDB process EOF")
                    self._state = GDBState.EXITED
                    self._reject_all_pending("GDB process exited unexpectedly")
                    return

                records = self._parser.feed(data.decode("utf-8", errors="replace"))
                for record in records:
                    self._dispatch(record)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Reader loop error: %s", exc)
            self._state = GDBState.EXITED
            self._reject_all_pending(f"Reader error: {exc}")

    def _dispatch(self, record: MIRecord) -> None:
        if isinstance(record, MIResultRecord):
            self._handle_result(record)
        elif isinstance(record, MIAsyncRecord):
            self._handle_async(record)
        elif isinstance(record, MIStreamRecord):
            self._handle_stream(record)

    def _handle_result(self, record: MIResultRecord) -> None:
        token = record.token
        if token is not None and token in self._pending:
            future = self._pending.pop(token)
            if token in self._pending_order:
                self._pending_order.remove(token)
            if not future.done():
                future.set_result(record)
            return

        # Tokenless fallback: resolve oldest pending
        if self._pending_order:
            oldest_token = self._pending_order.pop(0)
            future = self._pending.pop(oldest_token, None)
            if future and not future.done():
                future.set_result(record)
                return

        logger.debug("Unmatched result record: %s", record)

    def _handle_async(self, record: MIAsyncRecord) -> None:
        # Update state from exec async records
        if record.record_type == "exec":
            if record.async_class == "stopped":
                self._state = GDBState.STOPPED
            elif record.async_class == "running":
                self._state = GDBState.RUNNING

        try:
            self._event_queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop oldest
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._event_queue.put_nowait(record)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event: %s", record.async_class)

    def _handle_stream(self, record: MIStreamRecord) -> None:
        if record.stream_type == "console":
            try:
                self._console_queue.put_nowait(record.content)
            except asyncio.QueueFull:
                pass
        elif record.stream_type == "log":
            logger.debug("GDB log: %s", record.content.rstrip())

    def _reject_all_pending(self, reason: str) -> None:
        for token in list(self._pending):
            future = self._pending.pop(token)
            if not future.done():
                future.set_exception(ConnectionError(reason))
        self._pending_order.clear()


class GDBError(Exception):
    """Error returned by GDB."""

    def __init__(self, message: str, record: MIResultRecord | None = None) -> None:
        super().__init__(message)
        self.record = record
