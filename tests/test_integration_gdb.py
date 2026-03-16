"""Real end-to-end integration test using native GDB against a local binary.

Proves the MI parser, connection, and session layers work against a real
GDB process -- not mocks. Requires `gcc` and `gdb` on the host.

The test compiles tests/fixtures/test_target.c to a temp directory, spawns
GDB via MIConnection, and exercises breakpoints, register reads, memory
reads, expression evaluation, stepping, backtrace, and disassembly.
"""

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from server.gdb.mi_connection import MIConnection
from server.gdb.types import GDBState, MIAsyncRecord

_FIXTURES = Path(__file__).parent / "fixtures"
_SOURCE = _FIXTURES / "test_target.c"

needs_gcc = pytest.mark.skipif(
    not shutil.which("gcc"), reason="gcc not available"
)
needs_gdb = pytest.mark.skipif(
    not shutil.which("gdb"), reason="gdb not available"
)


async def _wait_for_stop(conn: MIConnection, timeout: float = 10.0) -> MIAsyncRecord:
    """Drain the event queue until we get an exec.stopped event."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        event = await asyncio.wait_for(
            conn.get_event_queue().get(), timeout=timeout
        )
        if event.record_type == "exec" and event.async_class == "stopped":
            return event
    raise TimeoutError("Never received exec.stopped event")


@pytest.fixture(scope="module")
def test_binary(tmp_path_factory):
    """Compile test_target.c once per module."""
    if not shutil.which("gcc"):
        pytest.skip("gcc not available")
    build_dir = tmp_path_factory.mktemp("build")
    elf = build_dir / "test_target"
    result = subprocess.run(
        ["gcc", "-g", "-O0", "-o", str(elf), str(_SOURCE)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"Compile failed: {result.stderr}")
    return str(elf)


@needs_gcc
@needs_gdb
class TestGDBIntegration:
    """Full GDB/MI integration against a real GDB process."""

    @pytest.mark.asyncio
    async def test_start_and_stop(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        assert conn.state == GDBState.CONNECTED
        assert conn.pid is not None
        assert conn.mi_version in ("mi3", "mi2")
        await conn.stop()
        assert conn.state == GDBState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_load_binary(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            result = await conn.send(f"-file-exec-and-symbols {test_binary}")
            assert result.result_class == "done"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_breakpoint_set_and_hit(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")

            result = await conn.send("-break-insert main")
            bkpt = result.results.get("bkpt", {})
            assert bkpt.get("number") == "1"
            assert "test_target.c" in bkpt.get("file", "")

            await conn.send("-exec-run")
            event = await _wait_for_stop(conn)
            assert event.results.get("reason") == "breakpoint-hit"
            frame = event.results.get("frame", {})
            assert frame.get("func") == "main"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_register_read(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert main")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            names_result = await conn.send("-data-list-register-names")
            reg_names = names_result.results.get("register-names", [])
            assert len(reg_names) > 0

            vals_result = await conn.send("-data-list-register-values x")
            reg_values = vals_result.results.get("register-values", [])
            assert len(reg_values) > 0
            # Every value entry should have number and value keys
            for rv in reg_values:
                assert "number" in rv
                assert "value" in rv
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_memory_read(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert main")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            # Get address of global_var
            addr_result = await conn.send(
                '-data-evaluate-expression "&global_var"'
            )
            addr_str = addr_result.results.get("value", "")
            assert "0x" in addr_str

            # Extract address
            hex_part = addr_str.split("0x")[-1].split()[0].strip(">")
            addr = int(hex_part, 16)

            # Read 4 bytes
            mem_result = await conn.send(
                f"-data-read-memory-bytes 0x{addr:x} 4"
            )
            memory = mem_result.results.get("memory", [])
            assert len(memory) > 0
            contents = memory[0].get("contents", "")
            # 0xDEADBEEF little-endian = efbeadde
            assert contents.lower() == "efbeadde"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_expression_evaluation(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert middle_func")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            a = await conn.send('-data-evaluate-expression "a"')
            assert a.results.get("value") == "10"

            b = await conn.send('-data-evaluate-expression "b"')
            assert b.results.get("value") == "20"

            s = await conn.send('-data-evaluate-expression "a + b"')
            assert s.results.get("value") == "30"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_step_and_backtrace(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert middle_func")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            # Step into inner_func
            await conn.send("-exec-step")
            event = await _wait_for_stop(conn)
            frame = event.results.get("frame", {})
            assert frame.get("func") == "inner_func"

            # Backtrace should show full chain
            bt = await conn.send("-stack-list-frames")
            stack = bt.results.get("stack", [])
            funcs = []
            for entry in stack:
                f = entry.get("frame", entry) if isinstance(entry, dict) else {}
                if isinstance(f, dict):
                    funcs.append(f.get("func", ""))
            assert "inner_func" in funcs
            assert "middle_func" in funcs
            assert "main" in funcs
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_continue_to_second_breakpoint(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert main")
            await conn.send("-break-insert inner_func")
            await conn.send("-exec-run")

            # Hit main first
            event = await _wait_for_stop(conn)
            assert event.results.get("frame", {}).get("func") == "main"

            # Continue to inner_func
            await conn.send("-exec-continue")
            event = await _wait_for_stop(conn)
            assert event.results.get("frame", {}).get("func") == "inner_func"
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_disassemble(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert inner_func")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            result = await conn.send("-data-disassemble -a inner_func -- 0")
            asm = result.results.get("asm_insns", [])
            assert len(asm) > 0
            # Each instruction should have address and inst
            first = asm[0]
            assert "address" in first or "addr" in first
            assert "inst" in first
        finally:
            await conn.stop()

    @pytest.mark.asyncio
    async def test_struct_field_read(self, test_binary):
        conn = MIConnection(gdb_path="gdb")
        await conn.start()
        try:
            await conn.send(f"-file-exec-and-symbols {test_binary}")
            await conn.send("-break-insert main")
            await conn.send("-exec-run")
            await _wait_for_stop(conn)

            result = await conn.send(
                '-data-evaluate-expression "my_struct.field_a"'
            )
            # 0x1234 = 4660
            assert result.results.get("value") == "4660"

            result = await conn.send(
                '-data-evaluate-expression "my_struct.name"'
            )
            val = result.results.get("value", "")
            assert "hello" in val
        finally:
            await conn.stop()
