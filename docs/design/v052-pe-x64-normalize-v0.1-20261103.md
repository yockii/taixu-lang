# x86_64-windows 产物的 PE 规范化（v0.1，2026-11-03）

动机：Windows Defender 的云端模型曾把一个太虚产物（`tests/debug/basic.tx` 编出的 basic.exe）判为 `Trojan:Win32/Wacatac.C!ml`。
原样文件每次都被拦，只改头部一个保留字节判定即消失。这不是所有太虚程序都带的特征，但用户的程序同样可能中招。云端模型的特征
不公开，任何改动都不能保证不再误判；能做的是让产物不再处处偏离常规工具链的产物（机器学习模型的训练集里，头部被抹零、缺展开
信息、不带重定位的程序多是加壳或手工拼装的恶意程序），同时补上本该有的安全加固。不靠在用户机器上加排除项解决。


## 一、对照（本机实测各工具链的 hello）

| 项 | 太虚 x86_64 | Go 1.26 | Rust 1.93（MSVC link） | Zig 0.16（lld） | gcc（mingw ld） | 太虚 aarch64-windows |
|---|---|---|---|---|---|---|
| 基址 | 0x400000 | 0x140000000 | 0x140000000 | 0x140000000 | 0x140000000 | 0x140000000 |
| DllCharacteristics | 0 | 0x8160 | 0x8160 | 0x8160 | 0x0160 | 0x8160 |
| `.reloc`（基址重定位） | 无 | 有 | 有 | 有 | 有 | 有 |
| `.pdata`（展开信息） | 无 | 有 | 有 | 有 | 有 | 有 |
| TimeDateStamp | 0 | 0 | 构建时间 | 内容哈希（可复现） | 构建时间 | 0 |
| 链接器版本 | 0.0 | 3.0 | 14.50 | 14.0 | 2.46 | 0.0 |
| SizeOfInitializedData | 0 | 实值 | 实值 | 实值 | 实值 | — |
| CheckSum | 0 | 0 | 0 | 0 | 实值 | 0 |

0x8160＝HIGH_ENTROPY_VA＋DYNAMIC_BASE（ASLR）＋NX_COMPAT（DEP）＋TERMINAL_SERVER_AWARE。四个对照工具链**一致**的是：
ASLR＋DEP、基址 0x140000000、带 `.reloc` 与 `.pdata`、头部字段填实值；校验和多数为 0，时间戳各有做法。版本资源与清单四家默认都
不带，不列入本稿。

## 二、现状（代码核对，`bootstrap/selfhost/link_pe.tx`）

- **绝对地址只有 64 位的**，没有按 32 位写出符号地址的地方（汇编器拒绝无基址的非 RIP 寻址，`lea [rip+sym]`，COFF ADDR32／ADDR32NB
  已拒绝），基址抬到 4 GiB 以上不会截断。x86_64-macos 基址本就是 4 GiB，可作旁证。
- **写绝对 VA 的四处**（ASLR 时每处都要一条 DIR64）：程序映像的 addr64 回填（函数表、`__tx_nrt_image` 里的指针、错误名表等，只在
  rdata／data）；内核 COFF 的 ADDR64；`__imp_` 槽（.data）；自建 TLS 目录的四个 VA。IAT、导入描述符、数据目录写的是 RVA，不受影响。
- **展开信息**：x86 汇编时已有帧事件（`assemble.tx`），内核对象带 `.txfn` 函数目录，但 link_pe.tx 不读；没有 x86 的 UNWIND_INFO
  编码器（`pe_unwind_a64.tx` 只管 ARM64）。这是已登记的债务（多架构条：“x86_64-windows 的 `.pdata` 与栈探测……另立”；
  多架构设计稿：调试器与崩溃转储需要）。没有 `.pdata`，系统、调试器、性能分析器在太虚函数里都无法回溯调用栈。
- **调试器与工具**：DAP 按（函数号, pc）工作，地址取自产物运行时报出的值；`--map` 给链接时 VA 与 RVA；disasm 用中间码下标——
  基址随机化后都不受影响，只要 `.reloc` 覆盖了 nrt 映像里的指针。
- **aarch64-windows**（`link_pe_objects.tx`）已有 `.reloc` 分块写出与 `.pdata` 排序，函数未导出，需抽成公共模块。

## 三、方案（分三步，各自过完整门）

