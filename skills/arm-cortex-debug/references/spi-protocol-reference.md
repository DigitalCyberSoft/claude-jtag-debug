# SPI Protocol Reference for Debug

## SPI Modes

| Mode | CPOL | CPHA | Clock Idle | Data Sampled | Data Shifted |
|------|------|------|------------|-------------|-------------|
| 0 | 0 | 0 | Low | Rising edge | Falling edge |
| 1 | 0 | 1 | Low | Falling edge | Rising edge |
| 2 | 1 | 0 | High | Falling edge | Rising edge |
| 3 | 1 | 1 | High | Rising edge | Falling edge |

## Identifying SPI Mode on Logic Analyzer

1. **Clock idle state**: Look at SCK when CS is high (inactive)
   - Idle low = CPOL=0 (Mode 0 or 1)
   - Idle high = CPOL=1 (Mode 2 or 3)

2. **Data valid edge**: Look at when MOSI/MISO data transitions relative to clock
   - Data stable on rising edge = CPHA=0 for CPOL=0, CPHA=1 for CPOL=1
   - Data stable on falling edge = CPHA=1 for CPOL=0, CPHA=0 for CPOL=1

## Common SPI Failure Patterns on Logic Analyzer

### Wrong CPOL
**Symptom**: Data decodes as shifted/wrong values, clock idles at wrong level
**On analyzer**: SCK idle state doesn't match expected mode
**Root cause**: Register CPOL bit doesn't match device requirements

### Wrong CPHA
**Symptom**: Data shifted by half a bit period, some bits decode wrong
**On analyzer**: Data transitions align with sampling edge instead of shifting edge
**Root cause**: Register CPHA bit inverted

### Clock Too Fast
**Symptom**: Intermittent errors, some transactions work, some don't
**On analyzer**: Clock edges are rounded/slow, data transitions overlap clock
**Root cause**: SPI baud rate prescaler too low for PCB/wiring

### CS Timing Violation
**Symptom**: First byte corrupted or device doesn't respond
**On analyzer**: Too little time between CS falling and first clock edge
**Root cause**: Missing setup time, need delay after asserting CS

### Missing CS (Software NSS)
**Symptom**: Device ignores all transactions
**On analyzer**: CS never goes low, or stays low permanently
**Root cause**: SSM/SSI/SSOE misconfiguration, GPIO not toggling CS

### MISO Floating
**Symptom**: MISO reads 0xFF or random data
**On analyzer**: MISO has no clear transitions, stays high or is noisy
**Root cause**: Device not connected, CS wrong, or device in wrong mode

### Overrun (OVR)
**Symptom**: Data loss, SPI peripheral stops responding
**On analyzer**: Transactions look correct on wire but MCU reports errors
**Root cause**: DMA not configured or interrupt handler too slow to read DR

## STM32 SPI Register Quick Reference

### CR1 Key Fields
- **BR[2:0]**: Baud rate = f_PCLK / (2^(BR+1)). BR=0 -> /2, BR=7 -> /256
- **CPOL**: Clock polarity (0=idle low, 1=idle high)
- **CPHA**: Clock phase (0=first edge sample, 1=second edge sample)
- **LSBFIRST**: 0=MSB first, 1=LSB first
- **SSM**: Software slave management (1=SS pin ignored, use SSI)
- **SSI**: Internal slave select (set=1 for master with SSM=1)
- **MSTR**: Master mode (1=master)
- **SPE**: SPI enable (1=enabled)
- **DFF**: Data frame format (0=8-bit, 1=16-bit) [F1/F4 only]

### SR Key Flags
- **TXE**: TX buffer empty (1=ready for new data)
- **RXNE**: RX buffer not empty (1=data available)
- **BSY**: Busy (1=transfer in progress)
- **OVR**: Overrun (1=data lost because RX not read in time)
- **MODF**: Mode fault (1=SS pulled low in master mode without SSM)
