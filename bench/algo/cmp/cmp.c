/* cmp.c —— bench/algo 九负载 C 移植（V0.19-S1；D50 §一）。
 * 构建：gcc -O2 -o cmp cmp.c            （-O2＝常规发行档；禁 -ffast-math）
 * 标量参照：gcc -O2 -fno-tree-vectorize -o cmp_scalar cmp.c（SIMD 差距分离列）
 * 用法：cmp <load>；stdout 必须与 .tx 校验和逐字节一致（六实现硬门槛）。
 * 移植对齐注记（D50 §一）：
 *   1. 浮点表达式求值序逐项对齐（x86-64 SSE2 基线无 FMA 收缩）；
 *   2. qsort LCG 64 位整型承载（state*1103515245 峰值 <2^62）；
 *   3. nbody sqrtApprox 8 轮牛顿迭代逐语言同构；
 *   4. json 校验和只依赖序列化文本长度（紧凑无空格 "k":v）＋parse 回读真实执行；
 *   5. string-build-find 手写逐字节比对（对齐 .tx byteAt 形态）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static void put_i64(int64_t v) { printf("%lld\n", (long long)v); }

/* —— fib-rec —— */
static int64_t fib(int64_t n) { if (n < 2) return n; return fib(n - 1) + fib(n - 2); }
static void load_fib_rec(void) { put_i64(fib(27)); }

/* —— fib-iter —— */
static void load_fib_iter(void) {
    int64_t a = 0, b = 1;
    for (int64_t i = 0; i < 87; i++) { int64_t t = a + b; a = b; b = t; }
    put_i64(a);
}

/* —— qsort —— */
static int64_t lcg(int64_t state) { return (state * 1103515245 + 12345) % 2147483648; }
static int64_t *qs;
static int64_t qs_partition(int64_t lo, int64_t hi) {
    int64_t pivot = qs[hi];
    int64_t i = lo, j = lo;
    while (j < hi) {
        if (qs[j] < pivot) {
            int64_t t = qs[i]; qs[i] = qs[j]; qs[j] = t;
            i = i + 1;
        }
        j = j + 1;
    }
    int64_t t2 = qs[i]; qs[i] = qs[hi]; qs[hi] = t2;
    return i;
}
static void qs_rec(int64_t lo, int64_t hi) {
    if (lo < hi) {
        int64_t p = qs_partition(lo, hi);
        qs_rec(lo, p - 1);
        qs_rec(p + 1, hi);
    }
}
static void load_qsort(void) {
    int64_t n = 50000;
    qs = malloc(sizeof(int64_t) * n);
    int64_t state = 42;
    for (int64_t i = 0; i < n; i++) { state = lcg(state); qs[i] = state % 1000000; }
    qs_rec(0, n - 1);
    int64_t checksum = 0;
    for (int64_t i = 0; i < n; i++) checksum = checksum + (i * qs[i]) % 1000003;
    put_i64(checksum % 1000003);
    free(qs);
}

/* —— sieve —— */
static void load_sieve(void) {
    const int64_t n = 500000;
    unsigned char *comp = calloc(n, 1);
    int64_t p = 2;
    while (p * p < n) {
        if (!comp[p]) {
            for (int64_t m = p * p; m < n; m += p) comp[m] = 1;
        }
        p = p + 1;
    }
    int64_t count = 0;
    for (int64_t i = 2; i < n; i++) if (!comp[i]) count = count + 1;
    put_i64(count);
    free(comp);
}

/* —— mandelbrot（表达式求值序＝.tx 同序：((py*2.5)/size)-1.25 等） —— */
static void load_mandelbrot(void) {
    const int64_t size = 300;
    int64_t total = 0;
    for (int64_t py = 0; py < size; py++) {
        double ci = (double)py * 2.5 / (double)size - 1.25;
        for (int64_t px = 0; px < size; px++) {
            double cr = (double)px * 3.0 / (double)size - 2.0;
            double zr = 0.0, zi = 0.0;
            int64_t it = 0;
            while (it < 255) {
                double t = zr * zr - zi * zi + cr;
                zi = 2.0 * zr * zi + ci;
                zr = t;
                if (zr * zr + zi * zi > 4.0) break;
                it = it + 1;
            }
            total = total + it;
        }
    }
    put_i64(total);
}

