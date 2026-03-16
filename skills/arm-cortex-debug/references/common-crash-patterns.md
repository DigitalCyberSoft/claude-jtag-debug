# Common Embedded Crash Patterns

## Hard Crashes (Immediate Fault)

### 1. NULL Pointer Dereference
**Fault**: PRECISERR, BFAR = small value (0x00-0xFF)
**Pattern**: Accessing struct member on NULL pointer: `ptr->field` where ptr=NULL
**BFAR value = field offset from struct start**
**Fix**: Check pointer before use, initialize to NULL, use static analysis

### 2. Stack Overflow
**Fault**: STKERR or MSTKERR
**Pattern**: Deep recursion, large local arrays, or heavy ISR nesting
**SP will be below stack start address**
**Fix**: Increase stack size, use heap for large buffers, reduce call depth
**Prevention**: Stack canary, MPU guard region, FreeRTOS stack overflow hook

### 3. Uninitialized Function Pointer
**Fault**: INVSTATE or UNDEFINSTR
**Pattern**: Callback pointer never set, contains 0x00000000 or garbage
**PC often at address 0 or in garbage memory**
**Fix**: Initialize all callbacks, assert before calling

### 4. Missing Volatile on Hardware Register
**Fault**: No crash, but wrong behavior
**Pattern**: Compiler optimizes away repeated reads of peripheral register
**Symptoms**: Status bits appear to never change, interrupt flags not clearing
**Fix**: Ensure peripheral register pointers use `volatile`

### 5. DMA Buffer Not in DMA-Accessible Memory
**Fault**: PRECISERR or DMA error flag
**Pattern**: Buffer on stack (DTCM on H7) not accessible by DMA
**Common on STM32H7**: DTCM at 0x20000000 is not DMA-accessible
**Fix**: Place DMA buffers in D1/D2 SRAM, use `__attribute__((section(".dma_buffer")))`

### 6. Cache Coherency (Cortex-M7)
**Fault**: No crash, corrupted data
**Pattern**: DMA writes to cached region, CPU reads stale cache
**Symptoms**: First read after DMA shows old data, subsequent reads correct after cache line eviction
**Fix**: Invalidate D-cache before reading DMA buffer, or use MPU to mark region as non-cacheable

## Slow Crashes (Delayed/Intermittent)

### 7. Heap Fragmentation
**Pattern**: malloc returns NULL after hours/days of operation
**Symptoms**: System works initially, random failures after extended runtime
**Fix**: Use fixed-size pools, avoid malloc in embedded, use static allocation

### 8. Priority Inversion
**Pattern**: High-priority task blocked by low-priority task holding mutex
**Symptoms**: Watchdog timeout on high-priority task, system appears hung
**Fix**: Priority inheritance mutexes, minimize critical section length

### 9. Interrupt Storm
**Pattern**: Peripheral interrupt flag not cleared, ISR re-enters immediately
**Symptoms**: System appears hung, only ISR runs, main loop starved
**Fix**: Always clear interrupt flag in ISR, check errata for clear-on-read quirks

### 10. Watchdog Not Fed in ISR-Heavy System
**Pattern**: Main loop watchdog kick never reached because ISR preemption
**Symptoms**: Periodic watchdog resets under heavy interrupt load
**Fix**: Feed watchdog from timer ISR, or use independent watchdog (IWDG)

## Bus Protocol Failures

### 11. SPI Mode Mismatch
**Fault**: No crash, wrong data
**Pattern**: MCU configured for Mode 0, device requires Mode 3
**Symptoms**: All bytes shifted or inverted, first/last byte wrong
**Debug**: Capture with Saleae, compare clock idle state

### 12. I2C Bus Lockup
**Fault**: I2C BUSY flag stuck
**Pattern**: Interrupted I2C transaction leaves SDA held low by slave
**Symptoms**: All subsequent I2C transactions fail, BUSY never clears
**Fix**: Toggle SCL 9 times to free slave, then STOP condition

### 13. UART Frame Error
**Pattern**: Baud rate mismatch between MCU and device
**Symptoms**: Garbage data, frame error flags
**Debug**: Measure actual baud rate on logic analyzer, compare with expected

## CAN/CAN-FD Common Issues

