# Cortex-M Fault Register Reference

## CFSR (0xE000ED28) -- Configurable Fault Status Register

Composed of three sub-registers:

### UFSR (bits [31:16]) -- Usage Fault Status
| Bit | Name | Description |
|-----|------|-------------|
| 25 | DIVBYZERO | Division by zero (requires CCR.DIV_0_TRP=1) |
| 24 | UNALIGNED | Unaligned access (requires CCR.UNALIGN_TRP=1) |
| 20 | STKOF | Stack overflow (Cortex-M33+ with stack limit) |
| 19 | NOCP | No coprocessor (FPU not enabled in CPACR) |
| 18 | INVPC | Invalid PC load on exception return |
| 17 | INVSTATE | Invalid state (EPSR.T=0 or bad IT state) |
| 16 | UNDEFINSTR | Undefined instruction |

### BFSR (bits [15:8]) -- Bus Fault Status
| Bit | Name | Description |
|-----|------|-------------|
| 15 | BFARVALID | BFAR holds valid address |
| 13 | LSPERR | Bus fault on FP lazy state preservation |
| 12 | STKERR | Bus fault on exception stacking |
| 11 | UNSTKERR | Bus fault on exception unstacking |
| 10 | IMPRECISERR | Imprecise data bus error (BFAR invalid) |
| 9 | PRECISERR | Precise data bus error (BFAR valid) |
| 8 | IBUSERR | Instruction bus error |

### MMFSR (bits [7:0]) -- MemManage Fault Status
| Bit | Name | Description |
|-----|------|-------------|
| 7 | MMARVALID | MMFAR holds valid address |
| 5 | MLSPERR | MemManage fault on FP lazy state preservation |
| 4 | MSTKERR | MemManage fault on stacking |
| 3 | MUNSTKERR | MemManage fault on unstacking |
| 1 | DACCVIOL | Data access violation (MMFAR valid) |
| 0 | IACCVIOL | Instruction access violation |

## HFSR (0xE000ED2C) -- HardFault Status Register
| Bit | Name | Description |
|-----|------|-------------|
| 31 | DEBUGEVT | Debug event caused HardFault |
| 30 | FORCED | Forced HardFault (escalated from configurable fault) |
| 1 | VECTTBL | Vector table read error |

## MMFAR (0xE000ED34) -- MemManage Fault Address
Valid only when MMFSR.MMARVALID=1. Contains the address that caused the MemManage fault.

## BFAR (0xE000ED38) -- Bus Fault Address
Valid only when BFSR.BFARVALID=1. Contains the address that caused the bus fault.

## DFSR (0xE000ED30) -- Debug Fault Status
| Bit | Name | Description |
|-----|------|-------------|
| 4 | EXTERNAL | External debug request |
| 3 | VCATCH | Vector catch |
| 2 | DWTTRAP | DWT match |
| 1 | BKPT | BKPT instruction or FPB match |
| 0 | HALTED | Halt request |

## SHCSR (0xE000ED24) -- System Handler Control and State
Controls which fault handlers are enabled:
| Bit | Name | Description |
|-----|------|-------------|
| 18 | USGFAULTENA | Usage fault handler enabled |
| 17 | BUSFAULTENA | Bus fault handler enabled |
| 16 | MEMFAULTENA | MemManage fault handler enabled |

If a configurable fault handler is not enabled, the fault escalates to HardFault.

## CCR (0xE000ED14) -- Configuration and Control
| Bit | Name | Description |
|-----|------|-------------|
| 4 | DIV_0_TRP | Enable divide-by-zero UsageFault |
| 3 | UNALIGN_TRP | Enable unaligned access UsageFault |
| 1 | USERSETMPEND | Allow unprivileged STIR access |
| 0 | NONBASETHRDENA | Allow thread mode from non-base level |
