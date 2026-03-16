#include <stdio.h>
#include <stdint.h>

volatile uint32_t global_var = 0xDEADBEEF;
volatile uint32_t counter = 0;

struct test_struct {
    uint32_t field_a;
    uint32_t field_b;
    char name[16];
};

volatile struct test_struct my_struct = { 0x1234, 0x5678, "hello" };

void inner_func(int x) {
    counter += x;
}

void middle_func(int a, int b) {
    inner_func(a + b);
}

int main(void) {
    global_var = 0xCAFEBABE;
    middle_func(10, 20);
    printf("counter = %u\n", counter);
    return 0;
}
