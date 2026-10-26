#define _GNU_SOURCE
#include <asm/unistd.h>
#include <stddef.h>
#include <signal.h>
#include <stdio.h>
#include <sys/prctl.h>
#include <sys/wait.h>
_Static_assert(__NR_prctl == 157 && __NR_getpid == 39 && __NR_waitid == 247 && __NR_wait4 == 61, "x86_64 syscall ABI");
_Static_assert(PR_SET_CHILD_SUBREAPER == 36 && PR_GET_CHILD_SUBREAPER == 37, "subreaper options");
_Static_assert(P_ALL == 0 && WNOHANG == 1 && WEXITED == 4 && WNOWAIT == 0x1000000, "waitid options");
_Static_assert(sizeof(siginfo_t) == 128 && offsetof(siginfo_t, si_pid) == 16, "kernel signal info layout");
int check_reaper_signal_layout(void);
int main(void) {
    if (check_reaper_signal_layout()) return 1;
    puts("PASS independent Linux reaper ABI headers");
    return 0;
}
