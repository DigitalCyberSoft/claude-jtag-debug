#!/usr/bin/env python3
"""PreToolUse hook: warn on Bash commands that should use plugin tools instead.

Soft warning only (exit 0 with systemMessage). Does not block.
"""

import json
import re
import sys


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    tool_input = event.get("tool_input", {})
    command = tool_input.get("command", "")

    if not command:
        sys.exit(0)

    warnings: list[str] = []

    # Patterns that should use plugin tools instead
    patterns = [
        (
            r"openocd.*-c.*flash",
            "Use the flash_write tool instead of running OpenOCD flash commands directly.",
        ),
        (
            r"mon(?:itor)?\s+reset",
            "Use the target_reset tool instead of monitor reset.",
        ),
        (
            r"(?:arm-none-eabi-gdb|gdb-multiarch)\s",
            "GDB is managed by the jtag-debug plugin. Use /debug to start a session.",
        ),
        (
            r"JLinkGDBServer",
            "J-Link GDB Server is managed by the probe manager. Use session_connect.",
        ),
        (
            r"pyocd\s+gdbserver",
            "pyOCD is managed by the probe manager. Use session_connect.",
        ),
    ]

    for pattern, message in patterns:
        if re.search(pattern, command, re.IGNORECASE):
            warnings.append(message)

    if warnings:
        result = {
            "decision": "allow",
            "systemMessage": "WARNING: " + " ".join(warnings),
        }
        json.dump(result, sys.stdout)
    else:
        json.dump({"decision": "allow"}, sys.stdout)


if __name__ == "__main__":
    main()
