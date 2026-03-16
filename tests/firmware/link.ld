/* Linker script for QEMU lm3s6965evb (Cortex-M3) */
/* Flash: 256KB at 0x00000000, SRAM: 64KB at 0x20000000 */

MEMORY
{
    FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 256K
    SRAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K
}

_stack_top = ORIGIN(SRAM) + LENGTH(SRAM);

SECTIONS
{
    .text :
    {
        KEEP(*(.vectors))
        *(.text*)
        *(.rodata*)
    } > FLASH

    .data :
    {
        *(.data*)
    } > SRAM AT > FLASH

    .bss :
    {
        *(.bss*)
        *(COMMON)
    } > SRAM
}
