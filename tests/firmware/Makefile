CC = arm-none-eabi-gcc
CFLAGS = -mcpu=cortex-m3 -mthumb -nostartfiles -g -O0
LDFLAGS = -T link.ld

all: test_fault.elf test_spi.elf

test_fault.elf: test_fault.c link.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

test_spi.elf: test_spi.c link.ld
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

clean:
	rm -f *.elf *.o

.PHONY: all clean
