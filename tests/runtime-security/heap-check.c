/* Test-only page heap (security review item 2, docs/design/v052-heap-check-v0.1-20261024.md): this driver links the
   kernel generated with --generic=heapcheck and calls the three allocator routines directly (through the thunks below).
   "ok" exercises the allocator (every size class up to 3 pages, alignment, realloc, NULL and refused arguments); every
   other argument is a positive control that must be caught: ledger and canary reports end through __tx_trap (code 4012, message
   "heapcheck: ..."), guard and quarantine hits end with an access violation. The kernel's own __tx_trap reads the
   thread block, which a C caller does not have, so the harness weakens it (objcopy --weaken-symbol) and the
   definition below takes its place. macOS has no GNU objcopy and its kernels are ELF objects for Taixu's own linker:
   there the driver links the page heap alone (bootstrap/native_heap_check_probe.tx, a Mach-O object without __tx_trap)
   and also defines the libSystem calls it makes (the __tx_mac_* trampolines of unix_sys.tx, same result convention). */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32) || defined(__aarch64__)
/* arm64: the kernel's private convention is AAPCS64. */
#define TXABI
#else
#define TXABI __attribute__((ms_abi))
#endif
#if defined(__APPLE__)
#include <errno.h>
#include <sched.h>
#include <sys/mman.h>
#define SYM(name) "_" name
#else
#define SYM(name) name
#endif

/* Image and tables the generated program normally provides: an empty image makes a trap report "<unknown>:0:0". */
uint64_t __tx_nrt_image[64];
uint64_t __tx_bi_table[64];
uint64_t __tx_fn_table_data[64];

/* The kernel's private convention lists rsi and rdi as callee-saved, but its Linux branch passes raw syscall arguments in
   them without saving (rtfn.tx, rtCallC: that holds because no Taixu caller keeps a value in a register across a call).
   An optimizing C compiler does keep values there, so every call goes through a thunk that saves them. */
#if defined(__aarch64__)
/* arm64 has no such branch (macOS reaches the system through libSystem): the routines are called directly. */
unsigned char *__tx_native_mem_alloc(int64_t size, int64_t align);
void __tx_native_mem_free(void *ptr);
unsigned char *__tx_native_mem_realloc(void *ptr, int64_t old, int64_t size, int64_t align);
#define hc_alloc __tx_native_mem_alloc
#define hc_free __tx_native_mem_free
#define hc_realloc __tx_native_mem_realloc
#else
TXABI unsigned char *hc_alloc(int64_t size, int64_t align);
TXABI void hc_free(void *ptr);
TXABI unsigned char *hc_realloc(void *ptr, int64_t old, int64_t size, int64_t align);
#define THUNK(name, target) ".globl " SYM(name) "\n" SYM(name) ":\npushq %rsi\npushq %rdi\nsubq $40,%rsp\ncall " \
    SYM(target) "\naddq $40,%rsp\npopq %rdi\npopq %rsi\nret\n"
__asm__(".text\n" THUNK("hc_alloc", "__tx_native_mem_alloc") THUNK("hc_free", "__tx_native_mem_free")
        THUNK("hc_realloc", "__tx_native_mem_realloc"));
#endif

#if defined(__APPLE__)
/* The page heap's libSystem calls, with the trampolines' result convention: non-negative on success, -errno on failure. */
TXABI int64_t __tx_mac_mmap(void *addr, uint64_t length, int64_t prot, int64_t flags, int64_t fd, int64_t offset) {
    void *p = mmap(addr, (size_t)length, (int)prot, (int)flags, (int)fd, (off_t)offset);
    return p == MAP_FAILED ? -(int64_t)errno : (int64_t)(uintptr_t)p;
}
TXABI int64_t __tx_mac_mprotect(void *addr, uint64_t length, int64_t prot) {
    return mprotect(addr, (size_t)length, (int)prot) == 0 ? 0 : -(int64_t)errno;
}
TXABI int64_t __tx_mac_munmap(void *addr, uint64_t length) { return munmap(addr, (size_t)length) == 0 ? 0 : -(int64_t)errno; }
TXABI int64_t __tx_mac_sched_yield(void) { return sched_yield() == 0 ? 0 : -(int64_t)errno; }
#if !defined(__aarch64__)
TXABI void *__tx_linux_memcpy(void *dst, const void *src, uint64_t n) { return memcpy(dst, src, (size_t)n); }
TXABI void *__tx_linux_memset(void *dst, int64_t byte, uint64_t n) { return memset(dst, (int)byte, (size_t)n); }
#endif
#endif

