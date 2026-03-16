/**
 * Test firmware: configures SPI1 with known settings for register verification.
 * Target: QEMU lm3s6965evb (Cortex-M3)
 *
 * Note: QEMU lm3s6965evb has SSI (SPI-like) peripheral.
 * This is a simplified example for testing register reads.
 */

#include <stdint.h>

extern uint32_t _stack_top;
void Reset_Handler(void);
void Default_Handler(void);

__attribute__((section(".vectors")))
uint32_t vectors[] = {
    (uint32_t)&_stack_top,
    (uint32_t)Reset_Handler,
    (uint32_t)Default_Handler,  /* NMI */
    (uint32_t)Default_Handler,  /* HardFault */
};

/* LM3S6965 SSI0 registers (SPI-compatible) */
#define SSI0_BASE   0x40008000
#define SSI0_CR0    (*(volatile uint32_t*)(SSI0_BASE + 0x000))
#define SSI0_CR1    (*(volatile uint32_t*)(SSI0_BASE + 0x004))
#define SSI0_DR     (*(volatile uint32_t*)(SSI0_BASE + 0x008))
#define SSI0_SR     (*(volatile uint32_t*)(SSI0_BASE + 0x00C))
#define SSI0_CPSR   (*(volatile uint32_t*)(SSI0_BASE + 0x010))

/* SYSCTL */
#define SYSCTL_RCGC1 (*(volatile uint32_t*)0x400FE104)

void configure_spi(void) {
    /* Enable SSI0 clock */
    SYSCTL_RCGC1 |= (1 << 4);
    /* Small delay for clock to stabilize */
    volatile int i;
    for (i = 0; i < 10; i++);

    /* Disable SSI before config */
    SSI0_CR1 = 0;

    /* Set clock prescaler: SSIClk = SysClk / (CPSDVSR * (1 + SCR))
     * CPSDVSR = 2 (minimum), SCR = 0 -> SSIClk = SysClk / 2 */
    SSI0_CPSR = 2;

    /* CR0: SCR=0, SPH=0 (CPHA=0), SPO=0 (CPOL=0), FRF=0 (Motorola SPI), DSS=0x7 (8-bit) */
    SSI0_CR0 = (0 << 8) |  /* SCR = 0 */
               (0 << 7) |  /* SPH = 0 (CPHA) */
               (0 << 6) |  /* SPO = 0 (CPOL) */
               (0 << 4) |  /* FRF = 0 (Motorola SPI format) */
               (0x7);      /* DSS = 7 (8-bit data) */

    /* Enable SSI, master mode */
    SSI0_CR1 = (1 << 1);  /* SSE = 1 (enable) */
}

void send_test_data(void) {
    /* Send some test bytes */
    SSI0_DR = 0xAA;
    SSI0_DR = 0x55;
    SSI0_DR = 0xFF;
    SSI0_DR = 0x00;
}

void Default_Handler(void) {
    while (1) { __asm volatile("nop"); }
}

void Reset_Handler(void) {
    configure_spi();
    send_test_data();

    /* Breakpoint here -- debugger can read SSI0 registers */
    while (1) {
        __asm volatile("nop");
    }
}
