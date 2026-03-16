# TI MSP432 / CC13xx / CC26xx Debug Reference

## MSP432P4 Family

### MSP432P401R Key Specs
- Cortex-M4F @ 48 MHz (1.62-3.7V operation)
- 256 KB flash, 64 KB SRAM
- 14-bit ADC (1 MSPS), comparators, timers
- Ultra-low-power: 80 uA/MHz active, 660 nA standby

### MSP432 Clock System

```
                    ┌─── MCLK (CPU)
HFXT (48 MHz) ────►├─── HSMCLK (high-speed subsystem)
LFXT (32.768 kHz) ─►├─── SMCLK (low-speed subsystem)
DCO (configurable) ─►├─── ACLK (auxiliary, always on)
REFO (32.768 kHz) ─►└─── BCLK (low-power backup)
VLO (~10 kHz) ─────►
MODOSC (~25 MHz) ──►
```

**Common issue**: LFXT fault. If 32.768 kHz crystal doesn't start (wrong load caps, PCB issue), system falls back to REFO and sets LFXTIFG flag. Many timers default to ACLK source, so timing goes wrong silently.

### MSP432 Flash Wait States

| MCLK Speed | Flash Wait States |
|-----------|-------------------|
| 1-24 MHz | 0 |
| 24-48 MHz | 1 |
| > 48 MHz | 2 |

```c
// Must set before increasing clock speed
FLCTL->BANK0_RDCTL = (FLCTL->BANK0_RDCTL & ~0xF000) | FLCTL_BANK0_RDCTL_WAIT_1;
FLCTL->BANK1_RDCTL = (FLCTL->BANK1_RDCTL & ~0xF000) | FLCTL_BANK1_RDCTL_WAIT_1;
```

**Crash pattern**: increase MCLK without setting wait states = HardFault on flash access.

### MSP432 Debug Registers

- Flash BSL address: 0x00202000
- JTAG lock: TLV (device descriptor) at 0x00201000
- Device ID: SYSCTL->REBOOT_CTL for DID0/DID1 (TI device identification)

## CC13xx / CC26xx Family (SimpleLink)

### Architecture

```
┌─────────────────────────────────────────┐
│ Application domain                       │
│ Cortex-M4F @ 48 MHz                    │
│ (user code, RTOS, drivers)              │
│                                          │
├─────────────────────────────────────────┤
│ Radio domain                             │
│ Cortex-M0 (RF Core)                    │
│ (BLE/Sub-GHz/Thread/Zigbee stack)       │
│ NOT debuggable via JTAG                  │
│                                          │
├─────────────────────────────────────────┤
│ Sensor Controller                        │
│ 16-bit RISC (ultra-low-power)           │
│ (autonomous ADC/GPIO/I2C while M4 sleeps)│
└─────────────────────────────────────────┘
```

### CC26xx Key Parts

| Part | Radio | Use Case |
|------|-------|----------|
| CC2652R | BLE 5.2 + IEEE 802.15.4 | BLE + Thread/Zigbee |
| CC2652RB | BLE 5.2 (no antenna) | Module integration |
| CC1352R | Sub-1 GHz + BLE 5.2 | Long-range IoT |
| CC1352P | Sub-1 GHz + BLE 5.2 + PA | Long-range high power |
| CC2340R5 | BLE 5.3 only | Low-cost BLE |

### CC26xx Debug Setup

```
# OpenOCD config
source [find interface/xds110.cfg]
transport select swd
source [find target/ti_cc13xx_cc26xx.cfg]
```

### CC26xx Memory Map

```
0x0000_0000 - 0x0007_FFFF  Flash (512 KB)
0x1000_0000 - 0x1001_FFFF  ROM (TI-RTOS, driverlib)
0x2000_0000 - 0x2002_7FFF  SRAM (80 KB on CC2652R)
0x4000_0000 - 0x4FFF_FFFF  Peripheral registers
0x5000_0000 - 0x5000_1FFF  CPU SCS (SCB, NVIC, etc.)
```

### RF Core Communication (IPCC)

The M4 communicates with M0 radio core via a command/status interface:

```c
// Send command to RF core
RF_CmdHandle RF_postCmd(RF_Handle h, RF_Op* pOp, RF_Priority ePri, RF_Callback pCb);

// Check RF core status
RF_Stat status = RF_getInfo(h, RF_GET_CURR_CMD, &info);
```

**Cannot debug M0 directly**. Debug strategy:
1. Check RF command return codes
2. Monitor RF callback events
3. Read RF core status registers via M4: `RFHWREG(RFC_DBELL_BASE + offset)`
4. Use RF driver trace output (configurable via SysConfig)

### CC26xx Common Debug Issues

1. **RF patch not loaded**: Radio firmware patch must be applied before RF operations.
   TI provides patch blobs per PHY mode. Missing patch = radio init fails silently.

2. **CCFG (Customer Configuration)** wrong: programmed at flash end (last page).
   Wrong CCFG = device won't boot, JTAG may be disabled.
   - `CCFG_DEFAULT_CCFG_PROT_31_0`: write-protect flash sectors
   - `CCFG_CCFG_TAP_DAP_0`: JTAG enable/disable
   - Recovery: mass erase via XDS110 `DSLite` tool or OpenOCD

3. **Standby/shutdown exit fails**: wakeup source not configured, or GPIO latch not released.

4. **BLE connection drops**: check for interrupt priority issues between RF callbacks and app code. RF ISR at priority 0 (highest) must not be blocked.

### XDS110 Debug Probe

- Built into all TI LaunchPad boards
- Also available standalone (XDS110-ET)
- Provides SWD + UART bridge
- cJTAG (compact JTAG) support: 2-wire debug
- TI UniFlash for programming: `dslite.sh --config=... --flash --verify firmware.hex`

### Sensor Controller Studio

The SC (Sensor Controller) has its own IDE: "Sensor Controller Studio" from TI.
- Generates autonomous tasks that run while M4 sleeps
- SC accesses ADC, GPIO, I2C, SPI at ~2 MHz
- Communication with M4 via shared memory + ALERT interrupt
- Debug: use SCS debug overlay to step through SC code (separate tool, not GDB)

## TI RTOS Debugging

### TI-RTOS (SYS/BIOS) Thread-Aware Debug

```gdb
# In GDB, after loading TI-RTOS symbols
info threads           # Shows TI-RTOS tasks
thread <n>             # Switch to task context
backtrace              # Stack trace for that task
```

### Common RTOS Issues

1. **Task stack overflow**: TI-RTOS checks stack sentinel on task switch. If corrupted, calls `Error_raise()` with `Task_E_stackOverflow`.

2. **Semaphore deadlock**: Task A holds sem1, waits for sem2. Task B holds sem2, waits for sem1.
   Debug: check `Semaphore_pend()` callsites in backtrace of both tasks.

3. **Clock tick missed**: if Hwi (hardware interrupt) runs too long, Clock module misses ticks. System time drifts.
