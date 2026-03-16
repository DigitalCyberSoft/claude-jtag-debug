---
name: arm-cortex-debug
description: Embedded MCU debugging domain knowledge -- ARM Cortex-M, STM32, ESP32, RP2040, Nordic, NXP, TI, Microchip, RISC-V, GD32/CH32, CAN-FD, and common peripheral protocols
---

# ARM Cortex-M Debug Knowledge

## Cortex-M Exception Model

Exception entry pushes 8 registers onto the active stack (MSP or PSP):
`R0, R1, R2, R3, R12, LR, PC, xPSR` (32 bytes)

If FPU context is active (FPCCR.LSPEN), additional 18 FP registers are pushed (lazy or eager).

EXC_RETURN values (LR on exception entry):
- `0xFFFFFFF1`: Return to Handler mode, MSP
- `0xFFFFFFF9`: Return to Thread mode, MSP
- `0xFFFFFFFD`: Return to Thread mode, PSP
- `0xFFFFFFE1`: Return to Handler mode, MSP, FP frame
- `0xFFFFFFE9`: Return to Thread mode, MSP, FP frame
- `0xFFFFFFED`: Return to Thread mode, PSP, FP frame

## System Control Block (SCB) Addresses

| Register | Address | Description |
|----------|---------|-------------|
| CPUID | 0xE000ED00 | CPU identification |
| ICSR | 0xE000ED04 | Interrupt control/state |
| VTOR | 0xE000ED08 | Vector table offset |
| AIRCR | 0xE000ED0C | Application interrupt/reset control |
| SCR | 0xE000ED10 | System control |
| CCR | 0xE000ED14 | Configuration/control |
| SHCSR | 0xE000ED24 | System handler control/state |
| CFSR | 0xE000ED28 | Configurable fault status |
| HFSR | 0xE000ED2C | HardFault status |
| DFSR | 0xE000ED30 | Debug fault status |
| MMFAR | 0xE000ED34 | MemManage fault address |
| BFAR | 0xE000ED38 | BusFault address |
| AFSR | 0xE000ED3C | Auxiliary fault status |
| CPACR | 0xE000ED88 | Coprocessor access control |

## Common Crash Patterns

### Pattern: INVSTATE (Usage Fault)
**Signature**: CFSR bit 17 set, faulting PC is even
**Cause**: Function pointer without thumb bit. In ARM Cortex-M (Thumb-only), all function addresses must have bit 0 set.
**Fix**: `func_ptr = (void(*)(void))((uint32_t)addr | 1);`

### Pattern: PRECISERR + BFAR Valid (Bus Fault)
**Signature**: CFSR bit 9 set, CFSR bit 15 set, BFAR contains faulting address
**Cause**: Read/write to invalid or unmapped address
**Common subcases**:
- BFAR = 0x0000000x: NULL pointer dereference (struct member offset)
- BFAR = 0x4xxxxxxx: Peripheral access without clock enable
- BFAR = 0x2xxxxxxx past RAM end: Buffer overflow into unmapped region

### Pattern: IMPRECISERR (Bus Fault)
**Signature**: CFSR bit 10 set, BFAR NOT valid
**Cause**: Write to invalid address, but due to write buffer, PC is past the faulting instruction
**Debug**: Check stores in the few instructions before stacked PC

### Pattern: STKERR (Bus Fault on Stacking)
**Signature**: CFSR bit 12 set
**Cause**: Stack overflow -- SP below allocated stack during exception entry
**Fix**: Increase stack size, reduce call depth, move large locals to heap

### Pattern: NOCP (Usage Fault)
**Signature**: CFSR bit 19 set
**Cause**: FPU instruction executed without enabling coprocessor
**Fix**: `SCB->CPACR |= (0xF << 20);` early in startup

### Pattern: UNALIGNED (Usage Fault)
**Signature**: CFSR bit 24 set, CCR.UNALIGN_TRP enabled
**Cause**: Unaligned 16/32-bit access
**Fix**: Use `__attribute__((packed))` carefully, or memcpy for unaligned data

### Pattern: Vector Table Corruption
**Signature**: HardFault at reset or on first interrupt, HFSR.VECTTBL set
**Cause**: VTOR points to wrong address or vector table has bad entries