TXABI void __tx_trap(uint64_t fn, uint64_t pc, uint64_t code, const char *message, uint64_t length) {
    (void)fn; (void)pc;
    printf("TRAP code=%llu %.*s\n", (unsigned long long)code, (int)length, message);
    fflush(stdout);
    exit(code == 4012 ? 88 : 89);
}

static void require(int ok, const char *what) {
    if (!ok) { printf("FAIL heapcheck: %s\n", what); fflush(stdout); exit(1); }
}

static void ok(void) {
    unsigned char *keep[64];
    for (int64_t size = 0; size < 64; ++size) {
        unsigned char *p = hc_alloc(size, 16);
        require(p && ((uintptr_t)p & 15) == 0, "aligned allocation");
        memset(p, 0x5a, (size_t)size);
        keep[size] = p;
    }
    for (int64_t size = 0; size < 64; ++size) hc_free(keep[size]);
    for (int64_t size = 4000; size < 12400; size += 97) {  /* crosses the one-, two- and three-page boundaries */
        unsigned char *p = hc_alloc(size, 8);
        require(p != NULL, "page-sized allocation");
        memset(p, 0x33, (size_t)size);
        hc_free(p);
    }
    unsigned char *p = hc_alloc(10, 1);
    memcpy(p, "0123456789", 10);
    unsigned char *q = hc_realloc(p, 10, 5000, 16);
    require(q && q != p && memcmp(q, "0123456789", 10) == 0, "realloc copies to a new block");
    memset(q + 10, 0x11, 4990);
    q = hc_realloc(q, 5000, 3, 16);
    require(q && memcmp(q, "012", 3) == 0, "shrinking realloc keeps the prefix");
    hc_free(q);
    hc_free(NULL);
    /* More frees than the quarantine ring holds (65536): the oldest blocks are released and deleted from the ledger,
       and the blocks still live must stay findable (backward-shift deletion keeps their probe chains intact). */
    static unsigned char *live[1000];
    for (int i = 0; i < 1000; ++i) { live[i] = hc_alloc(8, 8); require(live[i] != NULL, "live block"); live[i][0] = (unsigned char)i; }
    for (int i = 0; i < 70000; ++i) hc_free(hc_alloc(i & 63, 16));
    for (int i = 0; i < 1000; ++i) { require(live[i][0] == (unsigned char)i, "live block intact"); hc_free(live[i]); }
    require(hc_alloc(-1, 16) == NULL && hc_alloc(8, 3) == NULL &&
            hc_alloc(8, 32) == NULL && hc_alloc((int64_t)1 << 50, 16) == NULL,
            "refused arguments yield NULL");
    printf("PASS heapcheck: allocator\n");
}

int main(int argc, char **argv) {
    const char *control = argc > 1 ? argv[1] : "ok";
    if (strcmp(control, "ok") == 0) { ok(); return 0; }
    unsigned char *p = hc_alloc(20, 16);  /* 12 bytes of slack before the guard page */
    require(p != NULL, "control allocation");
    static unsigned char outside[64];
    if (strcmp(control, "double-free") == 0) { hc_free(p); hc_free(p); }
    else if (strcmp(control, "invalid-free") == 0) hc_free(outside + 32);
    else if (strcmp(control, "interior-free") == 0) hc_free(p + 16);
    else if (strcmp(control, "slack-write") == 0) { p[20] = 0; hc_free(p); }
    else if (strcmp(control, "left-write") == 0) { p[-33] = 0; hc_free(p); }
    else if (strcmp(control, "header-write") == 0) { p[-24] = 21; hc_free(p); }
    else if (strcmp(control, "guard-write") == 0) p[32] = 0;
    else if (strcmp(control, "use-after-free") == 0) { hc_free(p); p[0] = 0; }
    else if (strcmp(control, "realloc-stale") == 0) { unsigned char *q = hc_realloc(p, 20, 40, 16); (void)q; p[0] = 0; }
    else { printf("unknown control %s\n", control); return 64; }
    printf("MISSED heapcheck control %s\n", control);
    return 1;
}
