---
name: flash
description: Flash firmware to target (destructive operation)
argument: ELF file path
model: sonnet
allowed-tools:
  - mcp__jtag-debug__session_state
  - mcp__jtag-debug__session_connect
  - mcp__jtag-debug__flash_write
  - mcp__jtag-debug__target_reset
  - Glob
---

# /flash -- Flash Firmware

Flash an ELF binary to the target microcontroller.

## Workflow

1. Verify the ELF file exists (use Glob if path is ambiguous)
2. Check session state -- connect if needed
3. Confirm with the user: "This will erase and reprogram flash. Proceed?"
4. Call `flash_write` with `confirmed=True` and `verified=True`
5. Call `target_reset` with `confirmed=True` to start the new firmware
6. Report result

## Important

- This is a destructive operation that overwrites flash memory
- Always verify after flashing
- If flash fails, the target may be in an inconsistent state -- re-flash is needed
