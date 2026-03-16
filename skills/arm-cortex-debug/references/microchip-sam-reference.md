# Microchip SAM D/E/S/V Debug Reference

## SAM Family Overview

| Part | Core | Speed | Flash | RAM | Ecosystem |
|------|------|-------|-------|-----|-----------|
| SAMD21 | M0+ | 48 MHz | 256 KB | 32 KB | Arduino Zero, CircuitPython |
| SAMD51 | M4F | 120 MHz | 1 MB | 256 KB | Adafruit Feather M4 |
| SAME51 | M4F | 120 MHz | 1 MB | 256 KB | + CAN-FD, Ethernet-capable |
| SAME54 | M4F | 120 MHz | 1 MB | 256 KB | + Ethernet MAC |
| SAME70 | M7 | 300 MHz | 2 MB | 384 KB | Industrial/automotive |
| SAMV71 | M7 | 300 MHz | 2 MB | 384 KB | + CAN-FD, HSMCI |

## SAMD21 Debug

### Generic Clock System (GCLK)

The SAMD21 clock system is generator-based, not tree-based like STM32:

```
Clock Sources          Generators (0-8)          Peripherals
┌─────────┐     ┌──────┐     ┌──────────┐
│ XOSC32K ├────►│ GEN0 ├────►│ Core/CPU │
│ OSC32K  ├────►│ GEN1 ├────►│ SERCOM0  │
│ XOSC    ├────►│ GEN2 ├────►│ SERCOM1  │
│ OSC8M   ├────►│ GEN3 ├────►│ TC3      │
│ DFLL48M ├────►│ ...  ├────►│ USB      │
│ FDPLL96M├────►│ GEN8 ├────►│ ADC      │
└─────────┘     └──────┘     └──────────┘
```

Each peripheral has a GCLK channel that connects to one generator.
Generator multiplexes one clock source and can divide it.

**Common issue**: peripheral doesn't work because its GCLK channel isn't configured:
```c
// Must enable GCLK for each peripheral
GCLK->CLKCTRL.reg = GCLK_CLKCTRL_CLKEN |
                     GCLK_CLKCTRL_GEN_GCLK0 |
                     GCLK_CLKCTRL_ID_SERCOM0_CORE;
while (GCLK->STATUS.bit.SYNCBUSY);
```

### SAMD21 Synchronization

Many SAMD21 registers require synchronization between clock domains:
```c
// Write to RTC COUNT register
RTC->MODE0.COUNT.reg = value;
while (RTC->MODE0.STATUS.bit.SYNCBUSY);  // MUST wait
```

**Crash pattern**: reading a register before sync completes returns stale data. Writing without waiting may lose the write. This is the #1 SAMD21 debug headache.

### SAMD21 NVM (Non-Volatile Memory)

- Boot protection: NVMCTRL_FUSES_BOOTPROT (default 0x07 = 0 KB protected)
  - Setting to smaller value protects bootloader region from accidental overwrite
  - **Trap**: BOOTPROT prevents OpenOCD from programming the protected region
  - Fix: write BOOTPROT fuse to 0x07 before programming
- Security bit (NVM User Row bit 0): locks debug access permanently
  - Recovery: chip erase via SWD (erases everything including security bit)
  - `openocd -c "at91samd chip-erase"`

### SAMD21 Debug Connection

```
# OpenOCD
source [find interface/cmsis-dap.cfg]
transport select swd
source [find target/at91samdXX.cfg]
```

With Atmel-ICE:
```
source [find interface/cmsis-dap.cfg]
cmsis_dap_vid_pid 0x03eb 0x2141
transport select swd
source [find target/at91samdXX.cfg]
```

## SAMD51 / SAME51 Debug

### Key Differences from SAMD21

- Cortex-M4F with FPU (enable CPACR!)
- 120 MHz main clock via DPLL0/DPLL1
- CMCC (Cortex M Cache Controller) -- NOT the ARM standard cache
- Event System: hardware event routing between peripherals without CPU
- QSPI controller for external flash (XIP capable)

