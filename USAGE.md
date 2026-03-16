# Usage Guide

## Quick start

### 1. Install

```bash
cd claude-jtag-debug
pip install -e .
```

### 2. Add MCP server to Claude Code

```bash
claude mcp add --scope project --transport stdio jtag-debug -- python3 -m server
```

### 3. Verify

Start Claude Code and run `/mcp` to confirm the server is connected and tools are listed.

---

## Example workflows

### Debugging a crash on STM32

You have an STM32F407 connected via ST-Link, firmware is crashing.

```
/debug firmware.elf
```

Claude will:
1. Auto-detect the ST-Link probe
2. Start OpenOCD, connect GDB, load symbols
3. Check target state -- if in HardFault_Handler, auto-run fault analysis
4. Decode CFSR/HFSR, read the stacked exception frame, get backtrace
5. Tell you exactly what went wrong and how to fix it

**Manual equivalent using individual tools:**

```
Use session_connect with session_id "dbg-001", elf_path "/path/to/firmware.elf", svd_path "/path/to/STM32F407.svd"
```

```
Use session_state for session "dbg-001"
```

```
Use fault_analyze for session "dbg-001"
```

```
Use backtrace for session "dbg-001" with full=true
```

### Reading peripheral registers

After connecting, ask Claude to read SPI configuration:

```
Read the SPI1 CR1 register and explain the current configuration
```

Claude calls `peripheral_read` with peripheral="SPI1", register="CR1" and returns:

```
SPI1.CR1 @ 0x40013000 = 0x0000035C

Fields:
  CPHA     [0]    = 0  (First edge sample)
  CPOL     [1]    = 0  (Clock idle low) -> SPI Mode 0
  MSTR     [2]    = 1  (Master mode)
  BR       [5:3]  = 3  (fPCLK/16)
  SPE      [6]    = 1  (SPI enabled)
  LSBFIRST [7]    = 0  (MSB first)
  DFF      [11]   = 0  (8-bit frame)
```

Read all registers in a peripheral:

```
Show me all SPI1 registers
```

Claude calls `peripheral_read_all` and decodes every register with bitfield descriptions.

### Setting breakpoints and stepping

```
Set a breakpoint on SPI1_IRQHandler and continue
```

Claude calls:
- `breakpoint_set` with location="SPI1_IRQHandler"
- `target_continue`
- Reports stop reason and source location when breakpoint hits

```
Step into the next function call and show me the backtrace
```

Claude calls:
- `target_step` with step_type="into"
- `backtrace`
- Shows full call stack with source locations

### Memory inspection

```
Read 64 bytes at the stack pointer
```

Claude calls `register_read` for SP, then `memory_read` at that address:

```
0x2001ff80  00 00 00 00 34 12 00 08 01 00 00 00 00 00 00 00  |....4...........|
0x2001ff90  78 ff 01 20 a5 04 00 08 28 00 00 00 14 00 00 00  |x.. ....(.......|
0x2001ffa0  ...
```

### Disassembly

```
Disassemble the SPI1_IRQHandler function with source
```

Claude calls `disassemble` with function="SPI1_IRQHandler", mixed=true:

```
src/spi.c:42    void SPI1_IRQHandler(void) {
0x08001234:     push    {r7, lr}
0x08001236:     sub     sp, #8
src/spi.c:43        uint32_t sr = SPI1->SR;
0x08001238:     ldr     r3, [pc, #24]
0x0800123a:     ldr     r3, [r3, #8]
0x0800123c:     str     r3, [sp, #4]
```

### Symbol lookup

```
What's at address 0x08001234?
```

Claude calls `symbol_info` with address="0x08001234":

```
Symbol: SPI1_IRQHandler + 0 in section .text
Source: src/spi.c, line 42
```

### Saleae bus capture

Connect a Saleae Logic analyzer to the SPI bus:

```
/capture SPI1 at SPI1_IRQHandler
```

Claude will:
1. Connect to Logic 2 (must be running with automation enabled)
2. Ask you for channel mapping if not previously configured:
   - "Which Saleae channel is connected to SCK?" -> 0
   - "MOSI?" -> 1, "MISO?" -> 2, "CS?" -> 3
3. Read SPI1 registers to get CPOL/CPHA/baud rate
4. Auto-configure the SPI analyzer with matching settings
5. Set breakpoint on SPI1_IRQHandler
6. Start capture, resume target, wait for breakpoint
7. Decode captured traffic
8. Compare register config vs wire behavior

**Example finding:** "SPI1 is configured for Mode 0 (CPOL=0, clock idle low) but the Saleae capture shows the clock is idle high. The slave device likely requires Mode 2 or Mode 3."

### Timed capture without breakpoint

```
Capture 100ms of SPI traffic
```

Claude calls `saleae_capture_timed` with duration=0.1 and returns decoded frames.

### Fault diagnosis deep-dive

```
/diagnose the target keeps getting a HardFault after about 10 seconds of running
```

Claude (using opus model for complex reasoning):
1. Runs `fault_analyze` -- reads CFSR, HFSR, BFAR, MMFAR, stacked frame
2. Gets full backtrace with local variables
3. Disassembles the faulting instruction
4. Reads source code at the fault location
5. Checks relevant peripheral registers

