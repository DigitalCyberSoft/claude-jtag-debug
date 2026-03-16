"""Tests for safety tier enforcement."""

import pytest

from server.safety import (
    SafetyTier,
    TOOL_TIERS,
    require_confirmation,
    check_gdb_raw_safety,
)


class TestToolTiers:
    def test_read_tools_are_safe(self):
        safe_tools = [
            "session_state", "register_read", "peripheral_read",
            "memory_read", "svd_lookup", "fault_analyze",
            "backtrace", "evaluate", "disassemble",
            "source_context", "symbol_info",
        ]
        for tool in safe_tools:
            assert TOOL_TIERS[tool] == SafetyTier.SAFE, f"{tool} should be SAFE"

    def test_write_tools_are_destructive(self):
        destructive_tools = [
            "memory_write", "register_write", "peripheral_write",
            "target_reset", "flash_write", "gdb_raw",
        ]
        for tool in destructive_tools:
            assert TOOL_TIERS[tool] == SafetyTier.DESTRUCTIVE, f"{tool} should be DESTRUCTIVE"

    def test_execution_tools_are_state(self):
        state_tools = [
            "target_continue", "target_step", "breakpoint_set",
        ]
        for tool in state_tools:
            assert TOOL_TIERS[tool] == SafetyTier.STATE, f"{tool} should be STATE"


class TestConfirmation:
    def test_safe_tool_no_confirmation(self):
        result = require_confirmation("session_state", {}, "read state")
        assert result is None

    def test_destructive_without_confirmed(self):
        result = require_confirmation(
            "memory_write", {"confirmed": False}, "write 4 bytes to 0x20000000"
        )
        assert result is not None
        assert "confirmed=True" in result

    def test_destructive_with_confirmed(self):
        result = require_confirmation(
            "memory_write", {"confirmed": True}, "write 4 bytes to 0x20000000"
        )
        assert result is None

    def test_state_tool_no_confirmation_needed(self):
        result = require_confirmation("target_continue", {}, "continue execution")
        assert result is None


class TestGDBRawSafety:
    def test_safe_command(self):
        assert check_gdb_raw_safety("info registers") is None
        assert check_gdb_raw_safety("print variable") is None
        assert check_gdb_raw_safety("backtrace") is None

    def test_flash_erase_blocked(self):
        result = check_gdb_raw_safety("monitor flash erase_sector 0 0 7")
        assert result is not None
        assert "erase" in result

    def test_mass_erase_blocked(self):
        result = check_gdb_raw_safety("monitor flash mass_erase")
        assert result is not None

    def test_monitor_reset_blocked(self):
        result = check_gdb_raw_safety("monitor reset")
        assert result is not None

    def test_memory_write_blocked(self):
        result = check_gdb_raw_safety("set {int}0x20000000 = 0xDEAD")
        assert result is not None

    def test_openocd_write_blocked(self):
        result = check_gdb_raw_safety("monitor mww 0x20000000 0xDEAD")
        assert result is not None

    def test_load_blocked(self):
        result = check_gdb_raw_safety("load")
        assert result is not None

    def test_call_blocked(self):
        result = check_gdb_raw_safety("call some_function()")
        assert result is not None

    def test_jump_blocked(self):
        result = check_gdb_raw_safety("jump *0x08000000")
        assert result is not None

    def test_case_insensitive(self):
        result = check_gdb_raw_safety("MONITOR FLASH ERASE_SECTOR 0 0 7")
        assert result is not None
