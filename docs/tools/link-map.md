# 链接映射：人类摘要与 AI 事实

`taixu build --map` 由太虚的生产发射、汇编和 PE／ELF／Mach-O 链接管线生成，支持 x86_64 Windows／Linux／macOS 与 aarch64 macOS（macOS 两个目标可在任何宿主上交叉构建）。文本与 JSON 来自同一份拥有函数、事件和名字字节的快照。

```sh
taixu build app.tx --out build --map build/app.map
taixu build app.tx --out build --map build/app.map.json --map-format=json
taixu build app.tx --target=linux --out build-linux --map build-linux/app.map.json --map-format=json
taixu build app.tx --out build --map build/app.all.json --map-format=json --map-scope=all
taixu capabilities --format=json
```

缺省 `--map-scope=functions`。文本展示产物身份、输出节／载荷组、函数来源、名字、模块、真实起止地址、大小、序言偏移／尾声数、固定栈分配与保存寄存器。范围右端排他，控制字符经过转义。JSON 提供相同事实及完整尾声、帧事件，适合 AI、脚本和后续展开器读取。

AI 先查询 `link_map=text,json`、`link_map_schema=link-map/6`、`link_map_scopes=functions,all`、`link_map_default_scope=functions`、`link_map_object_metadata=txfn/3`，以及函数／帧覆盖和 `link_map_stack_models` 能力。未发布开发格式直接演进，不保留旧 schema。JSON 写入 `--map` 指定的 UTF-8 文件，恰为一个 JSON 文档；`build` 的 stdout 保持普通构建日志。

| 字段 | 读取方法与边界 |
|---|---|
| `schema` / `toolchain_version` / `ok` | 结构、开发版本与结果；原生数据接口失败时 `map=null`，CLI 通过退出码与 stderr 报告 |
| `map.scope` | `functions` 不构造原始符号报告；`all` 同时保留全部符号，函数事实相同 |
| `map.image` | 完整产物字节数、SHA-256、首选基址、入口；先核对身份再解释地址 |
| `map.numeric_encoding` | 字节／偏移／大小为无符号十进制字符串，地址和值为固定16位十六进制字符串；`sp_delta` 是有符号十进制字符串。用整数或 `BigInt` 读取 |
| `map.sections` | PE 实际节头；ELF 为生产 text／rodata／data／bss 载荷组，BSS 虚拟尾单列、文件偏移为 null，不代表 ELF 节头表；Mach-O 的四组与 `__text`／`__const`／`__data`／`__bss` 一一对应（`coverage.layout=macho-producer-sections`），链接器另加的 `__got`、`__thread_vars`、`__thread_bss` 不列 |
| `map.functions_total` / `functions` | 本次已提供的生产者函数目录，无隐藏截断；没有生产者目录的原始对象映射为 null／空表；不表示所有代码均有目录 |
| 函数的 `id` / `source_id` / `module` / `name` / `link_name` | id 为本次报告顺序；仅 tx-generated 的 source_id 对应本次画像／反汇编源函数，其他来源为 null；模块指实际生产源码，ID 只在本次构建内有效 |
| 函数的 `origin` | tx-generated、runtime-rtfn、ffi-rtfn、debug-rtfn、tool-rtfn、ffi-callback-shell 或 process-thread-entry，按生产源码分类；不等于对象位置，例如 Windows 程序内复用 nrt_proc 的 __tx_tool_quote 仍为 runtime-rtfn。RtFn 来源不代表所有运行时／FFI 外壳均已覆盖 |
| 函数的 `link_va` / `link_va_end` / `bytes` | 真实 `[start,end)` 范围与大小，来自显式生产边界和实际输入放置，不以相邻标签估计 |
| 函数的 `file_offset` / `input_section_*` / `output_section` | 文件位置、输入节内的起止偏移、输出节／组；保留实际输入对象索引与原节索引，PE 程序对象索引为 null |
| `prologue_end_offset` / `epilogues` | 相对函数起点的序言结束、每段尾声的 start_offset／排他 end_offset；太虚函数仍为唯一公共尾声，RtFn 可有多个或零个声明返回点 |
| `frame.body_sp` / `register_recovery` | fixed-call 才提供 frame，此时 body_sp=fixed；太虚函数与回调外壳给 saved-gpr-events，RtFn 为 not-provided，不能据此宣称完整寄存器恢复 |
| `frame.register_numbering` / `entry_sp` | 寄存器编号所属的架构与进入时栈指针的含义，按目标架构取值，见下文 |
| `frame.events` | 指令开始／完成偏移、动作、寄存器编号、字节量和栈指针变化，见下文 |
| `map.symbols_total` / `symbols` | 函数视图为 null／空表、`coverage.symbols=omitted-by-scope`；all 视图为完整总数／条目、`complete` |
| 符号的 `name_bytes` / `name_utf8` | 前者保留完整原始名字；后者仅有效 UTF-8 时给文本，否则为 null。不能拿显示文本当原始符号身份 |
| 符号的 `origin` / `object_index` / `symbol_index` | 程序标签、输入对象或合成项；局部同名符号用对象与原符号索引区分 |
| 符号的 `value_kind` / `value` | `link-va`、`absolute`、`section-relative`、`metadata`、`undefined`；只有链接 VA 提供 `image_relative_va`，未定义值为 null |
| 符号的 `declared_size` | ELF 声明的 `st_size`，完整无符号64位；COFF 未知为 null，不等于已验证函数大小 |
| 函数的 `stack_model` | fixed-call 提供既有帧事件；process-entry／thread-entry／stack-transition／stack-disposal 表示特殊边界，frame 和 prologue_end_offset 为 null、epilogues 为空；消费者须停止展开，不能把未知当作零分配或推断 noreturn |
| `map.coverage` | 函数范围为 producer-declared 或未知 null；有特殊边界时 frame_events 为 partial-producer-declared；frame_functions／boundary_functions 为精确十进制字符串数量，无目录时为 null。included/excluded_function_origins 列明实际来源与缺口；unwind／cpu_profile 仍为 null |
| `coverage.object_metadata` | 内部对象目录 txfn/3、有目录对象数／缺失目录对象数；目录以原输入节和实际 text SHA-256 绑定，损坏或重复时拒绝映射，丢弃节不生成函数 |