## STM32 Common Pitfalls

1. **Peripheral clock not enabled**: Must set RCC_APBxENR bit before any peripheral register access. Without it, reads return 0 and writes are ignored (or bus fault).

2. **Wrong GPIO alternate function**: Each STM32 pin has up to 16 AF mappings (AF0-AF15). The AF number for SPI1_SCK on PA5 differs from SPI1_SCK on PB3. Always check the datasheet AF mapping table.

3. **GPIO still in input mode**: After reset, GPIOs default to input. Must set MODER to AF mode (0b10) in addition to selecting the AF number.

4. **DMA stream conflicts**: On STM32F4, each DMA stream can only serve one peripheral at a time. Two peripherals sharing a stream causes silent data corruption.

5. **Interrupt priority grouping**: Default NVIC priority grouping may not match RTOS requirements. FreeRTOS requires `NVIC_SetPriorityGrouping(0)` for 4 bits of preemption priority.

## SPI Debugging Checklist

1. Clock enabled? (RCC_APB1ENR or RCC_APB2ENR)
2. GPIO pins configured as AF with correct AF number?
3. SPI enabled? (CR1.SPE = 1)
4. Master mode? (CR1.MSTR = 1)
5. Correct CPOL/CPHA for target device?
6. Correct baud rate prescaler?
7. Correct data frame format (8/16 bit)?
8. NSS management mode correct? (SSM, SSI, SSOE bits)
9. If DMA: DMA stream configured, enabled, and not conflicting?
10. If interrupt: NVIC enabled with correct priority?

## I2C Debugging Checklist

1. Clock enabled?
2. GPIO pins: open-drain, AF mode, correct AF number?
3. External pull-ups present? (I2C requires them)
4. Clock speed configuration (CCR register)?
5. I2C peripheral enabled?
6. BUSY flag stuck? (Common after incomplete transaction -- toggle SCL manually or reset peripheral)

## RISC-V Debug Basics

For RISC-V targets (ESP32-C3, GD32VF103):
- Debug interface via JTAG or cJTAG
- Abstract commands via Debug Module (DM)
- CSRs: `mstatus`, `mcause`, `mepc`, `mtval` for exception analysis
- `mcause` MSB: 1=interrupt, 0=exception
- Common exception codes: 2=illegal instruction, 5=load access fault, 7=store access fault

## ESP32 Debug Notes

- ESP32/ESP32-S3: Xtensa LX6/LX7 with JTAG via GPIO 12-15
- ESP32-C3/C6: RISC-V with USB-JTAG interface
- Use `openocd -f board/esp32-wrover-kit-1.8v.cfg` or similar
- Dual-core: select CPU with `set ESP32_ONLYCPU 1` in OpenOCD config

## RP2040/RP2350 Debug Notes

- SWD interface on dedicated debug pins
- Dual Cortex-M0+ (RP2040) or Cortex-M33/RISC-V (RP2350)
- picoprobe firmware turns one Pico into a debug probe
- OpenOCD target: `target/rp2040.cfg` or `target/rp2350.cfg`
- PIO state machines not directly visible via JTAG -- read PIO registers

## Nordic nRF52/nRF53/nRF54 Debug Notes

- nRF52832/nRF52840: Cortex-M4F with SWD, 64 MHz
- nRF5340: Dual Cortex-M33 (app core 128 MHz + network core 64 MHz)
- nRF54H20/nRF54L15: Latest generation, multiple cores, RISC-V + Cortex-M33
- Use J-Link (built into DK boards) or CMSIS-DAP
- Access port protection (APPROTECT) may block debug -- must be erased to re-enable
- nRF52 APPROTECT: write 0 to UICR.APPROTECT, then `nrfjprog --recover`
- nRF53 secure APPROTECT: hardware fuse, cannot be reversed without full erase
- Softdevice-aware debugging: Softdevice uses low interrupt priorities; breakpoints in SD region crash the device
- Don't step through BLE radio event code -- timing-critical, will desync
- OpenOCD target: `target/nrf52.cfg`, `target/nrf5340.cfg`
- nRF Connect SDK (NCS) uses Zephyr RTOS -- thread-aware debugging via GDB `info threads`
- Common issue: HFCLK not started -- BLE requires external 32 MHz crystal, radio won't function on RC oscillator
- Common issue: DCDC not enabled -- nRF52840 draws excessive current on LDO mode, brownout on battery

