"""Tests for QEMU user-mode emulation targets.

Adapted from GL.iNet firmware debugging workflow:
- Extract firmware binary
- Run under QEMU user-mode with GDB stub (-g port)
- Attach GDB-MI, verify connection and basic operations

These tests validate the QEMUUserTarget and QEMUSystemMIPS classes
without requiring actual firmware images. ELF arch detection and
command construction are tested with mocks and synthetic binaries.
"""

import asyncio
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from server.probe.qemu import (
    QEMUUserTarget,
    QEMUSystemMIPS,
    _detect_elf_arch,
    _QEMU_USER_MAP,
    _wait_for_port,
)


# ── ELF Architecture Detection ──────────────────────────────────────

def _make_elf_header(ei_class: int, ei_data: int, e_machine: int) -> bytes:
    """Build a minimal 20-byte ELF header for arch detection tests."""
    endian = "<" if ei_data == 1 else ">"
    header = b"\x7fELF"
    header += bytes([ei_class, ei_data, 1, 0])  # EI_CLASS, EI_DATA, EI_VERSION, EI_OSABI
    header += b"\x00" * 8                         # EI_ABIVERSION + padding
    header += struct.pack(f"{endian}H", 2)        # e_type = ET_EXEC
    header += struct.pack(f"{endian}H", e_machine) # e_machine
    return header


class TestELFArchDetection:
    def test_mipsel(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x08))  # 32-bit LE MIPS
        assert _detect_elf_arch(str(elf)) == "mipsel"

    def test_mips_big_endian(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 2, 0x08))  # 32-bit BE MIPS
        assert _detect_elf_arch(str(elf)) == "mips"

    def test_arm(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x28))  # 32-bit LE ARM
        assert _detect_elf_arch(str(elf)) == "arm"

    def test_aarch64(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(2, 1, 0xB7))  # 64-bit LE AArch64
        assert _detect_elf_arch(str(elf)) == "aarch64"

    def test_riscv32(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0xF3))  # 32-bit LE RISC-V
        assert _detect_elf_arch(str(elf)) == "riscv32"

    def test_riscv64(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(2, 1, 0xF3))  # 64-bit LE RISC-V
        assert _detect_elf_arch(str(elf)) == "riscv64"

    def test_not_elf(self, tmp_path):
        notelf = tmp_path / "binary"
        notelf.write_bytes(b"not an elf file at all")
        assert _detect_elf_arch(str(notelf)) is None

    def test_nonexistent_file(self):
        assert _detect_elf_arch("/nonexistent/path/binary") is None

    def test_truncated_elf(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(b"\x7fELF\x01")  # Too short
        assert _detect_elf_arch(str(elf)) is None


# ── QEMUUserTarget Construction ──────────────────────────────────────

class TestQEMUUserTargetConstruction:
    def test_auto_detect_arch(self, tmp_path):
        elf = tmp_path / "gl_health"
        elf.write_bytes(_make_elf_header(1, 1, 0x08))  # MIPS LE
        target = QEMUUserTarget(binary=str(elf))
        assert target.arch == "mipsel"

    def test_explicit_arch(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(b"not elf but arch is explicit")
        target = QEMUUserTarget(binary=str(elf), arch="arm")
        assert target.arch == "arm"

    def test_unknown_arch_raises(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(b"not elf")
        with pytest.raises(ValueError, match="Cannot detect architecture"):
            QEMUUserTarget(binary=str(elf))

    def test_port_allocation(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x28))
        target = QEMUUserTarget(binary=str(elf), port=9999)
        assert target.port == 9999

    def test_auto_port(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x28))
        target = QEMUUserTarget(binary=str(elf))
        assert target.port > 0

    def test_gdb_arch_name(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x08))
        target = QEMUUserTarget(binary=str(elf))
        assert target.gdb_arch_name() == "mips"

    def test_gdb_arch_name_arm(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x28))
        target = QEMUUserTarget(binary=str(elf))
        assert target.gdb_arch_name() == "arm"


# ── QEMUUserTarget Startup (Mocked) ─────────────────────────────────

class TestQEMUUserTargetStartup:
    @pytest.mark.asyncio
    async def test_missing_qemu_binary(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x08))
        target = QEMUUserTarget(binary=str(elf))

        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="QEMU user-mode"):
                await target.start()

    @pytest.mark.asyncio
    async def test_start_constructs_correct_command(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x08))  # MIPS LE
        sysroot = tmp_path / "squashfs-root"
        sysroot.mkdir()
        (sysroot / "lib").mkdir()
        (sysroot / "usr" / "lib").mkdir(parents=True)

        target = QEMUUserTarget(
            binary=str(elf),
            sysroot=str(sysroot),
            port=4567,
            env={"CONFIG_FILE": "/etc/gl.conf"},
            args=["--daemon"],
        )

        captured_cmd = []

        async def mock_exec(*cmd, **kwargs):
            captured_cmd.extend(cmd)
            proc = AsyncMock()
            proc.returncode = None
            proc.pid = 12345
            proc.stdout = AsyncMock()
            proc.stderr = AsyncMock()
            proc.stderr.read = AsyncMock(return_value=b"")
            return proc

        with patch("shutil.which", return_value="/usr/bin/qemu-mipsel-static"):
            with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
                with patch("server.probe.qemu._wait_for_port", return_value=True):
                    port = await target.start()

        assert port == 4567
        assert "qemu-mipsel-static" in captured_cmd[0]
        assert "-g" in captured_cmd
        assert "4567" in captured_cmd
        assert "-L" in captured_cmd
        assert str(sysroot) in captured_cmd
        assert str(elf) in captured_cmd
        assert "--daemon" in captured_cmd
        # Env vars
        env_idx = [i for i, x in enumerate(captured_cmd) if x == "-E"]
        env_vals = [captured_cmd[i + 1] for i in env_idx]
        assert any("CONFIG_FILE=/etc/gl.conf" in v for v in env_vals)

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, tmp_path):
        elf = tmp_path / "binary"
        elf.write_bytes(_make_elf_header(1, 1, 0x28))
        target = QEMUUserTarget(binary=str(elf))
        await target.stop()  # Should not raise
        assert not target.is_running()