帧事件覆盖 EMITMIRROR（x86_64）与可移植渲染器（aarch64）生成的太虚函数，以及 RtFn 生成的内核、FFI 内层／调用桩、调试与工具例程，以及手写回调外壳。Linux `_start`、各目标线程入口、Linux clone 与自解映射退出桩只提供范围和明确的特殊栈边界，不提供恢复帧。其余手写运行时、细分栈转换状态、链接跳板、无目录的外部对象仍未覆盖。函数范围可能包含多个内部标签与恢复入口，内部标签不会被误当作另一个函数。

每个事件的 `instruction_start_offset`／`instruction_end_offset` 相对函数起点，效果在指令完成后生效；指令长度由两者相减。事件的 `bytes` 是栈操作字节量，`sp_delta` 是有符号的栈指针变化，均不表示指令长度。`register` 采用 `register_numbering` 所指架构的硬件 GPR 编码：`x86-64-gpr` 为 rax／rcx／rdx／rbx／rsp／rbp／rsi／rdi 对应0…7、r8…r15对应8…15；`aarch64-gpr` 为 x0…x30 对应0…30、sp 为31；无寄存器时为 null。它不同于 DWARF 寄存器编号。

动作包括保存非易失寄存器、设置帧指针（x86_64 rbp=rsp，aarch64 x29=sp）、固定栈分配／释放、恢复寄存器和 return。x86_64 的 `entry_sp=return-address-slot` 指进入函数时 RSP 指向返回地址，return 的 `sp_delta=8`；aarch64 的 `entry_sp=call-site-sp` 指进入时 sp 等于调用点的 sp、返回地址在 x30，return 的 `sp_delta=0`。return 是终止控制流边的效果，其完成偏移等于所在尾声的排他终点。aarch64 的一条指令可带几个范围相同、依次生效的事件：`stp` 为“压入第二个、压入第一个寄存器”，`ldp` 为“弹出第一个、弹出第二个”，与逐个压栈的布局一致；`str x21, [sp, #-16]!` 记为压入16字节（含8字节对齐填充），弹出字节与压入相同；设帧指针紧跟压入 x30、x29 之后，x29 指向帧记录；每条指令完成后栈深为16的倍数；大于4095字节的分配分成几条 `sub sp`，各记一个分配，合计为本帧分配。每段尾声从序言完成后的状态独立恢复，不能把上一段尾声的状态线性带入下一段；函数末尾仍可能有普通代码。零尾声只表示没有声明 return，不是控制流必不返回的证明。

这些事实尚未编码成 Windows unwind 或 DWARF CFI，不能单凭本文件声称已经支持任意指令处的系统栈展开。固定栈分配也不是运行期总栈峰值、堆占用或函数 CPU 热点。

链接 VA 是首选映像中的值，实际进程加载偏移尚未测量，`runtime_load_bias=null`。映像相对地址不是文件偏移，导入槽／GOT 的 VA 也不是外部函数在进程中的地址。绝对值可以为零或包含完整64位高位，仍按绝对值解释。

ID、对象、符号和输出节／组索引从0起。函数 input_section_index 为原节表的0起索引（ELF 含0号空节）；PE 程序的0／1／2／3为 text／rdata／data／bss。原始符号的 COFF section_index 则保留1起节号，ELF 为原节表索引。null 表示不存在或不适用，不能当作索引或地址0。

构建映射的收集／对象目录解码仅在显式请求 map 时开启，不改变可执行字节。内核生成器始终把自身 RtFn 目录写进非装载 `.txfn` 节（两种架构；寄存器编号按对象的机器号解释，ELF 的 62 为 x86_64、183 为 aarch64，与目标不符即拒绝），随嵌入内核一起自举；它不进入客户端产物的装载区。退出码为0成功、1编译／元数据／输出失败、64用法错误；`--map-format` 与 `--map-scope` 必须同 `--map` 使用。映射写出失败可能发生在产物已生成之后，调用者仍须按失败处理。规范化后与可执行文件相同的映射路径会被拒绝。

全符号导出数量远大于函数数：上一批完整编译器 Windows 有944,175条、JSON约412 MiB，Linux有18,515条。函数视图在原始报告构造前就省略这些条目；若需定位原始符号，可显式选择 all，并按名字字节／对象索引筛选。两个目标保留局部标签的方式不同，不能直接比较符号总数。

外部 ELF `STT_TLS`／SHF_TLS 的通用 TLS 链接仍会明确拒绝，当前内核线程 TLS 模板走既有实现。本步为 T2／S0b3；其余函数来源、系统展开、可靠采样和通用堆分析继续推进。设计与独立验证见入口与栈边界、arm64 帧（§A5）、回调外壳、太虚函数与运行时目录。
