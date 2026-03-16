# NXP i.MX RT Debug Reference

## i.MX RT Family Overview

| Part | Core | Speed | Flash | RAM | Key Feature |
|------|------|-------|-------|-----|-------------|
| RT1010 | M7 | 500 MHz | 0 (XIP) | 128 KB | Low-cost crossover |
| RT1015 | M7 | 500 MHz | 0 (XIP) | 128 KB | + Audio |
| RT1050 | M7 | 600 MHz | 0 (XIP) | 512 KB | FlexSPI, LCD |
| RT1060 | M7 | 600 MHz | 0 (XIP) | 1 MB | Ethernet, CAN-FD |
| RT1064 | M7 | 600 MHz | 4 MB int | 1 MB | Internal flash |
| RT1170 | M7+M4 | 1 GHz+400 | 0 (XIP) | 2 MB | Dual-core, GbE |

## Memory Map

```
0x0000_0000 - 0x0007_FFFF  ITCM (512 KB, configurable via FlexRAM)
0x2000_0000 - 0x2007_FFFF  DTCM (512 KB, configurable via FlexRAM)
0x2020_0000 - 0x2027_FFFF  OCRAM (512 KB, configurable via FlexRAM)
0x6000_0000 - 0x7FFF_FFFF  FlexSPI (XIP, mapped external flash)
0x8000_0000 - 0x8FFF_FFFF  SEMC (external SDRAM)
```

### FlexRAM Bank Configuration

Total: 512 KB split into 16 x 32 KB banks. Each bank assigned to ITCM, DTCM, or OCRAM.

Default (RT1060): 128 KB ITCM + 128 KB DTCM + 256 KB OCRAM

```
IOMUXC_GPR->GPR17 = bank assignment (2 bits per bank)
IOMUXC_GPR->GPR16 bit 2 = FLEXRAM_BANK_CFG_SEL (1=use GPR17, 0=use fuses)
```

**Critical**: changing FlexRAM config after code is running in ITCM = instant crash.

## XIP (Execute-in-Place) Debug

Code executes directly from external QSPI/HyperFlash via FlexSPI.

### Hardware Breakpoints in XIP

- ARM CoreSight supports 6-8 hardware breakpoints (FPB)
- These work in XIP flash regions
- Software breakpoints (replacing instruction with BKPT) don't work in flash (read-only)
- GDB may fail silently when trying to set too many breakpoints in XIP code

### FlexSPI Configuration Block (FCB)

At offset 0x0000 of flash, defines flash parameters. Wrong FCB = boot failure.

```
Offset 0x000: Tag (0x42464346 = "FCFB")
Offset 0x004: Version
Offset 0x00C: readSampleClkSrc
Offset 0x010: csHoldTime, csSetupTime
Offset 0x044: sflashA1Size
Offset 0x060: LUT (lookup table for flash commands)
```

### Boot Process

1. BootROM reads BOOT_CFG pins
2. Loads FCB from flash offset 0x0000
3. Configures FlexSPI from FCB
4. Reads IVT (Image Vector Table) at flash offset 0x1000
5. Reads DCD (Device Configuration Data) -- configures SDRAM, clocks
6. Jumps to application entry point

**Debug tip**: if device doesn't boot, check BOOT_CFG pin state with multimeter.

## Cache Management

### D-Cache Operations for DMA

```c
// Before DMA TX (CPU -> peripheral): clean cache to write dirty lines to memory
SCB_CleanDCache_by_Addr((uint32_t*)buf, size);

// After DMA RX (peripheral -> CPU): invalidate cache to discard stale lines
SCB_InvalidateDCache_by_Addr((uint32_t*)buf, size);

// Buffer MUST be cache-line aligned (32 bytes on Cortex-M7)
__attribute__((aligned(32))) uint8_t dma_buf[256];
```

**Common bug**: cache ops on unaligned addresses corrupt adjacent data.

### MPU Configuration for DMA Buffers

```c
// Configure MPU region as non-cacheable for DMA buffer
MPU->RBAR = (uint32_t)dma_buf | MPU_RBAR_VALID_Msk | REGION_NUMBER;
MPU->RASR = MPU_RASR_ENABLE_Msk |
            (size_log2 - 1) << MPU_RASR_SIZE_Pos |
            MPU_RASR_TEX(1) |    // Normal memory
            MPU_RASR_S_Msk |     // Shareable
            MPU_RASR_AP(0x3);    // Full access
// TEX=001, C=0, B=0 = Non-cacheable
```

## EDMA (Enhanced DMA)

Transfer Control Descriptor (TCD) based, 32 channels.

### Key TCD Fields

| Field | Description |
|-------|-------------|
| SADDR | Source address |
| SOFF | Source offset per transfer |
| ATTR | Transfer attributes (size, modulo) |
| NBYTES | Bytes per minor loop |
| SLAST | Source adjust after major loop |
| DADDR | Destination address |
| DOFF | Destination offset per transfer |
| CITER | Current major loop count |
| DLAST_SGA | Dest adjust or scatter/gather address |
| CSR | Control/status (done, active, dreq, inthalf, intmajor) |
| BITER | Beginning major loop count |

### EDMA Debug Checklist

1. DMAMUX channel enabled and source selected?
2. TCD SADDR/DADDR point to valid memory?
3. NBYTES matches peripheral FIFO width?
4. CITER/BITER set correctly for transfer count?
5. SOFF/DOFF correct for src/dst increment?
6. CSR.DREQ set for single-shot, clear for continuous?

## OpenOCD Configuration

```
# For i.MX RT1060 with CMSIS-DAP
source [find interface/cmsis-dap.cfg]
transport select swd

source [find target/imxrt.cfg]

# Flash configuration
set CHIPNAME MIMXRT1062
set WORKAREASIZE 0x20000

# If using external flash
flash bank $_FLASHNAME fespi 0x60000000 0 0 0 $_TARGETNAME
```

## Common Debug Pitfalls

1. **SWD not connecting**: check BOOT_MODE pins, SWD may be disabled in fuses
2. **Flash programming fails**: FlexSPI must be properly configured before flash ops
3. **SDRAM not initialized**: DCD header wrong or missing, memory test fails
4. **Ethernet PHY not found**: MDIO/MDC pins need open-drain + pull-up configuration
5. **USB HS fails**: PHY needs clock from USBPHY PLL, check CCM analog settings
