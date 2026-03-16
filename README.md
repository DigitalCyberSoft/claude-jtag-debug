# claude-jtag-debug

JTAG/GDB debug plugin for Claude Code with Saleae Logic 2 integration.

An engineer says "help me debug why my STM32 is crashing in the SPI interrupt handler" and Claude creates a debug session, reads fault registers, decodes them via SVD bitfields, traces the stack, captures actual SPI bus traffic on the Saleae, compares register config vs wire traffic, and diagnoses the root cause.

## What it does

- Connects to embedded targets via debug probes (ST-Link, J-Link, CMSIS-DAP, etc.)
- Manages GDB sessions through the MI protocol (async, token-matched, crash-resilient)
- Decodes peripheral registers via SVD files into named bitfields with descriptions
- Analyzes Cortex-M faults: reads CFSR/HFSR/BFAR/MMFAR, decodes bits, diagnoses root cause
- Captures bus traffic with Saleae Logic 2 and auto-configures protocol analyzers from SVD data
- Closed-loop debugging: compares what registers say vs what actually happens on the wire
- QEMU userspace emulation for firmware debugging without hardware (MIPS, ARM, RISC-V)

## Requirements

**Core (always needed):**
- Python >= 3.10
- `fastmcp` >= 3.0
- `gdb` or `arm-none-eabi-gdb` or `gdb-multiarch`

**Optional:**
- `logic2-automation` -- Saleae Logic 2 integration
- `cmsis-svd-data` -- SVD file database for peripheral register decode
- `qemu-system-arm` -- QEMU system emulation for testing without hardware
- `qemu-user-static` -- QEMU user-mode emulation for firmware binary debugging
- `openocd` / `JLinkGDBServer` / `pyocd` -- GDB server for your debug probe

## Install

```bash
cd claude-jtag-debug
pip install -e ".[dev]"
```

With optional extras:

```bash
pip install -e ".[dev,saleae,svd]"
```

## Load as MCP server

### Project scope (from this directory)

```bash
claude mcp add --scope project --transport stdio jtag-debug -- python3 -m server
```

### User scope (available in all projects)

```bash
claude mcp add --scope user --transport stdio jtag-debug -- python3 -m server
```

Verify with `/mcp` inside a Claude Code session.

## Slash commands

| Command | Description |
|---------|-------------|
| `/debug [target or ELF]` | Start or resume a debug session with auto-detection |
| `/diagnose [symptoms]` | Cortex-M fault diagnosis workflow |
| `/capture <peripheral> [at breakpoint]` | Saleae bus capture and analysis |
| `/flash <elf-path>` | Flash firmware to target |

## Tools (33 total)

**Session:** `session_connect`, `session_disconnect`, `session_state`, `session_list`

**Registers/Memory:** `register_read`, `peripheral_read`, `peripheral_read_all`, `memory_read`, `svd_lookup`

**Execution:** `target_continue`, `target_step`, `target_interrupt`, `breakpoint_set`, `breakpoint_remove`, `breakpoint_list`, `backtrace`, `evaluate`

**Source/Symbols:** `disassemble`, `source_context`, `symbol_info`

**Write (requires confirmation):** `memory_write`, `register_write`, `peripheral_write`, `target_reset`, `flash_write`, `gdb_raw`

**Saleae:** `saleae_connect`, `saleae_configure`, `saleae_capture_timed`, `saleae_capture_triggered`, `saleae_capture_at_breakpoint` (unit tested with mocks; not yet tested against real Saleae hardware)

**Diagnostics:** `fault_analyze`, `target_dashboard`

The `target_dashboard` tool provides a SoftICE/IDA-style composite debug view: registers (with flag decode), disassembly around PC (with current instruction marker), stack dump, source context, optional data watch window, and backtrace -- all in one call. Supports `style="softice"` (dense, maximum info) or `style="ida"` (structured panels with clear separation).

### Dashboard examples

**SoftICE style** (`style="softice"`) -- dense, maximum info per line:

```
─── registers ─────────────────────────────────────────────────────────────────────
 R0=00000000  R1=00000000  R2=00000000  R3=00000000  R4=00000000  R5=00000000
 R6=00000000  R7=2000FFE8  R8=00000000  R9=00000000  R10=00000000 R11=00000000
 R12=00000000 SP=2000FFE8  LR=000000A1  PC=00000016
 XPSR=41000000  MSP=2000FFE8  [ N Z C V Q T ]
─── code ──────────────────────────────────────────────────────────────────────────
    <configure_spi>:
   00000010  push    {r7}
   00000012  sub     sp, #12
   00000014  add     r7, sp, #0
 > 00000016  ldr     r3, [pc, #64]   @ (0x58 <configure_spi+72>)  <<<
   00000018  ldr     r3, [r3, #0]
   0000001A  ldr     r2, [pc, #60]
   0000001C  orr.w   r3, r3, #16
─── source ────────────────────────────────────────────────────────────────────────
 34    void configure_spi(void) {
 35        /* Enable SSI0 clock */
 36        SYSCTL_RCGC1 |= (1 << 4);
─── stack [SP=2000FFE8] ───────────────────────────────────────────────────────────
 0x2000ffe8  00 00 00 00 00 00 00 00 f8 ff 00 20   |........... |
─── data [0x40008000] ─────────────────────────────────────────────────────────────
 0x40008000  07 00 00 00 02 00 00 00 00 00 00 00   |............|
─── backtrace ─────────────────────────────────────────────────────────────────────
 #0  configure_spi  test_spi.c:36  [0x00000016]
 #1  Reset_Handler  test_spi.c:72  [0x000000a0]
────────────────────────────────────────────────────────────────────────────────────
```

