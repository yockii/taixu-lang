# 运行时按需链入（v0.2，2026-11-08）

动机：原生内核（运行时）是每个目标一个预先生成的对象，此前整体链入每个产物——hello 只用到内核约两成的函数，却带着整个运行时
（x86_64-windows 约 190 KiB、40 个导入，含建进程、删文件、开线程等与程序无关的系统函数）。产物因此偏大，导入面也与程序做的事
不相称。本稿让链接器按内核对象的函数目录把代码节切成函数块，只放入程序够得着的块（同 ld64 的 `-dead_strip`、Go 链接器的
deadcode），覆盖全部目标；另给 Windows 产物加上声明可复现构建的调试目录项。


## 一、按函数切块的三条前提

链接器把一个代码节按函数目录（`.txfn`）切成块，块与函数一一对应；只放入够得着的块、按原相对次序紧排。这样做对，要求：

1. **函数之间的引用都带重定位**——否则丢掉或挪动一个块，另一块里就地解析好的位移就错了。x86_64：汇编器对标签的引用一律登记
   rel32／addr64 修补（`encJmp`、`encCall`、RIP 相对寻址、`asStructuredAddress`），只有 `asResolveLocalFixups` 就地解析同节修补，
   而它只用于程序映像与探针，内核生成不调。实测 x86_64-windows 内核：`.text` 到 `.text` 的重定位 5,278 处，跨函数的 2,238 处全部指向
   函数起点，没有指进别的函数中间的。ELF 写出器的 `bindsLocally` 原先把同节的分支就地绑定（处理见第五节）。
2. **函数不落入下一个函数**——每个函数须以无条件转移结束；否则下一个函数被丢掉、或不紧随其后时，执行会跑进别的代码。
3. **块里没有对齐要求**——块之间不补齐。内核代码节里没有对齐指令（对齐都在数据节），函数本来就逐字节紧排（x86_64-windows 内核
   455 个函数里 203 个起点是奇数）。

第 2 条现状不满足：x86_64 三个内核里有三处落空——`__tx_frame_release`（一条 `xor r9d, r9d` 后落入 `__tx_frame_release_from`）、
`__tx_native_list_append_v1`（紧凑形态条件跳走，其余落入 `__tx_lb_full`）、Linux 的 `_start`（以 `exit_group` 系统调用结束）。
前两处是有意的“备用入口”写法，等于 Mach-O 的 `.alt_entry`。

处理办法比较：

| 方案 | 做法 | 他家 | 评价 |
|---|---|---|---|
| **甲 生成器保证不落空**（采用） | 三处改写：`__tx_frame_release` 末尾加 `jmp __tx_frame_release_from`；`__tx_native_list_append_v1` 末尾加 `jmp __tx_lb_full`；`_start` 末尾加 `ud2`。汇编器加核查：内核生成时每个登记函数的末条指令须是 `ret`、`jmp` 或 `ud2`，否则生成失败并列出全部落空的函数 | Go 汇编函数须以 RET／JMP 等结束；glibc 的 `_exit` 在系统调用后接 `hlt` | 不改对象格式与链接器；代价是两条多出的直接跳转：`__tx_frame_release` 每次调用一条（例程本身逐槽释放，远重于一条跳转），列表追加只在较重的完整形态路径上（常见的紧凑形态不变） |
| 乙 用 NONE 重定位表达依赖 | 落空的函数在末尾带一条 NONE 类重定位指向下一个函数，链接器据此连边；紧排保证相邻 | ELF 的 `.reloc ., R_X86_64_NONE, sym`：lld 的 `MarkLive::resolveReloc` 对每条重定位不分类型都把目标标为存活 | 零运行代价，但四个链接器都要认 NONE 类型（COFF 为 IMAGE_REL_AMD64_ABSOLUTE），独立核对器也要跟着认；依赖“紧排”这一条才相邻，链接器若将来改放入次序就隐性出错 |
| 丙 函数目录标记落空 | `.txfn` 每个函数加“落入下一个”标志，链接器把这样的相邻函数并成一块 | Mach-O 的 `N_ALT_ENTRY`（`n_desc` 0x0200，符号钉在前一段内容上） | 语义最直接，但函数目录升到 txfn/4，编解码、链接映射、核对器都要改 |

## 二、x86_64-windows 的实现

