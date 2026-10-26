#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <windows.h>
#define TXABI
#else
#include <sys/mman.h>
#if defined(__aarch64__)
/* arm64：内核的私有约定即 AAPCS64，另以 x19 作值槽窗口（macOS 适配 M4 设计 §A2）。 */
#define TXABI
#else
#define TXABI __attribute__((ms_abi))
#endif
#endif
/* 汇编里的 C 符号名：Mach-O 加前导下划线。 */
#if defined(__APPLE__)
#define SYM(x) "_" #x
#else
#define SYM(x) #x
#endif

static uint32_t counts[4];
static uint64_t probes[4];
static uint64_t scalar;
static unsigned cascades;
uint32_t __tx_nrt_rc_flags;
TXABI uint32_t *__tx_rc_base(void) { return counts; }
TXABI uint64_t __tx_rc_scalar_flag(void) { return scalar; }
TXABI uint64_t *__tx_rc_probe_counts(void) { return probes; }
TXABI void __tx_rc_zero_cascade(uint64_t index) { if (index == 0 && counts[0] == 0) ++cascades; else cascades += 1000; }
#ifdef RC_GENERIC
/* The portable (rtfn) release hands a zero count to the unified dispatcher instead. */
TXABI void __tx_zero_dispatch(uint64_t index) { __tx_rc_zero_cascade(index); }
#endif
TXABI void __tx_retain(const uint64_t *value);
TXABI void __tx_release(const uint64_t *value);
#ifdef RC_NRT
TXABI void tx_test_retain(const uint64_t *, void *);
TXABI void tx_test_release(const uint64_t *, void *);
/* 先把值槽窗口寄存器（x86 rbx、arm64 x19，均为被调方保存）设成线程指针，再调内核例程。 */
#if defined(__aarch64__)
__asm__(".text\n.p2align 2\n.globl " SYM(tx_test_retain) "\n" SYM(tx_test_retain) ":\n"
        "stp x19, x30, [sp, #-16]!\nmov x19, x1\nbl " SYM(__tx_retain) "\nldp x19, x30, [sp], #16\nret\n"
        ".p2align 2\n.globl " SYM(tx_test_release) "\n" SYM(tx_test_release) ":\n"
        "stp x19, x30, [sp, #-16]!\nmov x19, x1\nbl " SYM(__tx_release) "\nldp x19, x30, [sp], #16\nret\n");
#else
__asm__(".text\n.globl " SYM(tx_test_retain) "\n" SYM(tx_test_retain) ":\n"
        "pushq %rbx\nsubq $32,%rsp\nmovq %rdx,%rbx\ncall " SYM(__tx_retain) "\naddq $32,%rsp\npopq %rbx\nret\n"
        ".globl " SYM(tx_test_release) "\n" SYM(tx_test_release) ":\n"
        "pushq %rbx\nsubq $32,%rsp\nmovq %rdx,%rbx\ncall " SYM(__tx_release) "\naddq $32,%rsp\npopq %rbx\nret\n");
#endif
static void *thread_pointer;
/* Over-release (runtime hardening H1): the kernel reports 4012 through __tx_trap and must not return. */
TXABI void __tx_trap(uint64_t fn, uint64_t pc, uint64_t code, const char *message, uint64_t length) {
    printf("TRAP code=%llu fn=%08llx pc=%llu %.*s\n", (unsigned long long)code, (unsigned long long)(fn & 0xffffffffu),
           (unsigned long long)pc, (int)length, message);
    fflush(stdout);
    exit(code == 4012 ? 88 : 89);
}
#define RETAIN(v) tx_test_retain((v),thread_pointer)
#define RELEASE(v) tx_test_release((v),thread_pointer)
#else
#define RETAIN(v) __tx_retain(v)
#define RELEASE(v) __tx_release(v)
#endif

int main(int argc, char **argv) {
    static const uint32_t cases[] = {0x7ffffffeu, 0x7fffffffu, 0x80000000u, 0xfffffffeu, 0xffffffffu};
    const uint64_t value[2] = {5, 0};
    unsigned failed = 0;
    (void)argc; (void)argv;
#ifdef RC_NRT
    const size_t region = (size_t)1 << 28;
    void *mapping;
#if defined(_WIN32)
    mapping = VirtualAlloc(NULL, region * 2, MEM_RESERVE, PAGE_NOACCESS);
    if (!mapping) return 97;
#else
    mapping = mmap(NULL, region * 2, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapping == MAP_FAILED) return 97;
#endif
    uintptr_t tb = ((uintptr_t)mapping + region - 1) & ~(uintptr_t)(region - 1);
#if defined(_WIN32)
    if (!VirtualAlloc((void *)tb, 4096, MEM_COMMIT, PAGE_READWRITE)) return 98;
#else
    if (mprotect((void *)tb, 4096, PROT_READ | PROT_WRITE) != 0) return 98;
#endif
    uint64_t slots[8] = {(uintptr_t)counts};
    *(uintptr_t *)(tb + 16) = (uintptr_t)slots;
    thread_pointer = (void *)(tb + 4096);
    /* Control "over-release <flags>": count already 0; each flag value takes a different arm
       (0 fast path, 1 scalar, 2 atomic with probes, 3 scalar with probes). Must trap 4012, never return. */
    if (argc == 3 && strcmp(argv[1], "over-release") == 0) {
        scalar = (uint64_t)atoi(argv[2]);
        __tx_nrt_rc_flags = (uint32_t)scalar;
        counts[0] = 0; cascades = 0;
        RELEASE(value);
        printf("FAIL over-release returned: count=%08x cascades=%u\n", counts[0], cascades);
        return 1;
    }
#endif
    for (scalar = 0; scalar < 4; ++scalar) {
        __tx_nrt_rc_flags = (uint32_t)scalar;
        counts[0] = 1; cascades = 0;
        RELEASE(value);
        if (counts[0] != 0 || cascades != 1) return 99;
        for (unsigned i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
            uint32_t input = cases[i];
            uint32_t retained = input & 0x80000000u ? input : input + 1;
            counts[0] = input; cascades = 0;
            RETAIN(value);
            if (counts[0] != retained || cascades != 0) {
                printf("FAIL retain scalar=%llu input=%08x actual=%08x expected=%08x\n", (unsigned long long)scalar, input, counts[0], retained);
                ++failed;
            }
            uint32_t released = input & 0x80000000u ? input : input - 1;
            counts[0] = input; cascades = 0;
            RELEASE(value);
            if (counts[0] != released || cascades != 0) {
                printf("FAIL release scalar=%llu input=%08x actual=%08x expected=%08x\n", (unsigned long long)scalar, input, counts[0], released);
                ++failed;
            }
            counts[0] = input; cascades = 0;
            RETAIN(value); RELEASE(value);
            uint32_t roundtrip = input == 0x7fffffffu ? 0x80000000u : input;
            if (counts[0] != roundtrip || cascades != 0) {
                printf("FAIL crossing scalar=%llu input=%08x actual=%08x expected=%08x\n", (unsigned long long)scalar, input, counts[0], roundtrip);
                ++failed;
            }
        }
    }
#ifdef RC_NRT
#if defined(_WIN32)
    VirtualFree(mapping, 0, MEM_RELEASE);
#else
    munmap(mapping, region * 2);
#endif
#endif
    printf("%s RC high-bit sentinels and zero-release controls; failures=%u\n", failed ? "FAIL" : "PASS", failed);
    return failed ? 1 : 0;
}