**P1 头部字段与 DEP**（小）：SizeOfCode／SizeOfInitializedData／SizeOfUninitializedData 按节填实值；链接器版本填工具链版本；
DllCharacteristics 加 NX_COMPAT 与 TERMINAL_SERVER_AWARE（运行时申请内存一律 `PAGE_RW`，代码只在映像 .text 里，不受 DEP 影响）；
时间戳填内容哈希（同 Zig／lld 的可复现构建：非 0、逐字节可复现），校验和照算（同 gcc）。aarch64-windows 同步。

**P2 ASLR**（中）：基址改 0x140000000；四处绝对写入点登记位置，写 `.reloc`（DIR64，按 4 KiB 页分块）与数据目录 5；加
DYNAMIC_BASE、HIGH_ENTROPY_VA。`.reloc` 写出与 aarch64 路径共用一个模块。外来 COFF 对象（FFI 链接的静态库）在 .text 里出现
ADDR64 时同样出 DIR64（PE 允许修补代码页，MSVC、lld 都这样做），不拒绝。

**P3 x86_64 展开信息**（中到大）：x86 的 UNWIND_INFO 编码器（push 非易失寄存器、建帧、分配栈、保存 xmm6–15 等事件→展开码；
超过 1 MiB 等边界照 ARM64 处理），驱动对 x86 PE 总收集帧事件，链接时读内核的 `.txfn`，写 `.xdata` 与 `.pdata`（数据目录 3）。
栈探测（大于一页的栈帧逐页触碰）一并补上——没有它，大栈帧跳过保护页时会直接访问越界而不是正常扩栈。

**P3 细化（P1、P2 实施之后的调研）**

规则依据：微软《x64 prolog and epilog》——凡分配栈、调用别的函数或保存非易失寄存器的函数都须有展开数据描述序言；尾声只许
`add rsp, 常量`（或 `lea rsp, 常量[帧寄存器]`）、若干 `pop`、`ret`（或 mod＝00 的内存间接 `jmp`），展开器靠向前扫描认尾声，v1 不带
尾声码；分配满一页须先调 `__chkstk`（RAX＝字节数，只改 R10、R11 与标志位，调用方再 `sub rsp, rax`）。

现状（代码与 `kernel.obj` 实测）：
- 程序函数：序言 `push rbp; mov rbp, rsp; push rbx/r14/r15[/r12/r13]; sub rsp, N`，唯一尾声 `add rsp, N; pop…; pop rbp; ret`，帧事件
  只在 `--map` 时收集（`driver.tx` 只对 aarch64 PE 总收集）。rtfn：`sub rsp, N`／多个 `add rsp, N; ret`。回调外壳：压 8 个寄存器后分配，
  xmm6–15 在序言之后以 `movupd` 存到未必 16 对齐的槽。生成器与 async 是无栈的恢复点，x86 没有切换栈的代码。
- 内核 `.text` 157,963 字节、目录 279 个函数（278 个 rtfn 带事件，覆盖 78%）；没有目录的手写例程 136 个、约 34.5 KB：
  不改 rsp 的叶函数 41 个（7.5 KB）、入口即 `sub rsp` 的 69 个（14.9 KB）、入口压寄存器再分配的 3 个（1.2 KB）——这三类都能照常描述；
  **收缩包装**（快路径在建帧前就 `ret`）14 个（4.2 KB，如 `__tx_retain`、`__tx_native_mem_free`）、**一个函数里多段建帧** 7 个（6.6 KB，如
  `__tx_native_mem_alloc`、`__tx_release`、`__tx_str_concat`）、**中段压栈** 2 个（memcpy、memset 为 `rep movs` 暂存 rdi／rsi）——一条
  RUNTIME_FUNCTION 描述不了，且前两类全都调用别的函数（分配器、计数、字符串），回溯经过它们就断。
- x86 帧可以超过一页（`frameTotal` 含至多 4096 字节的逃逸区，编译器自身的 `guiMain` 为 4152 字节），x86 现在完全不探测。

做法（第 1～6 项按 aarch64-windows 的先例；第 7 项取乙）：
1. **编码器** `pe_unwind_x64.tx`：帧事件 → UNWIND_INFO v1（PUSH_NONVOL、SET_FPREG、ALLOC_SMALL／LARGE），逐条解码序言指令与事件对照，
   序言不超过 255 字节；`.pdata` 每项 12 字节（起点、终点、UNWIND_INFO 的 RVA），按起点升序、互不重叠，单独一节、进数据目录 3；
   `.xdata` 并入 `.rdata`。不需要分片（x64 的 RUNTIME_FUNCTION 不限长度）。