## NXP i.MX RT Debug Notes

- i.MX RT1050/RT1060/RT1170: Cortex-M7 @ 600-1000 MHz crossover processors
- i.MX RT1010/RT1015: Lower-cost Cortex-M7 @ 500 MHz
- SWD or JTAG via J-Link, CMSIS-DAP, or on-board LinkServer probe (MCU-Link)
- FlexRAM configuration: ITCM/DTCM/OCRAM split is programmable via IOMUXC_GPR
  - Default: 128KB ITCM + 128KB DTCM + 256KB OCRAM
  - Misconfigured FlexRAM = HardFault on first access to wrong region
- XIP (execute-in-place) from external QSPI/HyperFlash via FlexSPI
  - Debug: cannot set hardware breakpoints in XIP flash (read-only to debug unit)
  - Workaround: copy function to RAM, or use unlimited software breakpoints in RAM
- Cache: 32KB I-cache + 32KB D-cache, MUST manage coherency with DMA
  - `SCB_CleanDCache_by_Addr()` before DMA TX, `SCB_InvalidateDCache_by_Addr()` after DMA RX
- EDMA (enhanced DMA): TCD (transfer control descriptor) based, more complex than STM32 DMA
- Common pitfall: Boot mode pins (BOOT_CFG) wrong = device doesn't boot from flash
- Common pitfall: DCD (device configuration data) header wrong = SDRAM not initialized
- OpenOCD: `target/imxrt.cfg` or use NXP's LinkServer (`LinkServer gdbserver`)
- pyOCD target: `--target MIMXRT1060` etc.

## NXP LPC Debug Notes

- LPC55S69: Dual Cortex-M33 with TrustZone
- LPC546xx: Cortex-M4F
- LPC1768/LPC1769: Legacy Cortex-M3, still widely deployed
- CMSIS-DAP probe built into LPCXpresso boards
- LPC55 secure boot: debug access requires provisioned debug credential certificate
- LPC55 dual-core: core 1 must be explicitly started from core 0 via SYSCON
- Common issue: ISP (in-system programming) mode entered unexpectedly -- check ISP pin state
- Common issue: LPC5500 GPIO port 0 vs port 1 confusion -- different register blocks

## TI MSP432/CC13xx/CC26xx Debug Notes

- MSP432P401R: Cortex-M4F @ 48 MHz, ultra-low-power
- CC2652R/CC1352R: Cortex-M4F (main) + Cortex-M0 (radio), BLE 5 / Sub-GHz / Thread
- CC2340R5: Latest BLE-only, Cortex-M0+ ultra-low-cost
- XDS110 debug probe built into LaunchPad boards, also works as standalone
- OpenOCD: `target/ti_cc13xx_cc26xx.cfg`, `target/ti_msp432.cfg`
- CC26xx radio core (Cortex-M0) not directly debuggable via JTAG -- use RF driver API traces
- CC26xx common issue: RF patch not loaded -- radio core needs binary patch blob from TI
- MSP432 common issue: LFXT fault -- 32.768 kHz crystal oscillator fails, system falls back to REFO
- MSP432 flash wait states: must configure FLCTL before exceeding 24 MHz
- TI's CCS (Code Composer Studio) adds proprietary GDB extensions -- some features don't work with vanilla GDB
- JTAG unlock sequence for locked devices: `xds110 unlock` or via CCS

## Microchip SAM D/E/S/V Debug Notes

