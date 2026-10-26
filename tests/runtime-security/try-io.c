#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include <windows.h>
#define TXABI
#else
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <time.h>
#if defined(__aarch64__)
/* arm64：内核的私有约定即 AAPCS64。 */
#define TXABI
#else
#define TXABI __attribute__((ms_abi))
#endif
#endif
#if defined(__APPLE__)
#include <errno.h>
#include <sched.h>
#include <sys/wait.h>
/* macOS 的探针对象（bootstrap/native_try_io_probe.tx）带 __tx_tool_nopipe 与状态字，不带 libSystem 蹦床：这里按蹦床的结果约定
   （成功非负、失败为 −errno）提供它用到的四个调用。sigaction 的 act／old 原样交给系统，顺带核对内核按 16 字节写的 struct sigaction。 */
TXABI int64_t __tx_mac_fcntl(int64_t fd, int64_t cmd, int64_t arg) {
    int r = fcntl((int)fd, (int)cmd, (int)arg);
    return r == -1 ? -(int64_t)errno : r;
}
TXABI int64_t __tx_mac_write(int64_t fd, const void *buffer, uint64_t count) {
    ssize_t r = write((int)fd, buffer, (size_t)count);
    return r == -1 ? -(int64_t)errno : (int64_t)r;
}
TXABI int64_t __tx_mac_sigaction(int64_t sig, const struct sigaction *act, struct sigaction *old) {
    return sigaction((int)sig, act, old) == 0 ? 0 : -(int64_t)errno;
}
TXABI int64_t __tx_mac_sched_yield(void) { return sched_yield() == 0 ? 0 : -(int64_t)errno; }
#endif