2. **数据源**：x86 PE 构建总收集程序函数的帧事件（同 aarch64，普通构建也做目录校验）；内核等对象读 `.txfn`；边界函数照旧不出。
3. **入口整形**：`__tx_thread_entry`（`push rbx`、`pop` 与 `ret` 之间夹着 `xor eax, eax`，不是合法尾声）改成带事件的 fixed-call，同 aarch64
   对 Windows 线程入口的处理；`__tx_entry` 桩（`sub rsp, 40` 后不归还即 `ret`）改成规整形状并登记。
4. **栈探测**：内核出 `__tx_chkstk`（MSVC 约定：RAX＝字节数、逐页触碰、只改 R10、R11），程序函数、rtfn、回调外壳的分配满 4096 字节时发
   `mov eax, N; call __tx_chkstk; sub rsp, rax`，分配事件记在这一组上（同 aarch64 的 `a64FrameAlloc`）。
5. **不做**：回调外壳的 xmm6–15 保存不入展开码（同 aarch64 对 d8–d15 的处理，另立；回溯只需 RSP／RIP，不受影响）；外来 COFF 对象自带的
   `.pdata` 不并入（要先支持现在拒绝的 ADDR32NB，另议）。
6. **核对**：门步 pe-image 增 `.pdata`／`.xdata` 核对（升序、不重叠、在 .text 里，展开码逐个解开、按指令对照序言，每个
   `push rbp; mov rbp, rsp` 开头都是某个 `.pdata` 函数的起点）；Windows 宿主上运行一个经 FFI 调 `RtlCaptureStackBackTrace` 的用例，
   逐帧对照 `--map` 的函数范围（含穿过内核分配器的一层）；另用 LLVM MC 的 `.seh_*` 输出对照编码（aarch64 同法），
   不进门。受影响的门断言随之更新（`test-native-functions.py` 的 `coverage.unwind`、单条 `sub rsp` 的分配、
   线程入口形状、开不开 `--map` 产物相同；`test-runtime-functions.py` 的去目录后产物不变——PE 将随 `.pdata` 变化）。
7. **手写内核例程里描述不了的 23 个**（收缩包装 14、多段建帧 7、中段压栈 2；取乙）：

   | 选项 | 做法 | 代价 |
   |---|---|---|
   | 甲 | Windows x86 内核改用可移植模块（`--generic=all`，同 aarch64-windows） | 实测指令数 +19%～21%（WSL 三负载，已含 rtfn 寄存器分配） |
   | 乙 | 改写这 23 个使其可描述：收缩包装与多段建帧把建帧的慢路径外提为独立函数（快路径仍是同一条条件跳转，指令数不变），memcpy／memset 把 rdi／rsi 暂存到 r10／r11 而不压栈（成为叶函数）；全部 136 个手写例程登记进函数目录（叶函数不带事件） | 工作量较大；改的是热路径（计数、分配），须逐个核对快路径指令不变并跑性能对照 |
   | 丙 | 不处理：这 23 个按叶函数对待 | 回溯经过分配器、计数、字符串例程即断，调试器、WER、剖析器在这些地方拿不到完整调用栈 |

   取乙：覆盖完整、不付性能代价，函数目录也随之补全（此前 `--map` 同样缺这 22%）。

**不做**：版本资源与清单（对照工具链默认都不带；以后可作构建选项）；按用到的系统函数裁剪导入表（内核是一个整体对象，
裁剪要先把内核按函数切节，收益不确定，另议）。

## 四、测试

- 新增 PE 核对（同 aarch64 的 `pe_arm64_check.py` 思路，用太虚写）：头部字段、DllCharacteristics、`.reloc` 覆盖全部绝对写入点
  （逐条对照链接器登记）、`.pdata` 按地址升序互不重叠、每个有帧函数都有展开项。
- 运行时：在随机基址下跑现有产物用例（Windows 的 ASLR 默认对 DYNAMIC_BASE 映像生效）；用 `RtlCaptureStackBackTrace`
  逐帧对照函数目录（同 aarch64-windows 的核对）；`--leak-check`、线程、FFI 回调各一例。
- 受影响、需随之更新的门步骤：asm-output、link-map（`coverage.unwind` 不再为 null）、functions、runtime-functions、debug-stub、
  product-identity；所有 Windows 产物字节都会变，交叉一致性与不动点照常要求逐字节相同。
- Defender：P1～P3 完成后用全部门产物跑一遍读取探针，记录结果；**不把“不被拦”当成验收条件**（云端判定不可控）。

## 五、兼容

产物布局变化，不影响太虚源码；进程地址随机化对太虚程序不可见（没有原始指针）。登记为兼容变更 `runtime.windows-pe-hardening`（运行时层），
写明 FFI 回调与外部库看到的地址每次运行不同。

