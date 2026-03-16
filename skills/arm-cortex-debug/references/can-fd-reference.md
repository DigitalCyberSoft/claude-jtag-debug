# CAN-FD Protocol Debug Reference

## CAN-FD Frame Structure

```
┌─────────┬────────────┬─────┬─────┬────────────────┬───────────┬─────┐
│   SOF   │ Arbitration│ FDF │ BRS │  Data Phase     │    CRC    │ EOF │
│         │ (nominal)  │     │     │ (fast bit rate) │           │     │
└─────────┴────────────┴─────┴─────┴────────────────┴───────────┴─────┘
           ◄──── nominal rate ────►◄── data rate ──►◄─ nominal ─►
```

- **FDF** (FD Format): 1 = CAN-FD frame, 0 = Classic CAN
- **BRS** (Bit Rate Switch): 1 = switch to fast data rate after BRS bit
- **ESI** (Error State Indicator): 0 = error active, 1 = error passive

## CAN-FD DLC Encoding

CAN-FD extends DLC beyond 8:

| DLC | Data Bytes |
|-----|-----------|
| 0-8 | 0-8 |
| 9 | 12 |
| 10 | 16 |
| 11 | 20 |
| 12 | 24 |
| 13 | 32 |
| 14 | 48 |
| 15 | 64 |

## Bit Timing Calculation

### Nominal Phase (Arbitration)

```
Nominal bit time = (1 + NTSEG1 + NTSEG2) time quanta
Time quantum (NTQ) = (NBRP + 1) / f_CAN_CLK
Nominal bit rate = f_CAN_CLK / ((NBRP + 1) * (1 + NTSEG1 + NTSEG2))
Sample point = (1 + NTSEG1) / (1 + NTSEG1 + NTSEG2) * 100%
```

### Data Phase (Fast)

```
Data bit time = (1 + DTSEG1 + DTSEG2) time quanta
Data TQ = (DBRP + 1) / f_CAN_CLK
Data bit rate = f_CAN_CLK / ((DBRP + 1) * (1 + DTSEG1 + DTSEG2))
```

### Recommended Sample Points

| Bit Rate | Sample Point |
|----------|-------------|
| 125 kbit/s | 87.5% |
| 250 kbit/s | 87.5% |
| 500 kbit/s | 87.5% |
| 1 Mbit/s | 75-80% |
| 2 Mbit/s (data) | 70-80% |
| 5 Mbit/s (data) | 60-70% |

## Transmitter Delay Compensation (TDC)

Required for data rates > 2 Mbit/s. Compensates for transceiver loop delay.

```
TDC offset = round(transceiver_delay / data_TQ)
Typical transceiver delay: 100-250 ns (depends on transceiver IC)
```

For data rate 5 Mbit/s (200 ns bit time), a 150 ns transceiver delay without TDC means the sample point sees the previous bit's transition.

## STM32 FDCAN Register Map

| Register | Offset | Key Fields |
|----------|--------|-----------|
| FDCAN_CCCR | 0x018 | INIT, CCE, ASM, CSR, MON, DAR, TEST, FDOE, BRSE |
| FDCAN_NBTP | 0x01C | NSJW, NBRP, NTSEG1, NTSEG2 |
| FDCAN_DBTP | 0x00C | DSJW, DBRP, DTSEG1, DTSEG2, TDC |
| FDCAN_TDCR | 0x048 | TDCF, TDCO |
| FDCAN_ECR | 0x040 | TEC, REC, RP, CEL |
| FDCAN_PSR | 0x044 | LEC, ACT, EP, EW, BO, DLEC, RESI, RBRS, RFDF |
| FDCAN_TXBAR | 0x0D0 | Add Tx buffer request |
| FDCAN_RXGFC | 0x080 | Global filter configuration |

## Common FDCAN Debug Patterns

### Bus-Off Recovery

When TEC > 255, node enters bus-off. Recovery:
1. Wait for 128 * 11 recessive bits (automatic if FDCAN_CCCR.DAR = 0)
2. Or: set FDCAN_CCCR.INIT = 1, reconfigure, clear INIT
3. Check FDCAN_ECR.CEL (cumulative error log) for root cause

### Error Frame Storm

**Symptom**: FDCAN_ECR.TEC/REC climbing rapidly
**Causes**:
- Baud rate mismatch between nodes
- Missing termination (measure bus with oscilloscope: should see ~2V differential)
- Stub length too long (>30cm at 1 Mbit/s, <15cm at 5 Mbit/s)
- Ground potential difference between nodes

### Silent Monitoring Mode

FDCAN_CCCR.MON = 1: node receives but does not transmit ACK or error frames.
Useful for bus analysis without interfering with traffic.

**Debug trap**: accidentally left in monitor mode after sniffing = node appears dead on bus.

## Logic Analyzer CAN-FD Capture Settings

For Saleae Logic 2:
- Analyzer: "CAN" (supports both classic and FD)
- Channel: connect to CAN_TX or CAN_RX pin (NOT differential bus)
- Bit rate: set to nominal rate
- Sample rate requirements:

| Data Rate | Min Sample Rate |
|----------|----------------|
| 500 kbit/s | 4 MS/s |
| 1 Mbit/s | 8 MS/s |
| 2 Mbit/s | 16 MS/s |
| 5 Mbit/s | 40 MS/s |
| 8 Mbit/s | 64 MS/s |

Note: Saleae Logic 2 CAN-FD analyzer may not decode data phase correctly at >5 Mbit/s with insufficient sample rate. If errors appear only in data phase, increase sample rate.
