
#include <stdint.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
/* 回调前后被调方保存的向量寄存器：Windows xmm6–15（xmm-windows.S），Apple arm64 d8–d15（simd-arm64.S）。 */
#if defined(_WIN32) || (defined(__APPLE__) && defined(__aarch64__))
extern int xmm_check(void (*cb)(void));
extern void xmm_noop(void);
extern void xmm_bad(void);
API int tx_xmm_probe(void (*cb)(void)) { return xmm_check(cb); }
API int tx_xmm_control(int bad) { return xmm_check(bad ? xmm_bad : xmm_noop); }
#endif
API uintptr_t tx_buffer_address(unsigned char *p) { return (uintptr_t)p; }
API int tx_buffer_probe(unsigned char *p, void (*grow)(void), uintptr_t (*current)(void)) {
    uintptr_t before = (uintptr_t)p;
    grow();
    /* Only compare integer addresses; never dereference p after re-entry. */
    return before != current();
}
API double tx_seed(void) { return 1.25; }

API int tx_buffer_duplicate(unsigned char *a, unsigned char *b, void (*cb)(void), uintptr_t (*current)(void)) {
    uintptr_t x=(uintptr_t)a, y=(uintptr_t)b; cb(); return x != y || x != current();
}
