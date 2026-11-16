#include <stdint.h>
#include <stddef.h>
#include <valgrind/memcheck.h>

int32_t tx_ct_running(void) { return RUNNING_ON_VALGRIND ? 1 : 0; }
void tx_ct_secret(unsigned char *p, size_t n) { VALGRIND_MAKE_MEM_UNDEFINED(p, n); }
void tx_ct_public(unsigned char *p, size_t n) { VALGRIND_MAKE_MEM_DEFINED(p, n); }
int32_t tx_ct_public_bool(uint8_t value) {
    VALGRIND_MAKE_MEM_DEFINED(&value, sizeof(value));
    return value != 0;
}
int32_t tx_ct_tainted(unsigned char *p, size_t n) {
    unsigned char bits[64];
    if (!n || n > 4096) return -1;
    int32_t any = 0;
    while (n) {
        size_t count = n < sizeof(bits) ? n : sizeof(bits);
        if (VALGRIND_GET_VBITS(p, bits, count) != 1) return -2;
        for (size_t i = 0; i < count; ++i) if (bits[i]) any = 1;
        n -= count; p += count;
    }
    return any;
}