- **块计划 `bootstrap/selfhost/link_blocks.tx`**：`blockPlan(大小, 起点表, 终点表)` 核对区间按次序、各自非空、互不重叠、不越出节尾，
  否则给出拒绝原因（重叠、空函数、越出节尾）；`blockAt`（偏移所在的块，恰在节尾的归最后一块）、`blockOfRange`（整个区间所在
  的块，跨块为 -1）。只做计划与查找，取舍仍由各链接器按节求可达完成。缺口（目录没登记的字节）并进紧随其后的块、该块钉住
  （见第五节）。
- **切块（`link_pe.tx` 的 `splitByFunctions`）**：读入并合并全部对象后、建符号表之前，对带函数目录的对象，把目录指向的代码节换成
  函数块：块共用原节的字节（`rawAt` 偏移到函数起点），首块沿用原节对齐、其余为 1；重定位分到所在的块、位置改为块内偏移；符号改指
  所在的块、值改为块内偏移（解析时的值另存 `originValue`，链接映射照旧列原值）。目录不完整或有重定位跨函数时不切，整节照旧。
- **根（A1）**：程序映像引用的符号，加进程入口 `__tx_entry` 与 TLS 目录 `__tx_tls_directory`（PE 序列化按名取这两个）。内核其余
  全局符号不再当根。内核对象没按函数目录切开（不带目录或目录不能切）时照旧，它的全部导出都是根。
- **放入次序**：按输入次序（对象序、节序；切开的节按函数在原节里的次序），不再按求可达时的弹出次序。同一节的块接着上一块放、
  不补齐；不同节之间照旧按节的对齐（至少 16）补齐。全部够得着时与整节放入逐字节相同。
- **展开信息**：切开的节，每个放入的块出它那个函数的 UNWIND_INFO（位置为块在合成 .text 里的偏移）；没放入的函数不出。
- **链接映射**：块的放入记录带函数序号（`linkMapAddBlockPlacement`），`linkMapAttachFunctions` 按块逐个绑定，没放入的函数不列出。
- **内核生成器的核查**：函数块模式 `asFunctionBlocks`（`assemble.tx`），x86_64 内核在 `asStructuredBegin` 之后打开（第一节第 2 条）。
  A64、龙芯、RISC-V 的内核见第五节。

## 三、`__tx_callbi` 走表

`__tx_callbi` 原先对 7 个 `__rt` 内部内建（`RT_BI_BASE` 起，只供标准库模块）按编号直接调用，只要程序用了任何经通用分派的内建，
这 7 个例程（含 `SetEnvironmentVariableW`、`GetEnvironmentVariableW`）就都被链进来。改为：程序的内建表 `__tx_bi_table` 在原有
`BI_TABLE_LEN` 项之后再加 `RT_BI_COUNT` 项（第 `BI_TABLE_LEN + k` 项对应编号 `RT_BI_BASE + k`），同样只填程序用到的；`__tx_callbi`
的 rt 分支查这一段，填 0 即“未知内建”（4006）。这 7 个例程改为内核导出。

核对：内核里经内建表分派的只有 `__tx_callbi` 一处（`__tx_bi_table` 别无引用），没有“内核内部经表调用内建”的用途，表里填 0 的项
只会由程序自己的 callbi 走到——而程序用到的已经填上。

## 四、调试目录 REPRO

照 lld-link `/Brepro`（`lld/COFF/Writer.cpp` 的 `DebugDirectoryChunk`，2026-11-08 查证）：一条 28 字节的 IMAGE_DEBUG_DIRECTORY，
类型 IMAGE_DEBUG_TYPE_REPRO（16），数据长度、数据地址、文件位置都为 0，时间戳与 COFF 头的相同（都是内容哈希）；目录项放 .rdata，
4 字节对齐，数据目录第 6 项指向它。内容哈希照旧（规范化 P1），计算时调试目录项的时间戳为 0，算完与 COFF 头一并填入。

## 五、ELF 一族

ELF 一族的三个写出器（`link_elf_objects` 四个 Linux 目标、`link_macho` 两个 macOS 目标、`link_pe_objects` aarch64-windows）共用
`link_objects.tx` 的放置、符号与重定位，按需取舍只改这一处，写出器各自给出根。

