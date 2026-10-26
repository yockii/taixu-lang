/* Weak-table kernel (weak_table.tx) under the independent test allocator (ledger.h): every allocation point of the
   two-table growth fails in turn from an empty and from a populated table; the table must stay bit-identical and the
   ledger balanced, then the same link succeeds. Chain link/unlink/notify order and destroy balance are checked too.
   State: s[0] heads (per target: u32 first, u32 last), s[1] links (per weak: u32 target, u32 next), s[2] capacity. */
#include "ledger.h"
TXABI uint64_t __tx_native_weak_link_v1(uint64_t *state, uint32_t target, uint32_t weak);
TXABI void __tx_native_weak_unlink_v1(uint64_t *state, uint32_t target, uint32_t weak);
TXABI void __tx_native_weak_notify_v1(uint64_t *state, uint32_t target);
TXABI void __tx_native_weak_destroy_v1(uint64_t *state);

enum { NONE = 0xffffffffu, MAX_DEAD = 64 };
static uint32_t dead[MAX_DEAD][2];
static unsigned dead_count;
TXABI void __tx_store_weak_dead_v1(uint32_t target, uint32_t weak) {
    require(dead_count < MAX_DEAD, "dead log capacity");
    dead[dead_count][0] = target;
    dead[dead_count][1] = weak;
    ++dead_count;
}
static uint32_t *head(uint64_t *s, uint32_t target) { return (uint32_t *)(uintptr_t)s[0] + 2 * (size_t)target; }
static uint32_t *link_of(uint64_t *s, uint32_t weak) { return (uint32_t *)(uintptr_t)s[1] + 2 * (size_t)weak; }
static int linked(uint64_t *s, uint32_t target, uint32_t weak) {
    return __tx_native_weak_link_v1(s, target, weak) == 1;
}

/* Fail each allocation point of the link that needs growth; table and ledger must not change. */
static void failure_rounds(uint64_t *s, uint32_t target, uint32_t weak) {
    uint64_t saved[3];
    static unsigned char heads[64 * 8], links[64 * 8];
    size_t bytes = (size_t)s[2] * 8;
    require(bytes <= sizeof(heads), "fixture capacity");
    memcpy(saved, s, sizeof(saved));
    if (bytes) {
        memcpy(heads, (void *)(uintptr_t)s[0], bytes);
        memcpy(links, (void *)(uintptr_t)s[1], bytes);
    }
    for (unsigned nth = 1; nth <= 2; ++nth) {
        unsigned before = live_count, reached = attempts;
        fail_at = attempts + nth;
        require(__tx_native_weak_link_v1(s, target, weak) == 0, "allocation failure result");
        require(attempts == reached + nth, "allocation point reached");
        fail_at = 0;
        require(live_count == before && memcmp(s, saved, sizeof(saved)) == 0, "failure mutated state or leaked");
        if (bytes) {
            require(memcmp((void *)(uintptr_t)s[0], heads, bytes) == 0, "failure changed heads");
            require(memcmp((void *)(uintptr_t)s[1], links, bytes) == 0, "failure changed links");
        }
        all_canaries();
    }
}

int main(int argc, char **argv) {
    (void)argv;
#if defined(_WIN32)
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
#endif
    if (argc > 1) return 98;
    uint64_t s[3] = {0, 0, 0};
    require(!linked(s, NONE, 1) && !linked(s, 1, NONE) && attempts == 0 && !s[0] && !s[1] && !s[2], "invalid indices");

    failure_rounds(s, 3, 5);
    require(linked(s, 3, 5) && s[2] == 16 && live_count == 2, "first growth after failures");
    require(head(s, 3)[0] == 5 && head(s, 3)[1] == 5 && link_of(s, 5)[0] == 3 && link_of(s, 5)[1] == NONE, "first link");
    for (uint32_t i = 0; i < 16; ++i)
        if (i != 3) require(head(s, i)[0] == NONE && head(s, i)[1] == NONE, "fresh heads empty");
    unsigned grown = attempts;
    require(linked(s, 3, 7) && linked(s, 3, 9) && attempts == grown, "chain without growth");
    require(head(s, 3)[0] == 5 && head(s, 3)[1] == 9 && link_of(s, 5)[1] == 7 && link_of(s, 7)[1] == 9, "chain order");
    require(!linked(s, 4, 5) && link_of(s, 5)[0] == 3 && attempts == grown, "weak slot already linked");

    failure_rounds(s, 20, 30);
    require(linked(s, 20, 30) && s[2] == 32 && live_count == 2, "second growth after failures");
    require(head(s, 3)[0] == 5 && head(s, 3)[1] == 9 && link_of(s, 7)[0] == 3 && link_of(s, 9)[1] == NONE, "growth preserves chains");
    require(head(s, 20)[0] == 30 && link_of(s, 30)[0] == 20 && head(s, 31)[0] == NONE && link_of(s, 31)[0] == NONE, "growth initializes tail");

    __tx_native_weak_unlink_v1(s, 3, 7);
    require(head(s, 3)[0] == 5 && head(s, 3)[1] == 9 && link_of(s, 5)[1] == 9 && link_of(s, 7)[0] == NONE, "unlink middle");
    __tx_native_weak_unlink_v1(s, 3, 9);
    require(head(s, 3)[1] == 5 && link_of(s, 5)[1] == NONE && link_of(s, 9)[0] == NONE, "unlink tail");
    __tx_native_weak_unlink_v1(s, 4, 5);
    __tx_native_weak_unlink_v1(s, 3, 99);
    __tx_native_weak_unlink_v1(s, 99, 5);
    require(head(s, 3)[0] == 5 && link_of(s, 5)[0] == 3, "unlink of a non-member is ignored");
    require(linked(s, 3, 7) && linked(s, 3, 9), "relink");
    __tx_native_weak_unlink_v1(s, 3, 5);
    require(head(s, 3)[0] == 7 && head(s, 3)[1] == 9 && link_of(s, 5)[0] == NONE, "unlink head");

    __tx_native_weak_notify_v1(s, 3);
    require(dead_count == 2 && dead[0][0] == 3 && dead[0][1] == 7 && dead[1][0] == 3 && dead[1][1] == 9, "notify in link order");
    require(head(s, 3)[0] == NONE && head(s, 3)[1] == NONE && link_of(s, 7)[0] == NONE && link_of(s, 9)[0] == NONE, "notify clears");
    __tx_native_weak_notify_v1(s, 3);
    __tx_native_weak_notify_v1(s, 1000);
    require(dead_count == 2, "notify of an empty or outside target");
    __tx_native_weak_notify_v1(s, 20);
    require(dead_count == 3 && dead[2][0] == 20 && dead[2][1] == 30, "second target");
    all_canaries();

    __tx_native_weak_destroy_v1(s);
    require(live_count == 0 && !s[0] && !s[1] && !s[2], "destroy ledger balance");
    __tx_native_weak_destroy_v1(s);
    require(live_count == 0, "destroy of an empty table");
    release_quarantine();
    puts("PASS weak: 2 allocation points x 2 growth states, rollback, chain link/unlink/notify order, destroy balance");
    return 0;
}
