.text
.globl xmm_check
xmm_check:
pushq %rbx
subq $224, %rsp
movq %rcx, 192(%rsp)
movdqu %xmm6, 32(%rsp)
movdqu pat6(%rip), %xmm6
movdqu %xmm7, 48(%rsp)
movdqu pat7(%rip), %xmm7
movdqu %xmm8, 64(%rsp)
movdqu pat8(%rip), %xmm8
movdqu %xmm9, 80(%rsp)
movdqu pat9(%rip), %xmm9
movdqu %xmm10, 96(%rsp)
movdqu pat10(%rip), %xmm10
movdqu %xmm11, 112(%rsp)
movdqu pat11(%rip), %xmm11
movdqu %xmm12, 128(%rsp)
movdqu pat12(%rip), %xmm12
movdqu %xmm13, 144(%rsp)
movdqu pat13(%rip), %xmm13
movdqu %xmm14, 160(%rsp)
movdqu pat14(%rip), %xmm14
movdqu %xmm15, 176(%rsp)
movdqu pat15(%rip), %xmm15
call *192(%rsp)
xorl %ebx, %ebx
movdqu pat6(%rip), %xmm0
pcmpeqb %xmm6, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good6
orl $1, %ebx
good6:
movdqu pat7(%rip), %xmm0
pcmpeqb %xmm7, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good7
orl $2, %ebx
good7:
movdqu pat8(%rip), %xmm0
pcmpeqb %xmm8, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good8
orl $4, %ebx
good8:
movdqu pat9(%rip), %xmm0
pcmpeqb %xmm9, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good9
orl $8, %ebx
good9:
movdqu pat10(%rip), %xmm0
pcmpeqb %xmm10, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good10
orl $16, %ebx
good10:
movdqu pat11(%rip), %xmm0
pcmpeqb %xmm11, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good11
orl $32, %ebx
good11:
movdqu pat12(%rip), %xmm0
pcmpeqb %xmm12, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good12
orl $64, %ebx
good12:
movdqu pat13(%rip), %xmm0
pcmpeqb %xmm13, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good13
orl $128, %ebx
good13:
movdqu pat14(%rip), %xmm0
pcmpeqb %xmm14, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good14
orl $256, %ebx
good14:
movdqu pat15(%rip), %xmm0
pcmpeqb %xmm15, %xmm0
pmovmskb %xmm0, %eax
cmpl $65535, %eax
je good15
orl $512, %ebx
good15:
movdqu 32(%rsp), %xmm6
movdqu 48(%rsp), %xmm7
movdqu 64(%rsp), %xmm8
movdqu 80(%rsp), %xmm9
movdqu 96(%rsp), %xmm10
movdqu 112(%rsp), %xmm11
movdqu 128(%rsp), %xmm12
movdqu 144(%rsp), %xmm13
movdqu 160(%rsp), %xmm14
movdqu 176(%rsp), %xmm15
movl %ebx, %eax
addq $224, %rsp
popq %rbx
ret
.globl xmm_noop
xmm_noop:
ret
.globl xmm_bad
xmm_bad:
pxor %xmm6, %xmm6
pxor %xmm7, %xmm7
ret
.section .rdata,"dr"
.balign 16
pat6:
.quad 1311768467283640326, 7383520307672973318
pat7:
.quad 1311768467283640327, 7383520307672973319
pat8:
.quad 1311768467283640328, 7383520307672973320
pat9:
.quad 1311768467283640329, 7383520307672973321
pat10:
.quad 1311768467283640330, 7383520307672973322
pat11:
.quad 1311768467283640331, 7383520307672973323
pat12:
.quad 1311768467283640332, 7383520307672973324
pat13:
.quad 1311768467283640333, 7383520307672973325
pat14:
.quad 1311768467283640334, 7383520307672973326
pat15:
.quad 1311768467283640335, 7383520307672973327