- **前提一（引用带重定位）**：ELF 写出器（`emit_elf_object.tx`）原先把同节的 PC32（A64 的 CALL26／JUMP26、龙芯的 B26、RISC-V 的
  CALL_PLT）就地绑定；带函数目录写出时只在同一函数内就地绑定（`sameFunction`，按与链接器同一个块计划判定），跨函数的留成重定位。
  A64、龙芯、RISC-V 的汇编器在函数块模式（`asFunctionBlocks`）下对跳到别的函数的标号不就地解析：
  b／bl（RISC-V 的 jal 与调用）登记修补；条件跳转改用长形式（取反的短跳转越过一条无条件跳转），那条无条件跳转登记修补
  （A64 JUMP26、龙芯 B26、RISC-V CALL_PLT 的 auipc＋jalr）。
- **前提二（不落空）**：A64、龙芯、RISC-V 内核同样核查——每个登记函数的末条指令字须是无条件转移（A64：b、br、ret、brk、udf；
  龙芯：b、rd 为 0 的 jirl、break；RISC-V：rd 为 0 的 jal／jalr、ebreak、unimp），否则生成失败并列出全部落空的函数。实测四种指令集的
  内核都满足，没有要改写的。
- **缺口**：x86_64 的 Linux、macOS 内核有尚未登记进函数目录的手写例程（平台例程、TLS 叶例程），目录不铺满整节。严格的计划（目录须铺满整节）
  在这里会整节不切，所以：缺口字节并进紧随其后的块（节尾的缺口并进最后一块），这样的块钉住、总是放入——缺口里的代码可能落入下一个函数，
  也可能被就地解析的引用指着，与下一个函数同进同出、保持相邻才安全。函数的地址、链接映射与展开信息按函数自身的范围算（块起点
  不一定是函数起点）。把这些例程登记进目录后钉住的块就不再总是放入（记为后续）。
- **切块**（`splitObjects`）：带函数目录、目录所指的是已载入的代码节、该节的重定位只有一张表且每条的位置都落在单个块里的对象，才切；
  否则整节照旧。不带目录的对象（程序对象、`--link-object` 的 C 对象）整节放入。
- **求可达**（`reachFrom`）：根为不切的对象的全部已载入节、钉住的块，以及写出器按名字取用的符号——Linux：入口 `_start`、
  `__tx_tls_template`、`__tx_dynamic_image`（RISC-V）与动态钩子槽；macOS：入口；aarch64-windows：入口与 `__tx_tls_directory`。沿重定位
  扩到所指符号所在的节或块；未定义的按名字找全局定义（与放置时一样先到先得）。被切对象的数据节也只在够得着时放入。
- **放置**：按输入次序（对象序、节序；切开的节按块在原节里的次序），切开的节第一块按节的对齐、其后紧接上一块不补齐。全部够得着时与
  整节放入逐字节相同。
- **符号**：没放入的定义不进全局表，但照旧参与重名判定（先到先得、后来的 GLOBAL 即重名）；写出器合成符号（GOT 槽、IAT 槽、导入桩、
  TLV 槽）前的重名判定改用 `lkoDefined`（含没放入的定义），结论与整节放入时相同。没放入的块里的重定位不应用。
- **导入**：`lkoUndefinedWithPrefix`（Mach-O 的导入、aarch64-windows 的 IAT）只算够得着的重定位引用的未定义符号，没放入的函数的导入
  不进产物。Linux 的动态导入由驱动按程序给出，不变。
- **产物函数符号与展开信息**：内核函数按函数取地址（`lkoAddressIn`），没放入的不出符号；aarch64-windows 的 `.pdata`／`.xdata` 同样
  只出放入的函数。

## 六、测试

新门步 `on-demand-link`（`scripts/test_on_demand_link.tx`，太虚写）：块计划的单元用例（探针，含缺口并块与钉住）；hello、读文件、
线程、异步、调试版、工具链驱动的放入清单黄金表，每个产物目标一张（`tests/odl/<目标>.txt`，任何宿主交叉编出）：放入的内核函数、
它们的字节数合计与导入，只优化一个目标时只有它的表变；四个 Linux 目标与两个 macOS 目标的 hello 与工具链样例，独立读产物的函数符号（ELF 的 `.symtab`、Mach-O 的 `LC_SYMTAB`），只供建
子进程用的三个内核函数 hello 里都没有、工具链样例里都有；产物在 Windows 宿主上逐例运行。现有门（全语料各目标的产物运行、qemu 下的
三种 Linux 新架构、泄漏检查、--debug、页堆、可移植渲染器对照、pe-image 的函数目录覆盖与展开核对、链接映射、对象变异、自举不动点）照旧，
它们都经过按块取舍的链接器。

## 七、兼容