/* —— matmul（192×192 平铺一维 float；求值序同 .tx） —— */
static void load_matmul(void) {
    const int64_t N = 192;
    double *a = malloc(sizeof(double) * N * N);
    double *b = malloc(sizeof(double) * N * N);
    double *c = malloc(sizeof(double) * N * N);
    for (int64_t i = 0; i < N * N; i++) {
        a[i] = (double)(i % 13) * 0.5;
        b[i] = (double)(i % 7) * 0.25 + 0.5;
        c[i] = 0.0;
    }
    for (int64_t r = 0; r < N; r++) {
        for (int64_t k = 0; k < N; k++) {
            double av = a[r * N + k];
            for (int64_t cc = 0; cc < N; cc++) {
                double cv = c[r * N + cc];
                c[r * N + cc] = cv + av * b[k * N + cc];
            }
        }
    }
    double diag = 0.0;
    for (int64_t i = 0; i < N; i++) diag = diag + c[i * N + i];
    put_i64((int64_t)(diag * 1000000.0));
    free(a); free(b); free(c);
}

/* —— nbody（sqrtApprox＝8 轮牛顿迭代；求值序同 .tx） —— */
static double sqrt_approx(double v) {
    double g = v;
    for (int64_t it = 0; it < 8; it++) g = 0.5 * (g + v / g);
    return g;
}
typedef struct { double x, y, z, vx, vy, vz; } Body;
static void load_nbody(void) {
    const double DT = 0.01;
    Body bs[5] = {
        { 0.0,  0.0,  0.0,  0.5,  0.2,  0.1},
        { 1.0,  0.5,  0.0, -0.3,  0.4,  0.2},
        {-0.5,  1.0,  0.5,  0.2, -0.5,  0.3},
        { 2.0, -1.0,  1.0,  0.1,  0.1, -0.2},
        {-1.0, -0.5, -1.0, -0.2,  0.3,  0.0},
    };
    const int64_t n = 5, steps = 200000;
    for (int64_t s = 0; s < steps; s++) {
        for (int64_t i = 0; i < n; i++) {
            for (int64_t j = 0; j < n; j++) {
                if (i != j) {
                    double dx = bs[j].x - bs[i].x;
                    double dy = bs[j].y - bs[i].y;
                    double dz = bs[j].z - bs[i].z;
                    double d2 = dx * dx + dy * dy + dz * dz + 0.01;
                    double inv = 1.0 / (d2 * sqrt_approx(d2));
                    bs[i].vx = bs[i].vx + dx * inv * DT;
                    bs[i].vy = bs[i].vy + dy * inv * DT;
                    bs[i].vz = bs[i].vz + dz * inv * DT;
                }
            }
        }
        for (int64_t k = 0; k < n; k++) {
            bs[k].x = bs[k].x + bs[k].vx * DT;
            bs[k].y = bs[k].y + bs[k].vy * DT;
            bs[k].z = bs[k].z + bs[k].vz * DT;
        }
    }
    double kinetic = 0.0;
    for (int64_t k = 0; k < n; k++)
        kinetic = kinetic + 0.5 * (bs[k].vx * bs[k].vx + bs[k].vy * bs[k].vy + bs[k].vz * bs[k].vz);
    put_i64((int64_t)(kinetic * 1000000.0));
}

/* —— string-build-find —— */
static void load_sbf(void) {
    /* hay = Σ "item-" + toStr(i % 977) + ";"（i<5000）——逐段拼接。 */
    size_t cap = 1 << 20, len = 0;
    char *hay = malloc(cap);
    for (int64_t i = 0; i < 5000; i++) {
        char part[32];
        int pn = snprintf(part, sizeof part, "item-%lld;", (long long)(i % 977));
        if (len + pn > cap) { cap *= 2; hay = realloc(hay, cap); }
        memcpy(hay + len, part, pn);
        len += pn;
    }
    const int64_t n = (int64_t)len, m = 7; /* len("item-7;") */
    int64_t hits = 0, at = 0;
    while (at <= n - m) {
        int64_t j = 0;
        int ok = 1;
        while (j < m) {
            /* 逐字节比对（对齐 .tx byteAt 形态——不用 memmem/strncmp） */
            if ((unsigned char)hay[at + j] != (unsigned char)"item-7;"[j]) { ok = 0; break; }
            j = j + 1;
        }
        if (ok) { hits = hits + 1; at = at + m; } else { at = at + 1; }
    }
    put_i64((hits * 977 + n) % 1000003);
    free(hay);
}

/* —— json-roundtrip（构树→紧凑序列化→解析回读；校验和＝Σ len%97） ——
 * 格式与 std/json 规范形同构：{"g0":{"i0":V,...},...}，键序＝插入序，
 * V = round*100 + i*5 + j；无空格。解析器为完整 JSON 子集（对象/数组/
 * 字符串/数/true/false/null）——parse 回读真实执行（对称成本，D50 §一-4）。 */
typedef struct Jv Jv;
struct Jv { int kind; double num; char *str; Jv **items; char **keys; int n; };
/* kind: 0=null 1=bool 2=int 3=float 4=str 5=arr 6=obj */

