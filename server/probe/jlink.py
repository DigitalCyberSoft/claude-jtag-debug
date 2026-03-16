"""J-Link GDB server process manager."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket

logger = logging.getLogger(__name__)

_JLINK_CANDIDATES = ["JLinkGDBServerCLExe", "JLinkGDBServer", "JLinkGDBServerExe"]


def _find_jlink() -> str:
    for candidate in _JLINK_CANDIDATES:
        if shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        f"J-Link GDB Server not found. Searched: {', '.join(_JLINK_CANDIDATES)}. "
        "Install SEGGER J-Link Software."
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class JLinkServer:
    """Manages a J-Link GDB Server process."""

    def __init__(
        self,
        device: str = "STM32F407VG",
        interface: str = "SWD",
        speed: int = 4000,
        port: int | None = None,
    ) -> None:
        self._device = device
        self._interface = interface
        self._speed = speed
        self._port = port or _find_free_port()
        self._process: asyncio.subprocess.Process | None = None

    @property
    def port(self) -> int:
        return self._port

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> int:
        if self.is_running():
            return self._port

        jlink_exe = _find_jlink()
        cmd = [
            jlink_exe,
            "-device", self._device,
            "-if", self._interface,
            "-speed", str(self._speed),
            "-port", str(self._port),
            "-nogui",
            "-noir",
            "-singlerun",
        ]

        logger.info("Starting J-Link GDB Server: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly for startup
        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
            stderr = await self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(
                f"J-Link exited immediately: {stderr.decode(errors='replace')[-500:]}"
            )
        except asyncio.TimeoutError:
            pass

        logger.info("J-Link GDB Server on port %d (pid %d)", self._port, self._process.pid)
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
        logger.info("J-Link GDB Server stopped")