各目标产物的字节都变（只放入够得着的函数，产物的函数符号随之变少），编译器本身也是，
不动点随之改变；x86_64 三个内核因第一节的三处改写也变，A64、龙芯、RISC-V 内核因跨函数引用改为重定位也变。登记兼容变更
`runtime.on-demand-link`、`runtime.windows-pe-debug-repro`。用户可见的语义、诊断与 trap 文案不变。

## 八、实测

x86_64-windows 产物（同一份源码，旧＝整体链入的编译器，新＝本稿实施后的编译器；交叉编、不带 --map）：

| 样例 | 大小（字节）旧 → 新 | 导入 旧 → 新 |
|---|---|---|
| `examples/hello.tx` | 195,072 → 53,248 | 40 → 14 |
| `tests/cases/03-readfile.tx` | 199,168 → 78,336 | 40 → 18 |
| `tests/cases/async-await-any.tx` | 202,240 → 85,504 | 40 → 16 |
| `tests/nrt-cases/async-thread-await.tx` | 205,824 → 104,448 | 40 → 18 |
| `tests/debug/basic.tx --debug` | 205,312 → 81,408 | 44 → 22 |
| `tests/pe/odl-toolchain.tx --toolchain` | 246,784 → 131,072 | 62 → 48 |

hello 的导入只剩 CloseHandle、ExitProcess、GetCommandLineW、GetConsoleMode、GetCurrentThreadId、GetCurrentThreadStackLimits、
GetStdHandle、MultiByteToWideChar、Sleep、VirtualAlloc、VirtualFree、WideCharToMultiByte、WriteConsoleW、WriteFile（`__tx_callbi`
走表去掉了 Get/SetEnvironmentVariableW）；链接映射列出 138 个函数（程序 2、内核 136）。编译器本身用到内核的绝大部分，
只少约 52 KB 代码与 2 个导入。


ELF 一族与 aarch64-windows（同上，旧＝整体链入，新＝本稿实施后；交叉编；函数数为产物里的函数符号，含程序的两个）：

| 目标 | hello 大小（字节）旧 → 新 | hello 函数 旧 → 新 | 工具链样例函数 |
|---|---|---|---|
| x86_64-linux | 209,168 → 64,424 | 459 → 135 | 238 |
| aarch64-linux | 348,416 → 202,816 | 459 → 114 | 225 |
| loongarch64-linux | 348,424 → 202,824 | 459 → 114 | 225 |
| riscv64-linux | 233,752 → 63,576 | 459 → 114 | 225 |
| x86_64-macos | 257,568 → 113,632 | 526 → 202 | 310 |
| aarch64-macos | 257,344 → 89,440 | 526 → 129 | 285 |
| aarch64-windows | 208,896 → 69,632 | —（导入 41 → 15） | — |

aarch64-linux 与 loongarch64-linux 按 64 KiB 页排段，文件大小下降有限，函数数与另两个 Linux 目标相同。aarch64-macos 的导入（未定义符号）
69 → 17。

x86_64-macos 的导入起初只由 69 减到 68：它的内核里 71 个 libc 蹦床（`__tx_mac_read` 等，`unix_sys.tx` 的 `trampoline`，共 15,773 字节）
没有登记进函数目录，整段并进其后一块并钉住，蹦床引用的导入因此总在。登记它们见第九节；aarch64-macos 的蹦床经 rtfn 生成、早已登记。
x86_64-linux 另有约 2.7 KB 未登记的手写例程（memcpy 一族、页适配器、文件读写、argv、越界 trap）同样钉住，都是系统调用、不涉导入。


## 九、x86_64-macos 的 libc 蹦床登记（重对齐帧）

蹦床把内核的私有调用约定转成 SysV 再调 libSystem：序言 `push rbp; mov rbp, rsp; push rsi; push rdi; and rsp, -16; sub rsp, 160`，
xmm6–15（私有约定的被调方保存寄存器）存在对齐后的分配区，尾声 `lea rsp, [rbp - 16]; pop rdi; pop rsi; pop rbp; ret`。系统调用形的
蹦床（`__tx_mac_sc_<名>`）是 syscall 指令的替身，除 rax、rcx、r11 外全部保留。两者都自己对齐栈：手写的调用点（页适配器、致命路径、
系统调用形蹦床的各个调用点）不保证对齐，libSystem 却要 16 字节对齐的调用点。

