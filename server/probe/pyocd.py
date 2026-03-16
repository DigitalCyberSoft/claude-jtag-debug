"""pyOCD GDB server fallback."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class PyOCDServer:
    """Manages a pyOCD GDB server process."""

    def __init__(
        self,
        target: str | None = None,
        port: int | None = None,
    ) -> None:
        self._target = target
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

        if not shutil.which("pyocd"):
            raise FileNotFoundError(
                "pyOCD not found. Install with: pip install pyocd"
            )

        cmd = ["pyocd", "gdbserver", "--port", str(self._port), "--no-wait"]
        if self._target:
            cmd.extend(["--target", self._target])

        logger.info("Starting pyOCD: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
            stderr = await self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(
                f"pyOCD exited immediately: {stderr.decode(errors='replace')[-500:]}"
            )
        except asyncio.TimeoutError:
            pass

        logger.info("pyOCD on port %d", self._port)
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
