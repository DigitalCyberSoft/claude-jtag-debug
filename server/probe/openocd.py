"""OpenOCD GDB server process manager."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket

logger = logging.getLogger(__name__)

_INTERFACE_MAP = {
    "stlink": "interface/stlink.cfg",
    "cmsis-dap": "interface/cmsis-dap.cfg",
    "jlink": "interface/jlink.cfg",
}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class OpenOCDServer:
    """Manages an OpenOCD process providing a GDB server port."""

    def __init__(
        self,
        interface: str = "stlink",
        target: str = "stm32f4x",
        port: int | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self._interface = interface
        self._target = target
        self._port = port or _find_free_port()
        self._extra_args = extra_args or []
        self._process: asyncio.subprocess.Process | None = None

    @property
    def port(self) -> int:
        return self._port

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> int:
        """Start OpenOCD. Returns GDB server port."""
        if self.is_running():
            return self._port

        if not shutil.which("openocd"):
            raise FileNotFoundError(
                "OpenOCD not found. Install with: sudo apt install openocd"
            )

        interface_cfg = _INTERFACE_MAP.get(self._interface, f"interface/{self._interface}.cfg")
        target_cfg = f"target/{self._target}.cfg"

        cmd = [
            "openocd",
            "-f", interface_cfg,
            "-f", target_cfg,
            "-c", f"gdb_port {self._port}",
            *self._extra_args,
        ]

        logger.info("Starting OpenOCD: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly to check it didn't crash immediately
        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.0)
            # If we get here, process exited
            stderr = await self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(
                f"OpenOCD exited immediately: {stderr.decode(errors='replace')[-500:]}"
            )
        except asyncio.TimeoutError:
            # Good -- still running
            pass

        logger.info("OpenOCD started on port %d (pid %d)", self._port, self._process.pid)
        return self._port

    async def stop(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._process = None
            return

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._process.kill()
        self._process = None
        logger.info("OpenOCD stopped")

    async def reset(self, halt: bool = True) -> None:
        """Reset target via OpenOCD telnet interface."""
        telnet_port = self._port + 1  # OpenOCD convention
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("localhost", telnet_port),
                timeout=3.0,
            )
            cmd = "reset halt\n" if halt else "reset run\n"
            writer.write(cmd.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError) as exc:
            logger.warning("Reset via telnet failed: %s", exc)