### 14. CAN Bus-Off
**Fault**: CAN controller enters bus-off state
**Pattern**: Too many errors (TEC > 255), often from termination or baud mismatch
**Fix**: Check 120-ohm termination at both ends, verify bit timing matches all nodes

### 15. CAN Silent Mode Left Active
**Pattern**: CAN peripheral in silent mode after debug, can receive but not transmit
**Fix**: Clear SILM bit in CAN_BTR after debug session

### 16. CAN-FD Data Phase Error
**Fault**: Error frames only during data phase, arbitration works fine
**Pattern**: TDC (transmitter delay compensation) not enabled at high data rates
**Symptoms**: intermittent CRC errors on FD frames, classic CAN works fine
**Fix**: Enable TDC in FDCAN_DBTP, set TDCO to match transceiver propagation delay

## Platform-Specific Crashes

### 17. STM32H7 AXI SRAM vs DTCM Confusion
**Fault**: PRECISERR when DMA accesses buffer
**Pattern**: Buffer allocated in DTCM (0x20000000), DMA can only access AXI SRAM (0x24000000+)
**Symptoms**: works when CPU copies data, fails when DMA copies same data
**Fix**: place DMA buffers in AXI SRAM with linker section or `__attribute__((section(".dma")))`

### 18. STM32H7 BDMA Domain Mismatch
**Fault**: BDMA transfer error flag
**Pattern**: BDMA (D3 domain) trying to access D1/D2 memory
**BDMA can only access D3 SRAM (0x38000000) and peripherals in D3 domain**
**Fix**: use DMA1/DMA2 for D1/D2 memory, BDMA only for D3

### 19. nRF52 Softdevice Stack Crash
**Fault**: device hangs or resets during BLE operation
**Pattern**: application ISR at priority 0-1 conflicts with Softdevice radio ISR
**Softdevice reserves priority levels 0-1 (and sometimes 4)**
**Fix**: all application interrupts must be priority 2+ (or 5+ depending on SD version)

### 20. ESP32 PSRAM Cache Miss Crash
**Fault**: LoadStoreError exception on ESP32 with PSRAM
**Pattern**: accessing PSRAM-backed memory from ISR or task with cache disabled
**PSRAM is accessed through cache; if cache is disabled (during flash operations), PSRAM access faults**
**Fix**: place ISR-accessed data in DRAM (IRAM_ATTR for functions, DRAM_ATTR for data)

### 21. RP2040 Multicore Spinlock Deadlock
**Fault**: both cores stuck, watchdog reset
**Pattern**: core 0 holds spinlock A, waits for B; core 1 holds B, waits for A
**RP2040 has 32 hardware spinlocks, easy to misuse in multicore code**
**Fix**: always acquire spinlocks in consistent order across both cores

### 22. SAMD21 Sync-Busy Stall
**Fault**: system hangs, no fault exception
**Pattern**: code reads register before synchronization completes between clock domains
**Some SAMD21 register reads block (stall CPU bus) until sync is done**
**If clock source is slow/stopped, sync never completes = permanent stall**
**Fix**: ensure clock source is running before accessing peripheral, check STATUS.SYNCBUSY

### 23. GD32F103 Misidentified as STM32F103
**Fault**: flash programming errors or clock configuration failure
**Pattern**: code assumes STM32F103 timing but part is actually GD32F103
**GD32F103 runs at 108 MHz (vs 72), flash wait states differ, PLL config differs**
**Fix**: read UID registers at 0x1FFFF7E0 (GD32) vs 0x1FFFF7E8 (STM32) to identify chip

### 24. NXP i.MX RT FlexRAM Misconfiguration
**Fault**: HardFault immediately after startup code
**Pattern**: FlexRAM bank assignment changed via IOMUXC_GPR registers while code runs from ITCM
**Remapping ITCM while executing from it = instant crash**
**Fix**: FlexRAM config must be done from ROM or non-ITCM code, before jump to main application

### 25. TI CC26xx CCFG JTAG Lockout
**Fault**: SWD debug connection fails completely
**Pattern**: CCFG (customer configuration) fuse disables JTAG TAP
**Accidentally deployed with debug-disabled CCFG config**
**Fix**: mass erase via XDS110 `DSLite --mode=mass_erase` (erases entire flash including CCFG)