- SAMD21: Cortex-M0+ @ 48 MHz (Arduino Zero, Adafruit Feather M0)
- SAMD51/SAME51: Cortex-M4F @ 120 MHz (Adafruit Feather M4, Grand Central)
- SAME70/SAMV71: Cortex-M7 @ 300 MHz (automotive/industrial)
- SWD via Atmel-ICE, J-Link, CMSIS-DAP, or EDBG (built into Xplained boards)
- OpenOCD: `target/atsame5x.cfg`, `target/at91samdXX.cfg`
- Device Service Unit (DSU): security bit locks debug access permanently until chip erase
- SAMD21 common issue: NVM BOOTPROT fuse locks bootloader region, prevents flash writes to first N KB
- SAMD21 common issue: generic clock (GCLK) configuration complex, wrong clock source = peripherals don't work
- SAMD51 cache: CMCC (Cortex M Cache Controller) separate from ARM cache architecture
- SAME70 MPU regions differ from standard ARM MPU -- Microchip-specific region attributes
- ASF (Atmel Software Framework) vs START vs Harmony: three incompatible HAL generations
  - ASF3 for SAM3/4, ASF4/START for SAMD/SAME5x, Harmony 3 for all (current)

## GD32 / CH32 Debug Notes (Chinese MCU Ecosystem)

- GD32F103: Cortex-M3 STM32F103 clone, pin-compatible, faster clock (108 MHz vs 72 MHz)
- GD32F303: Cortex-M4 with FPU, STM32F303-like
- GD32VF103: RISC-V (Bumblebee core), same peripherals as GD32F103 but different CPU
- CH32V003: RISC-V @ 48 MHz, ultra-low-cost ($0.10), popular in hobby projects
- CH32V307: RISC-V with USB HS OTG, Ethernet
- CH32X035: RISC-V with USB PD (power delivery) controller built in
- WCH-Link debug probe for CH32 series (proprietary protocol, NOT standard CMSIS-DAP)
- GD32 SWD works with standard J-Link/ST-Link/CMSIS-DAP
- GD32 flash: some parts have different page sizes than STM32 equivalents -- check errata
- GD32 USB: PMA (packet memory area) layout differs from STM32 -- USB HAL code not directly portable
- GD32VF103: uses RISC-V debug spec, needs specific OpenOCD config: `target/gd32vf103.cfg`
- CH32V003 uses single-wire debug (SWD-like but proprietary) -- only WCH-Link supported
- CH32V307: standard RISC-V JTAG, works with OpenOCD + FTDI adapter
- Common issue: GD32 labeled as STM32 on counterfeits -- check DBGMCU_IDCODE (0xE0042000)
  - GD32F103: IDCODE may read as 0x410 (same as STM32F103) but flash/RAM behavior differs
- Common issue: GD32 flash programming slower with ST-Link -- use GD32-specific OpenOCD flash driver

## STM32 Extended Family Notes

### STM32H7 Specific

- Dual Cortex-M7 (480 MHz) + Cortex-M4 (240 MHz) on H745/H747/H755/H757
- Complex memory map: ITCM, DTCM, AXI SRAM, SRAM1-4, backup SRAM
  - DTCM (0x20000000): fastest, CPU-only, NOT DMA-accessible
  - AXI SRAM (0x24000000): DMA-accessible, cached
  - SRAM1-3 (0x30000000-0x30047FFF): D2 domain, DMA-accessible, not cached by default
- MUST configure MPU for DMA buffers: set region to non-cacheable or use cache maintenance
- Voltage scaling: VOS (voltage output scaling) must match clock speed
  - VOS0 = boost mode (480 MHz), requires SYSCFG_PWRCR.ODEN
- BDMA vs DMA1/DMA2: BDMA operates from D3 domain, can only access D3 SRAM (0x38000000)
- OpenOCD: `target/stm32h7x.cfg` for single-core, `target/stm32h7x_dual_bank.cfg` for dual-core
- Dual-core debug: both cores halt independently, use `monitor cortex_m maskisr on` to prevent race

### STM32U5 Specific

- Cortex-M33 with TrustZone
- SMPS + LDO power management, complex power modes
- AES/PKA/HASH hardware crypto -- debug access may be restricted in secure state
- TZEN (TrustZone Enable) OTP fuse: once set, cannot be reverted
- Secure/non-secure memory partitioning via SAU + IDAU
- Debug in secure state requires secure debug authentication (certificate-based)
- Common issue: TZEN accidentally enabled -- device appears to lock up, only secure code can run

### STM32WB/WL Specific