# ── QEMUSystemMIPS Construction ──────────────────────────────────────

class TestQEMUSystemMIPS:
    def test_construction(self):
        target = QEMUSystemMIPS(
            kernel="/path/to/vmlinux.elf",
            rootfs="/path/to/rootfs.img",
        )
        assert target.port > 0
        assert target.ssh_port > 0

    def test_explicit_ports(self):
        target = QEMUSystemMIPS(
            kernel="/path/to/vmlinux.elf",
            port=3333,
            ssh_port=2222,
        )
        assert target.port == 3333
        assert target.ssh_port == 2222

    @pytest.mark.asyncio
    async def test_missing_qemu_system(self):
        target = QEMUSystemMIPS(kernel="/path/to/vmlinux.elf")
        with patch("shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError, match="qemu-system-mipsel"):
                await target.start()

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        target = QEMUSystemMIPS(kernel="/path/to/vmlinux.elf")
        await target.stop()  # Should not raise


# ── QEMU User Map Coverage ──────────────────────────────────────────

class TestQEMUUserMap:
    def test_all_archs_have_candidates(self):
        for arch, (candidates, gdb_arch) in _QEMU_USER_MAP.items():
            assert len(candidates) > 0, f"No QEMU candidates for {arch}"
            assert gdb_arch, f"No GDB arch for {arch}"

    def test_supported_architectures(self):
        assert "mipsel" in _QEMU_USER_MAP
        assert "mips" in _QEMU_USER_MAP
        assert "arm" in _QEMU_USER_MAP
        assert "aarch64" in _QEMU_USER_MAP
        assert "riscv32" in _QEMU_USER_MAP
        assert "riscv64" in _QEMU_USER_MAP


# ── Integration Pattern: GL.iNet Firmware Debug Flow ─────────────────

class TestGLiNetDebugPattern:
    """Test the full GL.iNet firmware debug pattern without actual firmware.

    This validates that the classes compose correctly for the workflow:
    1. Detect architecture from extracted binary
    2. Start QEMU user-mode with sysroot
    3. GDB connects to the stub port
    4. Set breakpoints and debug
    """

    def test_firmware_debug_workflow_setup(self, tmp_path):
        """Verify the full setup chain works with synthetic data."""
        # Simulate extracted firmware rootfs
        rootfs = tmp_path / "squashfs-root"
        rootfs.mkdir()
        (rootfs / "lib").mkdir()
        (rootfs / "usr" / "lib").mkdir(parents=True)
        (rootfs / "usr" / "sbin").mkdir(parents=True)

        # Create a fake MIPS LE binary
        binary = rootfs / "usr" / "sbin" / "gl_health"
        binary.write_bytes(_make_elf_header(1, 1, 0x08) + b"\x00" * 100)

        # Step 1: detect architecture
        arch = _detect_elf_arch(str(binary))
        assert arch == "mipsel"

        # Step 2: construct target
        target = QEMUUserTarget(
            binary=str(binary),
            sysroot=str(rootfs),
        )
        assert target.arch == "mipsel"
        assert target.gdb_arch_name() == "mips"

        # Step 3: GDB connection params would be:
        gdb_target = f"localhost:{target.port}"
        gdb_sysroot = str(rootfs)
        assert target.port > 0
        assert Path(gdb_sysroot).is_dir()

    def test_arm_firmware_workflow(self, tmp_path):
        """Same workflow for ARM firmware (GL.iNet MT7622/Filogic)."""
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        (rootfs / "lib").mkdir()

        binary = rootfs / "target_daemon"
        binary.write_bytes(_make_elf_header(1, 1, 0x28) + b"\x00" * 100)

        arch = _detect_elf_arch(str(binary))
        assert arch == "arm"

        target = QEMUUserTarget(binary=str(binary), sysroot=str(rootfs))
        assert target.arch == "arm"
        assert target.gdb_arch_name() == "arm"

    def test_system_mode_openwrt_malta(self):
        """Verify QEMUSystemMIPS construction for full OpenWrt emulation."""
        target = QEMUSystemMIPS(
            kernel="bin/targets/malta/le/openwrt-malta-le-vmlinux.elf",
            rootfs="bin/targets/malta/le/openwrt-malta-le-ext4-rootfs.img",
            memory="256M",
        )
        assert target.port > 0
        assert target.ssh_port > 0
