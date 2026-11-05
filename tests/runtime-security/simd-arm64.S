// xmm-windows.S 的 arm64 对应（Apple 与 Linux 共用）：AAPCS64 的被调方保存浮点寄存器是 d8–d15（v8–v15 的低 64 位）。
// xmm_check(cb) 先存下原值，装入各不相同的位型后调 cb，回来逐个比对，第 n 个（d8 起）被改写即置位 n，恢复原值后返回位掩码。
// xmm_bad 改写 d8、d9（得 3）。两家只差符号前缀、页内偏移的写法与只读数据节，由下面的宏选（文件经 C 预处理）。
#ifdef __APPLE__
#define SYM(x) _##x
#define FUNC(x)
#define PAGE(x) x@PAGE
#define PAGEOFF(x) x@PAGEOFF
#define CONST_SECTION .section __TEXT,__const
#else
#define SYM(x) x
#define FUNC(x) .type x, %function
#define PAGE(x) x
#define PAGEOFF(x) :lo12:x
#define CONST_SECTION .section .rodata
.section .note.GNU-stack,"",%progbits
#endif
.text
.globl SYM(xmm_check)
FUNC(SYM(xmm_check))
.p2align 2
SYM(xmm_check):
    stp x29, x30, [sp, #-96]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp d8, d9, [sp, #32]
    stp d10, d11, [sp, #48]
    stp d12, d13, [sp, #64]
    stp d14, d15, [sp, #80]
    mov x19, x0
    adrp x9, PAGE(Lpats)
    add x9, x9, PAGEOFF(Lpats)
    ldp d8, d9, [x9]
    ldp d10, d11, [x9, #16]
    ldp d12, d13, [x9, #32]
    ldp d14, d15, [x9, #48]
    blr x19
    adrp x9, PAGE(Lpats)
    add x9, x9, PAGEOFF(Lpats)
    mov w20, #0
    fmov x10, d8
    ldr x11, [x9]
    cmp x10, x11
    b.eq 1f
    orr w20, w20, #1
1:  fmov x10, d9
    ldr x11, [x9, #8]
    cmp x10, x11
    b.eq 2f
    orr w20, w20, #2
2:  fmov x10, d10
    ldr x11, [x9, #16]
    cmp x10, x11
    b.eq 3f
    orr w20, w20, #4
3:  fmov x10, d11
    ldr x11, [x9, #24]
    cmp x10, x11
    b.eq 4f
    orr w20, w20, #8
4:  fmov x10, d12
    ldr x11, [x9, #32]
    cmp x10, x11
    b.eq 5f
    orr w20, w20, #16
5:  fmov x10, d13
    ldr x11, [x9, #40]
    cmp x10, x11
    b.eq 6f
    orr w20, w20, #32
6:  fmov x10, d14
    ldr x11, [x9, #48]
    cmp x10, x11
    b.eq 7f
    orr w20, w20, #64
7:  fmov x10, d15
    ldr x11, [x9, #56]
    cmp x10, x11
    b.eq 8f
    orr w20, w20, #128
8:  mov w0, w20
    ldp d14, d15, [sp, #80]
    ldp d12, d13, [sp, #64]
    ldp d10, d11, [sp, #48]
    ldp d8, d9, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #96
    ret

.globl SYM(xmm_noop)
FUNC(SYM(xmm_noop))
.p2align 2
SYM(xmm_noop):
    ret

.globl SYM(xmm_bad)
FUNC(SYM(xmm_bad))
.p2align 2
SYM(xmm_bad):
    movi d8, #0
    movi d9, #0
    ret

CONST_SECTION
.p2align 3
Lpats:
    .quad 1311768467283640328, 7383520307672973320, 1311768467283640330, 7383520307672973322
    .quad 1311768467283640332, 7383520307672973324, 1311768467283640334, 7383520307672973326