### SAMD51 Clock System

```
XOSC0/1 (external crystal/clock, up to 48 MHz)
DFLL48M (48 MHz from USB SOF or 32K reference)
DPLL0/1 (PLL, up to 200 MHz output)
GCLK generators 0-11
```

### CMCC (Cortex M Cache Controller)

Not ARM's built-in cache, but a separate Microchip IP:
```c
// Enable cache
CMCC->CTRL.bit.CEN = 1;

// MUST disable cache before flash programming
CMCC->CTRL.bit.CEN = 0;
while (CMCC->SR.bit.CSTS);  // Wait for disable

// Invalidate after flash write
CMCC->MAINT0.bit.INVALL = 1;
```

**Debug trap**: OpenOCD flash write fails if CMCC is enabled. Disable before `load`.

### SAME51 CAN-FD (MCAN)

Same Bosch MCAN IP as STM32 FDCAN:
- Message RAM must be allocated in first 64 KB of SRAM
- MCAN_CCCR.INIT = 1 to configure, 0 to operate
- Separate bit timing for nominal (MCAN_NBTP) and data (MCAN_DBTP)
- Up to 2 CAN-FD instances

## SAME70 / SAMV71 Debug

### Key Features

- Cortex-M7 @ 300 MHz with I/D cache
- TCM: 256 KB ITCM + 128 KB DTCM
- True DMA (XDMAC) with 24 channels
- Ethernet MAC with IEEE 1588 PTP
- ISI (Image Sensor Interface) for camera

### Cache and DMA

Same issues as STM32H7 / i.MX RT:
```c
// Clean D-cache before DMA TX
SCB_CleanDCache_by_Addr(buf, size);

// Invalidate D-cache after DMA RX
SCB_InvalidateDCache_by_Addr(buf, size);

// Or configure MPU region as non-cacheable
```

### SAME70 MPU

Uses Cortex-M7 standard MPU but Microchip's Harmony3 configures it differently:
- Region 0: Flash (cacheable, write-through)
- Region 1: SRAM (cacheable, write-back)
- Region 2: Peripheral space (device, non-cacheable)
- Region 3-7: User-configurable for DMA buffers

### SAME70 Debug Connection

```
# J-Link
source [find interface/jlink.cfg]
transport select swd
set CHIPNAME at91same70q21
source [find target/atsamv.cfg]
```

## Harmony 3 / ASF Confusion

Microchip has three incompatible software frameworks:

| Framework | Targets | Status |
|-----------|---------|--------|
| ASF3 | SAM3, SAM4, older SAMD | Legacy, no new development |
| ASF4 / START | SAMD21, SAML, SAMC | Legacy, replaced by Harmony |
| Harmony 3 | All SAM, PIC32 | Current, actively developed |

**Debug impact**: driver register access patterns differ between frameworks. Check which framework the project uses before looking up register manipulation code.

## Common SAM Debug Pitfalls

1. **Sync-busy loops**: SAMD21 register writes need sync wait. Forgetting = data loss or stale reads.

2. **GCLK not enabled**: unlike STM32's single RCC enable, SAM requires GCLK channel + APBC/APBD mask for each peripheral.

3. **NVM BOOTPROT**: protects bootloader region, blocks flash programming.

4. **Security bit set**: locks debug permanently until chip erase.

5. **SERCOM pin mux**: each SERCOM has 4 pad options per pin. Wrong PMUX value = peripheral on wrong pins.
   Check datasheet "I/O Multiplexing" table carefully.

6. **Event System misconfiguration**: events routed to wrong channel or generator. Use EVSYS debug registers to trace event path.

7. **DFLL48M lock failure**: DFLL needs reference clock (32K or USB SOF). If reference missing, DFLL output unstable, all clocked peripherals misbehave.
