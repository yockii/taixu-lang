/* Foreign-library side of the page-heap product controls (heap-probe.tx): writes through a Bytes buffer at an offset
   past its end, and keeps a buffer's address to write through it after Taixu has freed the buffer. */
#include <stdint.h>
#if defined(_WIN32)
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

static volatile uint8_t *kept;

EXPORT void tx_hc_poke(uint8_t *data, uint64_t offset) { ((volatile uint8_t *)data)[offset] = 0x5a; }
EXPORT void tx_hc_keep(uint8_t *data) { kept = data; }
EXPORT void tx_hc_poke_kept(uint64_t offset) { kept[offset] = 0x5a; }
