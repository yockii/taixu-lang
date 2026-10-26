/* Windows command line: __tx_win_cmdline_split against shell32's CommandLineToArgvW (fixed and seeded random
   cases, including control characters and lone surrogates), the block bound the splitter sizes itself to, and
   allocation failure leaving outputs untouched. Child processes started with chosen command lines run the whole
   __tx_native_argv_acquire path (UTF-8 conversion, invalid UTF-16, failure at each allocation). */
#include "ledger.h"
#include <shellapi.h>
#include <wchar.h>

int __tx_win_cmdline_split(const wchar_t *cmd, wchar_t ***argv, int *argc);
int __tx_native_argv_acquire(char ***argv, int *argc);

static unsigned seed = 20261023;
static unsigned rnd(void) { seed = seed * 1103515245u + 12345u; return seed >> 8; }

static void show(const wchar_t *cmd) {
    fprintf(stderr, "case:");
    for (const wchar_t *p = cmd; *p; ++p) fprintf(stderr, " %x", (unsigned)*p);
    fprintf(stderr, "\n");
}

static void recycle(void) {
    if (entries < MAX_ALLOCS - 8 || live_count) return;
    release_quarantine();
    entries = 0;
}

static void same_as_shell(const wchar_t *cmd) {
    size_t n = wcslen(cmd);
    wchar_t **argv = (wchar_t **)1;
    int argc = -7;
    unsigned before = live_count;
    recycle();
    if (__tx_win_cmdline_split(cmd, &argv, &argc) != 0) { show(cmd); fail("split status", 91); }
    require(live_count == before + 1, "split allocation count");
    uintptr_t text = (uintptr_t)argv + (n + 2) * 8, end = (uintptr_t)argv + 12 * n + 20;
    if (n == 0) {
        require(argc == 1 && argv[0][0] == 0, "empty command line");
    } else {
        int count = 0;
        wchar_t **shell = CommandLineToArgvW(cmd, &count);
        require(shell != NULL, "CommandLineToArgvW");
        if (argc != count) { show(cmd); fail("argument count differs from CommandLineToArgvW", 92); }
        for (int i = 0; i < argc; ++i) {
            uintptr_t at = (uintptr_t)argv[i];
            if (at < text || at >= end || at + (wcslen(argv[i]) + 1) * 2 > end) { show(cmd); fail("argument outside the sized block", 93); }
            if (wcscmp(argv[i], shell[i]) != 0) { show(cmd); fail("argument differs from CommandLineToArgvW", 94); }
        }
        LocalFree(shell);
    }
    __tx_native_mem_free(argv);
    require(live_count == before, "split leak");
}

static void split_oom(const wchar_t *cmd) {
    wchar_t **argv = (wchar_t **)1;
    int argc = -7;
    unsigned before = live_count;
    recycle();
    fail_at = attempts + 1;
    int status = __tx_win_cmdline_split(cmd, &argv, &argc);
    fail_at = 0;
    require(status == 2, "split OOM status");
    require(argv == (wchar_t **)1 && argc == -7 && live_count == before, "split OOM changed outputs or leaked");
}

/* Child: acquire from this process's own command line; argv[1] of the parent's choice is not visible here, the
   expected status comes through TAIXU_ARGV_EXPECT. */
static int child(const char *expect) {
    char **argv = (char **)1;
    int argc = -7;
    int status = __tx_native_argv_acquire(&argv, &argc);
    if (expect[0] == '1') {
        require(status == 1 && argv == (char **)1 && argc == -7 && live_count == 0, "invalid UTF-16 not rejected cleanly");
        return 0;
    }
    require(status == 0, "acquire status");
    int count = 0;
    wchar_t **shell = CommandLineToArgvW(GetCommandLineW(), &count);
    require(shell != NULL && argc == count && argv[argc] == NULL, "acquire count");
    for (int i = 0; i < argc; ++i) {
        char want[4096];
        int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, shell[i], -1, want, sizeof want, NULL, NULL);
        require(bytes > 0 && strcmp(argv[i], want) == 0, "acquire text");
    }
    LocalFree(shell);
    __tx_native_mem_free(argv);
    require(live_count == 0, "acquire leak");
    for (unsigned k = 1; k <= 2; ++k) {
        char **again = (char **)1;
        int n = -7;
        fail_at = attempts + k;
        status = __tx_native_argv_acquire(&again, &n);
        fail_at = 0;
        require(status == 2 && again == (char **)1 && n == -7 && live_count == 0, "acquire OOM changed outputs or leaked");
    }
    return 0;
}

