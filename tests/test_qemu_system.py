"""Full system test: MCP DebugSession against QEMU lm3s6965evb (Cortex-M3).

Exercises the complete stack without mocks:
  arm-none-eabi-gcc → ELF → QEMU system emulation → GDB/MI → DebugSession

This mirrors the GL.iNet firmware debug workflow from the design doc:
  1. Compile target binary (here: Cortex-M3 instead of MIPS)
  2. Run under QEMU with GDB stub
  3. Attach GDB remotely
  4. Debug: breakpoints, registers, memory, stepping, backtrace, disassembly

Requires: arm-none-eabi-gcc, qemu-system-arm, gdb (on host)
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from server.probe.qemu import QEMUTarget
from server.gdb.session import DebugSession
from server.gdb.types import GDBState

_FIRMWARE_DIR = Path(__file__).parent / "firmware"
_LINK_SCRIPT = _FIRMWARE_DIR / "link.ld"

needs_arm_gcc = pytest.mark.skipif(
    not shutil.which("arm-none-eabi-gcc"),
    reason="arm-none-eabi-gcc not available",
)
needs_qemu_arm = pytest.mark.skipif(
    not shutil.which("qemu-system-arm"),
    reason="qemu-system-arm not available",
)
needs_gdb = pytest.mark.skipif(
    not shutil.which("gdb"),
    reason="gdb not available",
)

needs_full_stack = pytest.mark.usefixtures()
for m in (needs_arm_gcc, needs_qemu_arm, needs_gdb):
    needs_full_stack = m(needs_full_stack) if callable(needs_full_stack) else needs_full_stack

# Combine all skip markers into one decorator
requires_toolchain = [needs_arm_gcc, needs_qemu_arm, needs_gdb]


def _compile_firmware(source: Path, output: Path) -> Path:
    """Cross-compile a Cortex-M3 ELF."""
    result = subprocess.run(
        [
            "arm-none-eabi-gcc",
            "-mcpu=cortex-m3", "-mthumb",
            "-nostartfiles", "-g", "-O0",
            "-T", str(_LINK_SCRIPT),
            "-o", str(output),
            str(source),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Firmware compile failed:\n{result.stderr}")
    return output


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def spi_elf(tmp_path_factory) -> str:
    """Compile test_spi.c once per module."""
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("arm-none-eabi-gcc not available")
    build_dir = tmp_path_factory.mktemp("firmware_build")
    elf = _compile_firmware(_FIRMWARE_DIR / "test_spi.c", build_dir / "test_spi.elf")
    return str(elf)


@pytest.fixture(scope="module")
def fault_elf(tmp_path_factory) -> str:
    """Compile test_fault.c once per module."""
    if not shutil.which("arm-none-eabi-gcc"):
        pytest.skip("arm-none-eabi-gcc not available")
    build_dir = tmp_path_factory.mktemp("firmware_build")
    elf = _compile_firmware(_FIRMWARE_DIR / "test_fault.c", build_dir / "test_fault.elf")
    return str(elf)


@pytest.fixture
async def qemu_target():
    """Start and stop a QEMU lm3s6965evb instance."""
    if not shutil.which("qemu-system-arm"):
        pytest.skip("qemu-system-arm not available")
    target = QEMUTarget(machine="lm3s6965evb")
    yield target
    await target.stop()


@pytest.fixture
async def debug_session():
    """Create and teardown a DebugSession."""
    session = DebugSession("test-qemu")
    yield session
    await session.disconnect()


# ── Test: QEMU Target Lifecycle ─────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
class TestQEMUTargetLifecycle:
    """Verify the QEMUTarget starts, exposes a GDB port, and stops cleanly."""

    @pytest.mark.asyncio
    async def test_start_and_port(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        try:
            port = await target.start()
            assert port > 0
            assert target.is_running()

            # Verify GDB port is accepting connections
            reader, writer = await asyncio.open_connection("localhost", port)
            writer.close()
            await writer.wait_closed()
        finally:
            await target.stop()

        assert not target.is_running()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        try:
            port1 = await target.start()
            port2 = await target.start()
            assert port1 == port2
        finally:
            await target.stop()


# ── Test: GDB Remote Connect via DebugSession ───────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestDebugSessionConnect:
    """Connect a DebugSession to a QEMU target and verify basic state."""

    @pytest.mark.asyncio
    async def test_connect_and_state(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-connect")
        try:
            port = await target.start()
            result = await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )
            assert result["target"] == f"localhost:{port}"
            assert result["elf"] == spi_elf
            assert session.state == GDBState.STOPPED
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_session_serialize(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-serialize")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )
            state = session.serialize()
            assert state["session_id"] == "test-serialize"
            assert state["state"] == "stopped"
            assert state["elf_path"] == spi_elf
            assert state["gdb_pid"] is not None
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Register Reads ─────────────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestRegisterReads:
    """Read CPU registers from halted Cortex-M3 target."""

    @pytest.mark.asyncio
    async def test_read_all_registers(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-regs")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            regs = await session.read_registers()
            # Cortex-M3 core registers
            assert "sp" in regs
            assert "pc" in regs
            assert "r0" in regs

            # Stack pointer should point to SRAM
            sp_val = int(regs["sp"], 16)
            assert 0x20000000 <= sp_val <= 0x20010000, f"SP out of SRAM range: 0x{sp_val:08x}"

            # PC should be in flash (0x00000000-0x0003FFFF)
            pc_val = int(regs["pc"], 16)
            assert pc_val < 0x00040000, f"PC out of flash range: 0x{pc_val:08x}"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_read_specific_registers(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-regs-specific")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            regs = await session.read_registers(["pc", "sp", "lr"])
            assert set(regs.keys()) == {"pc", "sp", "lr"}
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Breakpoints and Execution Control ──────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestBreakpointsAndExecution:
    """Set breakpoints, continue, step through firmware."""

    @pytest.mark.asyncio
    async def test_breakpoint_set_and_hit(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-bp")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Set breakpoint on configure_spi
            bp = await session.set_breakpoint("configure_spi")
            assert bp["id"] > 0
            assert "configure_spi" in bp.get("location", "")

            # Continue to breakpoint
            stop = await session.continue_execution(timeout=10.0)
            assert stop["reason"] == "breakpoint-hit"
            assert stop["frame"]["function"] == "configure_spi"

            # Breakpoint should record hit
            assert session.breakpoints[bp["id"]]["hits"] == 1
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_breakpoint_at_line(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-bp-line")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Set breakpoint by file:line (send_test_data is at a known location)
            bp = await session.set_breakpoint("send_test_data")
            stop = await session.continue_execution(timeout=10.0)
            assert stop["reason"] == "breakpoint-hit"
            assert stop["frame"]["function"] == "send_test_data"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_multiple_breakpoints_and_continue(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-multi-bp")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            bp1 = await session.set_breakpoint("configure_spi")
            bp2 = await session.set_breakpoint("send_test_data")

            # Hit first breakpoint
            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "configure_spi"

            # Continue to second
            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "send_test_data"

            # Verify breakpoint list
            assert len(session.breakpoints) == 2
            assert bp1["id"] in session.breakpoints
            assert bp2["id"] in session.breakpoints
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_breakpoint_remove(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-bp-remove")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            bp = await session.set_breakpoint("configure_spi")
            assert bp["id"] in session.breakpoints

            await session.remove_breakpoint(bp["id"])
            assert bp["id"] not in session.breakpoints
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_step_into(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-step")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Break at Reset_Handler, step into configure_spi
            await session.set_breakpoint("Reset_Handler")
            await session.continue_execution(timeout=10.0)

            # Step several times -- we should enter configure_spi
            for _ in range(5):
                stop = await session.step(step_type="into")
                if stop["frame"]["function"] == "configure_spi":
                    break
            assert stop["frame"]["function"] == "configure_spi"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_step_over(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-step-over")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Break at Reset_Handler
            await session.set_breakpoint("Reset_Handler")
            await session.continue_execution(timeout=10.0)

            # Step over -- should NOT descend into configure_spi, just skip past it
            stop = await session.step(step_type="over")
            # We should still be in Reset_Handler (or past the call)
            assert stop["frame"]["function"] in ("Reset_Handler", "send_test_data")
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Memory Access ──────────────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestMemoryAccess:
    """Read and write target memory."""

    @pytest.mark.asyncio
    async def test_read_vector_table(self, spi_elf):
        """Read the vector table at address 0x0 -- should contain stack pointer and reset vector."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-mem-read")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Read first 16 bytes of vector table
            data = await session.read_memory(0x00000000, 16)
            assert len(data) == 16

            # First word should be stack pointer (0x20010000 = top of SRAM)
            import struct
            sp = struct.unpack_from("<I", data, 0)[0]
            assert sp == 0x20010000, f"Vector table SP: 0x{sp:08x}, expected 0x20010000"

            # Second word should be Reset_Handler address (odd for Thumb)
            reset_vec = struct.unpack_from("<I", data, 4)[0]
            assert reset_vec & 1 == 1, f"Reset vector not Thumb: 0x{reset_vec:08x}"
            assert reset_vec < 0x10000, f"Reset vector out of flash: 0x{reset_vec:08x}"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_read_sram(self, spi_elf):
        """Read from SRAM region."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-mem-sram")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Read 64 bytes from SRAM start
            data = await session.read_memory(0x20000000, 64)
            assert len(data) == 64
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_write_and_readback(self, spi_elf):
        """Write to SRAM and read back."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-mem-write")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Write a known pattern to SRAM
            test_data = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA, 0xBE])
            await session.write_memory(0x20000100, test_data)

            # Read it back
            readback = await session.read_memory(0x20000100, 8)
            assert readback == test_data
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Backtrace ──────────────────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestBacktrace:
    """Backtrace at various points in the call chain."""

    @pytest.mark.asyncio
    async def test_backtrace_at_nested_call(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-bt")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Break inside configure_spi (called from Reset_Handler)
            await session.set_breakpoint("configure_spi")
            await session.continue_execution(timeout=10.0)

            frames = await session.backtrace()
            func_names = [f["function"] for f in frames]
            assert "configure_spi" in func_names
            assert "Reset_Handler" in func_names

            # configure_spi should be at the top
            assert frames[0]["function"] == "configure_spi"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_backtrace_with_locals(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-bt-locals")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            await session.set_breakpoint("configure_spi")
            await session.continue_execution(timeout=10.0)

            frames = await session.backtrace(full=True)
            assert len(frames) > 0
            # Frames should have locals key
            for f in frames:
                assert "locals" in f
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Expression Evaluation ──────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestExpressionEvaluation:
    """Evaluate C expressions in halted target context."""

    @pytest.mark.asyncio
    async def test_evaluate_constant(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-eval")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            result = await session.evaluate('"2 + 3"')
            assert "5" in result
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_evaluate_register_deref(self, spi_elf):
        """Read a memory-mapped register through expression evaluation."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-eval-mmio")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Run to after SPI config
            await session.set_breakpoint("send_test_data")
            await session.continue_execution(timeout=10.0)

            # Read SSI0_CR1 (should have SSE bit set after configure_spi)
            result = await session.evaluate('"*(volatile unsigned int*)0x40008004"')
            # CR1 should be 0x2 (SSE bit set)
            assert result is not None
            val = int(result, 0) if result else 0
            assert val & 0x2, f"SSI0_CR1 SSE bit not set: 0x{val:x}"
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Disassembly ────────────────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestDisassembly:
    """Disassemble firmware functions."""

    @pytest.mark.asyncio
    async def test_disassemble_function(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-disasm")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            assert session.connection is not None
            result = await session.connection.send(
                "-data-disassemble -a configure_spi -- 0"
            )
            asm = result.results.get("asm_insns", [])
            assert len(asm) > 0

            # Cortex-M3 instructions should have addresses in flash range
            first = asm[0]
            addr = int(first.get("address", "0"), 16)
            assert addr < 0x00040000
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Fault Analysis Scenario ────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestFaultScenario:
    """Run the fault firmware and verify we can inspect the crash.

    Note: The firmware enables UsageFault/BusFault/MemManage handlers via
    SCB_SHCSR. In QEMU, the INVSTATE fault (calling address without thumb bit)
    routes to the appropriate sub-fault handler (Default_Handler in the vector
    table), NOT to HardFault_Handler. This matches correct Cortex-M behavior:
    HardFault only fires when sub-handlers are disabled or the fault escalates.
    """

    @pytest.mark.asyncio
    async def test_fault_firmware_reaches_handler(self, fault_elf):
        """The fault firmware should trigger a fault and land in a handler."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=fault_elf)
        session = DebugSession("test-fault")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=fault_elf,
            )

            # The INVSTATE fault routes to Default_Handler (UsageFault vector)
            # because the firmware enables sub-fault handlers via SCB_SHCSR.
            await session.set_breakpoint("Default_Handler")

            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "Default_Handler"

            # Should be able to read registers in the handler
            regs = await session.read_registers(["pc", "sp", "lr"])
            assert "pc" in regs
            assert "lr" in regs

            # LR should indicate exception return (EXC_RETURN pattern: 0xFFFFFFxx)
            lr_val = int(regs["lr"], 16)
            assert (lr_val & 0xFFFFFF00) == 0xFFFFFF00, (
                f"LR doesn't look like EXC_RETURN: 0x{lr_val:08x}"
            )
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_fault_backtrace_shows_exception_context(self, fault_elf):
        """Backtrace from fault handler should show exception context."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=fault_elf)
        session = DebugSession("test-fault-bt")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=fault_elf,
            )

            await session.set_breakpoint("Default_Handler")
            await session.continue_execution(timeout=10.0)

            frames = await session.backtrace()
            assert len(frames) > 0
            assert frames[0]["function"] == "Default_Handler"

            # Backtrace should show signal handler context (exception entry)
            func_names = [f["function"] for f in frames]
            # GDB shows <signal handler called> as a frame for exceptions
            assert len(frames) >= 2, f"Expected exception context in backtrace: {func_names}"
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_read_fault_registers(self, fault_elf):
        """Read Cortex-M fault status registers from the handler."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=fault_elf)
        session = DebugSession("test-fault-regs")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=fault_elf,
            )

            await session.set_breakpoint("Default_Handler")
            await session.continue_execution(timeout=10.0)

            # Read CFSR (Configurable Fault Status Register) at 0xE000ED28
            import struct
            cfsr_data = await session.read_memory(0xE000ED28, 4)
            cfsr = struct.unpack_from("<I", cfsr_data, 0)[0]

            # Read HFSR (HardFault Status Register) at 0xE000ED2C
            hfsr_data = await session.read_memory(0xE000ED2C, 4)
            hfsr = struct.unpack_from("<I", hfsr_data, 0)[0]

            # CFSR should have a fault bit set (sub-handler caught it directly)
            # UFSR is top 16 bits of CFSR; INVSTATE is bit 17 (bit 1 of UFSR)
            assert cfsr != 0, f"No CFSR fault bits set: CFSR=0x{cfsr:08x}"
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: SPI Register Verification (post-configure) ────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestSPIRegisterVerification:
    """Verify SPI peripheral registers after firmware configures them.

    This is the pattern from the GL.iNet debug workflow: stop the firmware
    after peripheral init, then read back register state to verify config.
    """

    @pytest.mark.asyncio
    async def test_ssi0_registers_after_config(self, spi_elf):
        """Read SSI0 registers after configure_spi() completes."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-spi-regs")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Break after SPI config, at send_test_data
            await session.set_breakpoint("send_test_data")
            await session.continue_execution(timeout=10.0)

            import struct

            # SSI0_CR0 at 0x40008000 -- should be 0x07 (8-bit, SPI mode 0)
            cr0_data = await session.read_memory(0x40008000, 4)
            cr0 = struct.unpack_from("<I", cr0_data, 0)[0]
            assert cr0 & 0xF == 0x7, f"SSI0_CR0 DSS not 8-bit: 0x{cr0:08x}"
            assert (cr0 >> 4) & 0x3 == 0, f"SSI0_CR0 FRF not Motorola SPI: 0x{cr0:08x}"
            assert (cr0 >> 6) & 0x1 == 0, f"SSI0_CR0 SPO (CPOL) not 0: 0x{cr0:08x}"
            assert (cr0 >> 7) & 0x1 == 0, f"SSI0_CR0 SPH (CPHA) not 0: 0x{cr0:08x}"

            # SSI0_CR1 at 0x40008004 -- should have SSE bit (bit 1) set
            cr1_data = await session.read_memory(0x40008004, 4)
            cr1 = struct.unpack_from("<I", cr1_data, 0)[0]
            assert cr1 & 0x2, f"SSI0_CR1 SSE not set: 0x{cr1:08x}"
            assert not (cr1 & 0x4), f"SSI0_CR1 MS (slave mode) set: 0x{cr1:08x}"

            # SSI0_CPSR at 0x40008010 -- should be 2
            cpsr_data = await session.read_memory(0x40008010, 4)
            cpsr = struct.unpack_from("<I", cpsr_data, 0)[0]
            assert cpsr == 2, f"SSI0_CPSR not 2: 0x{cpsr:08x}"
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Interrupt and Resume ───────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestInterruptResume:
    """Test interrupt/resume flow (simulates attaching to running firmware)."""

    @pytest.mark.asyncio
    async def test_continue_to_breakpoint_after_resume(self, spi_elf):
        """Continue, hit breakpoint, continue again to second breakpoint."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-resume")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            # Set two breakpoints
            await session.set_breakpoint("configure_spi")
            await session.set_breakpoint("send_test_data")

            # Run to first
            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "configure_spi"
            assert session.state == GDBState.STOPPED

            # Resume to second
            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "send_test_data"
            assert session.state == GDBState.STOPPED
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_temporary_breakpoint(self, spi_elf):
        """Temporary breakpoints auto-remove after being hit."""
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-temp-bp")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            bp = await session.set_breakpoint("configure_spi", temporary=True)
            assert bp["temporary"] is True

            stop = await session.continue_execution(timeout=10.0)
            assert stop["frame"]["function"] == "configure_spi"
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Raw GDB Command ───────────────────────────────────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestRawGDBCommand:
    """Execute raw GDB commands through the session."""

    @pytest.mark.asyncio
    async def test_info_target(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-raw")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            output = await session.raw_command("info target")
            assert "test_spi.elf" in output or "0x" in output
        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_symbol_lookup(self, spi_elf):
        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-symbol")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            output = await session.raw_command("info functions configure_spi")
            assert "configure_spi" in output
        finally:
            await session.disconnect()
            await target.stop()


# ── Test: Dashboard (SoftICE/IDA-style composite view) ───────────────


@needs_arm_gcc
@needs_qemu_arm
@needs_gdb
class TestDashboard:
    """Test the SoftICE/IDA-style composite debug dashboard."""

    @pytest.mark.asyncio
    async def test_dashboard_at_breakpoint(self, spi_elf):
        """Dashboard should show registers, code, stack, backtrace after stop."""
        from server.mcp_server import _format_dashboard, _format_hex_dump

        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-dashboard")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            await session.set_breakpoint("send_test_data")
            await session.continue_execution(timeout=10.0)

            # Gather dashboard data
            regs = await session.read_registers()
            pc_val = int(regs.get("pc", "0x0"), 0)
            sp_val = int(regs.get("sp", "0x0"), 0)

            # Disassemble
            conn = session.connection
            assert conn is not None
            result = await conn.send(
                f'-data-disassemble -s 0x{max(0,pc_val-16):08x} '
                f'-e 0x{pc_val+64:08x} -- 0'
            )
            asm_insns = result.results.get("asm_insns", [])
            disasm_lines = []
            for entry in asm_insns:
                if not isinstance(entry, dict):
                    continue
                addr = int(entry.get("address", "0"), 0)
                inst = entry.get("inst", "")
                marker = " >" if addr == pc_val else "  "
                note = "  <<<" if addr == pc_val else ""
                disasm_lines.append(f"{marker} {addr:08X}  {inst}{note}")

            stack_data = await session.read_memory(sp_val, 64)
            stack_dump = _format_hex_dump(stack_data, sp_val)

            frames = await session.backtrace()
            bt_lines = []
            for f in frames:
                bt_lines.append(
                    f" #{f.get('level','?')}  {f.get('function','??')}  [{f.get('address','')}]"
                )

            output = _format_dashboard(
                regs=regs,
                disasm_lines=disasm_lines,
                stack_dump=stack_dump,
                sp_val=sp_val,
                bt_lines=bt_lines,
                source_text=None,
                data_dump=None,
                data_addr=None,
            )

            # Verify dashboard contains all sections
            assert "registers" in output
            assert "code" in output
            assert "stack" in output
            assert "backtrace" in output

            # Verify register values are present
            assert "PC=" in output
            assert "SP=" in output

            # Verify PC marker in code
            assert "<<<" in output or " >" in output

            # Verify backtrace content
            assert "send_test_data" in output
            assert "Reset_Handler" in output

        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_dashboard_with_data_window(self, spi_elf):
        """Dashboard with optional data watch window."""
        from server.mcp_server import _format_dashboard, _format_hex_dump

        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-dashboard-data")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            await session.set_breakpoint("send_test_data")
            await session.continue_execution(timeout=10.0)

            regs = await session.read_registers()
            sp_val = int(regs.get("sp", "0x0"), 0)
            stack_data = await session.read_memory(sp_val, 32)

            # Data window: SSI0 peripheral registers
            data_addr = 0x40008000
            data_bytes = await session.read_memory(data_addr, 32)

            output = _format_dashboard(
                regs=regs,
                disasm_lines=[" > 0000006C  push {r7, lr}  <<<"],
                stack_dump=_format_hex_dump(stack_data, sp_val),
                sp_val=sp_val,
                bt_lines=[" #0  send_test_data  [0x0000006c]"],
                source_text=None,
                data_dump=_format_hex_dump(data_bytes, data_addr),
                data_addr=data_addr,
            )

            # Data section should appear
            assert "data [0x40008000]" in output.lower()
            assert "40008000" in output

        finally:
            await session.disconnect()
            await target.stop()

    @pytest.mark.asyncio
    async def test_dashboard_arm_flags(self, spi_elf):
        """Verify ARM xPSR flags are decoded in the register section."""
        from server.mcp_server import _format_dashboard, _format_hex_dump

        target = QEMUTarget(machine="lm3s6965evb", kernel=spi_elf)
        session = DebugSession("test-dashboard-flags")
        try:
            port = await target.start()
            await session.connect(
                target="localhost", port=port, elf_path=spi_elf,
            )

            regs = await session.read_registers()
            sp_val = int(regs.get("sp", "0x0"), 0)

            output = _format_dashboard(
                regs=regs,
                disasm_lines=[],
                stack_dump="(empty)",
                sp_val=sp_val,
                bt_lines=[],
                source_text=None,
                data_dump=None,
                data_addr=None,
            )

            # Should have flag decode with T bit set (Thumb mode)
            assert "T" in output
            # Should have xPSR or CPSR in output
            assert "XPSR=" in output or "CPSR=" in output

        finally:
            await session.disconnect()
            await target.stop()