先量收益：实验分支里把蹦床按函数登记（不带帧事件，只为数导入），x86_64-macos 的导入 hello 68 → 21、读文件 68 → 21、线程＋异步
68 → 28、工具链样例 68 → 58（aarch64-macos 各为 17、20、24、57；hello 多出的 close、openat、read、unlinkat 来自系统调用形蹦床，
被仍未登记的手写例程引用）。值得做。登记的难处在帧事实：现有模型（`nativeFunctionValidate`）只有压栈、设帧指针、定长分配／释放、
弹出与返回，`and rsp, -16` 与经帧指针的 `lea rsp` 都表达不了。比较（按判定套路自行判定）：

| 方案 | 做法 | 他家 | 评价 |
|---|---|---|---|
| **甲 重对齐帧作帧事件**（采用） | 新增 align-stack（`and rsp, −N`，只许在设帧指针之后、分配之前，只许 x86 手写例程）与 restore-stack-pointer（`lea rsp, [rbp − k]`，尾声的第一个事件，k 须恰为设帧指针之后压入的字节数）；对齐之后栈深相对进入时未知，链接映射的 `body_sp` 为 realigned、两种事件的 `sp_delta` 为 null | LLVM X86FrameLowering 在非 Windows 平台的栈重对齐：`mov rbp, rsp` 之后 `and rsp, −align`，尾声 `lea rsp, [rbp − 保存区]`，CFI 以 `def_cfa_register rbp` 给出 CFA | 如实、可由独立核对器逐字节核实；txfn/3 加种类号 7、8 不升版（同 A64 补种类号 6 的先例：旧读取方对带新事件的对象失败封闭）；链接映射升 link-map/7 |
| 乙 蹦床改定长帧 | 要求调用点保证对齐，蹦床里 `sub rsp, 定长` | Go 的 libc 蹦床在 systemstack 上调用，栈已对齐 | 系统调用形蹦床从任意栈状态调用，省不掉动态对齐；其余蹦床也要逐个审调用点，出错即 libc 里崩溃 |
| 丙 重对齐放进函数体 | 序言只压栈与设帧指针，`and`／`sub`／`lea` 都算函数体（同 Windows panic 入口的写法） | LLVM 在 Win64 上把重对齐放在序言之后，展开器经帧指针越过 | 不改模型，但链接映射对它声明 `body_sp=fixed` 与事实不符；macOS 不受 Win64 展开码的限制，没有理由沿用 |
| 丁 链接器按符号切原子 | 缺口里按全局符号再切块，不进函数目录 | ld64 的 `MH_SUBSECTIONS_VIA_SYMBOLS` | 与函数目录重复一套边界；缺口里还有落入下一函数的代码要另判；产物没有蹦床的函数符号 |
| 戊 登记为无帧事实的边界 | 新栈模型，只给范围 | 进程／线程入口的边界函数 | 展开在蹦床处停下，语义不对 |

实施：
- `assemble.tx` 的 `asBufferFrame` 增两种帧条目；`native_function_data.tx` 的校验（对齐后不许再压栈；有对齐的帧不许 release-stack，
  弹出与返回之前须已 restore；restore 的字节数须使 rsp 恰回到对齐之前）；`native_function_codec.tx` 种类号 7、8；链接映射
  （`link_map_function_render.tx`）`body_sp` 与 `sp_delta`，文本视图在固定栈一栏注“重对齐 16”；PE 展开信息遇到这两种事件照旧失败
  （Windows 内核没有重对齐帧）。
- `unix_sys.tx`：71 个蹦床与 `__tx_mac_gettid` 按函数登记（来源 runtime-manual），序言、尾声改由帧条目发出，指令字节不变。系统调用形
  蹦床原先把 rdx、r8–r10 也压栈，它们不是私有约定的被调方保存寄存器、不能作帧事件，改为与 xmm0–15 一起存进对齐后的分配区
  （`sub rsp, 288`），语义不变（仍是除 rax、rcx、r11 外全部保留）。
- 独立核对器 `check_frames.tx` 解码 `and rsp, imm` 与 `lea rsp, [rbp − disp]`，对齐后栈深记 0、复原时按帧指针取并核对恰回到对齐
  之前；`check_txfn.tx` 认种类号 7、8。门步 on-demand-link 对两个 macOS 目标另核：hello 的函数符号里没有 `__tx_mac_posix_spawn`、
  导入里没有 `_posix_spawn`、`_fork`、`_execve`，工具链样例两者都有。