- STM32WB55: Cortex-M4 (app) + Cortex-M0+ (BLE radio), shared SRAM
- STM32WL55: Cortex-M4 + Cortex-M0+, LoRa/Sub-GHz radio
- Radio core (M0+) firmware is pre-built binary from ST (stm32wb_copro_wireless_binaries)
- Cannot debug M0+ radio core directly -- use IPCC (inter-processor communication) mailbox traces
- Common issue: FUS (firmware upgrade services) version mismatch -- M0+ won't start
- Common issue: BLE stack crash on M0+ invisible from M4 debugger -- check IPCC error flags
- Flash: shared between cores, M4 must request flash access via HSEM (hardware semaphore)

## CAN-FD Debug Reference

### CAN-FD vs Classic CAN

| Feature | Classic CAN | CAN-FD |
|---------|-------------|--------|
| Max data | 8 bytes | 64 bytes |
| Arbitration speed | Up to 1 Mbit/s | Up to 1 Mbit/s |
| Data phase speed | Same as arbitration | Up to 8 Mbit/s |
| CRC | 15-bit | 17-bit or 21-bit |
| Error handling | Same | Enhanced |

### CAN-FD Bit Timing

Two separate bit timing configurations:
- **Nominal (arbitration)**: standard CAN timing, up to 1 Mbit/s
- **Data phase**: faster timing for payload, up to 8 Mbit/s

Transceiver must support CAN-FD data rate. Classic CAN transceivers work only at nominal speed.

### CAN-FD Debugging Checklist

1. Transceiver supports CAN-FD? (TJA1051T/3 or MCP2558FD, NOT MCP2551)
2. 120-ohm termination at BOTH ends of bus?
3. Bus length vs data rate: >2m at 5 Mbit/s requires careful stub management
4. Nominal bit timing matches all nodes?
5. Data phase bit timing matches all nodes?
6. TDC (transmitter delay compensation) enabled for data rates > 2 Mbit/s?
7. BRS (bit rate switch) flag set in TX frames?
8. FDF (FD format) flag set?
9. All nodes on bus support CAN-FD? (Classic CAN node will error-frame FD messages)

### CAN-FD Common Issues

**CAN-FD frames cause bus-off on one node**:
- That node doesn't support CAN-FD, sees FD frame as error
- Fix: ensure all nodes are FD-capable, or use gateway to bridge FD and classic segments

**Data phase errors but arbitration works**:
- TDC offset wrong -- measure with oscilloscope/logic analyzer
- Bus length too long for data rate -- reduce speed or shorten bus
- Transceiver rise/fall time too slow for data rate

**STM32 FDCAN peripheral notes**:
- Message RAM must be configured before use (FDCAN_RXGFC, etc.)
- Message RAM shared between FDCAN instances -- watch for overlap
- Clock source: FDCAN kernel clock separate from APB clock, check RCC_CCIPR
- FDCAN_CCCR.INIT must be set to configure, cleared to start
- BRS requires both FDCAN_CCCR.BRSE=1 and BRS flag in TX element

### Logic Analyzer CAN-FD Capture

- Saleae Logic 2 supports CAN-FD decode (analyzer type: "CAN-FD")
- Minimum sample rate: 40 MS/s for 5 Mbit/s data phase (8x oversampling)
- Single channel capture: CAN_TX or CAN_RX pin (not differential CAN_H/CAN_L)
- To see both TX and RX separately: capture both CAN_TX and CAN_RX GPIO pins

## UART/USART Extended Debug

### Common Baud Rate Issues

| Desired Baud | fPCLK | BRR Value | Actual Baud | Error |
|-------------|-------|-----------|-------------|-------|
| 9600 | 72 MHz | 7500 | 9600.0 | 0.00% |
| 115200 | 72 MHz | 625 | 115200.0 | 0.00% |
| 115200 | 48 MHz | 416.67 | 115384.6 | 0.16% |
| 921600 | 72 MHz | 78.125 | 923076.9 | 0.16% |
| 2000000 | 72 MHz | 36 | 2000000.0 | 0.00% |

Baud rate error > 2% will cause frame errors. Common with certain fPCLK/baud combinations.

### UART DMA Circular Buffer Pattern

