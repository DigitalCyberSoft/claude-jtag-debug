# GD32 / CH32 (Chinese MCU) Debug Reference

## GD32 Family (GigaDevice)

### GD32 vs STM32 Compatibility Matrix

| GD32 Part | STM32 Equivalent | Pin-Compat | Differences |
|-----------|-----------------|------------|-------------|
| GD32F103 | STM32F103 | Yes | Faster (108 MHz), different flash timing |
| GD32F303 | STM32F303 | Partial | Cortex-M4F, some peripheral differences |
| GD32F407 | STM32F407 | Partial | Different USB, DMA channel mapping |
| GD32E103 | STM32F103 | Yes | Enhanced, lower power |
| GD32E230 | STM32F030 | Partial | Cortex-M23, TrustZone |
| GD32VF103 | None (RISC-V) | Pin-compat with F103 | RISC-V Bumblebee core |

### GD32F103 Key Differences from STM32F103

**Clock**:
- Max HCLK: 108 MHz (vs 72 MHz on STM32F103)
- PLL input range and multiplier differ
- Flash wait states: 0 WS up to 30 MHz, 1 WS up to 60 MHz, 2 WS up to 90 MHz, 3 WS up to 108 MHz

**Flash**:
- Page size may differ (1 KB on some, 2 KB on others, vs 1 KB on STM32F103)
- Flash programming time slightly different
- Option bytes layout compatible but verify reset values

**USB**:
- PMA (Packet Memory Area) layout has been reported to differ
- USB HAL code from STM32Cube may need PMA offset adjustments
- USB clock: 48 MHz from PLL, divider calculation differs due to different PLL max

**DMA**:
- Channel assignments are the same as STM32F103
- But channel priority arbitration may differ under heavy load

**ADC**:
- Max ADC clock: 28 MHz (vs 14 MHz on STM32)
- Sampling times in cycles differ for same register values

### Identifying GD32 vs STM32

```c
// Read DBGMCU_IDCODE (same address as STM32)
volatile uint32_t idcode = *(volatile uint32_t*)0xE0042000;

// STM32F103: DEV_ID = 0x410, REV_ID varies
// GD32F103: DEV_ID = 0x410 (same!), but UNIQUE_ID at different addresses

// Better: read unique ID
// STM32F103: 0x1FFFF7E8 (96-bit unique ID)
// GD32F103: 0x1FFFF7E0 (different offset!)
volatile uint32_t *uid_stm32 = (volatile uint32_t*)0x1FFFF7E8;
volatile uint32_t *uid_gd32  = (volatile uint32_t*)0x1FFFF7E0;
// GD32 UID contains "GD32" ASCII in first word on some parts
```

### GD32F103 Debug Connection

Standard SWD, works with:
- ST-Link (may need `-c SWD` mode explicitly)
- J-Link (auto-detects as STM32F103, works fine)
- CMSIS-DAP / DAPLink

```
# OpenOCD (uses STM32F1 target config)
source [find interface/stlink.cfg]
transport select hla_swd
source [find target/stm32f1x.cfg]
# Set flash size manually if autodetect fails
set FLASH_SIZE 0x20000
```

**Issue**: OpenOCD may misdetect flash size. Override with `set FLASH_SIZE`.

### GD32VF103 (RISC-V)

- Bumblebee core: RV32IMAC + custom non-standard extensions
- Same peripherals as GD32F103 (SPI, I2C, UART, USB, etc.)
- Different interrupt controller: ECLIC (Enhanced Core Local Interrupt Controller) vs NVIC
- Debug: standard RISC-V JTAG debug module

```
# OpenOCD for GD32VF103
source [find interface/ftdi/olimex-arm-usb-ocd-h.cfg]
source [find target/gd32vf103.cfg]

# Or with Sipeed RV-Debugger
source [find interface/ftdi/minimodule.cfg]
set _CHIPNAME riscv
source [find target/gd32vf103.cfg]
```

### GD32VF103 Exception Handling

```c
// RISC-V CSRs for exception analysis
uint32_t mcause = read_csr(mcause);  // Exception cause
uint32_t mepc   = read_csr(mepc);    // Exception PC
uint32_t mtval  = read_csr(mtval);   // Trap value (faulting address)

// mcause values:
// 0 = Instruction address misaligned
// 1 = Instruction access fault
// 2 = Illegal instruction
// 3 = Breakpoint
// 4 = Load address misaligned
// 5 = Load access fault
// 6 = Store address misaligned
// 7 = Store access fault
// 8-11 = Environment call (ecall)
```

