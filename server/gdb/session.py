"""High-level debug session wrapping MIConnection with state management."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .mi_connection import MIConnection, GDBError, TIMEOUT_FLASH
from .types import GDBState, MIAsyncRecord, StopReason

logger = logging.getLogger(__name__)


class DebugSession:
    """State machine wrapping an MIConnection for a single debug target."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._conn: MIConnection | None = None
        self._target_info: dict[str, Any] = {}
        self._breakpoints: dict[int, dict] = {}
        self._elf_path: str | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._last_stop: dict[str, Any] = {}

    @property
    def state(self) -> GDBState:
        if self._conn is None:
            return GDBState.DISCONNECTED
        return self._conn.state

    @property
    def connection(self) -> MIConnection | None:
        return self._conn

    @property
    def target_info(self) -> dict[str, Any]:
        return self._target_info

    @property
    def breakpoints(self) -> dict[int, dict]:
        return self._breakpoints

    @property
    def last_stop(self) -> dict[str, Any]:
        return self._last_stop

    async def connect(
        self,
        gdb_path: str | None = None,
        target: str = "localhost",
        port: int = 3333,
        elf_path: str | None = None,
    ) -> dict[str, Any]:
        """Start GDB and connect to remote target."""
        self._conn = MIConnection(gdb_path)
        await self._conn.start()

        # Start background event consumer
        self._event_task = asyncio.create_task(self._event_loop())

        result: dict[str, Any] = {
            "gdb_path": self._conn.gdb_path,
            "mi_version": self._conn.mi_version,
            "pid": self._conn.pid,
        }

        # Load ELF symbols if provided
        if elf_path:
            self._elf_path = elf_path
            await self._conn.send(f"-file-exec-and-symbols {elf_path}")
            result["elf"] = elf_path

        # Connect to remote target
        target_str = f"{target}:{port}"
        await self._conn.send(f"-target-select remote {target_str}")
        self._target_info = {"host": target, "port": port, "elf": elf_path}
        result["target"] = target_str

        # Read initial state -- target should be stopped after connect
        self._stop_event.set()

        return result

    async def read_registers(self, names: list[str] | None = None) -> dict[str, str]:
        """Read CPU registers. Returns {name: hex_value}."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None

        # Get register names first
        result = await self._conn.send("-data-list-register-names")
        reg_names = result.results.get("register-names", [])

        # Get register values
        result = await self._conn.send("-data-list-register-values x")
        reg_values = result.results.get("register-values", [])

        registers: dict[str, str] = {}
        for entry in reg_values:
            if isinstance(entry, dict):
                num = int(entry.get("number", -1))
                val = entry.get("value", "")
                if 0 <= num < len(reg_names) and reg_names[num]:
                    name = reg_names[num]
                    if names is None or name in names:
                        registers[name] = val

        return registers

    async def read_memory(self, address: int, length: int) -> bytes:
        """Read target memory. Returns raw bytes."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None

        result = await self._conn.send(
            f"-data-read-memory-bytes 0x{address:08x} {length}",
            timeout=5.0,
        )
        memory = result.results.get("memory", [])
        if not memory:
            return b""

        # MI returns list of {begin, offset, end, contents}
        entry = memory[0] if isinstance(memory, list) else memory
        hex_str = entry.get("contents", "") if isinstance(entry, dict) else ""
        return bytes.fromhex(hex_str)

    async def write_memory(self, address: int, data: bytes) -> None:
        """Write bytes to target memory."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None
        hex_str = data.hex()
        await self._conn.send(
            f"-data-write-memory-bytes 0x{address:08x} {hex_str}",
            timeout=5.0,
        )

    async def set_breakpoint(
        self,
        location: str,
        condition: str | None = None,
        temporary: bool = False,
    ) -> dict[str, Any]:
        """Set a breakpoint. Location can be function name, file:line, or *address."""
        assert self._conn is not None
        cmd = "-break-insert"
        if temporary:
            cmd += " -t"
        if condition:
            cmd += f" -c {condition}"
        cmd += f" {location}"

        result = await self._conn.send(cmd)
        bkpt = result.results.get("bkpt", {})
        bp_id = int(bkpt.get("number", 0))
        info = {
            "id": bp_id,
            "location": location,
            "address": bkpt.get("addr", ""),
            "file": bkpt.get("file", ""),
            "line": bkpt.get("line", ""),
            "enabled": bkpt.get("enabled", "y") == "y",
            "hits": 0,
            "temporary": temporary,
        }
        self._breakpoints[bp_id] = info
        return info

    async def remove_breakpoint(self, bp_id: int) -> None:
        """Remove a breakpoint by ID."""
        assert self._conn is not None
        await self._conn.send(f"-break-delete {bp_id}")
        self._breakpoints.pop(bp_id, None)

    async def continue_execution(self, timeout: float = 30.0) -> dict[str, Any]:
        """Continue execution and wait for stop."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None

        self._stop_event.clear()
        await self._conn.send("-exec-continue")

        # Wait for *stopped event
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Try to interrupt
            try:
                await self._conn.send_raw("-exec-interrupt")
                await asyncio.wait_for(self._stop_event.wait(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                pass
            raise TimeoutError(f"Target did not stop within {timeout}s")

        return self._last_stop

    async def step(self, step_type: str = "into", count: int = 1) -> dict[str, Any]:
        """Step execution. step_type: 'into', 'over', 'out'."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None

        cmd_map = {"into": "-exec-step", "over": "-exec-next", "out": "-exec-finish"}
        cmd = cmd_map.get(step_type, "-exec-step")

        for _ in range(count):
            self._stop_event.clear()
            await self._conn.send(cmd)
            await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)

        return self._last_stop

    async def interrupt(self) -> dict[str, Any]:
        """Interrupt a running target."""
        assert self._conn is not None
        if self._conn.state != GDBState.RUNNING:
            return self._last_stop

        self._stop_event.clear()
        await self._conn.send_raw("-exec-interrupt")
        await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
        return self._last_stop

    async def backtrace(self, full: bool = False) -> list[dict[str, Any]]:
        """Get backtrace. Returns list of frame dicts."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None

        cmd = "-stack-list-frames"
        result = await self._conn.send(cmd)
        raw_stack = result.results.get("stack", [])

        frames: list[dict[str, Any]] = []
        for entry in raw_stack:
            frame = entry.get("frame", entry) if isinstance(entry, dict) else {}
            if not isinstance(frame, dict):
                continue
            info: dict[str, Any] = {
                "level": frame.get("level", ""),
                "address": frame.get("addr", ""),
                "function": frame.get("func", "??"),
                "file": frame.get("file", ""),
                "line": frame.get("line", ""),
                "fullname": frame.get("fullname", ""),
            }

            if full:
                # Get locals for this frame
                try:
                    level = info["level"]
                    locals_result = await self._conn.send(
                        f"-stack-list-locals --frame {level} 1"
                    )
                    info["locals"] = locals_result.results.get("locals", [])
                except GDBError:
                    info["locals"] = []

            frames.append(info)

        return frames

    async def evaluate(self, expression: str) -> str:
        """Evaluate an expression in the current frame."""
        self._require_state(GDBState.STOPPED)
        assert self._conn is not None
        result = await self._conn.send(f"-data-evaluate-expression {expression}")
        return result.results.get("value", "")

    async def flash(self, elf_path: str, verify: bool = True) -> dict[str, Any]:
        """Flash firmware to target."""
        assert self._conn is not None
        await self._conn.send(f"-file-exec-and-symbols {elf_path}")
        await self._conn.send("-target-download", timeout=TIMEOUT_FLASH)

        result: dict[str, Any] = {"elf": elf_path, "flashed": True}

        if verify:
            await self._conn.send("compare-sections", timeout=TIMEOUT_FLASH)
            result["verified"] = True

        return result

    async def reset(self, halt: bool = True) -> None:
        """Reset the target."""
        assert self._conn is not None
        if halt:
            await self._conn.send("-interpreter-exec console \"monitor reset halt\"")
        else:
            await self._conn.send("-interpreter-exec console \"monitor reset run\"")
        if halt:
            self._stop_event.set()

    async def raw_command(self, command: str) -> str:
        """Execute a raw GDB command and return console output."""
        assert self._conn is not None
        # Drain console queue
        while not self._conn.get_console_queue().empty():
            try:
                self._conn.get_console_queue().get_nowait()
            except asyncio.QueueEmpty:
                break

        await self._conn.send(f"-interpreter-exec console \"{command}\"")

        # Collect console output
        output_parts: list[str] = []
        await asyncio.sleep(0.1)
        while not self._conn.get_console_queue().empty():
            try:
                output_parts.append(self._conn.get_console_queue().get_nowait())
            except asyncio.QueueEmpty:
                break

        return "".join(output_parts)

    async def disconnect(self) -> None:
        """Disconnect and clean up."""
        if self._event_task:
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
            self._event_task = None

        if self._conn:
            await self._conn.stop()
            self._conn = None

        self._breakpoints.clear()
        self._last_stop = {}

    def serialize(self) -> dict[str, Any]:
        """Serialize session state for persistence."""
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "target_info": self._target_info,
            "elf_path": self._elf_path,
            "breakpoints": list(self._breakpoints.values()),
            "last_stop": self._last_stop,
            "gdb_pid": self._conn.pid if self._conn else None,
            "gdb_path": self._conn.gdb_path if self._conn else None,
            "mi_version": self._conn.mi_version if self._conn else None,
        }

    def _require_state(self, *states: GDBState) -> None:
        current = self.state
        if current not in states:
            state_names = ", ".join(s.value for s in states)
            raise RuntimeError(
                f"Operation requires state {state_names}, but session is {current.value}"
            )

    async def _event_loop(self) -> None:
        """Background consumer of async events from the connection."""
        assert self._conn is not None
        queue = self._conn.get_event_queue()
        try:
            while True:
                event: MIAsyncRecord = await queue.get()
                if event.record_type == "exec" and event.async_class == "stopped":
                    self._process_stop(event)
                elif event.record_type == "exec" and event.async_class == "running":
                    pass  # State already updated by connection
        except asyncio.CancelledError:
            return

    def _process_stop(self, event: MIAsyncRecord) -> None:
        reason_str = event.results.get("reason", "unknown")
        frame = event.results.get("frame", {})

        self._last_stop = {
            "reason": reason_str,
            "stop_reason": StopReason.from_string(reason_str).value,
            "signal_name": event.results.get("signal-name", ""),
            "signal_meaning": event.results.get("signal-meaning", ""),
            "frame": {
                "function": frame.get("func", "??") if isinstance(frame, dict) else "??",
                "file": frame.get("file", "") if isinstance(frame, dict) else "",
                "line": frame.get("line", "") if isinstance(frame, dict) else "",
                "address": frame.get("addr", "") if isinstance(frame, dict) else "",
            },
            "thread_id": event.results.get("thread-id", ""),
            "breakpoint_id": event.results.get("bkptno", ""),
        }

        # Update breakpoint hit counts
        bp_id_str = event.results.get("bkptno", "")
        if bp_id_str:
            try:
                bp_id = int(bp_id_str)
                if bp_id in self._breakpoints:
                    self._breakpoints[bp_id]["hits"] = (
                        self._breakpoints[bp_id].get("hits", 0) + 1
                    )
            except (ValueError, TypeError):
                pass

        self._stop_event.set()
