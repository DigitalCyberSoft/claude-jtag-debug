"""Safety tier enforcement for MCP tools."""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Any

# Dangerous GDB raw commands that require explicit confirmation
_DANGEROUS_GDB_PATTERNS: list[tuple[str, str]] = [
    (r"monitor\s+flash\s+erase", "erase flash sectors"),
    (r"monitor\s+flash\s+mass_erase", "mass-erase entire flash"),
    (r"monitor\s+reset", "reset the target"),
    (r"set\s+\{", "write directly to memory"),
    (r"monitor\s+mw[whb]", "write to memory via OpenOCD"),
    (r"^load$", "flash firmware to target"),
    (r"^call\s+", "call a function on target (arbitrary side effects)"),
    (r"^jump\s+", "change program counter"),
]


class SafetyTier(IntEnum):
    SAFE = 0       # Read-only, always allowed
    STATE = 1      # Changes execution state, generally safe
    DESTRUCTIVE = 2  # Writes memory/registers/flash, requires confirmation


# Tool -> tier mapping
TOOL_TIERS: dict[str, SafetyTier] = {
    # Tier 0: Safe (read-only)
    "session_state": SafetyTier.SAFE,
    "session_list": SafetyTier.SAFE,
    "register_read": SafetyTier.SAFE,
    "peripheral_read": SafetyTier.SAFE,
    "peripheral_read_all": SafetyTier.SAFE,
    "memory_read": SafetyTier.SAFE,
    "svd_lookup": SafetyTier.SAFE,
    "backtrace": SafetyTier.SAFE,
    "evaluate": SafetyTier.SAFE,
    "fault_analyze": SafetyTier.SAFE,
    "saleae_connect": SafetyTier.SAFE,
    "saleae_capture_timed": SafetyTier.SAFE,
    "breakpoint_list": SafetyTier.SAFE,
    "disassemble": SafetyTier.SAFE,
    "source_context": SafetyTier.SAFE,
    "symbol_info": SafetyTier.SAFE,
    # Tier 1: State-changing
    "session_connect": SafetyTier.STATE,
    "session_disconnect": SafetyTier.STATE,
    "target_continue": SafetyTier.STATE,
    "target_step": SafetyTier.STATE,
    "target_interrupt": SafetyTier.STATE,
    "breakpoint_set": SafetyTier.STATE,
    "breakpoint_remove": SafetyTier.STATE,
    "saleae_configure": SafetyTier.STATE,
    "saleae_capture_triggered": SafetyTier.STATE,
    "saleae_capture_at_breakpoint": SafetyTier.STATE,
    # Tier 2: Destructive (requires confirmed=True)
    "memory_write": SafetyTier.DESTRUCTIVE,
    "register_write": SafetyTier.DESTRUCTIVE,
    "peripheral_write": SafetyTier.DESTRUCTIVE,
    "target_reset": SafetyTier.DESTRUCTIVE,
    "flash_write": SafetyTier.DESTRUCTIVE,
    "gdb_raw": SafetyTier.DESTRUCTIVE,
}


def require_confirmation(
    tool_name: str,
    params: dict[str, Any],
    action_description: str,
) -> str | None:
    """Check if a Tier 2 tool needs confirmation.

    Returns None if confirmed or not required.
    Returns a warning string if confirmation needed.
    """
    tier = TOOL_TIERS.get(tool_name, SafetyTier.SAFE)

    if tier < SafetyTier.DESTRUCTIVE:
        return None

    if params.get("confirmed") is True:
        return None

    return (
        f"**{tool_name}** would {action_description}. "
        f"Call again with `confirmed=True` to proceed."
    )


def check_gdb_raw_safety(command: str) -> str | None:
    """Check if a raw GDB command is dangerous.

    Returns description of danger, or None if safe.
    """
    command_stripped = command.strip()
    for pattern, description in _DANGEROUS_GDB_PATTERNS:
        if re.search(pattern, command_stripped, re.IGNORECASE):
            return description
    return None