**IDA style** (`style="ida"`) -- boxed panels:

```
┌─ CPU Registers ──────────────────────────────────────────────────────────────────┐
│ R0   00000000           R1   00000000           R2   00000000           R3   00000000
│ R4   00000000           R5   00000000           R6   00000000           R7   2000FFE8
│ R12  00000000           SP   2000FFE8           LR   000000A1           PC   00000016
│──────────────────────────────────────────────────────────────────────────────────│
│ XPSR     41000000  [Z T]
│ MSP      2000FFE8
├─ Disassembly ────────────────────────────────────────────────────────────────────┤
│    <configure_spi>:
│  > 00000016  ldr     r3, [pc, #64]   @ (0x58 <configure_spi+72>)  <<<
│    00000018  ldr     r3, [r3, #0]
├─ Source ─────────────────────────────────────────────────────────────────────────┤
│ 34    void configure_spi(void) {
│ 36        SYSCTL_RCGC1 |= (1 << 4);
├─ Stack [SP=0x2000FFE8] ─────────────────────────────────────────────────────────┤
│ 0x2000ffe8  00 00 00 00 00 00 00 00 f8 ff 00 20   |........... |
├─ Backtrace ──────────────────────────────────────────────────────────────────────┤
│  #0  configure_spi  test_spi.c:36  [0x00000016]
│  #1  Reset_Handler  test_spi.c:72  [0x000000a0]
└──────────────────────────────────────────────────────────────────────────────────┘
```

All tool output includes ANSI 16-color for syntax highlighting: green register values, yellow function names, cyan addresses, bold white PC line, bold green/dim flags.

## Safety tiers

- **Tier 0 (Safe):** Read-only operations. Always allowed.
- **Tier 1 (State-changing):** Execution control (continue, step, breakpoints). Allowed, logged.
- **Tier 2 (Destructive):** Writes to memory/registers/flash. Requires `confirmed=True` parameter.

## Supported hardware

**Debug probes:** ST-Link V2/V3, J-Link, CMSIS-DAP/DAPLink, Black Magic Probe, Raspberry Pi Debug Probe, NXP MCU-Link, TI XDS110, Atmel-ICE, WCH-Link, ESP-Prog

**MCU families:** STM32 (F0/F1/F3/F4/F7/H7/U5/WB/WL), ESP32 (Xtensa + RISC-V), RP2040/RP2350, Nordic nRF52/nRF53/nRF54, NXP i.MX RT/LPC, TI MSP432/CC26xx, Microchip SAM D/E/S/V, GD32, CH32

**Protocols:** SPI, I2C, UART, CAN/CAN-FD, USB

## Tests

```bash
python -m pytest tests/ -v -p no:seleniumbase
```

184 tests covering:
- MI parser: all record types, nested structures, escapes, incomplete lines, corruption
- SVD decoder: register decode, bitfield extraction, read-modify-write, derived peripherals
- Safety enforcement: tier classification, confirmation gating, dangerous command detection
- Saleae config: CPOL/CPHA mapping, sample rate calculation, channel configuration
- QEMU userspace: ELF arch detection, command construction, GL.iNet firmware workflow
- Probe detection: USB VID/PID matching, lsusb parsing
- **Integration (real GDB):** breakpoints, register reads, memory reads, expression evaluation, stepping, backtrace, disassembly, struct field access
- **QEMU system (real QEMU + GDB):** full stack against lm3s6965evb: session connect, registers, breakpoints, step, memory R/W, backtrace, expression eval, fault analysis, SPI register verification, dashboard

## Project structure

```
claude-jtag-debug/
  .claude-plugin/plugin.json    # Plugin manifest
  .mcp.json                     # MCP server config
  commands/                     # /debug, /diagnose, /capture, /flash
  agents/                       # fault-analyst, bus-comparator, register-explorer
  skills/arm-cortex-debug/      # Domain knowledge + reference docs
  hooks/                        # Safety gate for Bash commands
  server/                       # FastMCP server + all tool implementations
    gdb/                        #   MI parser, async connection, session state machine
    svd/                        #   SVD XML parser, register decoder, file discovery
    probe/                      #   Probe detection, OpenOCD/JLink/pyOCD/QEMU managers
    saleae/                     #   Logic 2 connection, capture coordination, analyzer config
    mcp_server.py               #   All 33 tool definitions + output formatters
    safety.py                   #   Safety tier enforcement
    session_store.py            #   Session persistence
  tests/                        # 184 tests
    fixtures/test_target.c      #   Native C binary for GDB integration tests
    firmware/                   #   Cortex-M test firmware (QEMU lm3s6965evb)
```

## License

MIT