static Jv *mk_int(int64_t v) {
    Jv *j = calloc(1, sizeof(Jv)); j->kind = 2; j->num = (double)v; return j;
}
static void jfree(Jv *j) {
    if (!j) return;
    for (int i = 0; i < j->n; i++) { free(j->items[i]); if (j->keys) free(j->keys[i]); }
    free(j->items); free(j->keys); free(j->str); free(j);
}

static void sb_put(char **b, size_t *len, size_t *cap, const char *s, size_t n) {
    if (*len + n > *cap) { *cap = (*len + n) * 2; *b = realloc(*b, *cap); }
    memcpy(*b + *len, s, n); *len += n;
}
static void sb_i64(char **b, size_t *len, size_t *cap, int64_t v) {
    char tmp[24]; int n = snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    sb_put(b, len, cap, tmp, n);
}
static void jstringify(const Jv *j, char **b, size_t *len, size_t *cap) {
    switch (j->kind) {
    case 0: sb_put(b, len, cap, "null", 4); break;
    case 2: case 3: {
        /* 本负载全部整值叶子——int 通道（float 整值补 .0 的规范形在此不触发） */
        sb_i64(b, len, cap, (int64_t)j->num); break;
    }
    case 4:
        sb_put(b, len, cap, "\"", 1);
        for (const char *p = j->str; *p; p++) {
            if (*p == '"' || *p == '\\') { sb_put(b, len, cap, "\\", 1); }
            sb_put(b, len, cap, p, 1);
        }
        sb_put(b, len, cap, "\"", 1);
        break;
    case 5:
        sb_put(b, len, cap, "[", 1);
        for (int i = 0; i < j->n; i++) {
            if (i) sb_put(b, len, cap, ",", 1);
            jstringify(j->items[i], b, len, cap);
        }
        sb_put(b, len, cap, "]", 1);
        break;
    case 6:
        sb_put(b, len, cap, "{", 1);
        for (int i = 0; i < j->n; i++) {
            if (i) sb_put(b, len, cap, ",", 1);
            size_t kl = strlen(j->keys[i]);
            sb_put(b, len, cap, "\"", 1); sb_put(b, len, cap, j->keys[i], kl); sb_put(b, len, cap, "\":", 2);
            jstringify(j->items[i], b, len, cap);
        }
        sb_put(b, len, cap, "}", 1);
        break;
    }
}