**Example output:**

```
Fault Analysis:
  Type: BusFault (PRECISERR)
  BFAR: 0x40003808 (valid)
  Faulting PC: 0x08002A4C (spi_transfer+0x1C in src/spi.c:87)

  Diagnosis: Precise bus fault accessing SPI2 status register at 0x40003808.
  Root cause: SPI2 peripheral clock is not enabled in RCC_APB1ENR.

  RCC_APB1ENR = 0x00000000 (bit 14 = SPI2EN = 0)

  Fix: Add RCC->APB1ENR |= RCC_APB1ENR_SPI2EN before accessing SPI2 registers.
```

### Flashing firmware

```
/flash build/firmware.elf
```

Claude confirms: "This will erase and reprogram flash with build/firmware.elf. Proceed?"

After confirmation, calls `flash_write` with confirmed=true, verifies, and resets the target.

### Debug dashboard (SoftICE/IDA style)

Get everything in one call -- registers, disassembly, source, stack, memory watch, and backtrace:

```
Show me the full debug dashboard with SSI0 registers
```

Claude calls `target_dashboard` with style="softice" or style="ida" and data_address="0x40008000":

- **SoftICE style**: Dense single-screen view inspired by NuMega SoftICE. Registers packed 8 per row, section separators, current instruction marked with `> ... <<<`
- **IDA style**: Box-drawn panels inspired by IDA Pro Debug Bridge. 4-column register grid, structured sections with `┌├└│` borders

Both styles are architecture-aware (ARM, x86, AArch64, MIPS) and include flag decode (xPSR N/Z/C/V/Q/T for ARM, EFLAGS for x86).

### QEMU testing (no hardware)

For testing against QEMU without physical hardware:

1. Start QEMU manually:
```bash
qemu-system-arm -machine lm3s6965evb -kernel test.elf -gdb tcp::1234 -S -nographic
```

2. Connect:
```
Use session_connect with session_id "qemu-test", target "localhost", gdb_port 1234, elf_path "test.elf"
```

### Firmware binary debugging (GL.iNet / OpenWrt style)

For debugging extracted firmware binaries under QEMU user-mode emulation:

```python
from server.probe.qemu import QEMUUserTarget

# Auto-detects MIPS LE from ELF header
target = QEMUUserTarget(
    binary="squashfs-root/usr/sbin/gl_health",
    sysroot="squashfs-root/",
)
port = await target.start()
# Now connect GDB to localhost:port
```

### Raw GDB commands

For anything not covered by the dedicated tools:

```
Run raw GDB command: info threads
```

Claude calls `gdb_raw` with command="info threads". Dangerous commands (flash erase, memory write, reset) require explicit confirmation.

---

## Configuration

### Session persistence

Sessions save to `~/.claude/jtag-debug-sessions/`. On MCP server restart, saved sessions can be reconnected if the probe server is still running.

### Channel map persistence

Saleae channel mappings save per session. Once you tell Claude which channel is SCK/MOSI/MISO/CS, it remembers for that board.

### SVD files

SVD files are searched in order:
1. Path provided in `session_connect`
2. `cmsis-svd-data` package (pip install)
3. `/usr/share/cmsis-svd/`
4. `~/.local/share/cmsis-svd/`

### Safety hook

The `hooks/safety_gate.py` hook warns (but doesn't block) when Bash commands duplicate plugin functionality:
- Running `openocd` directly -> "Use flash_write tool instead"
- Running `gdb` directly -> "GDB is managed by the plugin"

---

## Agents

Three specialized agents are available for complex tasks:

| Agent | Trigger | Purpose |
|-------|---------|---------|
| **fault-analyst** (red) | Crashes, faults | Reads all fault registers, decodes, diagnoses root cause |
| **bus-comparator** (blue) | "Register says X but wire shows Y" | Compares SVD config vs Saleae captures |
| **register-explorer** (green) | "Why isn't my peripheral working?" | Traces full init chain: RCC -> GPIO -> peripheral -> DMA -> NVIC |

These are invoked automatically by Claude when the task matches, or you can reference them directly.

---

## Troubleshooting

**MCP server won't start:**
```bash
# Test directly
cd claude-jtag-debug
python -m server
# Should hang waiting for stdio input -- Ctrl+C to exit
```

**GDB not found:**
Install one of: `arm-none-eabi-gdb`, `gdb-multiarch`, or `gdb`. The connection layer tries all three.

**SVD not found:**
```bash
pip install cmsis-svd-data
# Or provide path explicitly in session_connect
```

**Saleae won't connect:**
- Logic 2 must be running
- Enable automation: Settings > Preferences > Enable Automation (bottom of page)
- Default port 10430
- Only one automation client at a time

**OpenOCD permission denied:**
Add udev rules for your probe. For ST-Link:
```
# /etc/udev/rules.d/99-stlink.rules
ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3748", MODE="0666"
ATTRS{idVendor}=="0483", ATTRS{idProduct}=="374b", MODE="0666"
```
Then `sudo udevadm control --reload-rules && sudo udevadm trigger`.

**Tests fail with seleniumbase error:**
```bash
python -m pytest tests/ -v -p no:seleniumbase
```