TXABI int64_t __tx_tool_try_wr(uintptr_t handle, void *buffer, uint64_t count);
#if defined(_WIN32)
TXABI int64_t __tx_tool_wr(uintptr_t handle, void *buffer, uint64_t count);
#endif
static void require(int yes, const char *why) { if (!yes) { fprintf(stderr, "FAIL %s\n", why); exit(90); } }
#if !defined(_WIN32)
static void getmask(sigset_t *s) { sigemptyset(s); require(sigprocmask(SIG_SETMASK, NULL, s) == 0, "get signal mask"); }
static int same_mask(const sigset_t *a, const sigset_t *b) {
    for (int s = 1; s < NSIG; ++s) if (sigismember(a, s) != sigismember(b, s)) return 0;
    return 1;
}
#endif
int main(void) {
    unsigned char *buffer = malloc(1048576);
    require(buffer != NULL, "fixture allocation"); memset(buffer, 0x3c, 1048576);
    uintptr_t writer;
#if defined(_WIN32)
    HANDLE read_handle, write_handle;
    require(CreatePipe(&read_handle, &write_handle, NULL, 65536), "CreatePipe");
    writer = (uintptr_t)write_handle;
    DWORD original = 0, current = 0;
    require(GetNamedPipeHandleStateW(write_handle, &original, NULL, NULL, NULL, NULL, 0), "get original pipe mode");
    // D3 v0.33：第一次成功切成 PIPE_NOWAIT 后保持；procWrite（__tx_tool_wr）写之前切回 PIPE_WAIT。
    int64_t first = __tx_tool_try_wr(writer, buffer, 1048576);
    require(first > 0 && first <= 4096, "blocking handle partial progress");
    require(GetNamedPipeHandleStateW(write_handle, &current, NULL, NULL, NULL, NULL, 0) && current == (original | PIPE_NOWAIT), "nonblocking mode kept after progress");
    require(__tx_tool_wr(writer, buffer, 100) == 100, "blocking write after try-write writes everything");
    require(GetNamedPipeHandleStateW(write_handle, &current, NULL, NULL, NULL, NULL, 0) && current == original, "blocking write switches back with bytes queued");
    // 管道里还有未读数据时 PIPE_WAIT -> PIPE_NOWAIT 被拒（231）：得 0、不关流、模式不变。
    require(__tx_tool_try_wr(writer, buffer, 1048576) == 0, "busy mode switch retries without closing stream");
    require(GetNamedPipeHandleStateW(write_handle, &current, NULL, NULL, NULL, NULL, 0) && current == original, "refused switch leaves mode intact");
    unsigned char initial_drain[8192]; DWORD initial_count;
    require(ReadFile(read_handle, initial_drain, (DWORD)first + 100, &initial_count, NULL) && initial_count == first + 100, "drain before mode change");
    require(memcmp(initial_drain, buffer, (size_t)first + 100) == 0, "queued payload bytes");
    current = original | PIPE_NOWAIT;
    require(SetNamedPipeHandleState(write_handle, &current, NULL, NULL), "set empty pipe nonblocking");
    uint64_t start = GetTickCount64();
#else
#if defined(__APPLE__)
    /* 父进程交下 SIG_IGN 时原样保留（M3 设计 G5）：在第一次调用（装空处理函数）之前分出子进程核对，写坏管道仍得 −1。 */
    pid_t child = fork();
    require(child >= 0, "fork");
    if (child == 0) {
        int cfd[2]; struct sigaction kept;
        if (signal(SIGPIPE, SIG_IGN) == SIG_ERR || pipe(cfd) != 0 || close(cfd[0]) != 0) _exit(91);
        if (__tx_tool_try_wr((uintptr_t)cfd[1], buffer, 16) != -1) _exit(92);
        if (sigaction(SIGPIPE, NULL, &kept) != 0 || kept.sa_handler != SIG_IGN) _exit(93);
        _exit(0);
    }
    int status = 0;
    require(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0, "inherited SIG_IGN kept");
    struct sigaction initial;
    require(sigaction(SIGPIPE, NULL, &initial) == 0 && initial.sa_handler == SIG_DFL, "fixture starts with default SIGPIPE");
#endif
    int fd[2]; require(pipe(fd) == 0, "pipe"); writer = (uintptr_t)fd[1];
    int original = fcntl(fd[1], F_GETFL);
    require(original >= 0, "get original pipe flags");
    sigset_t before, after; getmask(&before);
    struct timespec start, end; require(clock_gettime(CLOCK_MONOTONIC, &start) == 0, "clock");
#endif
    require(__tx_tool_try_wr(writer, buffer, 0) == 0, "empty write");
#if defined(__APPLE__)
    require(__tx_tool_try_wr(writer, buffer, 1) == 1, "first write");
    require(read(fd[0], buffer + 1, 1) == 1 && buffer[1] == buffer[0], "first write drained");
    /* XNU 给写过的描述符在 F_GETFL 里多报一位 0x10000（内核的 FWASWRITTEN，F_SETFL 改不掉；纯 C 写一次即复现）：
       之后的“标志还原”以第一次写之后的标志为准。 */
    original = fcntl(fd[1], F_GETFL);
    require(original >= 0 && (original & O_NONBLOCK) == 0, "flags after the first write");
    struct sigaction installed;
    require(sigaction(SIGPIPE, NULL, &installed) == 0 && installed.sa_handler != SIG_DFL && installed.sa_handler != SIG_IGN &&
            (installed.sa_flags & SA_RESTART), "SIGPIPE gets an empty SA_RESTART handler before the first write");
#endif
    uint64_t total = 0; int full = 0;
    for (unsigned i = 0; i < 100; ++i) {
        int64_t n = __tx_tool_try_wr(writer, buffer, 1048576);
        if (n < 0 || n >= 1048576) {
#if defined(_WIN32)
            fprintf(stderr, "write index=%u result=%lld winerr=%lu\n", i, (long long)n, (unsigned long)GetLastError());
#else
            fprintf(stderr, "write index=%u result=%lld\n", i, (long long)n);
#endif
        }
        require(n >= 0 && n < 1048576, "partial or full-pipe result");
        if (!n) { full = 1; break; }
        total += (uint64_t)n;
    }
    require(total > 0 && total < 1048576 && full, "bounded fill and full detection");
    require(__tx_tool_try_wr(writer, buffer, 1048576) == 0, "full pipe returns zero");
#if defined(_WIN32)
    require(GetTickCount64() - start < 1000, "nonblocking deadline");
    require(GetNamedPipeHandleStateW(write_handle, &current, NULL, NULL, NULL, NULL, 0) && current == (original | PIPE_NOWAIT), "nonblocking mode retained after fill");
    require(__tx_tool_try_wr(writer, buffer, 4096) == 0, "existing nonblocking full");
    require(GetNamedPipeHandleStateW(write_handle, &current, NULL, NULL, NULL, NULL, 0) && current == (original | PIPE_NOWAIT), "nonblocking mode preserved");
    unsigned char drained[4096]; DWORD got;
    require(ReadFile(read_handle, drained, sizeof(drained), &got, NULL) && got == sizeof(drained), "drain");
    require(memcmp(drained, buffer, sizeof(drained)) == 0, "payload bytes");
    require(__tx_tool_try_wr(writer, buffer, 1048576) > 0, "progress after drain");
    require(SetNamedPipeHandleState(write_handle, &original, NULL, NULL), "fixture restore mode");
    CloseHandle(read_handle);
    require(__tx_tool_try_wr(writer, buffer, 4096) == -1, "closed peer");
    CloseHandle(write_handle);
#else
    require(clock_gettime(CLOCK_MONOTONIC, &end) == 0, "clock");
    require((end.tv_sec - start.tv_sec) * 1000000000ll + end.tv_nsec - start.tv_nsec < 1000000000ll, "nonblocking deadline");
    require(fcntl(fd[1], F_GETFL) == original, "blocking flags restored");
    require(fcntl(fd[1], F_SETFL, original | O_NONBLOCK) == 0, "set fixture nonblock");
    require(__tx_tool_try_wr(writer, buffer, 4096) == 0 && fcntl(fd[1], F_GETFL) == (original | O_NONBLOCK), "existing nonblock preserved");
    require(fcntl(fd[1], F_SETFL, original) == 0, "fixture restore");
    unsigned char drained[4096]; require(read(fd[0], drained, sizeof(drained)) == sizeof(drained), "drain");
    require(memcmp(drained, buffer, sizeof(drained)) == 0, "payload bytes");
    require(__tx_tool_try_wr(writer, buffer, 1048576) > 0, "progress after drain");
    getmask(&after); require(same_mask(&before, &after), "signal mask restored on EAGAIN and success");
    close(fd[0]);
    require(__tx_tool_try_wr(writer, buffer, 4096) == -1, "closed peer and SIGPIPE survival");
    getmask(&after); require(same_mask(&before, &after), "signal mask restored on EPIPE");
    sigset_t pending; sigemptyset(&pending); require(sigpending(&pending) == 0 && !sigismember(&pending, SIGPIPE), "new SIGPIPE consumed");
    sigset_t blocked = before; sigaddset(&blocked, SIGPIPE);
    require(sigprocmask(SIG_SETMASK, &blocked, NULL) == 0 && raise(SIGPIPE) == 0, "preexisting blocked signal");
    require(__tx_tool_try_wr(writer, buffer, 4096) == -1, "closed peer when already blocked");
    getmask(&after); require(same_mask(&blocked, &after), "preexisting blocked mask preserved");
    sigemptyset(&pending); require(sigpending(&pending) == 0 && sigismember(&pending, SIGPIPE), "preexisting pending SIGPIPE preserved");
    sigset_t one; sigemptyset(&one); sigaddset(&one, SIGPIPE);
#if defined(__APPLE__)
    int drained_signal = 0;  /* macOS 没有 sigtimedwait；信号已挂起，sigwait 立即返回 */
    require(sigwait(&one, &drained_signal) == 0 && drained_signal == SIGPIPE, "fixture drains pending signal");
#else
    struct timespec zero = {0, 0};
    require(sigtimedwait(&one, NULL, &zero) == SIGPIPE, "fixture drains pending signal");
#endif
    require(sigprocmask(SIG_SETMASK, &before, NULL) == 0, "fixture restores original mask");
    close(fd[1]);
#endif
    free(buffer);
    puts("PASS try-write: stopped reader, full/partial, drain recovery, pipe mode rules and signal state, closed peer");
    return 0;
}