static unsigned spawn(const wchar_t *self, const wchar_t *line, const wchar_t *expect) {
    wchar_t buffer[512];
    require(wcslen(line) < 512, "child command line");
    wcscpy(buffer, line);
    require(SetEnvironmentVariableW(L"TAIXU_ARGV_EXPECT", expect) != 0, "child environment");
    STARTUPINFOW si;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    PROCESS_INFORMATION pi;
    require(CreateProcessW(self, buffer, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi) != 0, "CreateProcessW");
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 99;
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return code;
}

int main(void) {
    const char *expect = getenv("TAIXU_ARGV_EXPECT");
    if (expect) return child(expect);
    static const wchar_t *fixed[] = {
        L"", L" ", L"a", L" a b", L"\ta\tb", L"\"a b\"c d", L"\"a b\" c", L"\"\" x", L"\"a", L"a\"b c\" d",
        L"x \"a\"\"b\"", L"x \"a\"\" b\"", L"x \"\"\"a\"\"\"", L"x a\"\"\"b", L"x \"\"\"\"", L"x \"\"\"\"\"\"",
        L"x a\\\\\"b c\"", L"x a\\\\\\\"b", L"x a\\b\\\\c", L"x \\\"", L"x \\\\\"\"", L"x \"\\\\\" y",
        L"x a ", L"x a\t", L"x \"\"", L"x \"", L"x \"a b", L"x a\"b\"\"c d\"e", L"x \"\"\"\"\" y",
        L"\v\v", L"a\nb c", L"\x1f" L"a b", L"x\r\n y", L"\xA0" L"a b", L"x \xD800 \xDC00", NULL};
    unsigned cases = 0, oom = 0;
    for (int i = 0; fixed[i]; ++i) { same_as_shell(fixed[i]); split_oom(fixed[i]); ++cases; ++oom; }
    static const wchar_t alphabet[] = {L'a', L'b', L' ', L'\t', L'"', L'\\', L'"', L'\\', L'"', L' ',
        L'\n', 0x3000, 0xD800, 0xDC00, 0x0B, L'\r', 0xA0, 0xFEFF, 0x85, 0x01, 0x20AC, 0x7F};
    wchar_t buffer[48];
    for (unsigned k = 0; k < 60000; ++k) {
        unsigned len = 1 + rnd() % 40, width = k % 2 ? 10 : sizeof alphabet / sizeof alphabet[0];
        for (unsigned i = 0; i < len; ++i) buffer[i] = alphabet[rnd() % width];
        buffer[len] = 0;
        same_as_shell(buffer);
        ++cases;
    }
    /* The longest command line Windows allows, in the shapes that need the most arguments and terminators. */
    static wchar_t longest[32768];
    static const wchar_t *patterns[] = {L"a ", L"\"\" ", L"\\\\\\\"", L"\"\"\"", L"\xD83D\xDE00"};
    for (unsigned p = 0; p < sizeof patterns / sizeof patterns[0]; ++p) {
        size_t step = wcslen(patterns[p]), at = 0;
        while (at + step <= 32767) { wmemcpy(longest + at, patterns[p], step); at += step; }
        longest[at] = 0;
        same_as_shell(longest);
        split_oom(longest);
        ++cases; ++oom;
    }
    wchar_t self[MAX_PATH];
    require(GetModuleFileNameW(NULL, self, MAX_PATH) > 0 && GetModuleFileNameW(NULL, self, MAX_PATH) < MAX_PATH, "module path");
    static const struct { const wchar_t *line, *expect; } children[] = {
        {L"\"C:\\a b\\p.exe\" one \"two three\" four\\\\\"five \"\"\"six\"", L"0"},
        {L"p \x00E9t\x00E9 \xD834\xDD1E \"\x4E2D \x6587\"", L"0"},
        {L"p", L"0"},
        {L"  p  ", L"0"},
        {L"p a\tb \"\" \"\"\"\"", L"0"},
        {L"p ok \xD800", L"1"},
        {L"p \xDC00x", L"1"},
    };
    unsigned processes = 0;
    for (unsigned i = 0; i < sizeof children / sizeof children[0]; ++i) {
        unsigned code = spawn(self, children[i].line, children[i].expect);
        if (code != 0) { fprintf(stderr, "child %u exit %u\n", i, code); fail("child", 95); }
        ++processes;
    }
    all_canaries();
    printf("PASS argv: %u differential, %u oom, %u processes\n", cases, oom, processes);
    return 0;
}