## CH32 Family (WCH / Nanjing Qinheng Micro)

### CH32 Product Line

| Part | Core | Speed | Flash | RAM | Notable |
|------|------|-------|-------|-----|---------|
| CH32V003 | RV32EC | 48 MHz | 16 KB | 2 KB | $0.10, 8-pin to 20-pin |
| CH32V103 | RV32IMAC | 80 MHz | 64 KB | 20 KB | STM32F103-like peripherals |
| CH32V203 | RV32IMAC | 144 MHz | 256 KB | 64 KB | USB FS, CAN |
| CH32V208 | RV32IMAC | 144 MHz | 256 KB | 64 KB | + BLE 5.3 |
| CH32V307 | RV32IMACF | 144 MHz | 480 KB | 128 KB | USB HS OTG, Ethernet, CAN-FD |
| CH32X035 | RV32IMAC | 48 MHz | 62 KB | 20 KB | USB PD controller built-in |

### CH32V003 Debug

**Single-wire debug (SWD-like, but proprietary WCH protocol)**:
- ONLY works with WCH-Link debug probe
- NOT compatible with J-Link, ST-Link, or CMSIS-DAP
- Debug pin: PD1 (SWIO) -- single bidirectional wire

```
# WCH OpenOCD fork
openocd -f wch-riscv.cfg
# Or use WCH's MounRiver Studio IDE (Eclipse-based)
```

**Programming without WCH-Link**: CH32V003 supports UART bootloader on PA2/PA1 (activated by BOOT0 pin).

### CH32V003 Limitations

- RV32EC: only 16 registers (x0-x15), no multiply/divide instructions
- 2 KB RAM total, no DMA
- Single interrupt priority level (no nesting)
- No hardware breakpoint in debug module (software EBREAK only)
- GDB step-over doesn't work reliably -- use step-into or breakpoints

### CH32V307 Debug

Standard RISC-V JTAG. Works with:
- WCH-Link (native support)
- FTDI-based JTAG adapters
- CMSIS-DAP with JTAG mode

```
# OpenOCD for CH32V307
adapter driver cmsis-dap
transport select jtag
source [find target/wch-riscv.cfg]
# Or with FTDI
adapter driver ftdi
ftdi_vid_pid 0x0403 0x6010
source [find target/wch-riscv.cfg]
```

### CH32V307 Key Features for Debug

- USB HS OTG (480 Mbit/s) with internal PHY -- rare for RISC-V MCUs
- 10/100 Ethernet with DMA
- DVP (digital video port) camera interface
- 2x CAN 2.0B
- Hardware FPU (single precision)

### WCH Debug Quirks

1. **WCH-Link firmware versions**: older firmware has bugs with GDB attach. Update with WCH's ISP tool.

2. **MounRiver vs standard toolchain**: WCH's GCC fork has non-standard extensions. Code compiled with standard riscv32-gcc may not use WCH-specific CSRs (fast interrupt, hardware stack push/pop).

3. **Interrupt handling differs from standard RISC-V**:
   - WCH uses hardware vector table (like ARM NVIC) instead of standard mtvec trap
   - PFIC (Programmable Fast Interrupt Controller) at 0xE000E000 (same address as ARM NVIC!)
   - This confuses tools that detect ARM vs RISC-V by probing NVIC address

4. **Flash programming**: WCH flash controller interface differs from standard. Need WCH-specific OpenOCD flash driver.

## Counterfeit Detection

GD32 parts are sometimes relabeled and sold as STM32. Indicators:

1. **UID register offset**: STM32F103 UID at 0x1FFFF7E8, GD32 at 0x1FFFF7E0
2. **Flash read at high speed**: GD32 handles 108 MHz with 3 wait states; genuine STM32F103 maxes at 72 MHz
3. **Package markings**: look for lot codes that don't match ST's format
4. **DBGMCU_IDCODE revision field**: may have unexpected revision IDs
5. **Flash page erase time**: GD32 typically faster than STM32

**In GDB**:
```gdb
# Read UID at both offsets
x/3w 0x1FFFF7E8
x/3w 0x1FFFF7E0
# GD32: one of these will contain manufacturer-specific pattern
```
