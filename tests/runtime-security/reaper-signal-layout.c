#include <asm/unistd.h>
#include <asm/signal.h>
#include <stddef.h>
_Static_assert(__NR_rt_sigaction == 13 && SIGCHLD == 17, "x86_64 signal ABI");
_Static_assert(sizeof(struct sigaction) == 32, "kernel sigaction size");
_Static_assert(offsetof(struct sigaction, sa_handler) == 0 && offsetof(struct sigaction, sa_flags) == 8
               && offsetof(struct sigaction, sa_restorer) == 16 && offsetof(struct sigaction, sa_mask) == 24,
               "kernel sigaction fields");
int check_reaper_signal_layout(void) { return 0; }
