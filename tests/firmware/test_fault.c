/**
 * Test firmware: triggers known Cortex-M faults for testing fault analysis.
 * Target: QEMU lm3s6965evb (Cortex-M3)
 * Build: arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -T link.ld -nostartfiles -o test_fault.elf test_fault.c
 */

#include <stdint.h>

/* Vector table */
extern uint32_t _stack_top;
void Reset_Handler(void);
void HardFault_Handler(void);
void Default_Handler(void);

__attribute__((section(".vectors")))
uint32_t vectors[] = {
    (uint32_t)&_stack_top,
    (uint32_t)Reset_Handler,
    (uint32_t)Default_Handler,  /* NMI */
    (uint32_t)HardFault_Handler,
    (uint32_t)Default_Handler,  /* MemManage */
    (uint32_t)Default_Handler,  /* BusFault */
    (uint32_t)Default_Handler,  /* UsageFault */
};

/* SCB registers */
#define SCB_SHCSR   (*(volatile uint32_t*)0xE000ED24)
#define SCB_CFSR    (*(volatile uint32_t*)0xE000ED28)
#define SCB_HFSR    (*(volatile uint32_t*)0xE000ED2C)

/* Trigger INVSTATE fault: call function pointer without thumb bit */
void trigger_invstate(void) {
    void (*func)(void) = (void (*)(void))0x08000100;  /* Even address = no thumb bit */
    func();  /* This will fault with INVSTATE */
}

/* Trigger NULL pointer dereference */
void trigger_null_deref(void) {
    volatile uint32_t *ptr = (volatile uint32_t *)0;
    *ptr = 0xDEADBEEF;  /* Write to address 0 */
}

/* Trigger unaligned access (if trap enabled) */
void trigger_unaligned(void) {
    volatile uint32_t *ptr = (volatile uint32_t *)0x20000001;  /* Misaligned */
    volatile uint32_t val = *ptr;
    (void)val;
}

void HardFault_Handler(void) {
    /* Spin here -- debugger can inspect fault registers */
    while (1) {
        __asm volatile("nop");
    }
}

void Default_Handler(void) {
    while (1) {
        __asm volatile("nop");
    }
}

void Reset_Handler(void) {
    /* Enable UsageFault, BusFault, MemManage handlers */
    SCB_SHCSR |= (1 << 16) | (1 << 17) | (1 << 18);

    /* Trigger a fault -- change this to test different faults */
    trigger_invstate();

    while (1) {
        __asm volatile("nop");
    }
}