Most common UART debug issue: lost data in high-speed receive.
- DMA circular mode with half-transfer + transfer-complete interrupts
- Idle line detection (USART_CR1.IDLEIE) for variable-length packets
- Common bug: processing in HT interrupt overlaps TC interrupt = double-processing

### RS-485 Direction Control

- STM32 has hardware DE (driver enable) pin via USART_CR3.DEM
- Common issue: DE timing -- assertion/deassertion delay too short for transceiver
- Adjust USART_CR1.DEAT (assertion time) and USART_CR1.DEDT (deassertion time)

## USB Debug Notes

### USB Common Issues on STM32

- USB DP needs 1.5k pull-up to 3.3V for full-speed detection -- some boards use GPIO-controlled pull-up
- USB clock must be exactly 48 MHz -- check RCC PLL configuration, CRS (clock recovery system) for HSI48
- PMA (packet memory area) on STM32F1/F3/L0: 16-bit accessible only, packet buffer descriptors have alignment requirements
- OTG peripherals (F4/F7/H7): FIFO allocation must match endpoint configuration
- Common issue: USB enumeration fails intermittently -- check VBUS sense configuration (OTG_FS_GCCFG.VBDEN)
- Common issue: USB works in debug but not standalone -- debugger holds reset long enough for host to re-enumerate

### USB Logic Analyzer Capture

- Saleae Logic Pro 16 can decode USB LS/FS (1.5/12 Mbit/s)
- USB HS (480 Mbit/s) requires specialized USB protocol analyzer (Beagle USB 480)
- Capture DP and DM lines, set analyzer to "USB LS and FS"
- Minimum sample rate: 50 MS/s for full-speed USB (12 Mbit/s)

## Timer/PWM Debug Notes

### Common Timer Issues

1. **PWM output not working**: GPIO not configured as AF, or wrong AF number for timer channel
2. **Timer frequency wrong**: prescaler and ARR relationship: f_PWM = f_TIM / ((PSC+1) * (ARR+1))
3. **Complementary outputs (TIM1/TIM8)**: MOE (main output enable) bit in BDTR must be set
4. **Dead time insertion**: BDTR.DTG field uses non-linear encoding -- check reference manual formula
5. **Timer overflow interrupt fires twice**: clear interrupt flag (SR.UIF) at START of ISR, not end
6. **Input capture noise**: enable digital filter (ICxF bits in CCMR) before enabling capture

### Motor Control Timer Debugging

- Center-aligned mode (CMS != 00): counter counts up then down, update events at both peaks
- Break input (BKIN): emergency stop, forces outputs to safe state -- check if accidentally triggered
- Common: MOE cleared by break event, must be re-enabled in software

## ADC Debug Notes

### Common ADC Issues

1. **ADC reads 0 or 4095 always**: check analog input pin configured as analog mode (MODER = 0b11), not GPIO
2. **ADC values noisy**: add sampling time (increase SMP bits), add hardware RC filter on input
3. **ADC conversion never completes**: ADC clock out of range, check ADC prescaler and APB clock
4. **Multi-channel scan mode wrong order**: check rank configuration in SQR registers
5. **DMA overrun**: DMA not fast enough to service ADC at configured sample rate

### ADC + DMA Pattern

- Configure DMA in circular mode for continuous conversion
- STM32H7: ADC DMA requests go through DMAMUX -- must configure DMAMUX channel
- Common bug: DMA buffer alignment -- 32-bit aligned for word transfers, cache-line aligned on M7

## Power Management Debug

### Low-Power Mode Debug Issues

- **SWD disconnects in sleep**: debug interface clock gated in Stop/Standby mode
  - STM32: set DBGMCU_CR.DBG_STOP / DBG_STANDBY to keep debug active
  - Nordic: NRF_POWER->SYSTEMOFF debug: use `nrfjprog --pinreset`
- **Device won't wake from Stop mode**: wrong wakeup source configured, RTC not running
- **Current consumption too high in sleep**: peripheral clocks not disabled, GPIO leaking current
  - Check all GPIO pins: floating inputs on powered pins waste current
  - Use analog mode (MODER=0b11) for unused pins to minimize leakage
