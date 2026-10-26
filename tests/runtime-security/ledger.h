/* Independent test allocator shared by the runtime fault drivers: guard pages, left canaries, quarantine, ledger,
   and the n-th allocation failure (fail_at). Each driver is one translation unit that includes this header. */
#ifndef TAIXU_TEST_LEDGER_H
#define TAIXU_TEST_LEDGER_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <windows.h>
#define TXABI
#else
#include <sys/mman.h>
#include <unistd.h>
#if defined(__aarch64__)
/* arm64：内核的私有约定即 AAPCS64（x19 另作值槽窗口，这里的例程不用）。 */
#define TXABI
#else
#define TXABI __attribute__((ms_abi))
#endif
#endif

/* 页大小按系统取（Apple arm64 为 16 KiB；Rosetta 下的 x86_64 进程为 4 KiB）：守护页与隔离区的保护以页为单位。 */
#if defined(_WIN32)
#define PAGE ((size_t)4096)
#else
#define PAGE ((size_t)sysconf(_SC_PAGESIZE))
#endif
enum { MAX_ALLOCS = 1024 };
typedef struct { unsigned char *base, *pointer; size_t pages, size; int live; } Allocation;
static Allocation ledger[MAX_ALLOCS];
static unsigned entries, attempts, fail_at, live_count;
static void fail(const char *reason, int code) { fprintf(stderr, "DETECTOR %s\n", reason); fflush(stderr); exit(code); }
static void require(int yes, const char *why) { if (!yes) fail(why, 90); }
static Allocation *find(void *p) {
    for (unsigned i = 0; i < entries; ++i) if (ledger[i].pointer == p) return ledger + i;
    return NULL;
}
static void canary(Allocation *a) {
    if (!a->live) return;
    for (unsigned char *p = a->base + PAGE; p < a->pointer; ++p) if (*p != 0xa5) fail("left-canary", 86);
}
static void all_canaries(void) { for (unsigned i = 0; i < entries; ++i) canary(ledger + i); }
TXABI void *__tx_native_mem_alloc(uint64_t size, uint64_t alignment) {
    ++attempts;
    if (attempts == fail_at) return NULL;
    require(size > 0 && size <= 1048576 && alignment && alignment <= 16, "test allocation scope");
    require(entries < MAX_ALLOCS, "ledger capacity");
    size_t pages = ((size_t)size + PAGE - 1) / PAGE;
    size_t total = (pages + 2) * PAGE;
    unsigned char *base;
#if defined(_WIN32)
    base = VirtualAlloc(NULL, total, MEM_RESERVE, PAGE_NOACCESS);
    require(base != NULL && VirtualAlloc(base + PAGE, pages * PAGE, MEM_COMMIT, PAGE_READWRITE) != NULL, "VirtualAlloc");
#else
    base = mmap(NULL, total, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    require(base != MAP_FAILED && mprotect(base + PAGE, pages * PAGE, PROT_READ | PROT_WRITE) == 0, "mmap/mprotect");
#endif
    memset(base + PAGE, 0xa5, pages * PAGE);
    unsigned char *p = base + (pages + 1) * PAGE - (size_t)size;
    require((uintptr_t)p % alignment == 0, "requested alignment");
    ledger[entries++] = (Allocation){base, p, pages, (size_t)size, 1};
    ++live_count;
    return p;
}
TXABI void __tx_native_mem_free(void *pointer) {
    if (!pointer) return;
    Allocation *a = find(pointer);
    if (!a) fail("invalid-free", 84);
    if (!a->live) fail("double-free", 85);
    canary(a);
#if defined(_WIN32)
    DWORD old;
    require(VirtualProtect(a->base + PAGE, a->pages * PAGE, PAGE_NOACCESS, &old) != 0, "quarantine protect");
#else
    require(mprotect(a->base + PAGE, a->pages * PAGE, PROT_NONE) == 0, "quarantine protect");
#endif
    a->live = 0;
    --live_count;
}
static void range(void *pointer, size_t n) {
    if (!n) return;
    uintptr_t p = (uintptr_t)pointer;
    for (unsigned i = 0; i < entries; ++i) {
        Allocation *a = ledger + i;
        uintptr_t b = (uintptr_t)a->pointer;
        if (a->live && p >= b && p - b <= a->size && n <= a->size - (p - b)) return;
    }
    fail("copy-range", 87);
}
TXABI void *__tx_linux_memcpy(void *dst, const void *src, uint64_t n) { range(dst, n); range((void *)src, n); return memcpy(dst, src, n); }
TXABI void *__tx_linux_memset(void *dst, int byte, uint64_t n) { range(dst, n); return memset(dst, byte, n); }
static void release_quarantine(void) {
    for (unsigned i = 0; i < entries; ++i) {
        size_t total = (ledger[i].pages + 2) * PAGE;
#if defined(_WIN32)
        require(VirtualFree(ledger[i].base, 0, MEM_RELEASE) != 0, "release quarantine");
#else
        require(munmap(ledger[i].base, total) == 0, "release quarantine");
#endif
        (void)total;
    }
}
#endif