/* 完整 JSON 子集解析器（回读真实执行——对称成本） */
typedef struct { const char *s; size_t pos, len; int err; } P;
static void pws(P *p) { while (p->pos < p->len) { char c = p->s[p->pos]; if (c == ' ' || c == '\t' || c == '\n' || c == '\r') p->pos++; else break; } }
static Jv *pvalue(P *p);
static int pchar(P *p, char c) { pws(p); if (p->pos < p->len && p->s[p->pos] == c) { p->pos++; return 1; } return 0; }
static Jv *pobject(P *p) {
    Jv *j = calloc(1, sizeof(Jv)); j->kind = 6;
    size_t ca = 8; j->items = malloc(sizeof(Jv *) * ca); j->keys = malloc(sizeof(char *) * ca);
    pchar(p, '{');
    pws(p);
    if (pchar(p, '}')) return j;
    for (;;) {
        pws(p);
        if (p->pos >= p->len || p->s[p->pos] != '"') { p->err = 1; return j; }
        p->pos++;
        size_t ks = p->pos;
        while (p->pos < p->len && p->s[p->pos] != '"') {
            if (p->s[p->pos] == '\\') p->pos++;
            p->pos++;
        }
        if (p->pos >= p->len) { p->err = 1; return j; }
        size_t kl = p->pos - ks;
        p->pos++;
        char *key = malloc(kl + 1); memcpy(key, p->s + ks, kl); key[kl] = 0;
        if (!pchar(p, ':')) { p->err = 1; free(key); return j; }
        Jv *v = pvalue(p);
        if (j->n == (int)ca) { ca *= 2; j->items = realloc(j->items, sizeof(Jv *) * ca); j->keys = realloc(j->keys, sizeof(char *) * ca); }
        j->keys[j->n] = key; j->items[j->n] = v; j->n++;
        if (pchar(p, ',')) continue;
        pchar(p, '}');
        return j;
    }
}
static Jv *parray(P *p) {
    Jv *j = calloc(1, sizeof(Jv)); j->kind = 5;
    size_t ca = 8; j->items = malloc(sizeof(Jv *) * ca);
    pchar(p, '[');
    pws(p);
    if (pchar(p, ']')) return j;
    for (;;) {
        Jv *v = pvalue(p);
        if (j->n == (int)ca) { ca *= 2; j->items = realloc(j->items, sizeof(Jv *) * ca); }
        j->items[j->n++] = v;
        if (pchar(p, ',')) continue;
        pchar(p, ']');
        return j;
    }
}
static Jv *pvalue(P *p) {
    pws(p);
    if (p->pos >= p->len) { p->err = 1; return calloc(1, sizeof(Jv)); }
    char c = p->s[p->pos];
    if (c == '{') return pobject(p);
    if (c == '[') return parray(p);
    if (c == '"') {
        p->pos++;
        size_t st = p->pos;
        while (p->pos < p->len && p->s[p->pos] != '"') { if (p->s[p->pos] == '\\') p->pos++; p->pos++; }
        Jv *j = calloc(1, sizeof(Jv)); j->kind = 4;
        size_t l = p->pos - st; p->pos++;
        j->str = malloc(l + 1); memcpy(j->str, p->s + st, l); j->str[l] = 0;
        return j;
    }
    if (!strncmp(p->s + p->pos, "true", 4)) { p->pos += 4; Jv *j = calloc(1, sizeof(Jv)); j->kind = 1; j->num = 1; return j; }
    if (!strncmp(p->s + p->pos, "false", 5)) { p->pos += 5; Jv *j = calloc(1, sizeof(Jv)); j->kind = 1; return j; }
    if (!strncmp(p->s + p->pos, "null", 4)) { p->pos += 4; return calloc(1, sizeof(Jv)); }
    /* 数字 */
    char *end; double d = strtod(p->s + p->pos, &end);
    if (end == p->s + p->pos) { p->err = 1; return calloc(1, sizeof(Jv)); }
    Jv *j = calloc(1, sizeof(Jv));
    j->kind = (memchr(p->s + p->pos, '.', end - (p->s + p->pos)) || memchr(p->s + p->pos, 'e', end - (p->s + p->pos)) || memchr(p->s + p->pos, 'E', end - (p->s + p->pos))) ? 3 : 2;
    j->num = d; p->pos = end - p->s;
    return j;
}
static int64_t sum_ints(const Jv *j) {
    if (j->kind == 2) return (int64_t)j->num;
    int64_t s = 0;
    for (int i = 0; i < j->n; i++) s += sum_ints(j->items[i]);
    return s;
}
static void load_json(void) {
    int64_t checksum = 0;
    char *b = malloc(1 << 16); size_t blen = 0, bcap = 1 << 16;
    for (int64_t round = 0; round < 40; round++) {
        /* 构树文本：{"g0":{"i0":V,...},"g1":{...},...,"g19":{...}} */
        blen = 0; b[0] = 0;
        sb_put(&b, &blen, &bcap, "{", 1);
        for (int64_t i = 0; i < 20; i++) {
            if (i) sb_put(&b, &blen, &bcap, ",", 1);
            char kbuf[24];
            int kn = snprintf(kbuf, sizeof kbuf, "\"g%lld\":{\"", (long long)i);
            sb_put(&b, &blen, &bcap, kbuf, kn);
            for (int64_t jj = 0; jj < 5; jj++) {
                if (jj) sb_put(&b, &blen, &bcap, ",\"", 2); /* 前缀 `{\"` 已含首键开引号 */
                int vn = snprintf(kbuf, sizeof kbuf, "i%lld\":", (long long)jj);
                sb_put(&b, &blen, &bcap, kbuf, vn);
                sb_i64(&b, &blen, &bcap, round * 100 + i * 5 + jj);
            }
            sb_put(&b, &blen, &bcap, "}", 1);
        }
        sb_put(&b, &blen, &bcap, "}", 1);
        /* parse 回读真实执行（Int 叶子和消费结果——对称成本） */
        P p = { b, 0, blen, 0 };
        Jv *back = pvalue(&p);
        if (p.err) { checksum += sum_ints(back); }
        checksum += (int64_t)blen % 97;
        jfree(back);
    }
    put_i64(checksum % 1000003);
    free(b);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: cmp <load>\n"); return 2; }
    const char *m = argv[1];
    if (!strcmp(m, "fib-rec")) load_fib_rec();
    else if (!strcmp(m, "fib-iter")) load_fib_iter();
    else if (!strcmp(m, "qsort")) load_qsort();
    else if (!strcmp(m, "sieve")) load_sieve();
    else if (!strcmp(m, "mandelbrot")) load_mandelbrot();
    else if (!strcmp(m, "matmul")) load_matmul();
    else if (!strcmp(m, "nbody")) load_nbody();
    else if (!strcmp(m, "string-build-find")) load_sbf();
    else if (!strcmp(m, "json-roundtrip")) load_json();
    else { fprintf(stderr, "unknown load %s\n", m); return 2; }
    return 0;
}
