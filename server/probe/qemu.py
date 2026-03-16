"""QEMU targets for testing without hardware.

Supports two modes:
- System emulation: full machine model with GDB stub (Cortex-M, MIPS, etc.)
- Userspace emulation: single binary via qemu-user with GDB stub (MIPS, ARM, RISC-V)
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """Wait for a TCP port to accept connections."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("localhost", port),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.2)
    return False


class QEMUTarget:
    """Manages a QEMU ARM system emulator with GDB stub."""

    def __init__(
        self,
        machine: str = "lm3s6965evb",
        kernel: str = "",
        port: int | None = None,
        cpu: str | None = None,
    ) -> None:
        self._machine = machine
        self._kernel = kernel
        self._port = port or _find_free_port()
        self._cpu = cpu
        self._process: asyncio.subprocess.Process | None = None

    @property
    def port(self) -> int:
        return self._port

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> int:
        if self.is_running():
            return self._port

        qemu_exe = None
        for candidate in ("qemu-system-arm", "qemu-system-gnuarmeclipse"):
            if shutil.which(candidate):
                qemu_exe = candidate
                break

        if not qemu_exe:
            raise FileNotFoundError(
                "QEMU ARM not found. Install with: sudo apt install qemu-system-arm"
            )

        cmd = [
            qemu_exe,
            "-machine", self._machine,
            "-nographic",
            "-gdb", f"tcp::{self._port}",
            "-S",  # Start halted
        ]

        if self._kernel:
            cmd.extend(["-kernel", self._kernel])

        if self._cpu:
            cmd.extend(["-cpu", self._cpu])

        logger.info("Starting QEMU: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            await asyncio.wait_for(self._process.wait(), timeout=1.0)
            stderr = await self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(
                f"QEMU exited immediately: {stderr.decode(errors='replace')[-500:]}"
            )
        except asyncio.TimeoutError:
            pass

        logger.info("QEMU on port %d (pid %d)", self._port, self._process.pid)
        return self._port

    async def stop(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._process = None
            return

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            self._process.kill()
        self._process = None
        logger.info("QEMU stopped")


# ── QEMU User-Mode Emulation ────────────────────────────────────────

# Architecture -> (qemu-user binary, gdb-multiarch arch set)
_QEMU_USER_MAP: dict[str, tuple[list[str], str]] = {
    "mipsel": (["qemu-mipsel-static", "qemu-mipsel"], "mips"),
    "mips": (["qemu-mips-static", "qemu-mips"], "mips"),
    "arm": (["qemu-arm-static", "qemu-arm"], "arm"),
    "aarch64": (["qemu-aarch64-static", "qemu-aarch64"], "aarch64"),
    "riscv32": (["qemu-riscv32-static", "qemu-riscv32"], "riscv:rv32"),
    "riscv64": (["qemu-riscv64-static", "qemu-riscv64"], "riscv:rv64"),
}


def _detect_elf_arch(binary_path: str) -> str | None:
    """Detect ELF architecture from binary header."""
    try:
        with open(binary_path, "rb") as f:
            magic = f.read(20)
            if magic[:4] != b"\x7fELF":
                return None
            ei_class = magic[4]  # 1=32-bit, 2=64-bit
            ei_data = magic[5]   # 1=LE, 2=BE
            e_machine = int.from_bytes(magic[18:20], "little" if ei_data == 1 else "big")

            arch_map = {
                (0x08, 1): "mipsel",      # MIPS LE
                (0x08, 2): "mips",         # MIPS BE
                (0x28, 1): "arm",          # ARM LE
                (0xB7, 1): "aarch64",      # AArch64
                (0xF3, 1): "riscv32" if ei_class == 1 else "riscv64",  # RISC-V
            }
            return arch_map.get((e_machine, ei_data))
    except (OSError, IndexError):
        return None


class QEMUUserTarget:
    """QEMU user-mode emulation: run a single foreign-arch binary with GDB stub.

    This is the approach used for debugging extracted firmware binaries
    (e.g., GL.iNet OpenWrt daemons, busybox utilities) without a full
    system emulator or physical hardware.

    Usage:
        target = QEMUUserTarget(
            binary="/path/to/squashfs-root/usr/sbin/gl_health",
            sysroot="/path/to/squashfs-root",
        )
        port = await target.start()
        # Then: gdb-multiarch -ex "target remote :port" binary
    """

    def __init__(
        self,
        binary: str,
        arch: str | None = None,
        sysroot: str | None = None,
        port: int | None = None,
        env: dict[str, str] | None = None,
        args: list[str] | None = None,
    ) -> None:
        self._binary = binary
        self._arch = arch or _detect_elf_arch(binary)
        self._sysroot = sysroot
        self._port = port or _find_free_port()
        self._env = env or {}
        self._args = args or []
        self._process: asyncio.subprocess.Process | None = None

        if not self._arch:
            raise ValueError(
                f"Cannot detect architecture of {binary}. "
                "Specify arch= explicitly (mipsel, arm, aarch64, riscv32, riscv64)."
            )

    @property
    def port(self) -> int:
        return self._port

    @property
    def arch(self) -> str | None:
        return self._arch

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> int:
        """Start the binary under QEMU user-mode with GDB stub.

        Returns the GDB port number.
        """
        if self.is_running():
            return self._port

        if not self._arch or self._arch not in _QEMU_USER_MAP:
            raise ValueError(f"Unsupported architecture: {self._arch}")

        candidates, _ = _QEMU_USER_MAP[self._arch]
        qemu_exe = None
        for candidate in candidates:
            if shutil.which(candidate):
                qemu_exe = candidate
                break

        if not qemu_exe:
            raise FileNotFoundError(
                f"QEMU user-mode for {self._arch} not found. "
                f"Searched: {', '.join(candidates)}. "
                f"Install with: sudo apt install qemu-user-static"
            )

        cmd = [qemu_exe, "-g", str(self._port)]

        # Set sysroot for shared library resolution
        if self._sysroot:
            cmd.extend(["-L", self._sysroot])

        # Environment variables
        for key, val in self._env.items():
            cmd.extend(["-E", f"{key}={val}"])

        # Add LD_LIBRARY_PATH for common firmware library locations
        if self._sysroot:
            lib_paths = []
            for subdir in ("lib", "usr/lib", "lib/mipsel-linux-gnu", "usr/lib/mipsel-linux-gnu"):
                candidate_path = Path(self._sysroot) / subdir
                if candidate_path.is_dir():
                    lib_paths.append(str(candidate_path))
            if lib_paths and "LD_LIBRARY_PATH" not in self._env:
                cmd.extend(["-E", f"LD_LIBRARY_PATH={':'.join(lib_paths)}"])

        cmd.append(self._binary)
        cmd.extend(self._args)

        logger.info("Starting QEMU user-mode: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # QEMU user-mode with -g halts before executing the first instruction,
        # waiting for GDB to connect. Verify the port is accepting connections.
        port_ready = await _wait_for_port(self._port, timeout=5.0)
        if not port_ready:
            # Check if process died
            if self._process.returncode is not None:
                stderr = b""
                if self._process.stderr:
                    stderr = await self._process.stderr.read()
                raise RuntimeError(
                    f"QEMU exited (code {self._process.returncode}): "
                    f"{stderr.decode(errors='replace')[-500:]}"
                )
            raise RuntimeError(
                f"QEMU started but GDB port {self._port} not accepting connections"
            )

        logger.info(
            "QEMU user-mode ready: %s on :%d (pid %d)",
            self._arch, self._port, self._process.pid,
        )
        return self._port

    async def stop(self) -> None:
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._process = None
            return

        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            self._process.kill()
        self._process = None
        logger.info("QEMU user-mode stopped")

    def gdb_arch_name(self) -> str:
        """Return the GDB architecture name for `set architecture`."""
        if self._arch:
            _, gdb_arch = _QEMU_USER_MAP.get(self._arch, ([], "auto"))
            return gdb_arch
        return "auto"


class QEMUSystemMIPS:
    """QEMU system-mode for MIPS targets (OpenWrt Malta).

    For debugging firmware that needs a full Linux environment
    (networking, init, multiple processes).
    """

    def __init__(
        self,
        kernel: str,
        rootfs: str | None = None,
        port: int | None = None,
        ssh_port: int | None = None,
        gdbserver_port: int = 2345,
        endian: str = "little",
        memory: str = "256M",
    ) -> None:
        self._kernel = kernel
        self._rootfs = rootfs
        self._port = port or _find_free_port()
        self._ssh_port = ssh_port or _find_free_port()
        self._gdbserver_port = gdbserver_port
        self._endian = endian
        self._memory = memory
        self._process: asyncio.subprocess.Process | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def ssh_port(self) -> int:
        return self._ssh_port

    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> int:
        if self.is_running():
            return self._port

        qemu_exe = "qemu-system-mipsel" if self._endian == "little" else "qemu-system-mips"
        if not shutil.which(qemu_exe):
            raise FileNotFoundError(
                f"{qemu_exe} not found. Install with: sudo apt install qemu-system-mips"
            )

        cmd = [
            qemu_exe,
            "-M", "malta",
            "-m", self._memory,
            "-kernel", self._kernel,
            "-nographic",
            "-gdb", f"tcp::{self._port}",
        ]

        if self._rootfs:
            cmd.extend([
                "-drive", f"file={self._rootfs},format=raw",
                "-append", "root=/dev/sda console=ttyS0",
            ])

        # Port forwards: SSH + gdbserver
        hostfwd = (
            f"hostfwd=tcp::{self._ssh_port}-:22,"
            f"hostfwd=tcp::{self._gdbserver_port}-:{self._gdbserver_port}"
        )
        cmd.extend([
            "-netdev", f"user,id=net0,{hostfwd}",
            "-device", "e1000,netdev=net0",
        ])

        logger.info("Starting QEMU MIPS system: %s", " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            await asyncio.wait_for(self._process.wait(), timeout=2.0)
            stderr = await self._process.stderr.read() if self._process.stderr else b""
            raise RuntimeError(
                f"QEMU exited immediately: {stderr.decode(errors='replace')[-500:]}"
            )
        except asyncio.TimeoutError:
            pass

        logger.info(
            "QEMU MIPS system on GDB:%d SSH:%d (pid %d)",
            self._port, self._ssh_port, self._process.pid,
        )
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
        logger.info("QEMU MIPS system stopped")
