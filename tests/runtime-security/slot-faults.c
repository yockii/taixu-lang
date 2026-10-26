/* Store-slot kernel under the independent test allocator (ledger.h). */
#include "ledger.h"
TXABI void *__tx_native_slots_create_v2(uint32_t stride);
TXABI uint32_t __tx_native_slots_acquire_v2(void *state);
TXABI uint32_t __tx_native_slots_free_v2(void *state, uint32_t index);
TXABI void __tx_native_slots_destroy_v2(void *state);

static void failure_rounds(uint64_t *s) {
    unsigned char saved_state[64], saved_rc[256], saved_next[256], saved_objects[64 * 72];
    size_t count = (size_t)s[2];
    require(count == 0 || count == 64, "growth fixture shape");
    memcpy(saved_state, s, sizeof(saved_state));
    if (count) {
        memcpy(saved_rc, (void *)(uintptr_t)s[0], sizeof(saved_rc));
        memcpy(saved_next, (void *)(uintptr_t)s[1], sizeof(saved_next));
        memcpy(saved_objects, (void *)(uintptr_t)s[6], sizeof(saved_objects));
    }
    for (unsigned nth = 1; nth <= 3; ++nth) {
        unsigned before = live_count;
        fail_at = attempts + nth;
        require(__tx_native_slots_acquire_v2(s) == UINT32_MAX, "allocation failure result");
        fail_at = 0;
        require(live_count == before && memcmp(s, saved_state, 64) == 0, "failure mutated state or leaked");
        if (count) {
            require(memcmp((void *)(uintptr_t)s[0], saved_rc, sizeof(saved_rc)) == 0, "failure changed RC");
            require(memcmp((void *)(uintptr_t)s[1], saved_next, sizeof(saved_next)) == 0, "failure changed free chain");
            require(memcmp((void *)(uintptr_t)s[6], saved_objects, sizeof(saved_objects)) == 0, "failure changed objects");
        }
        all_canaries();
    }
}

int main(int argc, char **argv) {
#if defined(_WIN32)
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
#endif
    if (argc > 1) {
        unsigned char *p = __tx_native_mem_alloc(64, 16);
        if (!strcmp(argv[1], "invalid-free")) { __tx_native_mem_free(p + 1); return 99; }
        if (!strcmp(argv[1], "left-canary")) { p[-1] = 0; __tx_native_mem_free(p); return 99; }
        if (!strcmp(argv[1], "right-bound")) { ((volatile unsigned char *)p)[64] = 1; return 99; }
        __tx_native_mem_free(p);
        if (!strcmp(argv[1], "double-free")) { __tx_native_mem_free(p); return 99; }
        if (!strcmp(argv[1], "use-after-free")) { ((volatile unsigned char *)p)[0] = 1; return 99; }
        return 98;
    }
    require(__tx_native_slots_create_v2(0) == NULL && attempts == 0, "zero stride");
    fail_at = attempts + 1;
    require(__tx_native_slots_create_v2(72) == NULL && live_count == 0, "create failure recovery");
    fail_at = 0;
    uint64_t *s = __tx_native_slots_create_v2(72);
    require(s && s[2] == 0 && s[3] == 0 && s[4] == UINT32_MAX && s[5] == 0 && s[7] == 72, "initial state");
    failure_rounds(s);
    for (uint32_t i = 0; i < 64; ++i) {
        require(__tx_native_slots_acquire_v2(s) == i, "sequential acquire");
        memset((unsigned char *)(uintptr_t)s[6] + (size_t)i * 72, (int)(i + 1), 72);
    }
    require(s[2] == 64 && s[3] == 64 && live_count == 4, "first growth state");
    failure_rounds(s);
    require(__tx_native_slots_acquire_v2(s) == 64 && s[2] == 65 && s[3] == 128 && live_count == 4, "successful growth after failures");
    for (unsigned i = 0; i < 64 * 72; ++i) require(((unsigned char *)(uintptr_t)s[6])[i] == i / 72 + 1, "payload preserved after growth");
    require(__tx_native_slots_free_v2(s, 0) == 0 && __tx_native_slots_free_v2(s, 65) == 0, "reject live or outside free");
    ((uint32_t *)(uintptr_t)s[0])[0] = 0;
#ifdef SLOTS_QUARANTINE
    /* 检测形态（页堆内核的对象号隔离，设计 v052-heap-check-tool）：状态 88 字节，{隔离环@64, 环首@72, 环内项数@80}。 */
#define RC(i) (((uint32_t *)(uintptr_t)s[0])[i])
#define NEXT(i) (((uint32_t *)(uintptr_t)s[1])[i])
    fail_at = attempts + 1;
    require(__tx_native_slots_free_v2(s, 0) == 1 && s[8] == 0 && s[4] == 0 && s[5] == 1, "quarantine ring OOM falls back to free chain");
    fail_at = 0;
    require(__tx_native_slots_acquire_v2(s) == 0 && RC(0) == 1 && s[5] == 0, "fallback index reused");
    RC(0) = 0;
    require(__tx_native_slots_free_v2(s, 0) == 1 && __tx_native_slots_free_v2(s, 0) == 0, "single free and duplicate rejection");
    require(NEXT(0) == 0xFFFFFFFDu && s[4] == UINT32_MAX && s[5] == 0 && s[8] != 0 && s[9] == 0 && s[10] == 1, "freed index quarantined");
    require(__tx_native_slots_acquire_v2(s) == 65 && RC(65) == 1, "quarantined index not reused");
    /* 填满隔离环（探针为 64 项，页堆内核为 65536）：此前的号码 0 一直不复用；再释放一个时它才出环、挂回空闲链。 */
    uint32_t idx = 65;
    for (uint32_t k = 1; k < 64; ++k) {
        RC(idx) = 0;
        require(__tx_native_slots_free_v2(s, idx) == 1, "quarantine fill free");
        idx = __tx_native_slots_acquire_v2(s);
        require(idx != 0 && idx != UINT32_MAX, "quarantine fill acquire");
    }
    require(s[10] == 64 && s[4] == UINT32_MAX && s[5] == 0, "quarantine ring full, nothing reused");
    RC(idx) = 0;
    require(__tx_native_slots_free_v2(s, idx) == 1 && s[4] == 0 && NEXT(0) == UINT32_MAX && s[5] == 1 && s[9] == 1 && s[10] == 64,
            "oldest index evicted to the free chain");
    require(__tx_native_slots_acquire_v2(s) == 0 && RC(0) == 1, "evicted index reused");
#undef RC
#undef NEXT
#else
    require(__tx_native_slots_free_v2(s, 0) == 1 && __tx_native_slots_free_v2(s, 0) == 0, "single free and duplicate rejection");
    require(__tx_native_slots_acquire_v2(s) == 0 && ((uint32_t *)(uintptr_t)s[0])[0] == 1 && s[5] == 0, "free-chain reuse");
#endif
    all_canaries();
    __tx_native_slots_destroy_v2(s); __tx_native_slots_destroy_v2(NULL);
    require(live_count == 0, "destroy ledger balance");
    release_quarantine();
#ifdef SLOTS_QUARANTINE
    puts("PASS slots: create OOM, 3 allocation points x 2 growth states, payload/state rollback, guards, quarantine; index quarantine (ring OOM fallback, no reuse, eviction when the ring is full)");
#else
    puts("PASS slots: create OOM, 3 allocation points x 2 growth states, payload/state rollback, reuse, guards, quarantine");
#endif
    return 0;
}
