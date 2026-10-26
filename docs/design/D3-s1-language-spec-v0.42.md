# D3 S1 语言规范 v0.42

**v0.42 增补（2026-10-26，工具链内建 `setEnv`；未发布，无兼容义务；实施设计见 `docs/design/v052-gate-tx-v0.1-20261026.md`）**：§7 增工具链专用内建 `setEnv(name, value): void | Error`，与 `getEnv` 对称：写同一张进程级环境表（Windows 为进程环境块，`SetEnvironmentVariableW`；类 Unix 目标为运行时持锁的表，`std/os` 的 `setenv` 写的也是它），此后起的子进程按 v0.39 带上。名字为空、含 `=` 或 NUL，或值含 NUL 时得 `Error`（`invalid argument: <名字>`）；内存不足同其余内建为致命错误。用途：太虚写的门与构建脚本给子进程设环境（如编译器按 `TAIXU_STDLIB` 找标准库根）——`std/os` 经 `__rt` 内部内建，只供标准库模块，核心的门也不依赖它要验证的标准库。取舍：Go 的 `os.Setenv`、Python 的 `os.environ`、Node 的 `process.env` 都改本进程环境、子进程随之继承；Rust 的 `set_var` 在 2024 版起为 `unsafe`，因为与 C 的 `getenv` 并发读写 `environ` 会竞争——太虚的表归运行时所有、持锁读写，不碰 C 库的 `environ`，代价是经外部函数调用的 C 代码在类 Unix 目标上用 `getenv` 看不到改动（与 `std/os` 的 `setenv` 相同）。只给某个子进程的环境（Go 的 `Cmd.Env`、Rust 的 `Command::env`）本版不做。只影响工具链构建。

**v0.41 修订（2026-10-25，命令行参数的编码；未发布，无兼容义务；登记为兼容变更 `lang.argv-valid-utf8`；实施设计见 `docs/design/v052-argv-entry-v0.1-20261025.md`）**：§7 入口写明 `args` 逐项是命令行原文（空参数保留），参数不是合法 UTF-8（Windows 为不成对的 UTF-16 代理）时进程在入口拒绝启动：stderr `taixu aot: <平台> argv acquisition failed: InvalidUnicode`，退出码 64。此前 Windows 已如此（2026-10-07 起），Linux、macOS 却把参数字节原样做成 string，破坏 string 恒为合法 UTF-8 的不变量（实测 `strSub` 以 4010 trap、`strIndexOf` 静默得错的下标）。取舍：同 Rust 的 `env::args()`，拒绝而不替换——替换成 U+FFFD（Swift、Node、Java）会让程序拿着改过的名字去打开、删除另一个路径；环境变量与当前目录仍逐字节替换（它们是继承来的环境，无关的一项编码不对不该让程序起不来）。另：入口把参数表留给标准库（`__rt` 内部内建 `__rtArgCount`、`__rtArgAt`），`std/os` 的参数因此与 `main(args)` 逐项相同（此前 Linux 读 /proc、macOS 经 sysctl、Windows 按两态规则自己切分，三端都丢掉空参数）。

**v0.40 修订（2026-10-25，工具链子进程继承的描述符；未发布，无兼容义务；登记为兼容变更 `tool.child-handles-std-only`；实施设计见 `docs/design/v052-child-handles-v0.1-20261025.md`）**：§7 `runProcess`、`procSpawn`（及 `procSpawnLimited`）写明子进程只得到标准输入、输出、错误三个（`procSpawn` 另加调试控制通道的两端），本进程其余的文件、套接字、管道等描述符／句柄一律不继承，不论是否设了 CLOEXEC 或可继承标志，含经外部函数打开的。此前 Windows 的 `runProcess` 以 `bInheritHandles` 继承本进程全部可继承句柄（ws2_32 建的套接字缺省可继承：实测监听套接字被留在后台的孙进程拿着，父进程关掉后端口仍被占），并把父进程的标准句柄永久改成可继承；Linux 两个内建与 macOS 的 `runProcess` 只靠 CLOEXEC，经外部函数打开、未设它的描述符进子进程。实现：Windows 把标准句柄与 stderr 文件各复制成可继承的副本、只经 `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` 交出（同 Go 的 `syscall.StartProcess`）；macOS 设 `POSIX_SPAWN_CLOEXEC_DEFAULT` 并逐个列明留下的描述符（存在才列，父进程关了标准输入也照常起进程）；Linux 子进程在 exec 前 `close_range`，内核不支持时逐个关到 `RLIMIT_NOFILE` 软上限（同 Python 的 `close_fds=True`）。取舍：Python（PEP 446 一律不可继承＋`close_fds` 缺省为真）与 Go 是白名单；Rust 在 Windows 上以全局锁串起建可继承句柄与起进程，锁外建的句柄照样进子进程。另修（跟进第 2 条）：父进程关了 2 号时，`runProcess` 的 stderr 重定向文件落在 2 号、带着 CLOEXEC，`dup2(2, 2)` 不清它，exec 时子进程的 stderr 随之关掉、写的内容丢失；落在 0、1 号时 macOS 还会把它当成子进程的标准输入输出交出去——改为打开后号码小于 3 即挪到 3 以上（同 `procSpawn` 的管道、Python 的 `_posixsubprocess`），子进程的标准输入、输出与父进程的实际状态一致。只影响工具链构建（这几个内建仅 `--toolchain`）。

**v0.39 修订（2026-10-25，工具链子进程的环境；未发布，无兼容义务；登记为兼容变更 `tool.child-env-current`）**：§7 `runProcess`、`procSpawn`（及 `procSpawnLimited`）写明子进程的环境是本进程**当前**的环境——Windows 为进程环境块，类 Unix 目标为进程级环境表（初值为启动时的 envp，`std/os` 的 `setenv` 写入它，即 `getEnv` 读的同一张表），在起子进程之前持锁取一份快照，不与别的线程的 `setenv` 撕裂；`procSpawn` 仍去掉 `TAIXU_DEBUG_CHANNEL`、建控制通道时换成本次的。此前 Windows 本就继承当前环境块，Linux、macOS 却传启动时的 envp（`procSpawn` 在 Linux 上读 `/proc/self/environ`），`setenv` 之后起的子进程看不到改动，三个目标不一致。取舍：Go 的 `os/exec`（取 `syscall.Environ()`）、Python（`os.environ` 经 `putenv` 写进 C 的 `environ`）、Rust 的 `Command`（在环境锁下取当前环境）、Node 的 `child_process`（缺省传 `process.env`）都让子进程继承当前环境。只影响工具链构建（这几个内建仅 `--toolchain`）。

**v0.38 修订（2026-10-24，同一 `Future` 多次 await；未发布，无兼容义务；登记为兼容变更 `lang.future-await-repeat`）**：§6.12-2 写明同一 `Future` 可以多次 `await`，每次得同一结果——结果与 future 共享（引用类型得同一对象，值类型得相同的值），future 释放时连带放掉它那一份；已取消的每次得 `err("task cancelled")`。此前的实现（沿用 VM）把结果移给第一个 `await`、future 里留 `None`，第二次 `await` 得到不合 `T | Error` 类型的 `None`，一用即 trap——类型不健全，由安全审查攻关第 2 项的场景矩阵（`docs/design/v052-heap-check-v0.1-20261024.md` 四）查出。取舍：JS/TS 的 Promise、C# 的 Task、Kotlin 的 Deferred、Swift 的 `Task.value`、Dart 的 Future、Python 的 asyncio.Future 都允许重复 await 并得同一结果；Rust 以仿射类型在编译期禁止第二次 `.await`，太虚没有移动语义，做不到编译期禁止。改为运行期报 `Error`（同 `threadJoin` 的“只能 join 一次”）也健全，但把 TS 的常见写法变成错误路径；`threadJoin` 只能一次源于系统线程资源的回收，不是结果的归属。临时 future（`const v = await f(x)`）用后即释放，共享不延长结果的寿命；存进容器的 future 让结果活到 future 释放为止，同 Promise。

**v0.37 增补（2026-10-24，`Bytes` 批量操作，LANG-32；未发布，无兼容义务；实施设计见 `docs/design/v052-bytes-bulk-v0.1-20261024.md`）**：§6.15-8 与 §7 增六个内建——`bytesCopy`（区间拷贝，可重叠）、`bytesFill`（区间填充）、`bytesGetLE`／`bytesGetBE<T>`（按端序读定宽数值，越界得 `None`）、`bytesSetLE`／`bytesSetBE<T>`（按端序写，越界 trap 4002）；`T` 为 `i8`…`u64`、`int`、`f32`、`float`，须写出，写入的值须正是 `T`。全部带越界检查，写冻结的 `Bytes` trap 4022；不放宽任何安全规则。不用新内建的程序产物不变。

**v0.36 修订（2026-10-24，外部函数的字符串与 C 变参、NaN 的打印；未发布，无兼容义务；实施设计见 `docs/design/v052-ffi-string-variadic-v0.1-20261024.md`）**：§6.8-12 外部函数签名顶层的 `string` 即 UTF-8、NUL 结尾的 C 串（规则同原 `cstr`），`cstr` 退场，写 `cstr` 报 3033 并提示改写 `string`；`wstr` 照旧。§5、§6.8-21 新增 C 变参参数 `...名: 类型`：排在全部定参之后、至少一个定参，按 C 变参约定传，编译器做 C 的默认实参提升；系统 C 库里常见 C 变参函数的定参个数须同 C 原型（3033）；`from syscall` 不收变参参数，回调不能作变参参数。§6.21-18 与 §7 `toStr`：任何 NaN 一律打印 `nan`（不带符号），`floatToBits` 照旧给出原始位；无效运算产生的 NaN 的符号与载荷由架构决定。§8 排除清单相应更新（调用处任意个数的变参仍排除）。

**v0.35 增补（2026-10-23，macOS 适配 M0b；未发布，无兼容义务；实施设计见 `docs/design/v052-macos-m0b-language-rules-v0.1-20261023.md`）**：§6.1-4 按目标选择文件增 `x_<os>_<arch>.tx` 与（类 Unix 目标的）`x_unix.tx` 两档，兄弟文件比对随之覆盖全部后缀；§6.1-5 导入写法须与磁盘逐字节一致（3008，全部宿主）；§6.8-7、9 被调用的外部函数的目标检查在语义分析里做（编辑器与检查命令即可看到），macOS 目标上 `from syscall` 报 3033；§7 增工具链专用内建 `hostArch`（目标架构名，现为 `"x86_64"`）与 `localListDir`（本地根内句柄式目录列举）；`hostTarget` 的取值说明补 macOS。macOS 目前只用于分析（`taixu dev-check --target=macos`），产物在 macOS 适配 M1 实施。**补记（2026-10-24，macOS 适配 M1，不改语言规则）**：`x86_64-macos`（写 `macos` 亦可）自 M1 起可 `build`／`disasm`，产物为经 libSystem 动态链接、ad-hoc 签名的 Mach-O，运行时只移植了一部分（线程、文件与目录、子进程等调用即以退出码 70 报“macOS 运行时尚未支持”），外部函数（`from "库"`）、工具链例程与 `--debug` 构建暂拒绝，没有 `--static`／`--lib`；`aarch64-macos` 仍只用于分析。实施设计见 `docs/design/v052-macos-m1-macho-v0.1-20261024.md`。**补记（2026-10-24，macOS 适配 M2）**：x86_64 macOS 的运行时移植完成（线程、文件与目录、时间、子进程经 libSystem，不再有“macOS 运行时尚未支持”的终局）；外部函数 `from "库"` 在 macOS 上写 dylib 的安装名（§6.8-7），被调用而库名不是安装名的报 3033；与运行时经 libSystem 导入的符号同名的外部函数只能来自 libSystem（构建时报 3033，§6.8-9）；x86_64 上从 libSystem 导入的 stat 与目录族等按 Apple 头文件改名链接（如 `stat` → `stat$INODE64`），声明按 64 位 inode 的结构布局写即与 C 程序一致。工具链例程与 `--debug` 构建在 macOS 上仍拒绝（M3）。实施设计见 `docs/design/v052-macos-m2-runtime-v0.1-20261024.md`。**补记（2026-10-24，macOS 适配 M3a，不改语言规则）**：工具链例程（`--toolchain` 的 §7 工具链专用内建）在 x86_64 macOS 上可用。子进程：`procSpawn` 经 posix_spawn（未显式接上的描述符一律关闭），`procSpawnLimited` 经 fork＋exec，CPU 与 core 限额同 Linux；macOS 没有可用的地址空间限额，内存限额改由看守线程按进程组的物理足迹事后执行（超过即整组 SIGKILL，`procWait` 得 −9，不是分配失败，见 §7）。工具链例程第一次写管道之前，若 SIGPIPE 为缺省处置则装空处理函数（exec 时复位，子进程不受影响）：工具链写坏管道得 −1，`print`／`eprint` 与运行期报错写标准输出／错误遇坏管道仍按 SIGPIPE 结束，与 Linux 相同；父进程交下的 SIG_IGN 原样保留。本地根原语以 `O_NOFOLLOW_ANY` 打开（任一组件是符号链接即拒绝），终点与根的设备号不同即拒绝（跨挂载），原语本身不查文件系统类型（同 Linux）。`tempDir` 在类 Unix 目标上去掉 `$TMPDIR` 末尾的 `/`（只剩根时保留；macOS 的缺省值带 `/`，Linux 同此更正）。`--debug` 构建与 `lsp`／`mcp`／`dap` 服务在 macOS 上随 M3b、M3c 交付。实施设计见 `docs/design/v052-macos-m3-toolchain-v0.1-20261024.md`。**补记（2026-10-24，macOS 适配 M3b，不改语言规则）**：宿主服务 `taixu lsp`／`taixu mcp` 在 x86_64 macOS 上可用。信任根的挂载核验经 `getfsstat`（没有 MNT_LOCAL 的挂载拒绝，规则同 Linux）；MCP 的 worker 只能自证 CPU 与 core 限额，内存限额由服务的看守线程事后执行；macOS 的 `procSpawnLimited` 在子进程环境里放回收 cookie `TAIXU_REAP_COOKIE`（继承来的旧值保留、以 `:` 追加），服务凭它找回脱离进程树的后代——exec 时清空或改掉环境的认不出，`server/discover` 以 `orphan_reaping_scope` 写明（三个宿主都报）。**补记（2026-10-24，macOS 适配 M3c，不改语言规则）**：x86_64 macOS 上可 `--debug` 构建，`run --debug`、`taixu dap` 与 MCP 的 `debug_*` 可用，行为与 Linux 相同（同一份金转录）。

**v0.35 安全修正（2026-10-24；未发布，无兼容义务）**：§7 写明交给系统的串含 NUL 一律得 invalid argument（`entryKind` 得 0）。此前只有 Unix 的文件读写原语与工具链进程内建这样做；Windows 的文件原语、各目标的 `makeDir`、`removeFile`、`listDir`、`entryKind` 与 `runProcess` 把串在 NUL 处截断后照常操作（看到的是另一个路径或实参）。实现随之修改，用例 `tests/nrt-cases/path-nul.tx`。

**v0.34 语义修订（2026-10-23，用户裁决 B3；未发布，无兼容义务）**：`Bytes`（含经 `bytesAsList`／`listAsBytes` 的 `List<u8>` 别名）交给外部函数或系统调用的借用期内，**任何改变长度的操作**一律在修改之前 trap 4024——`bytesPush`、非空的 `bytesAppend`、`listPush`、`listInsert`、非空表的 `listPop`、`listRemoveAt`；原地读写（`bytesGet`／`bytesSet`／`listGet`／`listSet`）与不改变长度的调用（空的 `bytesAppend`，空表的 `listPop` 得 `None`）照常。取代 v0.33 按容量的判定（只拒绝超出当前容量、会迁移数据区的增长）：旧规则下同一程序是否 trap 取决于当时的容量（倍增策略、此前的弹出），不可预测；缩短也照常进行，调用方另传给 C 的长度就会在调用中途与实际长度不符（C 写入的尾部在返回后不可见）。先例：Python `bytearray` 存在缓冲导出（`memoryview`、C 扩展持有的 `Py_buffer`）时拒绝任何改变长度的操作、同长写入照常（3.13 实测 append／pop／insert／del／clear 得 `BufferError`，同长切片赋值与空 `extend` 照常）；Swift `withUnsafeMutableBytes` 期间对同一数组的其他访问按独占性规则 trap，更严；Rust 由借用检查在编译期拒绝。4024 改由各内建报出，带调用处的源码位置（运行时内部的兜底检查不带）。§6.8 第 12、16、19 条随之修订。**同版补记（内存耗尽）**：附录 A 写明运行期内存耗尽的结局——致命、`taixu: 内存不足`、退出码 3；内存上限扫描查出两处偏离，一并改正：启动时取参数表的分配失败原报用法错误 64（Windows 上 `CommandLineToArgvW` 内存不足时报的错误码与系统失败分不开，改为运行时按同一规则自己拆分命令行），新线程建不起自己的区域原以内部错误 70 结束整个进程，现按附录 A 原有的“创建失败时 `threadJoin` 得错误值”处理。

**v0.33 增补（2026-10-23）**：`procTryWrite` 为工具链专用单次非阻塞写，允许部分进展，暂不可写为 0，关闭／错误为 −1；不改变 `procWrite` 写满语义。Bytes 传给 C/syscall 时在调用期间保持 backing 地址：回调经 Bytes 或 `List<u8>` 别名请求容量增长且会迁移时，在修改之前 trap 4024；允许不迁移的原地读写，正常返回后借用解除，嵌套与重复实参按 LIFO。冻结 Bytes 仍在交给外部前 trap 4022。C 保留返回后的指针、外部异常／longjmp 不属于安全借用合同。普通 RC 的永生判定为 32 位最高位；越界后不再 retain/release，级联一致。Windows 回调保存完整 XMM6–15。见安全边界 v0.2。

**v0.33 审查修正（2026-10-23，codex/taixu 合入前审查；实现随之修改，未发布无兼容义务）**：`procWait` 增“状态不可知得 −256”（原为 −1，与被 SIGHUP 终止撞值）；Windows 上 `procTryWrite` 切成非阻塞后保持、`procWrite` 写前切回阻塞（逐次还原使管道有未读数据时每次都切换失败，吞吐只有约 200 KB/s）。

**v0.33 同日勘误（2026-10-23，对照 68d82a0 的实现核对；纯文档更正，语义与实现不变，不另升版，同 v0.24 附录 A 同日更正的先例）**：§6.8-12 写实长度为 0 的 `Bytes` 的数据区指针（新建的空 `Bytes` 为哨兵地址 1，空的紧凑 `List<u8>` 经 `listAsBytes` 为 0，不再写“可能是空指针”）；§6.8-16、§6.8-19 补冻结 `Bytes` 交给外部之前 trap 4022 与调用期间会迁移数据区的增长 trap 4024；§6.22-8 删去 v0.26“冻结 `Bytes` 作外部函数实参不检查”的过时注记；§7 `procTryWrite` 补“得 −1 后该流已关闭、此后恒为 −1”，`procSpawnLimited` 写实 Windows 只限用户态 CPU 与 Linux 限额、清理的实际范围，`readLocalFileBytes` 写明原语本身不查文件系统类型；“修订 v0.27”条目恢复原文（`procTryWrite` 属 v0.33，此前被误插进该条目的九行名单）。

**v0.32 增补（2026-10-23，工具输入与执行边界加固）**：新增四个工具链专用原语，为已批准的本地信任根和 MCP 资源边界提供系统支持。普通应用代码不使用；仅 `--toolchain`，原生构建。新增原语不意味着服务接入已经完成，也不构成不可信代码沙箱。详见实施设计。

**v0.31 现行边界（2026-10-21，第 6 轮 6e）**：当前核心、运行时和 CLI 由太虚原生实现，公开 VM 与 `.txc` 产物退场。模块级 `declare function` 必须有 `from "库"` 或 `from syscall 编号`；无 `from` 的宿主 extern 报 3033，即使未调用也报错。接口内的方法签名不是宿主 extern，不受影响。带 `from` 的既有 FFI、类型和 ABI 不变。GUI 引擎外移后的空窗已由用户在 2026-10-18 接受，当前不提供 desktop/gui 模板或内置 GUI 宿主。原生 `build/run/test`、格式化、调试、DAP、LSP、MCP 与源码汇编清单保留；`build --bytecode` 报用法错误 64。旧版 Zig/VM 描述只解释历史，不构成当前可用能力。

**状态：** v0.1–v0.3 已评审通过；v0.4 随 V0.6 收官追认；v0.5 随 V0.7 审查收官（2026-09-10）；v0.7 随 V0.9-L1 落地；v0.8 随 V0.9 审查收官追认（2026-09-10，审查方验收——同步链三副本已亲核）；**v0.9 随 V0.10-A1 落地（2026-09-10，D29 §一/§二评审定档授权执行——同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）**；**v0.10 随 V0.21-L1 落地（2026-09-16，D57 §一评审执行一体授权——同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）**；**v0.11 随 V0.22-L1 落地（2026-09-16，D57 续行/LANG-12 评审执行一体授权——同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）**；**v0.15 随 V0.41-LANG-16 落地（2026-09-24，design-plan-v0.41 M0-② 时序裁决授权——同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）**；**v0.16 随 V0.50-S5-① 批 A 落地（2026-10-03，D102 P-1 原语小合同＋独立审计通过授权——同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）**。
**v0.30 修订历史：** 2026-10-19，补记三个通用内建：`eprint`、`listDir`（V0.45 起，BOOT-38）与 `entryKind`（V0.47 起，BOOT-42）的实现、用例与诊断早已在册，§3 预声明标识符与 §7 内建表一直漏列；第 6 轮 6d-3b 的 LSP 内建表以 §7 为准逐条核对 semamirror 的 `builtinOf` 时发现。§3、§7 补记，语义不变；**其余章节零改动**。

**v0.29 修订历史：** 2026-10-19，第 6 轮 6d-1a 工具链内建 `fileSize`（TOOL-104——`docs/design/v052-round6-lsp-mcp-v0.1-20261019.md` §三-4 改定：LSP 按用户裁决 §十一-6 对 `embedBytes` 只查存在与大小、不读内容；只取元数据并跟随符号链接，对照 Rust `fs::metadata`、Go `os.Stat`）：§3 预声明标识符增一个工具链专用内建；§7 内建表增一行。**实现面只在太虚自举编译器与 NRT 运行时**；用到它的程序只能原生构建，字节码构建报错。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.28 修订历史：** 2026-10-18，第 6 轮 6c-4a 工具链内建 `currentDir`（TOOL-103 增补——`docs/design/v052-round6-debug-v0.1-20261018.md` §四-3、§四-6：调试会话要把模块路径与 VM 一样给成绝对路径；返回 `string | Error` 对照 Go `os.Getwd`、Rust `env::current_dir`）：§3 预声明标识符增一个工具链专用内建；§7 内建表增一行。**实现面只在太虚自举编译器与 NRT 运行时**；用到它的程序只能原生构建，字节码构建报错。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.27 修订历史：** 2026-10-18，第 6 轮 6c-3 工具链进程与标准输入内建（TOOL-103——`docs/design/v052-round6-debug-v0.1-20261018.md` §三-1、§四-6；按“新增系统能力先只对工具链开放”的先例自定，接口在实施前对照 POSIX `read(2)`、Go `io.Reader`、Rust `Read` 修订）：§3 预声明标识符增九个工具链专用内建；§7 内建表增九行与“工具链进程句柄”一段；附录 A 增一行。**实现面只在太虚自举编译器与 NRT 运行时**（Zig seed 冻结于 v0.17）；用到它们的程序只能原生构建，字节码构建报错。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.26 修订历史：** 2026-10-18，定宽数值第二步（O4 小轮；LANG-28 的第二步——`docs/design/v052-fixed-width-step2-v0.1-20261016.md`，2a、2b、2c1、2c1b、2c2、2c3 均已落地，实施记录见该文 §八；语义由 `docs/design/v052-fixed-width-numerics-v0.1-20261015.md` §四“第二步”裁决（用户 2026-10-15：七问按推荐），实施设计五问用户 2026-10-16 按推荐裁决并要求复核性能与安全（该文 §七）；移位的类型规则按判定套路自定（该文 §八 2a-2）；原定 v0.24、v0.25，容量附录与线程先后占用后顺延）：§3 整数字面量与预声明标识符（`wrapAdd` `wrapSub` `wrapMul` `bytesAsList` `listAsBytes`，prelude 增 `i8Hash`…`usizeHash`）；§4.1 定宽数值、容器、字节三行注记；§4.2-1 字面量对称取型；§6.8-12 `Bytes` 实参的空数据区注记；§6.13-1 注记；§6.15-1 修订“`Bytes` 成为 `List<u8>` 的别名”的说法、§6.15-7 与 `List<u8>` 零拷贝互转（新条）；§6.19-2 定宽整数实现 `Hashable`；§6.21 增第二步（第 13–25 条：算术、除法与取余、位运算、移位、一元负号、`f32` 运算与 `toStr(f32)`、字面量对称取型、`u64` 大字面量、环绕运算、紧凑列表、`Bytes` 与 `List<u8>`、`Hashable`、诊断）并修订第 3、5、8–12 条（第 19 条写明对称取型给 v0.23 定宽比较带来的两处可见变化与迁移写法）；§6.22-8 冻结的 `Bytes` 作外部函数实参不检查的注记、§6.22-9 注记；§7 内建表增五行、`toStr` 扩 `f32`、`imul` 注记；§8 O4 注记与 v0.26 排除增补。实现只在太虚编译器与 NRT（Zig seed 冻结于 v0.17）。

**v0.25 修订历史：** 2026-10-17，线程、发送与共享（LANG-30——`docs/design/v052-threads-v0.1-20261015.md`；用户 2026-10-16：八问按推荐，擦除类型按推荐“运行期按表核对”；第 4 步的实现选择按判定套路自定，见该文 §二十一）：§3 预声明类型名 `Thread`、`Shared`、`Atomic` 与九个内建；§4.1 类型清单增三行、§4.2 增句柄规则；§6.5-3 三者不可比较；§6.8 回调与错误码按线程；§6.22 线程、发送与共享（新节）；§7 内建表增九行（BuiltinId 61..69）；附录 A 增共享块计数一行。实现只在太虚编译器与 NRT（Zig seed 冻结于 v0.17）。

**v0.24 修订历史：** 2026-10-16，容量与编码上限（LANG-29——`docs/design/v052-capacity-limits-review-v0.1-20261016.md`；用户 2026-10-16 要求上限按写操作系统、训练大模型等长期目标前瞻评估，裁决泛型实例护栏与引用计数两项，其余按判定套路定）：§5 泛型实例化语义的“每程序实例数上限 512”改为“实例化嵌套不超过 256 层、单个实例的类型参数按展开树计不超过 2^20 个类型节点，不限每程序实例总数”；新增附录 A“实现限制”——逐项列出原生链（TXC v6，64 位指令字）与冻结的 Zig 链（TXC v4）的上限、超限的诊断或运行期行为，以及守护它们的门。语言语义其余不变；原生链放宽了字段号、模块顶层绑定、闭包捕获、接口方法、外部函数、单函数常量与跳转距离的上限。同日补充：单函数寄存器与参数上限放宽（容量批 C3b）；§7 标准库内部内建（`__rt` 前缀只供标准库模块）与 `getEnv` 在 Linux 改读进程级环境表（线程 2c）；每线程值栈与原生调用深度放宽（线程 2d）；`readFile` 去掉 64 MiB 上限、大于 4 GiB 的写出逐块完成（容量批 C4b）；数组、切片元素数一行更正（容量批 C4c）。

**v0.23 修订历史：** 2026-10-15，去 Zig 第 5 轮（上）定宽数值第一步与 FFI（LANG-28 定宽数值——`docs/design/v052-fixed-width-numerics-v0.1-20261015.md`，用户 2026-10-15 七问按推荐；LANG-27 FFI——`docs/design/v052-ffi-platform-v0.1-20261015.md`，用户 2026-10-15 十二问按推荐）：§3 预声明类型名 `i8 i16 i32 u8 u16 u32 u64 isize usize f32`（`i64`/`f64` 为 `int`/`float` 的别名）、`i`/`u` 后接数字的其余名字在类型位置保留，外部声明的 `from "库"`／`from syscall 编号` 子句与 `declare type`／`declare struct`（按标识符形态识别，不新增关键字），外部签名里的上下文类型名 `cstr`/`wstr`，预声明标识符增 `wrapTo` `handleBits` `handleFromBits` `ffiLastError` `floatToBits` `floatFromBits`；§4.1 数值行改写，增“定宽数值”“句柄”“外部结构体”三行；§4.2-1 无损扩宽；§5 EBNF `extern_decl` 增 `from` 子句、增 `extern_type_decl`；§6.1-4 按目标选择文件（新条）；§6.5-3 注记；§6.8 增 v0.23 FFI（第 7–20 条）；§6.21 定宽数值（新节）；§7 内建表增六项、`toStr` 扩定宽整数；§8 O4／O1 注记与 v0.23 排除增补。**实现面只在太虚自举编译器与 NRT 运行时**（Zig seed 冻结于 v0.17：SEB1 的定宽类型 21 与句柄类型 22 使冻结的加载器拒绝载入，含 FFI 的 `.txc` 写格式版本 5、按 BadVersion 拒绝）。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。
**v0.22 修订历史：** 2026-10-15，去 Zig 第 4b 轮生成器（LANG-26——`docs/design/v052-generators-v0.1-20261015.md`；用户 2026-10-15：九问按推荐，并要求评估对整体语言的影响，见该文 §十一）：§3 `function*` 生成器声明与生成器体内的关键字 `yield`、预声明标识符增 `Gen` 与 `genNext`；§4.1 增“生成器”行；§4.2-11 Gen 句柄语义（新条）；§5 EBNF `function_decl` 增 `function*` 形态、`base` 增 `Gen<T>`、`stmt` 增 `yield_stmt`；§6.5-3 Gen 不可比较；§6.10-2 可迭代面增 `Gen<T>`（提前离开循环即关闭）；§6.20 生成器（新节）；§7 内建表增 `genNext`；§8 v0.22 排除增补（异步生成器、`yield*`、`next(v)` 等）。**实现面只在太虚自举编译器与 NRT 运行时**（Zig seed 冻结于 v0.17，遇生成器拒绝载入）。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.21 修订历史：** 2026-10-14，去 Zig 第 4 轮原生异步、原生 run/test 与太虚版 fmt（LANG-25／TOOL-81／TOOL-82——`docs/design/v052-native-run-test-async-v0.1-20261014.md`；用户 2026-10-14 裁决：十问全部按推荐）：§3 预声明标识符增七个工具链系统内建；§4.1 Future 行注记；§6.12-6 取消时执行已登记的 `defer`、§6.12-8 只剩定时器时等待到期（死锁判据随之收窄）、§6.12-9 注记、§6.12-10 原生实现（新条）；§6.16-6 解除“async 函数内不支持 defer”、新增“defer 体内不得 await”（3026），§6.16-8 async 函数中的 defer（新条）；§6.17-10 措辞；§7 内建表增 `hostTarget` `getEnv` `runProcess` `tempDir` `removeFile` `selfExePath` `monoNs`（工具链专用），`test` 块注记原生每个 test 一个进程。**实现面只在太虚自举编译器与 NRT 运行时**；Zig VM 只做两处缺陷修复（只剩定时器时等待到期、嵌套帧中未决 await 的报错），其余冻结于 v0.17。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.20 修订历史：** 2026-10-14，去 Zig 第 3 轮接口、泛型约束与泛型 Map/Set（LANG-22／LANG-23／LANG-24——`docs/design/v052-interfaces-generics-v0.1-20261014.md`；用户 2026-10-14 评审：设计无异议，约束检查适当加强为调用点＋定义处，其余九问按推荐）：§3 关键字增 `interface`（23 → 24）、`extends` 扩到类型参数、预声明标识符增 prelude 导出名；§4.1 增“接口”“Map/Set”两行，List 行元素面扩值类型；§4.2-2 注记；§5 EBNF 增 `interface_decl`、类型参数约束；§6.9-5 List 元素放开到 struct/enum/定长数组（值语义）；§6.10-1 方法语法补泛型实例与接口值注记，§6.10-2 可迭代面增泛型 Map 与 Set；§6.18 接口与泛型约束（新节）；§6.19 编译器自带 prelude：Map 与 Set（新节）；§8 摘除 `interface`、Map/Set 注记更新，“虚分派永久排除”改为“类、继承、`this`、重载仍排除；接口值的动态分派入 S1”。**实现面只在太虚自举编译器与 NRT 运行时**：Zig seed（VM、Zig 前端、AOT）冻结于 v0.17，用例放 `tests/nrt-cases/`。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.19 修订历史：** 2026-10-14，去 Zig 第 2 轮 `defer` 与结构化错误（LANG-20／LANG-21——`docs/design/v052-defer-errors-v0.1-20261014.md`；用户 2026-10-14 评审：`defer` 块作用域且循环体内逐轮执行，其余九问按推荐裁决）：§3 关键字增 `defer`（22 → 23）、上下文关键字增 `extends`、预声明标识符增 `errAs` `errWrap`；§4.1 错误与失败联合行修订、增“错误 enum”行；§4.2-8 `?` 放宽规则；§5 EBNF 增 `defer_stmt`、`enum_decl` 的 `extends Error` 与变体消息；§6.3-6 指向新节；§6.16 `defer`（新节）；§6.17 结构化错误（新节）；§7 入口允许 `int | Error`、内建表增 `errAs`/`errWrap`、`toStr` 扩错误 enum。**实现面只在太虚自举编译器与 NRT 运行时**：Zig seed（VM、Zig 前端、AOT）冻结于 v0.17，用例放 `tests/nrt-cases/`。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.18 修订历史：** 2026-10-13，V0.52 Bytes 类型与编译期嵌入（LANG-19——`docs/design/v052-bytes-embed-stage0-v0.1-20261013.md`；用户 2026-10-13 批准，六个开放问题按推荐裁决）：§4.1 类型清单增 `Bytes`；§6.15 Bytes 与 `embedBytes`（新节）；§7 内建表增 12 项、`len`／`writeFileBytes`／`writeExecutable` 扩定义域；BuiltinId 37..47 尾段追加（`embedBytes` 不占 BuiltinId，编译期降为字符串常量＋`bytesFromStr`）；TXC TyKind 18；值 tag 17。**实现面只在太虚自举编译器与 NRT 运行时**：Zig seed（VM、Zig 前端、AOT）冻结于 v0.17，`taixu run`/`taixu test`（Zig CLI）不支持本版新增面，用例放 `tests/nrt-cases/`。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.17 修订历史：** 2026-10-13，V0.52 目录创建与工具链可执行写出内建（LANG-18——`docs/design/v052-toolchain-builtins-v0.1-20261013.md`；用户 2026-10-13 裁决：普通程序不能写出可执行文件，只有编译器产物可以；目录创建采用单层内建＋标准库递归）：§7 内建表增 `makeDir`、`writeExecutable` 两行＋§3 预声明标识符尾段追加；BuiltinId 35/36 尾段追加；`writeExecutable` 为**工具链专用内建**——语义检查默认以 3001 拒绝，仅编译选项 `--toolchain` 放行。四后端同臂（interp／AOT shim／NRT Windows·Linux／自举 semamirror·codegen·emit）；verifyProgram callbi id 上界随尾位；SEIN 头部开关开启时为 v2（关闭时字节不变）；semamirror.tx 镜像同步＋embed 重烘。**其余章节零改动**。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.16 修订历史：** 2026-10-03，V0.50-S5-① 批 A 二进制字节写出内建（LANG-17——P-1 小合同 `docs/build/selfhost-delimitation-v050.md` §三；独立审计通过）：§7 内建表增 `writeFileBytes` 一行＋§3 预声明标识符尾段追加；BuiltinId 34 尾段追加（追加只能尾加纪律）；interp/shim 双后端同臂（元素 int 0..255，越界/非 int 结构化 Error 无部分写——tx_file_write_all 宿主面）；verifyProgram callbi id 上界随尾位；EMITMIRROR 记录构造通用路面级扩面（通用路 id 无关——零新发射形态，恒等义务链不触）；semamirror.tx 镜像同步＋embed 重烘。**其余章节零改动**。`--frontend` 双态 .txc 恒等（含新内建程序亲证）；无该内建程序零扰动（五门随批）。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.15 修订历史：** 2026-09-24，V0.41-M0-② 字符字面量（LANG-16——实测反馈 #15 `byteAt(s,i)==97` 可读性之痛；本关语言面唯一件，先于 R0-a parser 批落位）所需的最小语言面增量：§3 词法增字符字面量（单引号 'a'；kindId 72 尾段追加——与 taixu lex 的表逐名一致；单引号由非法字符转为字符字面量引入符＝零冲突加法）、§5 EBNF primary 增 CHAR_LIT、§6.14 字符字面量语义（新节：int 码点/转义集六件/结构化拒绝四形〔1005 bad_char_lit〕/解析期 int 字面量节点解糖＝sema/codegen/AOT 零新面）、§10 修订记录 26；**其余章节零改动**。金哈希 `69b4e54b` 恒等（无字面量程序零扰动亲证——解析期解糖不触 bytecode 面）。同步链 llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/` 随走。

**v0.14 修订历史：** 2026-09-23，V0.39-G0 位运算批＋重写清偿（LANG-15——弧 G1-b 承接；V0.38 范围缺口追加首位）所需的语言面增量：§3 运算符集增 `& ^ ~` 三 token（词法器变更加尾段 kindId 68/69/71——70＝comment 既有占用；`&` 与 `&&` 最大匹配不冲突；`|` 复用既有 token）、§3 注记位移词法零变更（`<< >> >>>` 解析器零空白邻接组合）、§5 EBNF 表达式链插位运算五级＋一元 `~`（优先级 TS 同构）、§5 语法注记 6（邻接组合与泛型实参互操作串联定序）、§6.13 位运算语义（新节：int 专属/移位 0..63 界外 shift_count trap〔RuntimeCode 4016〕/sar 算术·shr 逻辑右移分立/imul 64 位环绕）、§7 内建表增 `imul`（BuiltinId 31 尾段追加＝旧产物不含先例第 10 批；**非宿主调用**——codegen 直发 IMUL opcode，per-char 哈希内环不走宿主表分发）、§10 修订记录 25；**其余章节零改动**。金哈希 `7314fe47` 恒等（无位运算程序零扰动——opcode 追加编码值＝旧产物不含）。同步链 llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/` 随走。

**v0.11 修订历史：** 2026-09-16，V0.22 语言核心续批（LANG-12，D57 续行——错误链 DX）所需的最小语言面增量：§7 内建表 `toStr` 面扩 `Error`（BuiltinId 2 `err` 产物值可读化——`toStr(e)` ⇒ `"Error: " + e.message` 逐字节同源语义，interp 与 AOT shim 双实现；插值段经 toStr 解糖同享，§6.10-3 段类型面随扩）；**其余章节零改动**。金哈希 `3873a6a8` 恒等（零新内建零新 opcode——toStr 既有 BuiltinId 3 的实参面扩一类型，旧程序零扰动）。§10 修订记录 23。

**v0.10 修订历史：** 2026-09-16，V0.21 语言核心回补批（LANG-11，D57 §一——G1 实战 feedback 驱动的 DX 导向增量）所需的最小语言面增量：§3 预声明标识符增 `listRemoveAt`/`listInsert`/`strSub`/`strIndexOf`/`strSplit` 五内建、§7 内建表增五件（BuiltinId 26–30——"追加编码值，旧产物不含"先例第 8 批；零 .txc 格式变更，金哈希 `3873a6a8` 恒等——无新内建程序零扰动）、§10 修订记录 22；**其余章节零改动**。新增面语义：`listRemoveAt`（索引删除，越界 trap，被删元素 RELEASE——所有权移交后无人接管的 void 语义）；`listInsert`（插入，`i==len` 即尾加，越界 trap，元素 RETAIN）；`strSub`/`strIndexOf`/`strSplit`（**码元面**——按 Unicode 码点计 units 非字节，CJK 安全；`strSub` 越界 trap、`strIndexOf` 缺席 `-1`/空针 `0`、`strSplit` 空分隔符 trap＋连续分隔符产空片）；错误读取面＝既有 `e.message` 字段访问（D3 §4.1 内建结构 `{ message: string }`——本批复核确认并教材化，不增设冗余内建）。诊断面增补：`or`/`and` 误写就近诊断＋恢复解析（G1 feedback #4）；结构化诊断随附 `taixu explain <code>` 对照行。

**v0.9 修订历史：** 2026-09-10，V0.10 异步深化（RT-08，D29 §一–三）落地所需的最小语言面增量：§3 预声明标识符增 `sleepMs`、§6.12 异步与任务运行时扩 8（定时器面）/9（库层超时与选择通道——timeout 单态注记：S1 无用户泛型函数，泛型维持排除）、§7 内建表增 `sleepMs`（BuiltinId 25——"追加编码值，旧产物不含"先例第 7 批）、§10 修订记录 21；**其余章节零改动**。金哈希 `806dd58c` 不变（无 sleepMs 程序零扰动）；AOT 拒绝门覆盖不变（sleepMs 随异步面结构化拒绝，登记文本提及 BuiltinId 25——D29 §一-4）。
**v0.8 修订历史：** 2026-09-10，V0.9 异步语言面（LANG-10＋RT-07 运行时段，D26 §一–三/§五）落地所需的语言面增量：§3 上下文关键字增 `async`/`await`、预声明标识符增 `Future` 与三任务内建、§4.1 类型清单增 `Future<T>`（TyKind.future=17）、§4.2 类型规则增 10（Future 句柄语义）、§5 EBNF 增 `async` 前缀与 `await_expr`（语法注记 5 await 上下文消歧）、§6.5-3 增 Future 不可比较、§6.12 异步与任务运行时（新节）、§7 内建表增 `taskCancel`/`taskCancelled`/`taskAwaitAny`、§7 List 元素面注记（扩 Future，D26 §三-5）、§8 排除增补、§9 示例、§10 修订记录 20；**其余章节零改动**。金哈希 `806dd58c` 不变（无异步程序零扰动）；AOT 对异步面结构化拒绝（Op 38/39 兼容报告，D26 §三）。
**v0.7 修订历史：** 2026-09-10，V0.9 闭包语言面（LANG-09，D25 §一–五）落地所需的语言面增量：§4.1 类型清单增函数类型 `(T, …) => R`（TyKind.fn=16＋TXFNT 尾节；零新 opcode）、§4.2 类型规则增 9（函数值句柄语义）、§5 EBNF 类型原子增 `fn_type`、primary 增 `arrow_fn`（语法注记 4 箭头头消歧）、§6.5-3 增函数值不可比较、§6.11 闭包与函数类型（新节）、§7 List 元素面注记（扩函数类型，D25 §3.1）、§8 排除清单修订、§9 示例、§10 修订记录 19；**其余章节零改动**。金哈希 `806dd58c` 不变（无闭包程序零扰动）。
**v0.6 修订历史：** 2026-09-10，V0.8 DX 批次（LANG-08）落地所需的最小语言面增量：§3 上下文关键字增 `in`、词法增模板串字面量、§5 EBNF 增 `for_in_stmt` 与 `TEMPLATE_LIT`、语法注记 2 修订（方法语法解糖入后缀链定夺）、§6.2-5 修订（方法语法指路）、§6.3-3 增发散默认值规则、§6.10 语法糖三件语义（新节：方法语法/for-in/插值——解糖为既有指令面，零 .txc 格式变更，金哈希 `806dd58c` 恒等）、§7 内建表 `toStr` 面扩 `string`、§8 排除注记、§9 示例、§10 修订记录 18；**其余章节零改动**。
**v0.5 修订历史：** 2026-09-09，V0.7 stdlib 主线（LANG-07）落地所需的最小语言面增量：§3 预声明标识符增 `List` 与六内建、§4.1 类型清单增 `List<T>`、§5 EBNF 类型原子增 List、§6.5-3 相等语义注记、§6.9 List 容器语义（新节）、§7 内建函数表增六件、§8 排除注记、§9 示例、§10 修订记录 17；**其余章节零改动**。
**v0.4 修订历史：** 2026-09-09，V0.6 GUI 语言绑定（GUI-07）落地所需的最小语言面增量：§3 预声明无变化、§5 EBNF 增 `extern_decl`、§6.8 外部函数与宿主封送（新节）、§8 排除注记、§10 修订记录 16；**其余章节零改动**。
**v0.1 修订历史：** 同日自评审（B 方案失败通道 + 四项缺陷修复）→ 用户采纳"TS 优先修订包" → 用户确认 `Ref<T>` 方案、enum 限定名构造 → 强制分号确认 → 编写全景示例补入四处缺口 → 导入三级规则与 Map/Set 推迟 → 用户评审通过（"其它 D3 内容没有问题"）。两项问询（int 单一 64 位默认、`catch None` 桥接）经澄清无需规范变更，澄清记录见 §10
**v0.2 修订历史：** 2026-09-04，V0.2 范围确认（内存模型落地）后按 D6 §5 定案引入弱引用：§3 预声明标识符、§4.1 类型清单、§5 EBNF、§6.7 弱引用语义、§7 内建函数表、§9 示例；**其余章节零改动**。评审通过后须走同步链（llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`，DOC-03/04/05）
**覆盖需求：** LANG-01 至 LANG-05、LANG-07（List<T> 容器内建，v0.5）、LANG-08（语法糖三件，v0.6）、TEST-02、MEM-03（Weak<T> 语言面）；同时为 D4（字节码）与 D6（对象模型）提供语义输入
**依据：** SRS 评审稿 §4.1–4.3、§4.5、§5.4；D6 v0.1 §5（弱引用规则定稿，2026-09-04 评审通过）；用户确认的 TS 风格语法决策

## 1. S1 的定位

S1 是 Stage 1 自举子集：V0.1 编译器实现的语法白名单（即 LANG-01 要求的正式版），须同时满足两个目标：

1. **PoC 目标**：V0.1 能编译运行 Hello World，具备 LANG-05 五项能力（模块导入、函数、结构体、显式错误返回、基础字符串）；
2. **自举目标**：V0.3 用 S1 能写出词法分析器、语法分析器、语义检查器与字节码生成器（SRS §4.5 的闭环要求）。

冻结管理：本文档即白名单正式版；任何扩展 = 修订本文档（评审）+ TEST-02 守护用例同步。S1 ⊂ SRS §4.5"第一阶段"：闭包、泛型等第一阶段特性排至 S2（见 §8），与需求清单 V0.1 范围一致。

## 2. 设计总则

1. **TS 皮、自定语义，TS 优先**：**TS 有对应物的语法，必用 TS 形式；TS 没有的（错误传播、模式匹配等接缝处），发明时用 TS 词汇拼**。语义上不继承任何 JS 包袱（§8 永久排除清单）；
2. **两个失败类型、一套处理机制**（B 方案）：**可选类型 `T?`（缺失）与错误联合 `T | Error`（故障）是两个独立的内建联合类型**；`None` 是可选类型的缺席值，不是 Error。`?` 传播与 `catch` 处理对两者统一生效，但**不隐式互转**。TS 的 `?.` 与 `??` 是这条路的空值先例，我们把它推广到 Error（选择理由 §2.1）；
3. **值语义优先，引用显式拼写**：struct 是值类型；堆对象显式 `new`，返回 `Ref<T>`（RC 管理）。**TS 心智映射**：struct 值 ≈ TS 原始类型（赋值拷贝、按值比较）；`Ref<T>` ≈ TS object（共享、字段可变、按同一性比较）；
4. **无隐藏控制流**：无异常、无运算符重载、无隐式类型转换、无真值转换。

### 2.1 为什么选 B（分离类型 + 统一机制）而不合并

- **语义清晰**：`User?`（查无此人，正常业务）与 `string | Error`（IO 故障，异常路径）在签名上即区分，调用方与审查者无需读实现；
- **与现代语言生态一致**：Rust（Option/Result）、Swift（Optional/throws）、Go（nil/error）、TS（null/异常）乃至 Zig（`?T` / `E!T`）全部是分离模型，无主流语言合并二者；
- **对 AI 生成与修复友好**：大模型训练语料里"缺失与故障分离"占压倒性多数，生成正确率高；且签名即意图，编译器可检查 AI 代码是否用错通道——**语言层面每个语义决策显式化、可机检，是 AI 原生的核心原则**；
- **合并方案的真实收益由统一机制保留**：`?` 与 `catch` 一套语法通吃两类联合（Rust 的 `?` 同时作用于 Option 与 Result 是先例）；
- **对齐外部数据模型**：JSON / SQL / HTTP 中 null 与 error 本就是两个概念，结构化数据双向直通不需要额外映射约定；
- **消除 `None.message` 类语义空洞**：`catch (e)` 绑定的 `e` 恒为真 Error，必有 message。

## 3. 词法结构

- **源文件**：UTF-8 编码，扩展名 `.tx`；
- **注释**：`//` 行注释；`/* */` 块注释（**不嵌套**）；
- **标识符**：`[A-Za-z_][A-Za-z0-9_]*`，区分大小写；
- **关键字（S1 全集，v0.19 增 `defer` 为 23 个；v0.20 增 `interface` 为 24 个）**：`export function struct enum let const if else while for break continue return switch case test import true false new catch as defer interface`；**上下文关键字（v0.4 增 `declare`；v0.6 增 `in`；v0.8 增 `async`/`await`；v0.19 增 `extends`——`enum 名` 之后识别，§6.17；v0.20 起也在类型参数名之后识别为约束引入符，§6.18-6；v0.22 增 `yield`）**：`declare`——仅顶层声明位且后随 `function` 时识别为外部函数声明引入符（§6.8），其余位置恒为普通标识符；`in`——仅 `for (IDENT [, IDENT] in expr)` 形态位识别为 for-in 迭代引入符（§6.10-2），其余位置恒为普通标识符；`async`——仅顶层声明位且后随 `function`（§6.12-1）或表达式位且后随箭头头形态（§6.12-2）时识别，其余位置恒为普通标识符；`await`——仅一元表达式位且后随表达式起始记号时识别为前缀 await（§6.12-4），其余位置恒为普通标识符（词法零改动，自举词法表零同步）；`yield`（v0.22）——生成器函数（`function*`，§6.20）体内（含其中的箭头函数）恒为关键字、不能作标识符，其余位置恒为普通标识符；`function` 后紧跟的 `*` 是生成器声明标记（v0.22；词法零改动：`*` 复用乘号记号，`yield` 由解析器按所在函数识别）；
- **预声明标识符（非关键字）**：`Ref`、`Weak`、`List`（v0.5 增；内建泛型类型）、`Future`（v0.8 增；内建泛型类型，§6.12）、`Error`（类型）、`None`（缺席值）、`err` `print` `readFile` `len` `toStr` `unref` `weak` `deref` `byteAt` `utf8Encode` `listNew` `listPush` `listPop` `listGet` `listSet` `listLen`（v0.5 增六件容器内建） `assert` `panic` `taskCancel` `taskCancelled` `taskAwaitAny`（v0.8 增三件任务内建，见 §7） `sleepMs`（v0.9 增定时器内建，见 §7） `listRemoveAt` `listInsert` `strSub` `strIndexOf` `strSplit`（v0.10 增五件回补内建，见 §7） `imul`（v0.14 增环绕乘内建，见 §7） `writeFileBytes`（v0.16 增二进制字节写出内建，见 §7） `makeDir` `writeExecutable`（v0.17 增目录创建与工具链可执行写出内建，见 §7；后者仅 `--toolchain` 构建可用） `errAs` `errWrap`（v0.19 增结构化错误内建，见 §6.17 与 §7）；v0.20 增编译器自带 prelude 的导出名 `Hashable` `Map` `Set` `mapNew` `mapSet` `mapGet` `mapHas` `mapRemove` `mapLen` `mapKeys` `mapValues` `mapClear` `setNew` `setAdd` `setHas` `setRemove` `setLen` `setToList` `setClear` `intHash` `stringHash` `boolHash` `bytesHash`（§6.19；用户声明与显式导入优先）；v0.21 增工具链系统内建 `hostTarget` `getEnv` `runProcess` `tempDir` `removeFile` `selfExePath` `monoNs`（§7；仅 `--toolchain` 构建可用；v0.42 增 `setEnv`）；v0.22 增 `Gen`（内建泛型句柄类型，§6.20；当前模块有同名类型时以用户类型为准）与 `genNext`（§7）；v0.27 增工具链进程与标准输入内建 `stdinRead` `stdoutWrite` `procSpawn` `procWrite` `procRead` `ioWait` `procKill` `procWait` `procClose`（§7；仅 `--toolchain` 构建可用）；v0.28 增工具链内建 `currentDir`（§7；仅 `--toolchain` 构建可用）；v0.29 增工具链内建 `fileSize`（§7；仅 `--toolchain` 构建可用）。预声明标识符可被用户声明遮蔽（同 TS 全局名政策）； v0.32 工具链专用：`procSpawnLimited` `readLocalFileBytes` `localEntryKind` `localFileSize`；v0.33 增 `procTryWrite`；v0.35 增 `hostArch`、`localListDir`。
- **定宽数值类型名与外部声明用词（v0.23）**：预声明类型名 `i8 i16 i32 u8 u16 u32 u64 isize usize f32`，`i64`/`f64` 是 `int`/`float` 的别名；它们在类型位置优先于用户声明，`i`/`u` 后接数字的其余名字（`i3`、`u128`…）在类型位置保留（§6.21-1）。外部函数声明的 `from "库"`／`from syscall 编号` 子句与 `declare type`／`declare struct` 按标识符形态识别（同 `import … from`；`type`、`syscall` 不是关键字）；`wstr` 只在带 `from` 的外部函数签名顶层识别为类型名（§6.8-12；v0.36 起原 `cstr` 并入 `string`，写 `cstr` 报 3033）。预声明标识符增 `wrapTo`（§6.21-6）、`handleBits` `handleFromBits` `ffiLastError`（§6.8）、`floatToBits` `floatFromBits`（§7）；
- **线程（v0.25）**：预声明类型名 `Thread`、`Shared`、`Atomic`（内建泛型句柄类型，§6.22；当前模块有同名类型时以用户类型为准）；预声明标识符增 `threadSpawn` `threadJoin` `sharedNew` `sharedGet` `atomicNew` `atomicLoad` `atomicStore` `atomicAdd` `atomicCas`（§6.22、§7）；
- **补记（v0.30）**：预声明标识符 `eprint`、`listDir`（V0.45 起）与 `entryKind`（V0.47 起）一直漏列，补上（§7）；
- **定宽数值第二步（v0.26）**：预声明标识符增 `wrapAdd` `wrapSub` `wrapMul`（§6.21-21）、`bytesAsList` `listAsBytes`（§6.15-7）；prelude 导出名增 `i8Hash` `i16Hash` `i32Hash` `u8Hash` `u16Hash` `u32Hash` `u64Hash` `isizeHash` `usizeHash`（§6.19-2）；
- **`Bytes` 批量操作（v0.37）**：预声明标识符增 `bytesCopy` `bytesFill` `bytesGetLE` `bytesGetBE` `bytesSetLE` `bytesSetBE`（§6.15-8）；
- **整数字面量**：十进制、`0x`/`0o`/`0b` 前缀，`_` 作分隔（`1_000_000`）；类型默认 `int`；v0.23 起有定宽期望类型时取该类型（§6.21-3）；v0.26 起二元运算一侧为字面量时取另一侧的类型（§6.21-19），期望型为 `u64`/`usize` 时可到 2^64−1（§6.21-20）；
- **浮点字面量**：含小数点或指数（`1.5`、`1e3`、`3.14e-2`）；类型 `float`；
- **字符串字面量**：双引号；转义支持 `\n \t \r \\ \" \0 \u{XXXX}`；相邻字符串字面量**不**自动拼接；
- **字符字面量（v0.15 增）**：单引号包裹单字节字符（'a'＝int 码点 97）；转义集 \n \t \r \\ \' \0（\u{...} 为评估位——结构化拒绝）；空/多字符/多字节/未知转义/未闭合带位置诊断；kindId 72（尾段追加）；语义见 §6.14；
- **模板串字面量（v0.6 增）**：反引号包裹（`` `a=${expr} b` ``）；`${`…`}` 为插值段（嵌套深度 1 冻结：段内不支持模板串、不支持未配对的 `)`/`]` 越出段界）；转义集＝字符串转义 ∪ `` \` ``（反引号）与 `\$`（字面 `$`）；裸 `$` 非段首时为字面；段内字面部分禁止裸换行（未闭合诊断）。词法只定界（模板串为单一记号），段切分与解糖在解析期完成（§6.10-3）；
- **运算符与标点**：`+ - * / % == != < <= > >= && || ! ? = => . , ; : ( ) { } [ ] |`；v0.14 增位运算 `& ^ ~` 三 token（单字符、词法无歧义——`&` 与 `&&` 最大匹配不冲突：S1 无一元 `&`；`|` 复用既有 token，类型联合/按位或按文法位置分派）；**位移 `<< >> >>>` 词法器零变更**——`<` `>` 分离发射不动，组合上移解析器（§5 注记 6）：嵌套 `Ref<Ref<T>>` 词法无碍的既有性质原样保留（类型位逐个消费 `>` 永不组合）。

## 4. 类型系统

### 4.1 类型清单（S1）

| 类别 | 类型 | 说明 |
|---|---|---|
| 数值 | `int`、`float` | `int` 为 64 位有符号；`float` 为 f64；字面量与未标注推断的默认数值类型。v0.23 起 `i64`、`f64` 是它们的别名 |
| 定宽数值（v0.23） | `i8 i16 i32 u8 u16 u32 u64 isize usize f32` | 一般类型：可声明变量、作字段、`List` 元素、泛型实参与外部函数签名；赋值类位置无损扩宽，收窄须 `as`（越界 trap 4018）或 `wrapTo<T>`；v0.23 只有类型、字面量、转换、比较与 `toStr`（整数），**v0.26 起**算术、位运算与移位按公共类型计算，整数溢出 trap 4004（环绕须显式写 `wrapAdd`/`wrapSub`/`wrapMul`），`f32` 运算逐步舍入到 f32，`toStr(f32)` 可用；`isize`/`usize` 宽度为目标指针宽度；运行期复用 `int`/`float` 的表示（`List` 的窄元素紧凑存放），语义见 §6.21。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 布尔 | `bool` | 条件表达式只接受 `bool` |
| 字符串 | `string` | 不可变 UTF-8 字节串，值语义（D6 定实现策略） |
| 空 | `void` | 仅作函数返回类型 |
| 错误 | `Error` | 内建结构：`{ message: string }`；作为失败联合的失败侧出现，也可作普通值存储与传递；v0.19 起另有只读的 `cause: Error?`（§6.17-5） |
| 可选 | `T?` | `T` 或缺席 `None`；表达"预期内的缺失" |
| 失败联合 | `T \| Error`、`T \| E` | 表达"操作可能故障"；v0.19 起失败侧可以是错误 enum `E`（§6.17-2） |
| 结构 | `struct` 声明 | 名义类型，值语义（≈ TS 原始类型的行为） |
| 变体 | `enum` 声明 | 名义和类型（tagged union），变体可带字段（≈ TS 判别联合的语义等价物） |
| 错误 enum（v0.19） | `enum E extends Error` 声明 | 每个变体带消息的 enum：值同时是 `Error`（可流入 `Error` 位置），按值比较、`switch` 须穷尽；不能用 `new` 构造；语义见 §6.17。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 序列 | `[]T`、`[N]T` | 切片（长度 + 指针）、定宽数组 |
| 堆引用 | `Ref<T>` | RC 管理的安全堆句柄（`new` 产生，≈ TS object 的行为）；不支持指针算术；**FFI 裸指针（V0.2 O1）将用独立拼写（预留 `Ptr<T>`），不与 `Ref` 混用** |
| 弱引用（v0.2） | `Weak<T>` | 不拥有目标的观察句柄（`weak()` 产生）：不延长目标生命周期、`deref` 目标已死得 `None`；`T` 为被指向类型（实际限 struct/enum，由 `weak()` 的参数类型约束）；语义详见 §6.7 与 D6 §5 |
| 容器（v0.5） | `List<T>` | 预声明泛型堆容器（RC 堆对象，D20 §二）：**句柄语义**（赋值/传参共享底层存储，对齐 `[]T`）；元素类型冻结面＝`int/float/bool/string/Ref<T>/Weak<T>/List`（嵌套）——**v0.20 起元素面放开到 struct/enum/定长数组与接口值（值类型按值语义存取，§6.9-5；仅太虚编译器与 NRT）**；**v0.7 注记：元素面扩函数类型（D25 §3.1）；v0.8 注记：元素面扩 `Future<T>`（D26 §三-5 `taskAwaitAny` 形参面）**；**v0.26 注记：元素为 8/16/32 位整数或 `f32` 时按元素宽度紧凑存放（实现说明，语义不变，§6.21-22）；`List<u8>` 与 `Bytes` 可零拷贝互转（§6.15-7）**；操作仅经 §7 六内建（`listNew/listPush/listPop/listGet/listSet/listLen`），无方法、无索引语法；语义详见 §6.9 |
| 函数（v0.7） | `(T, …) => R` | 函数类型（D25 §二）：**句柄语义**（赋值/传参零 clone）；不可 `==`（§6.5-3）；struct 字段/List 元素可承载（GUI 回调主用例）；不入 extern 面（§6.8 类型面冻结不变）；字节码 `TyKind.fn=16`＋TXFNT 可选尾节——**零新 opcode**（既有产物零扰动）；语义详见 §6.11 |
| 字节（v0.18） | `Bytes` | 可变、可增长的字节序列（元素 0..255，紧凑存储）：**句柄语义**（同 `List<T>`：赋值/传参/存字段共享底层存储；`==` 比较句柄同一性，内容比较用 `bytesEqual`）；可作 struct 字段与 `List` 元素；不入 extern 面；操作经 §7 `bytesXxx` 内建、方法语法（`b.push(x)` 解为 `bytesPush(b, x)`）与 `len`；无字面量，编译期嵌入见 §6.15。**v0.26**：与 `List<u8>` 是两个类型、运行期是同一种对象形态，`bytesAsList`／`listAsBytes` 零拷贝互转（返回同一个对象，§6.15-7）。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 句柄（v0.23） | `declare type H;` 声明 | 指针宽度的名义不透明句柄（§6.8-10）：只来自外部函数的返回值、输出参数或 `handleFromBits<H>`；能传回外部函数、同一句柄类型之间 `==`/`!=`，不能算术、不能 `as`、不能与 `int` 隐式转换（`handleBits` 取数值）；`H?` 可空（空指针为 `None`）；可作 `List` 元素；可 `export`。**仅太虚编译器与 NRT 实现** |
| 外部结构体（v0.23） | `declare struct S { … }` 声明 | C 布局的值类型 struct（§6.8-11）：字段只能是定宽数值、`int`/`float`、句柄、定长数组 `[N]标量`、其他外部结构体；太虚侧读写同普通 struct；只以 `Ref<S>` 传给外部函数（调用前拷入、调用后拷回）；可 `export`。**仅太虚编译器与 NRT 实现** |
| 接口（v0.20） | `interface` 声明 | 只含方法签名的类型：结构化实现（不写 `implements`），可作泛型约束（`T extends I`），也可作值的类型——接口值是共享句柄、动态分派；语义见 §6.18。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 映射与集合（v0.20） | `Map<K, V>`、`Set<T>` | 编译器自带 prelude 中的泛型类型（§6.19）：键须实现 `Hashable`，插入序迭代，句柄语义（同 `List`） |
| Future（v0.8） | `Future<T>` | 异步句柄（D26 §二）：`async` 函数调用的值形态（调用即 spawn，§6.12-3）；**句柄语义**（赋值/传参零 clone）；不可 `==`（§6.5-3）；不可嵌套 `Future<Future<T>>`；`await` 的操作数类型（§6.12-4）；字节码 `TyKind.future=17`（追加编码值先例，旧产物不含）；v0.21 起太虚编译器与 NRT 原生实现（§6.12-10） |
| 生成器（v0.22） | `Gen<T>` | 惰性产出 `T` 值的句柄（§6.20）：生成器函数（`function*`）调用的结果；**句柄语义**（赋值/传参共享同一个生成器与进度）；可存进变量、struct 字段与 `List`，可作参数与返回值；不可 `==`（§6.5-3）；`T` 不能是 `void`、可选、失败联合、`Error` 与错误 enum（§6.20-2）；字节码 `TyKind` 19（追加值）。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 线程（v0.25） | `Thread<T>` | `threadSpawn` 返回的线程句柄（§6.22-1）：`threadJoin` 取回结果 `T`；**句柄语义**；不可 `==`、不可发送（只能由建它的线程 join）；放掉句柄不影响线程运行。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）** |
| 共享（v0.25） | `Shared<T>` | 跨线程共享的只读对象图（§6.22-7）：`sharedNew` 深拷贝进共享区，`sharedGet` 取出冻结的对象图；**句柄语义**；可发送（发送的是句柄，不拷贝内容）；不可 `==`。**仅太虚编译器与 NRT 实现** |
| 原子（v0.25） | `Atomic<int>` | 原子整数（§6.22-10）：五个内建操作，内存序 seqcst；**句柄语义**；不冻结；只能经 `Shared` 跨线程（直接发送报 3035）；不可 `==`；`Atomic<T>` 目前只有 `T = int`。**仅太虚编译器与 NRT 实现** |

### 4.2 类型规则

1. **无隐式数值转换**：`int` ↔ `float` 需显式 `as`；**v0.23**：定宽数值在赋值类位置按无损方向自动扩宽（§6.21-4），收窄与整数↔浮点仍须 `as`；整型字面量在期望 `int` 处取 `int`，浮点字面量在期望 `float` 处取 `float`，无期望时分别取默认类型；**v0.26**：二元运算一侧为数值字面量时，字面量取另一侧的类型、左右对称（§6.21-19），`1 + x`（`x: float`）因此合法；
2. 名义类型之间互不相容；struct 无子类型。**v0.20**：接口（§6.18）按结构化实现关系检查，值装入接口值是显式期望类型位置上的转换，不是子类型；接口之间不转换；
3. `let` 为可变绑定、`const` 为不可变绑定（不可变性对值类型**深层传递至字段**）；函数参数绑定不可变。**唯一例外是 `Ref<T>`：`const` 只约束绑定不可重绑，其堆对象字段仍可变**——对齐 TS object 心智（`const` 绑定 ≠ 对象冻结）；`Weak<T>` 绑定同此例外不适用——`Weak` 是句柄且不可解引用写目标，`const`/`let` 仅区别可否重绑；
4. **switch 穷尽性**：对 enum 的 switch 必须覆盖全部变体或含 `else =>` 臂；
5. **返回路径检查**：声明非 `void` 返回类型的函数，所有控制流路径必须 `return` 或经 `?` 传播离开；
6. 局部绑定可省略类型标注，但类型必须可静态确定（推断失败即编译错误，不做最右猜测）；
7. **失败值不得丢弃**（LANG-04）：类型为 `T?` 或 `T | Error` 的表达式作为表达式语句被丢弃，是编译错误——必须 `?` 传播、`catch` 处理、绑定或传参；
8. **失败通道不混用**：`?` 传播要求外层函数返回类型的联合成员与被传播值的失败侧一致（`None` 侧对应 `U?`，`Error` 侧对应 `U | Error`）；跨通道需用 `catch` 显式转换（§6.3-4）。**v0.19**：失败侧为错误 enum `E` 时，外层失败侧须同为 `E`，或为 `Error`（自动放宽，具体种类保留，可用 `errAs` 取回）；其他组合是编译错误（§6.17-3）；
9. **函数值句柄语义（v0.7）**：函数类型值赋值/传参/存容器均复制句柄不 clone（对齐 `Ref` 心智）；函数值不可比较（§6.5-3）；语义详见 §6.11。
10. **Future 句柄语义（v0.8）**：`Future<T>` 值赋值/传参/存容器均复制句柄不 clone；不可比较（§6.5-3）；丢弃 Future 合法（发后即忘，受 §6.12-8 结构化出口纪律约束）；不可嵌套。语义详见 §6.12。
11. **Gen 句柄语义（v0.22）**：`Gen<T>` 值赋值/传参/存容器均复制句柄（各绑定共享同一个生成器与进度）；不可比较（§6.5-3）；丢弃生成器合法——最后一个引用释放时释放它持有的值，不执行生成器体（§6.20-7）。语义详见 §6.20。
12. **线程、共享与原子句柄（v0.25）**：`Thread<T>`、`Shared<T>`、`Atomic<int>` 值赋值/传参/存容器均复制句柄；不可比较（§6.5-3）；跨线程只经发送与 `Shared`（可发送规则见 §6.22-3）。语义详见 §6.22。

## 5. 语法（EBNF）

```ebnf
(* 程序与顶层声明 *)
program      = { import_decl | top_decl } ;
top_decl     = [ "export" ] ( function_decl | struct_decl | enum_decl
             | interface_decl | binding_decl | test_decl | extern_type_decl ) | extern_decl ;
extern_decl  = "declare" "function" IDENT "(" [ extern_params ] ")"
             ":" type [ extern_from ] ";" ;
extern_params = param { "," param } { "," "..." param } [","] ;  (* v0.36：C 变参参数，§6.8-21；"..." 为三个相邻的点，fmt 让它紧贴参数名 *)
extern_from  = "from" ( STRING | "syscall" INT_LIT ) ;        (* v0.23：FFI，§6.8-7；from/syscall 按标识符形态识别 *)
extern_type_decl = "declare" "type" IDENT ";"                 (* v0.23：不透明句柄，§6.8-10 *)
             | "declare" "struct" IDENT "{" field { "," field } [","] "}" ;  (* v0.23：外部结构体，§6.8-11 *)
import_decl  = "import" "{" IDENT { "," IDENT } [","] } "from" STRING ";" ;

function_decl= ( [ "async" ] "function" | "function" "*" ) IDENT [ type_params ]
               "(" [ param { "," param } [","] ] ")" ":" type block ;  (* v0.8：async 为上下文关键字，仅此处识别；v0.13 类型参数；v0.22：function* 为生成器，不与 async 组合，§6.20 *)
param        = IDENT ":" type ;
type_params  = "<" type_param { "," type_param } ">" ;     (* v0.13 *)
type_param   = IDENT [ "extends" type_atom ] ;             (* v0.20：约束须为接口，§6.18-6 *)
struct_decl  = "struct" IDENT [ type_params ] "{" [ field { "," field } [","] ] "}" ;
interface_decl = "interface" IDENT [ type_params ] "{" { method_sig } "}" ;   (* v0.20，§6.18 *)
method_sig   = IDENT "(" [ param { "," param } [","] ] ")" ":" type ";" ;
field        = IDENT ":" type ;
enum_decl    = "enum" IDENT [ "extends" "Error" ] "{" [ enum_case { "," enum_case } [","] ] "}" ;  (* v0.19：extends 为上下文关键字 *)
enum_case    = IDENT [ "(" [ param { "," param } [","] ] ")" ] [ "=" expr ] ;  (* v0.19：消息仅用于错误 enum，且必填；expr 限字符串字面量或模板串，§6.17-1 *)
binding_decl = ( "let" | "const" ) IDENT [ ":" type ] "=" expr ";" ;
test_decl    = "test" STRING block ;

(* 类型——尖括号仅存在于类型文法（Ref/Weak/List 为预声明泛型），无比较符歧义 *)
type         = type_atom { "|" type_atom } ;
type_atom    = base [ "?" ] | fn_type [ "?" ] ;
fn_type      = "(" [ type { "," type } ] ")" "=>" type ;   (* v0.7：参数位只写类型不写名 *)
base         = "int" | "float" | "bool" | "string" | "void" | "Error"
             | "Ref" "<" type_atom ">" | "Weak" "<" type_atom ">"
             | "List" "<" type_atom ">" | "Future" "<" type_atom ">" | IDENT
             | "Gen" "<" type_atom ">"                       (* v0.22：当前模块无同名类型时，§6.20-2 *)
             | "[" [ INT_LIT ] "]" type_atom ;               (* v0.23：定宽类型名、句柄与外部结构体名均以 IDENT 形出现，§6.21-1 *)

(* 语句 *)
block        = "{" { stmt } "}" ;
stmt         = binding_decl | assign_stmt | expr_stmt | if_stmt | while_stmt
             | for_stmt | for_in_stmt | return_stmt | break_stmt | continue_stmt
             | defer_stmt | yield_stmt ;
defer_stmt   = "defer" ( block | assign_stmt | expr_stmt ) ;   (* v0.19，§6.16 *)
yield_stmt   = "yield" expr ";" ;                              (* v0.22：只直接出现在生成器函数体内，§6.20-4 *)
assign_stmt  = lvalue "=" expr ";" ;
lvalue       = postfix_base { "." IDENT | "[" expr "]" } ;
if_stmt      = "if" "(" expr ")" block [ "else" ( if_stmt | block ) ] ;
while_stmt   = "while" "(" expr ")" block ;
for_stmt     = "for" "(" [ for_init ] ";" [ expr ] ";" [ for_update ] ")" block ;
for_init     = for_binding | lvalue "=" expr ;
for_binding  = ( "let" | "const" ) IDENT [ ":" type ] "=" expr ;
for_update   = lvalue "=" expr | expr ;
for_in_stmt  = "for" "(" IDENT { "," IDENT } "in" expr ")" block ;(* v0.6：变量 1–2 个；`in` 上下文关键字，见 §3 *)
return_stmt  = "return" [ expr ] ";" ;
expr_stmt    = expr ";" ;

(* 表达式——优先级自高到低：后缀(含 as) > 一元 > * / % > + - > 移位(v0.14) > 比较 > & (v0.14) > ^ (v0.14) > | (v0.14) > && > || > catch *)
expr         = catch_expr ;
catch_expr   = or_expr [ "catch" [ "(" IDENT ")" ] ( block | catch_expr ) ] ;
or_expr      = and_expr { "||" and_expr } ;
and_expr     = bitor_expr { "&&" bitor_expr } ;
bitor_expr   = bitxor_expr { "|" bitxor_expr } ;            (* v0.14：按位或 *)
bitxor_expr  = bitand_expr { "^" bitand_expr } ;            (* v0.14：按位异或 *)
bitand_expr  = cmp_expr { "&" cmp_expr } ;                  (* v0.14：按位与 *)
cmp_expr     = shift_expr [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) shift_expr ] ;
shift_expr   = add_expr { ( "<<" | ">>" | ">>>" ) add_expr } ;  (* v0.14：词法零变更——解析器邻接组合，语法注记 6 *)
add_expr     = mul_expr { ( "+" | "-" ) mul_expr } ;
mul_expr     = unary { ( "*" | "/" | "%" ) unary } ;
unary        = [ "-" | "!" | "~" ] postfix | "await" unary ;  (* v0.8：await 前缀；v0.14：~ 位非 *)
postfix      = primary { "(" [ arg_list ] ")" | "[" expr "]"
             | "." IDENT | "?" | "as" base } ;
arg_list     = expr { "," expr } [","] ;
primary      = INT_LIT | CHAR_LIT | FLOAT_LIT | STRING_LIT | TEMPLATE_LIT | "true" | "false" | "None"
             | IDENT | "(" expr ")" | arrow_fn
             | struct_lit | array_lit | new_expr | switch_expr ;
arrow_fn     = [ "async" ] "(" [ param { "," param } ] ")" ":" type "=>" ( expr | block ) ;  (* v0.7；v0.8 增 async 前缀——仅后随箭头头形态时识别 *)
param        = IDENT ":" type ;
(* v0.6：TEMPLATE_LIT 解糖为拼接链（§6.10-3），AST 无独立节点 *)
(* v0.15：CHAR_LIT 解糖为 int 字面量节点（§6.14），AST 无独立节点 *)
struct_lit   = [ IDENT ] "{" [ field_init { "," field_init } [","] ] "}" ;
array_lit    = "[" [ expr { "," expr } [","] ] "]" ;
variant_ctor = IDENT "." IDENT "(" [ arg_list ] ")" ;
new_expr     = "new" ( struct_lit | variant_ctor ) ;
field_init   = IDENT ":" expr ;
switch_expr  = "switch" "(" expr ")" "{" switch_arm { "," switch_arm } [","] "}" ;
switch_arm   = "case" switch_pat "=>" expr ;
switch_pat   = IDENT [ "(" [ IDENT { "," IDENT } [","] ] ")" ] | "else" ;
```

**运算符优先级与结合性**：后缀（调用/索引/字段/`?`/`as`）> 一元（`- ! ~`）> `* / %` > `+ -` > 移位（v0.14）> 比较 > `&` > `^` > `|` > `&&` > `||` > `catch`（v0.14 位运算五级与移位层插入——TS 同构）；双目运算均左结合；`=` 不是表达式（赋值只能是语句）。

**语法注记**：

1. **struct 字面量**：期望类型已知时用裸对象字面量 `{ x: 1, y: 2 }`（TS 形式，主推）；无上下文时用类型名前缀 `Point { x: 1, y: 2 }` 兜底。裸字面量不得作为语句首记号（需括号包裹或出现在绑定/参数中）——与 JS 对对象字面量的限制一致；
2. **enum 变体构造**：表达式位置用限定名 `Expr.Add(l, r)`（TS 命名空间/静态调用同形）。它由普通后缀链（标识符 + 字段访问 + 调用）解析，sema 定夺：接收者标识符为作用域内 enum 类型名时为变体构造（v0.6 前的唯一解）；否则为**方法语法**糖形（§6.10-1，v0.6 增）——零新增文法；模式位置用裸名 `case Add(l, r) =>`（模式文法独立，无歧义）；

4. **箭头头消歧（v0.7）**：表达式位置 `(` 后为 `)` 或 `IDENT :` 时按 `arrow_fn` 解析，否则按分组表达式——单 token 前瞻即唯一（分组表达式内不容 `:`）。参数/返回类型必须显式（与 `function` 声明同面），`(x) => x` 形态（省类型）是编译错误。词法零新增（`=>` 已在 switch 臂文法）；自举 parser.tx 同单镜像（diff-parser 全仓守护覆盖箭头源）。
3. 花括号构造与语句块无歧义（块只出现在语句/声明位置，且 if/while/for 的条件带括号）；`lvalue` 限定为名字起头的后缀链，排除字面量与调用结果被赋值。
5. **await 上下文消歧（v0.8）**：`await` 后随表达式起始记号（标识符/字面量/`(` `[` `{`/`-` `!`/`new`/`switch`/`true`/`false`）时识别为前缀 await，否则恒为普通标识符；操作数含后缀链（`await f().x` ＝ `await (f().x)`，与 TS 一致）；await 右结合可叠缀。`async` 箭头同理：`async` 后随箭头头形态（§5 注记 4）才识别。

6. **位移邻接组合与泛型实参互操作（v0.14）**：`<<`/`>>`/`>>>` 不新增词法 token——表达式二元运算符位遇**零空白邻接**（同行且位置区间相接亲证）的两个 `>` 组合为 `>>`、三个为 `>>>`，两个 `<` 组合为 `<<`；带空白的 `a > > b` 不组合（沿比较/期望表达式路径给结构化诊断——C++ 惯例）。**排除理由所保护的词法极简原封不动**：与 TS 机制对比，TS 扫描器合并 `>>` 系 token、解析器在类型实参位回调拆分；Taixu 从不合并故无需拆分——解析器只在表达式位组合，无歧义的结构保证＝D3 既有不变量"类型不出现在表达式位置"（类型实参位逐个消费 `>` 永不组合、表达式二元位才组合——两态文法位置不相交）。**`<<`×显式类型实参互操作＝串联定序**：调用位平衡前瞻（v0.13）成功即消费泛型实参、失败回落比较位；比较位操作数才遇移位层邻接组合——`f<int>(x) << 2`／`a << (b+1)`／空白形 `f < int > (x)` 三形锚入 TEST-02。

## 6. 语义规则

### 6.1 模块与导入（2026-09-03 补导入说明符三级规则，用户确认裸标识符形式）

1. 文件即模块。导入说明符按形式分三级：
   - **相对路径**（`./`、`../` 开头）：相对当前文件的 `.tx` 文件（扩展名省略）——**S1/V0.1 唯一可解析形式**：`import { a, b } from "./sub/token";`
   - **裸标识符**（如 `"io"`）：标准库命名空间，为 V0.2 标准库（taixu-stdlib 新仓）预留——S1 期为编译错误（"标准库未提供"），语法保留以避免 V0.2 破坏语法（TEST-02 负例守护）：`import { open } from "io";`
   - **其他形式**（绝对路径、包名路径等）：V0.2+ 包管理议题，S1 一律编译错误；
2. 顶层声明默认模块私有，`export` 后才可被导入；
3. 循环导入是编译错误；导入路径不存在或导出符号缺失是编译错误。
4. **按目标选择文件（v0.23；v0.35 增两档）**：原生编译器解析导入 `./x`（`../x`、`std/x`、`gui/x` 同法）时，依次找同目录的 `x_<系统>_<架构>.tx`、`x_<系统>.tx`、`x_unix.tx`（只对类 Unix 目标：linux、macos）、`x.tx`，用第一个存在的（系统名 `windows`、`linux`、`macos`；架构名 `x86_64`、`aarch64`；目标由 `--target` 决定，缺省为宿主），模块显示名随之为 `./x_windows` 等，诊断里看得出用的是哪个文件。`unix` 后缀对应 Go 的 `//go:build unix` 约束——太虚不引入布尔条件编译，所以做成文件后缀；不提供只按架构的后缀。同一组兄弟文件（`x.tx` 与上述各后缀中存在的，编辑器里未保存的也算）的导出面须一致——名字、种类与按书写文本比较的签名，参数名不计——每次构建都比对全部存在的兄弟文件，缺失或不同报 3033（位置为第一处导入），在一个平台上就能发现另一个平台的不一致。用途：平台相关的实现（外部函数声明与库名）放在按目标文件里；公共实现可放在 `x.tx`，也可只提供导出面一致的目标文件。历史 Zig CLI 的选文件规则不再适用。
5. **导入写法须与磁盘逐字节一致（v0.35）**：导入说明符里写出的每段目录名与最终选中的文件名（含目标后缀与 `.tx`），都必须在其所在目录的列举结果里逐字节出现，否则报 3008（“导入 `./x` 的写法与磁盘不一致：`a` 在磁盘上为 `A`”）；三个宿主同一规则——大小写不敏感的 Windows、macOS 上按别的写法也能打开，到 Linux 就打不开，现在一律报同一条（同 TypeScript `forceConsistentCasingInFileNames`）。逐字节比较、不做 Unicode 规范化（NTFS 与 ext4 都对规范化敏感）。按目标选文件时，只差大小写的文件不算这个名字的目标文件，兄弟导出面比对同理。编辑器里打开的文件以编辑器给的写法为准。

### 6.2 struct、enum 与 new

1. **struct 字面量**（值语义）：期望类型已知时 `const p: Point = { x: 1, y: 2 };`；无上下文时 `Point { x: 1, y: 2 }`。字段必须全量初始化（缺字段 = 编译错误，多字段同样）；
2. **堆分配**：`new Point { x: 1, y: 2 }`（struct，产生 `Ref<Point>`）与 `new Expr.Add(l, r)`（enum，产生 `Ref<Expr>`）；`new` 是唯一堆分配入口；
3. **enum 变体构造**（值语义）：`Expr.Add(l, r)`（限定名，见 §5 语法注记 2）；
4. **自动解引用**：`p.x`、`p[i]`、对 `p` 的赋值、switch 的 scrutinee，在 `p: Ref<T>` 时自动解引用（链式逐层）；**语言无显式解引用操作符**，整体取值拷贝用内建 `unref`（§7）；
5. 字段访问 `.`；**书写层面的方法语法**见 §6.10-1（v0.6 增，纯调用糖，零对象系统语义——本条 v0.5 前"方法不存在"指无虚分派/无 this 的语义层，仍成立）。

### 6.3 失败：可选、错误、传播与处理

1. **构造**：`err("message")` 构造 `Error`；`None` 是 `T?` 的缺席值（不是 Error）；
2. **传播 `expr?`**：`expr` 为 `T | Error` 时，失败则当前函数立即返回该 `Error`（要求外层返回 `U | Error`）；`expr` 为 `T?` 时，缺席则返回 `None`（要求外层返回 `U?`）。跨通道传播是编译错误，需先 `catch` 显式转换；
3. **处理 `expr catch F`**：`F` 为默认值表达式或 `(e)` 绑定块（TS 词汇的括号形式）。统一类型规则：**整体类型 = 成功侧类型 ∪ F 的类型**——F 为 `T` 得 `T`；F 为 `None` 得 `T?`（把故障降级为缺席）；F 为 `err(...)` 得 `T | Error`（把缺席升级为故障）；**(e)** 形式仅对 `T | Error` 合法，`e: Error` 恒有 `message`。**块形式的值规则**（无隐式块值，同 TS 函数体必须显式 return）：成功侧非 `void` 时，`(e)` 块必须经 `return` / `panic` 发散（此时表达式类型取成功侧）；成功侧为 `void` 时块作语句序列执行。**发散默认值（v0.6 增）**：默认值表达式若恒发散（`panic(...)`），不参与上列类型并集——整体类型取成功侧（`catch panic("impossible")` 是"此路不可达"的显式拼写，for-in 解糖依此收口句柄容器的失败通道，§6.10-2）；
4. **显式桥接**：`opt catch err("missing")`（`T?` → `T | Error`）、`risky catch None`（`T | Error` → `T?`）都合法且类型在签名处可见；
5. `Error` 可像普通值一样存储与比较（按 message 相等）；`T?` 之间及与 `None` 比较按值相等（`None` 仅等于 `None`）。
6. **结构化错误（v0.19）**：失败侧可以是错误 enum（`T | E`），`catch (e)` 中 `e: E`；`?` 的同种传播与放宽、`errAs`／`errWrap`／`cause` 见 §6.17。本节其余规则对 `T | E` 同样成立（F 为 `E` 的值得 `T | E`）。

### 6.4 控制流

1. `if` 条件必须 `bool`；`else` 必须显式；悬空 else 归最近 if（块语法本身消歧）；
2. `while`、C 风格 `for`；`break` / `continue` 仅对最近的循环生效；
3. `switch` 是表达式（有值），`case` 臂间**不穿透**（无 TS 的 fallthrough 与 `break` 遗忘问题）；解构绑定的变体字段在臂表达式内只读；臂类型必须一致；对 enum 的 switch 必须穷尽或含 `else =>` 臂。

### 6.5 求值顺序与相等

1. 表达式求值从左到右；`&&` / `||` 短路；
2. `string` 不可变；`+` 产生新串；
3. **`==` / `!=` 语义**：数值与布尔按值；string 按 UTF-8 内容；struct 按字段值；enum 按 tag 与字段值（递归）；`Ref<T>` 按同一性（对齐 TS `===` 对 object 的行为）；`[N]T` 按内容；`[]T` 按同一性（对齐 TS 数组 `===` 的行为）；`Weak<T>` 按盒同一性、**不比较目标**（见 §6.7-4）；`List<T>` 按**句柄同一性**（同 `Ref`/`[]T` 心智，§6.9-6；逐元素比较拆用户函数）；`T?` 见 §6.3-5；**函数类型不可比较（v0.7）**——操作数为函数类型即编译错误（TS 的函数比较是无底洞，D25 §二）。**Future 不可比较（v0.8）**——操作数为 `Future<T>` 即编译错误（完成与否用 `taskAwaitAny`/`await` 表达，D26 §二-2）。**Gen 不可比较（v0.22）**——操作数为 `Gen<T>` 即编译错误 3032（生成器是句柄，没有相等性面，同 Future；§6.20-8）。**v0.23**：定宽数值按公共类型比较（§6.21-7）；句柄只在同一句柄类型之间按指针值比较（§6.8-10）。 **Thread、Shared、Atomic 不可比较（v0.25）**——操作数为三者之一即编译错误 3002（它们是句柄，没有相等性面；§6.22-11）。

### 6.6 数组、切片与字面量（2026-09-03 补，编写全景示例时发现的缺口）

1. **字面量** `[a, b, c]`：期望类型为 `[N]T` 时校验元素数；期望类型为 `[]T` 时构造新切片；无期望类型时推断为 `[N]T`（元素类型须一致）；
2. **`[N]T` 是值类型**：赋值与传参整体拷贝，按内容比较；
3. **`[]T` 是共享句柄**：赋值共享底层存储（对齐 TS 数组行为）；S1 不提供索引读写以外的切片操作（追加、子切片等属 S2 标准库）；

### 6.7 弱引用（v0.2 新增；实现规则 D6 §5 定稿）

1. **构造**：`weak(r)`，`r: Ref<T>` —— 产生 `Weak<T>`（`T` 为 `r` 的指向类型）。`weak` 只接受 `Ref<...>` 实参，传其他类型为编译错误；
2. **解引用**：`deref(w)`，`w: Weak<T>` —— 返回 `T?`：目标存活时等价于对目标 `unref`（**值拷贝**，不产生新 `Ref`、不延长目标生命周期）；目标已释放时返回 `None`。因此弱引用观察的是"目标死没死 + 死前最后状态的拷贝"，**不是**长期共享句柄——需要长期持有请直接持有 `Ref<T>`；
3. **生命周期与计数**：`Weak<T>` 不 RETAIN 目标、不阻止目标释放（`--mem-report` 与守护用例验证该语义）；`Weak` 盒自身是 RC 堆对象（可存字段/传参/入数组，随普通规则计数）；
4. **相等**：`w1 == w2` 按 Weak 盒**自身同一性**（同 `Ref` 的句柄心智），不比较目标内容——深度相等比较不穿透 Weak（D6 §3 不变式：EQ 深比较不触碰计数）；
5. **不提供**：弱引用回调 / finalizer（D6 §5，V1.0 前不纳入）；循环中的目标在循环收集器（D6 §6，V0.3+ 选型）落地前不会被回收，属已记录的残留而非 Weak 语义缺口。

### 6.8 外部函数与原生 FFI（v0.31）

模块级外部函数必须带 `from "库"` 或 `from syscall 编号`，无函数体且返回类型标注必填。无 `from` 的旧宿主 extern 已退场，一律在语义阶段报 3033，即使未调用也报错；接口内的方法签名不属于宿主 extern。当前不提供宿主注册表或 GUI 绑定。

旧版第 1–6 条的宿主 id、封送映射及 4013 运行期绑定规则只见于历史 D3 v0.30 §6.8，不再构成现行合同。下面沿用原生 FFI 的既有条款号，保留引用锚；类型、ABI 与调用期间有效的封送规则不变。

7. **声明**：`declare function 名(参数): 返回 from "库";`——Windows 写 DLL 名（可省 `.dll`，如 `"kernel32"`），Linux 写共享库全名（如 `"libc.so.6"`）；Linux 另有 `from syscall 编号;`：直接发系统调用、不经任何库，编号 0..4095，产物保持静态、无 libc。macOS 不提供稳定的系统调用接口（Go 曾因直接发系统调用在系统更新后失效），以 macOS 为目标时被调用的 `from syscall` 报 3033，改经 `from "libSystem"` 声明并放进 `_macos` 或 `_unix` 文件（v0.35）。macOS 的库名写 dylib 的安装名：`/` 开头的绝对路径（如 `"/usr/lib/libz.1.dylib"`），或 `@executable_path/`、`@loader_path/` 开头的装载路径，libSystem 可简写为 `"libSystem"`；交叉链接不读目标平台的库文件，所以不按短名搜索，其他写法以 macOS 为目标时报 3033（v0.35 补记 M2）。x86_64 macOS 上从 libSystem 导入的部分函数按 Apple 头文件改名链接（stat 与目录族的 `$INODE64`、`realpath$DARWIN_EXTSN`、`select$1050` 等），太虚里声明的 `stat` 与 C 程序得到同一个函数，结构按 64 位 inode 布局声明。`from`、`syscall`、`type` 按标识符形态识别，不新增关键字。
8. **可见性与导出**：FFI 外部函数只在声明它的模块可见，不可 `export`（2001）——平台差异封装在模块自己的导出函数里；各模块可以各自声明同一个 C 函数；同一模块内重复声明或与本模块的声明、导入重名报 3003。`declare type`、`declare struct` 是类型，可以 `export`。
9. **只检查被调用的**：目标检查、调用桩与导入只针对程序实际调用的外部函数；只声明不调用的不检查、不链接，共用文件里可以放另一目标的声明（同 Go、Zig：引用到才报）。目标检查（`from syscall` 只用于 Linux、`wstr` 只用于 Windows）在语义分析里于第一次调用时做，诊断落在声明处，检查命令与编辑器即可看到（v0.35；此前只在构建尾部）；同名符号来自不同库、macOS 上与运行时经 libSystem 导入的符号同名却来自别的库（v0.35 补记 M2），以及 `--static` 的限制属于链接层，仍在构建时报。平台相关的声明通常放在按目标文件里（§6.1-4）。
10. **句柄**：`declare type H;` 声明指针宽度的名义不透明句柄：只能来自外部函数的返回值或输出参数，能传回外部函数、同一句柄类型之间 `==`/`!=`，不能算术、不能 `as`（3013，提示用 `handleBits`/`handleFromBits<H>`）、不能与 `int` 隐式转换。需要数值或哨兵值（如 `INVALID_HANDLE_VALUE`）时用 `handleBits(h): int` 与 `handleFromBits<H>(v): H`（`v` 为能无损扩宽到 `int` 的整数）。外部函数返回 `H?` 时空指针得 `None`，参数为 `H?` 时可传 `None`。不同模块的同名句柄是不同类型。句柄不可发送到其他线程（随线程落地生效）。
11. **外部结构体**：`declare struct S { a: i32, b: [4]u16 }` 按 C 布局（声明序、C 对齐规则；x86_64 上 `isize`/`usize`/句柄 8 字节）：字段只能是定宽数值、`int`/`float`、句柄、定长数组 `[N]标量`、其他外部结构体——`bool` 不在其内（Win32 `BOOL` 写 `i32`），字段不收可空句柄（空句柄写 `handleFromBits<H>(0)`）；至少一个字段，不收类型参数，不能按值包含自身（3033）。太虚侧是普通值类型 struct，读写规则同一般代码。只能以 `Ref<S>` 传给外部函数：调用前按布局拷入临时 C 缓冲（填充清零），调用后拷回字段（输入输出两用）；同一个 `Ref` 作两个实参时各拷一份，按参数顺序拷回、后者覆盖前者。
12. **封送**（实参检查按一般赋值规则：字面量按形参取型、无损扩宽，不另设特例）：

   | 签名中的类型 | C 形态 | 说明 |
   |---|---|---|
   | 定宽整数、`int`（`i64`） | 同宽整数 | 值传递；返回值按种类符号或零扩展 |
   | `f32`、`float`（`f64`） | `float`、`double` | 值传递 |
   | `bool` | `uint8_t`（0/1） | 值传递；返回只看低 8 位 |
   | `string` | `const char*`（UTF-8，NUL 结尾） | 实参拷到临时缓冲并补 NUL，串内含 NUL 字节 trap 4019；返回值拷成 `string`，非法 UTF-8 的坏字节替换为 U+FFFD，**空指针得 `""`**（v0.36 起；此前写作 `cstr`） |
   | `wstr` | `const wchar_t*`（UTF-16LE，NUL 结尾；Windows） | 同 `string`；Linux 目标调用带 `wstr` 的库函数报 3033（Linux 的 `wchar_t` 是 UTF-32） |
   | `Bytes` | 数据区指针 | 只作参数；C 端可写入（输出缓冲），调用期间有效，长度由调用方另传；冻结的 `Bytes` 在交给外部函数之前 trap 4022，调用期间改变其长度 trap 4024（第 16 条，v0.34）；长度为 0 的 `Bytes` 的数据区指针不可解引用，C 端须以长度为准，取值随来源而定、不保证为空指针：`bytesNew(0)`、`bytesFromStr("")`、`bytesSlice` 取 0 个、`readFileBytes` 读到空文件等新建的空 `Bytes` 是非空的哨兵地址 1，由 `listNew` 建的空紧凑 `List<u8>` 经 `listAsBytes` 得到的是空指针 0，长度经 `listPop` 等缩到 0 的仍指向原先持有的块（v0.33 勘误：v0.26 写作“可能是空指针”）；`List<u8>` 先经 `listAsBytes`（§6.15-7） |
   | 句柄 `H`、`H?` | 指针 | `H?` 的 `None` 为空指针 |
   | `Ref<S>`（`S` 为外部结构体） | `S*` | 只作参数；拷入拷出，见第 11 条 |
   | 函数类型 | C 函数指针 | 只作参数（回调），见第 14 条 |
   | `void` | — | 只作返回 |

   签名顶层的 `string` 即 C 串（v0.36）：太虚的 `string` 本身就是 UTF-8，不另设类型名（同 Swift 的 `String` 实参、Kotlin/Native 的 `String` 自动桥接）；`string` 在签名以外照旧是普通字符串，回调签名不收字符串（第 14 条）。`wstr` 只在带 `from` 的外部函数签名顶层识别（太虚侧即 `string`），用在别处报 3033 并提示改用 `string`；UTF-16 是另一种表示，编译器不按函数名（如 W 后缀）推断。写 `cstr` 报 3033“`cstr` 已并入 `string`”（v0.36）。其余形态——按值传递或返回结构体、`Ref<普通 struct>`、`List`、可选（句柄除外）、失败联合、返回 `Bytes`、联合与位域、指向指针的指针——报 3033，文案按形态给出改法；C 变参见第 21 条。
13. **`from syscall` 的额外限制**：最多 6 个参数；参数只收整数、`bool`、句柄、`string`、`Bytes`、`Ref<外部结构体>`（无浮点、`wstr`、回调，也不收变参参数——系统调用的实参个数固定，第 21 条）；返回只能是整数类型或 `void`，返回值本身就是 `-errno`；Windows 目标调用 `from syscall` 的外部函数报 3033。
14. **回调**：参数可为函数类型（如 `cmp: (usize, usize) => i32`），其参数与返回只收标量、`bool`、句柄，不能 `async`。实参必须是顶层的非泛型、非 `async`、非生成器函数名，签名与形参完全一致（不做扩宽），否则 3033——闭包与函数值变量都不行。编译器为每个（函数, 回调签名）生成 C ABI 蹦床。回调只能在一次外部函数调用期间、由发起这次调用的太虚线程调用（如 `qsort` 的比较函数、`EnumWindows`；v0.25 起各线程各自的外部调用期间都可以），否则运行期 trap 4020；回调里可以再调外部函数（嵌套与重入安全）。需要上下文时用 C 接口自带的 userdata 传 `int`，自己查表。
15. **错误码**：每次库函数调用后立即保存系统错误码（Windows `GetLastError`、Linux 经 libc 的 errno），`ffiLastError(): int` 读取本线程最近一次库函数调用留下的值（v0.25 起每线程一份）；`from syscall` 不写它（返回值本身就是 `-errno`）。标准库包装层把它们转成返回码或 `T | Error`。
16. **生存期与安全契约**：所有临时缓冲（`string`、`wstr`、外部结构体）只在本次调用期间有效。`Bytes` 实参（含经 `listAsBytes` 的 `List<u8>`）交给外部函数或系统调用之前做冻结检查，冻结的 trap 4022（§6.22-8；冻结的外部结构体以 `Ref` 传入同样 trap 4022，第 11 条）。调用期间该 `Bytes` 被临时借用，数据区与长度都不变：回调经该 `Bytes` 或其 `List<u8>` 别名做任何改变长度的操作（`bytesPush`、非空的 `bytesAppend`、`listPush`、`listInsert`、非空表的 `listPop`、`listRemoveAt`），在修改之前 trap 4024；原地读写（`bytesGet`、`bytesSet`、`listGet`、`listSet`）与不改变长度的调用（空的 `bytesAppend`、空表的 `listPop`）照常（v0.34；v0.33 只拒绝超出当前容量的增长，是否 trap 取决于当时的容量）。调用正常返回后借用解除，嵌套调用与同一对象作多个实参时按后进先出恢复。C 端在调用返回后保留指针、外部异常或 longjmp 越过太虚帧，不在这一安全合同之内。太虚堆不暴露给 C：没有原始指针类型，C 端拿到的只有临时缓冲或 `Bytes` 数据区。C 端保留指针、越界写缓冲、签名写错（参数个数、宽度、调用约定）属于调用方契约违反，规范写明、运行时查不出——**标准库已覆盖的能力不得手写绑定**，外部声明集中在标准库与平台层。
17. **链接与产物形态**：两个目标都按符号名导入，交叉链接不需要目标平台的库文件。Windows 经 PE 导入表（每个库一个导入目录项）；两个被调用的声明给同一个名字写了不同的库报 3033。Linux 目标调用了 `from "库"` 的外部函数时产物动态链接（解释器 `/lib64/ld-linux-x86-64.so.2`，DT_NEEDED 按声明的库名首次出现序并补 `libc.so.6`，立即绑定），经 libc 的 `exit` 退出（C 侧 stdio 照常刷新），只支持 glibc；只用 `from syscall` 或不用 FFI 的产物保持静态、无 libc。库缺失时进程起不来、由系统报告（没有懒装载）。
18. **`--static` 与构建报告**：`taixu build`/`run` 接受 `--static`：Linux 目标调用了 `from "库"` 的外部函数报 3033 并指出声明处；Windows 产物不受影响。程序调用了外部函数时，构建在产物行后多一行报告：Windows“外部库 kernel32.dll（模块）、…”，Linux 动态“动态链接 库（模块）、…；解释器 …”，只用 `from syscall` 为“静态产物（外部函数均为 from syscall）”；不用外部函数的构建输出不变。
19. **运行期码**：4019 `ffi_arg`（`string`/`wstr` 实参含 NUL 字节）、4020 `ffi_callback`（回调不在外部调用期间，或调用它的线程不是发起这次外部调用的太虚线程）、4022 `frozen_write`（冻结的 `Bytes` 或冻结的外部结构体 `Ref` 作外部函数实参，第 16 条）、4024 `ffi_buffer_borrow`（v0.33 起；v0.34 起为调用期间经 `Bytes` 或其 `List<u8>` 别名改变长度，第 16 条；诊断带调用处的源码位置）；退出码 3。
20. **内部编码与历史产物**：原生编译器内部以 TXC IR 表示外部函数调用；它不是公开 `.txc` 产物，也没有 VM 执行入口。旧 v4/v5 产物、`TXFFI` 与冻结加载器的兼容规则仅见历史 D3 v0.30；当前容量按附录 A 的原生 TXC v6 列。
21. **C 变参参数（v0.36）**：C 变参函数在声明里把变参位置写成带 `...` 前缀的参数，读作“变参位置上的这种类型”：

   ```
   declare function openat(fd: i32, path: string, flags: i32, ...mode: u32): i32 from "libSystem";
   declare function snprintf(buf: Bytes, size: usize, format: string, ...value: float, ...count: int): i32 from "libc.so.6";
   ```

   - **位置**：变参参数排在全部定参之后、至少一个定参（2002）；一个声明可有多个变参参数，每个都写 `...`（三个相邻的点；fmt 让它紧贴参数名）。
   - **类型**：同定参可用的封送类型（第 12 条：整数、定宽整数、`bool`、`float`、`f32`、句柄、`string`、`wstr`、`Bytes`、`Ref<外部结构体>`）；回调不能作变参参数（3033）；`from syscall` 不收变参参数（3033）。
   - **调用**：照常逐个传实参，个数与类型按声明核对（同定参），没有调用处推断。一个 C 函数在一个模块里只有一种声明：同一函数要以不同的变参调用，须是同一套类型（printf 族通常用不上，太虚有自己的格式化）。
   - **默认实参提升**：编译器在变参位置做 C 的默认实参提升，不要求手写转换（同 C 编译器）——`i8`、`i16` 符号扩展，`u8`、`u16`、`bool` 零扩展到 C 的 `int`；`f32` 转成 `double`；其余整数、指针类按本身的宽度扩展，各占一个 8 字节变参槽。
   - **调用约定**：x86_64 的 SysV 与 Win64 约定下变参与定参同样传（SysV 的 `al` 计入变参浮点个数；Win64 前四个位置上的变参浮点同时放进 xmm 与对应的通用寄存器）；Apple arm64 上变参一律经栈传、各占 8 字节，从定参的栈区之后按 8 字节对齐起——变参函数声明成定参在 x86 上碰巧能用、在 Apple arm64 上读到垃圾，故须如实声明。
   - **已知变参函数的核对**：对系统 C 库（`libc.so.6`、`libSystem`、`ucrtbase`、`msvcrt`、`user32`，不分大小写，可带 `.dll`）的声明，下列 C 变参函数的定参个数须等于表中个数，多出的参数须写成变参参数，否则 3033（文案给出改写，如“第 4 个参数起写成变参参数，如 `...mode: u32`”）：`printf 1`、`fprintf 2`、`dprintf 2`、`sprintf 2`、`snprintf 3`、`_snprintf 3`、`asprintf 2`、`scanf 1`、`fscanf 2`、`sscanf 2`、`open 2`、`open64 2`、`openat 3`、`openat64 3`、`fcntl 2`、`fcntl64 2`、`ioctl 2`、`execl 2`、`execlp 2`、`execle 2`、`syslog 2`、`prctl 1`、`semctl 3`、`sem_open 2`、`mq_open 2`、`err 2`、`errx 2`、`warn 1`、`warnx 1`、`_open 2`、`wsprintfA 2`、`wsprintfW 2`。不在表里的库函数由声明者负责，与 C 原型不一致的声明属第 16 条的契约违反。这张表是太虚的补充：Rust、Zig 有头文件或手写原型可依，太虚的外部声明是唯一来源。

### 6.9 List 容器（v0.5 新增；D20 §二，LANG-07）

1. **构造**：`listNew<T>()`——空表；元素类型 `T` 经**期望类型推断**（同 `[]T` 字面量规则：`let l: List<int> = listNew();`），无期望为编译错误（编译器不定 `T`，TEST-02 负例）；
2. **操作**（仅此六件，无方法、无索引语法——语言面最小化红线）：
   - `listPush(l, v): void`——尾追加；
   - `listPop(l): T?`——尾弹出，空表 `None`；
   - `listGet(l, i): T?`——索引读，越界 `None`（`byteAt` 心智：服务边界判断，不 trap）；
   - `listSet(l, i, v): void`——索引写，越界 **trap**（越界码 4002——写入位置的丢失是程序错误而非预期缺席）；
   - `listLen(l): int`——长度；
3. **句柄语义**：`List<T>` 是 RC 堆对象；赋值/传参/存字段共享底层存储（对齐 `Ref`/`[]T`）；`==` 按句柄同一性（§6.5-3）；
4. **计数规则**（D6 §3 帧窗口所有权制的常规延伸，`--mem-report` 守护）：`listPush` 对元素 RETAIN（表持独立所有权）；`listPop` 将元素所有权自表**移交**结果槽（不 RETAIN）；`listGet` 为共享读 RETAIN（表仍持有）；`listSet` RETAIN 新值、RELEASE 旧值；表对象释放时级联释放全部元素；
5. **元素类型面**：`int/float/bool/string/Ref<T>/Weak<T>/List`（嵌套）、函数类型、`Future<T>`、`Bytes`；**v0.20 起放开到 struct/enum（错误 enum 除外）/定长数组与接口值**——值类型元素按值语义（与 `const q = p` 同一规则）：`listPush`/`listSet`/`listInsert` 存入拷贝，`listGet` 取出拷贝（修改取出的值不影响表内元素），`listPop`/`listRemoveAt` 把元素移交出来（不拷贝）；要在表内原地修改，存 `Ref<T>`。失败通道类型（`T?`/`T | Error`）仍不收（用包装 enum 承载，stdlib JSON 先例）。**v0.20 的放开（LANG-23）仅在太虚编译器与 NRT 实现**：用到值类型元素的程序，SEB1 追加 `LVE1` 标记节，`taixu run`/`taixu test`（Zig CLI，其代码生成不插拷贝）因此拒绝而不是按旧语义编译；冻结的 Zig 前端（`--frontend=zig`）仍按 v0.17 规则报错；
6. **无驻留特例**：表与其元素全程常规计数（无永生哨兵），循环 push/pop 零滞留由 `--mem-report` 用例守护（D20 §六）。

### 6.10 语法糖三件（v0.6 新增；D22 §一，LANG-08）

**总则**：三件全部是**书写层面的糖**，解糖为既有指令面——**零 .txc 格式变更**（金哈希 `806dd58c` 守护预期恒等；等价性由指令流逐位对照单测锁定）；不引入对象系统、虚分派、`this` 或新内建。

1. **方法语法 `recv.name(args)`**：解析为普通后缀链（接收者 + 字段访问 + 调用），sema 检查期解糖为纯函数调用——**解析序（冻结）**：
   - 接收者为作用域内 enum 类型名 → 变体构造（§5 注记 2，v0.6 前语义不变）；
   - 否则按接收者**静态类型**归一化 `TypeName_methodName`，经**驼峰约定映射**（`List_push`→`listPush`、`Map_getInt`→`mapGetInt`、`string_trim`→`stringTrim`）得目标函数名，按普通调用同一解析序（作用域 → extern → 内建）查找——内建/库函数经此路命中（`l.push(v)`≡`listPush(l, v)`、`m.get(k)`≡`mapGet(m, k)`）；
   - 约定名未命中 → 以**方法名直击作用域**（鸭子方法：首个实参为接收者的用户函数，`p.magnitude()`≡`magnitude(p)`——零新语义，参数与返回类型沿普通函数调用诊断）；
   - 两路皆未命中 → 既有 `undefined_symbol`（3001，消息含接收者类型与方法名）；
   - **不进语言核心**：无 method 表、无 `this`、无重载——它是书写的糖（§8"方法语法"排除条目由本条取代：书写形态入 S1，类、继承、`this`、重载仍永久排除）。
   - **v0.20 补充**：泛型 struct 实例按模板名映射（`Box<int>` 上的 `b.get()` 找 `boxGet`）；接收者为接口值时是动态分派（§6.18-5）；泛型实例体内按上述顺序找不到时，回落到接收者类型的声明模块（§6.18-2）。
2. **for-in 迭代 `for (x in e)` / `for (k, v in e)`**（`in` 为上下文关键字，§3）：检查期解糖为**计数循环**——与今日手写完全同构（`const #it = e;` 绑定 → `for (let #i = 0; #i < len(#it); #i += 1 形态)` → 体首元素绑定）；合成名以 `#` 前缀承载（非合法用户标识符，永不碰撞）。**可迭代面（冻结）**：`List<T>`（元素，`listLen`/`listGet`）、`string`（**字节序**，`len`/`byteAt`——码点迭代 v0.9 评估）、数组 `[N]T` 与切片 `[]T`（下标序，`len`/索引）；`Map`（stdlib 类型：单变量迭代**键**、双变量迭代键值对，经 `mapKeys`/`mapGet`——**插入序红线**由 stdlib 守护；`mapKeys`/`mapGet` 未导入时报定向 `undefined_symbol`）；**v0.20**：prelude 的泛型 `Map<K, V>` 同此规则，`Set<T>` 单变量迭代元素（经 `setToList`），均为插入序（§6.19）。句柄容器的失败通道（`listGet`/`byteAt` 得 `T?`）以**发散 catch** 收口（循环界保证在界，运行期不可达，§6.3-3）；`break`/`continue` 沿用最近循环语义。可迭代面之外报 `not_iterable`（3022）；双变量形态仅 `Map` 合法（v0.20：`Set` 只收单变量）。**v0.22**：`Gen<T>` 可迭代（单变量，元素为 `T`；双变量报 3022），逐个取值、`None` 即结束；提前离开循环即关闭生成器（§6.20-5）。
3. **字符串插值 `` `a=${x} b` ``**：解析期解糖为**拼接链**——字面段成为字符串字面量（raw＝引号包裹的模板内原文切片，转义原样保留——渲染红线不破），表达式段统一 `toStr` 化后以 `+` 左结合串联（与手写 `"a=" + toStr(x)` 逐位同构；空字面段不入链）。**段类型面**＝`toStr` 之面（v0.6 起 `int/float/bool/string`，v0.11 增 `Error`——错误值可读化，§7）——`Ref`/`List` 等面外类型沿 `toStr` 既有诊断报错；段内不支持嵌套模板（词法结构化拒绝）。无插值段的模板串 ≡ 普通字符串字面量。

### 6.11 闭包与函数类型（v0.7 新增；D25 §一–五，LANG-09）

1. **单一书写形式**：只有箭头形式 `arrow_fn`；`function` 保留声明位，不引入 function 表达式。参数与返回类型必须显式，零推断；类型位函数类型的参数只写类型不写名。
2. **捕获＝创建时值快照**：值类型（int/float/bool/struct/enum/定宽数组）创建时 clone 进 env；句柄类型（`Ref`/string/切片/`List`/函数）复制句柄。**闭包体内被捕获名只读**——赋值即编译错误（`closure_capture_readonly`，3023）；要可变状态就持 `Ref`（穿过捕获句柄写字段合法）。无引用捕获（by-ref 不入面）。
3. **捕获解析**：闭包体内自由标识符按最近绑定解析——外层局部/形参 → 捕获；顶层绑定 → 不捕获（经 env 的定义模块状态基座直连）；遮蔽规则与块作用域一致。嵌套闭包对外层捕获名的引用＝再次捕获。绑定名在自身初始化器内不可见（禁自指，与既有 const 一致）——递归走普通函数或经参数传递。
4. **双形态收敛**：**箭头字面量恒 env 形态**（闭包值＝env 对象引用，字段布局冻结：f0＝函数位 fnv、f1＝定义模块状态引用、f2..＝捕获快照——普通 RC 对象，全部计数点走既有机制零特判）；**具名函数值恒 fnv 形态**（`const f: (int) => int = helper;` 直接装载函数索引）。调用点双臂收敛：`.call` 对 fnv 与 env 形态统一 R0 语义（fnv 路径改写模块状态、env 路径保留 env 引用随槽移交被调帧——同槽同账目形态）。`.ret`/trap 卸载的 R0 释放规则自 v0.7 起为「R0 一律 releaseSlot」（模块状态永生哨兵＝空操作——旧程序逐字节不受影响；金哈希 `806dd58c` 不变）。
5. **调用约定边界注记**：「ref 且 f0 为 fnv」是 VM 层闭包调用的 **ABI 契约**，不是用户可依赖的语言特性——sema 把关调用点 callee 必须为函数类型；文档/教程/llm-guide 一律不得将「可调用 struct」当特性叙述。
6. **方法解析序增补（§6.10-1 序的中位插入）**：变体构造 → **fn 类型字段直调**（`obj.onClick(e)`——字段存在且类型为函数类型时接管；GUI 回调存字段主用例）→ 约定名 → 同名用户函数 → 编译错误。fn 类型字段为 v0.7 新面，与既有程序零交叠。
7. **显式不做（D25 §八）**：引用捕获/被捕获可变绑定、闭包与 `Weak` 的组合糖、函数重载/方法表（`this`）、函数类型泛型参数（高阶内建按用例另立）、闭包相等、`function` 表达式形式。

### 6.12 异步与任务运行时（v0.8 新增；D26 §一–三/§五，LANG-10＋RT-07）

1. **着色规则**：`async function f(): T` 的 `T` 为**成功侧**类型；直接调用 `f(x)` **启动任务**（§6.12-3）并返回 `Future<T>`；`async` 箭头（§5 `arrow_fn`）同面。着色跟随**被调表达式**——经普通 fn 类型绑定/形参转发调用＝着色擦除（同步执行；体内 await 即结构化 trap）；类型同一性不比较着色位（sig 面无 async 位，async 值→同签名 fn 类型＝宽化）。声明体面＝`T | Error`：体内 `return v`（T）与 `return err(...)` 皆合法，`?`/`catch` 照常作用；missing_return 按书面返回判定（void 的 async 观察者函数不因体面包装误报）。
2. **await**：`await fut` 仅在 `async` 函数体（含 `async` 箭头体）内合法（词法着色检查，普通函数/main 内出现即编译错误 3024）；操作数须为 `Future<T>`（3025）；产出 `T | Error`（任务内错误原样穿透联合通道）——await 天然接 `?`/`catch`，零新增通道语义。**v0.38**：同一 `Future` 可多次 `await`（同一任务里先后、或多个任务各自），每次得同一结果——结果与 future 共享（引用类型得同一对象，值类型得相同的值），future 释放时连带放掉它那一份；已取消的每次得 `err("task cancelled")`。
3. **spawn＝惰性入队**：调用 async 函数建任务＋Future 对象后**入 FIFO 就绪队即返回**——不急切执行；启动序＝FIFO 序＝可观测执行序。async 体内调用 async 函数＝spawn 子任务（不经 await 即并发）。
4. **调度器＝同步栈排空后的 FIFO 泵**：main 同步体自然执行；帧窗口排空后进泵——FIFO 取就绪任务→跑至完成或再次挂起→重复。全序确定：完成序、await 唤醒序、`taskAwaitAny` 裁决序全部由 FIFO＋观察集插入序决定。
5. **挂起＝单帧快照（stackless 不变式）**：`await` 未决时本帧窗口值整体移入快照＋pc 存档→弹帧回调度器；future 完成后任务恢复并**重执行挂起点**。不变式：async 帧链恒单帧——await 仅在 async 体内词法面；async→async 经 Future 解耦，快照/恢复无需帧链序列化。
6. **取消即确定性清理**：`taskCancel(f)` 对未决任务置取消——**未启动/挂起中的任务当帧立即释放**（窗口值逐槽 release，局部/捕获 RC 确定性回收）；已完成者空操作；await 被取消的 future 得 `err("task cancelled")` 走联合通道；任务体内 `taskCancelled()` 为协作检查点旗标查询（main 同步体恒 false）。**v0.21**：挂起中（含已被唤醒、尚在就绪队里）的任务若在挂起点已登记 `defer`，先按后进先出执行这些 `defer` 再释放（§6.16-8）。
7. **taskAwaitAny**：`(List<Future<T>>) int | Error`——按 List 插入序扫描，有已完成/已取消者立即返回其下标；全未决→挂起登记观察集，任一完成即唤醒重扫（重新按插入序取最先完成者，确定性裁决）；空列表得 `Error`。
8. **定时器面（v0.9 增；D29 §一）**：`sleepMs(ms)` 建 pending future＋登记一次性定时器；调度泵每轮**先于就绪任务**收割到期定时器（deadline 升序、同刻按登记序——与 FIFO 全序共同构成确定性时间维度）；钟源恒单调钟。到期＝future 置 done（空结果，await 得 void）并唤醒观察者（观察集插入序）；任务已完成后关联的定时器到期**收割为空操作**；泵出口未到期定时器**排水为空操作**（仅释放引用——零滞留，库层超时依赖此条）。**只剩定时器时等待到期（v0.21）**：就绪队空、仍有未决任务且有未到期定时器时，调度泵等待到最早的 deadline 再收割（Windows `Sleep`、Linux `nanosleep`，毫秒向上取整），不判死锁；没有未决任务时泵出口照旧排水。唤醒序仍只由 deadline 序决定。`await sleepMs(0)`＝下一调度轮到期（确定边界）；负值＝即 err future。**确定性纪律（沿 nowMs 从严）**：e2e/断言面只允许 0ms（立即到期）与不到期（任务先完成）两类确定形态，真实中间时长只入手工冒烟面。
9. **库层超时与选择通道（v0.9 注记；D29 §二/§三）**：超时/组级取消/异构选择均为 **stdlib 纯 .tx 库面**（std/task），语言核心零增量——`timeout(f, ms)`（**单态 `Future<int>`**——S1 无用户泛型函数〔D3 排除清单在案〕，他类型按用例逐型增包装，**泛型函数维持排除**）；竞速走"包装 runner 同构化"：taskAwaitAny 只收同构 `List<Future<T>>`，异构 future 经 async 包装统一为同型标记值（下标携带来源）后裁决——f 先完成透传（定时器事后收割空操作）、定时器先到期 taskCancel(f)（失败者取消，§6.12-6 语义复用）→ `err("timeout")`；组级取消 `taskGroupCancel`（登记序遍历逐 taskCancel；**cancel 后 drain 必达零 pending**——结构化闭环）。select 语法面（`select { case … }`）维持排除（§8）。
8. **结构化出口双守护（trap，退出码 3）**：①**死锁**——调度泵就绪排空、也没有未到期定时器（v0.21）而仍有未决任务（await 等待环）。②**退出 pending**——全部可推进任务完成后仍有未决任务（发后即忘未 join——强制 TaskGroup/显式取消）；**错误出口同样守护**：main 经 panic/非零退出码退出时同样执行退出 pending 检查——原错误保留并附未决计数诊断（结构化纪律不分正误出口）。
9. **实现边界**：当前原生 `build/run/test` 支持异步面（§6.12-10），并经普通、调试和泄漏检查产物验证。旧 Zig AOT 对 `async_start`/`await_fut` 的拒绝只是归档实现的限制，不限制当前语言。
10. **原生实现（v0.21 新增；LANG-25）**：太虚编译器与 NRT 实现本节全部规则，执行序、唤醒序、`taskAwaitAny` 裁决序与两种出口 trap（消息文本、退出码 3）与解释器逐项一致，NRT 差分逐字节对照。只有任务的基帧能挂起：着色经函数值擦除后在任务内同步调用的 async 函数，遇到未决 `await`（或 `taskAwaitAny`）按 trap「await 执行于无任务上下文」结束（解释器原先把嵌套帧当任务帧挂起、随后以 `corrupt_code` 结束，同修）。不含 `async` 的程序产物不变。

## 7. 内建函数与程序入口

**入口**：`function main(args: []string): int`（返回 `void` 视为返回 0；v0.19 起也可返回 `int | Error`，失败时 stderr 输出 `toStr(e)`、退出码 1，§6.17-9）；`args` 为命令行参数（不含程序名），逐项为命令行原文、空参数保留；参数不是合法 UTF-8（Windows 为不成对的 UTF-16 代理）时进程在入口拒绝启动——stderr `taixu aot: <平台> argv acquisition failed: InvalidUnicode`，退出码 64，`main` 不运行（v0.41；string 恒为合法 UTF-8）。

| 内建 | 签名 | 说明 | 对应需求 |
|---|---|---|---|
| `print` | `(string) void` | 标准输出一行（INFO 级日志通道） | RT-02 |
| `eprint` | `(string) void` | 标准错误输出一行（原样写出、末尾加换行，不加前缀；与 `print` 对偶）（V0.45 起，v0.30 补记） | BOOT-38 |
| `err` | `(string) Error` | 构造 Error | LANG-05 |
| `readFile` | `(string) string \| Error` | 读文本文件（UTF-8） | RT-03 |
| `len` | `(string \| []T \| [N]T \| Bytes) int` | 长度（字符串与 Bytes 为字节数；Bytes 自 v0.18） | STD-01 |
| `toStr` | `(int \| float \| bool \| string \| Error) string` | 转字符串（v0.6：`string` 为恒等——插值解糖统一 toStr 化，§6.10-3；v0.11：`Error` ⇒ `"Error: " + message`——错误链打印可读化，LANG-12；v0.19：错误 enum 值 ⇒ `"E.V: " + message`，有原因时追加 `"；原因：" + toStr(原因)`，§6.17-7；v0.23：全部定宽整数，`u64`/`usize` 按无符号十进制，§6.21-8；v0.26：`f32`，按 f32 的最短往返表示，§6.21-18；v0.36：任何 NaN 打印 `nan`，不带符号） | STD-01／LANG-08／LANG-12 |
| `unref` | `(Ref<T>) T` | 从堆引用整体取值拷贝（唯一显式"解引用"形式） | LANG-05 |
| `weak` | `(Ref<T>) Weak<T>` | 构造弱引用（不拥有目标；§6.7） | MEM-03 |
| `deref` | `(Weak<T>) T?` | 解引用弱引用：目标存活得值拷贝、已死得 `None`（§6.7） | MEM-03 |
| `byteAt` | `(string, int) int?` | **自举层内建**（V0.3，D11）：源码逐字节访问——在界得 UTF-8 字节值（0–255），越界得 `None`（服务边界判断，不 trap） | BOOT-02 |
| `utf8Encode` | `(int) string \| Error` | **自举层内建**（V0.3，D11）：合法 Unicode 码点（≤0x10FFFF 且非代理区段）得 UTF-8 编码串，否则得 `Error` | BOOT-02 |
| `listNew` | `listNew<T>(): List<T>` | 空表；`T` 经期望类型推断，无期望编译错误（§6.9-1） | LANG-07 |
| `listPush` | `(List<T>, T) void` | 尾追加（元素 RETAIN；§6.9-4） | LANG-07 |
| `listPop` | `(List<T>) T?` | 尾弹出（所有权移交；空表 `None`） | LANG-07 |
| `listGet` | `(List<T>, int) T?` | 索引读（越界 `None`） | LANG-07 |
| `listSet` | `(List<T>, int, T) void` | 索引写（越界 trap；RETAIN 新/RELEASE 旧） | LANG-07 |
| `listLen` | `(List<T>) int` | 长度 | LANG-07 |
| `listRemoveAt` | `(List<T>, int) void` | 索引删除（v0.10 增，D57 §一）：越界 trap（越界码）；被删元素所有权自表移交后**无人接管**——堆引用即 RELEASE | LANG-11 |
| `listInsert` | `(List<T>, int, T) void` | 插入（v0.10 增，D57 §一）：`i==len` 即尾加；越界 trap（`0 <= i <= len`）；元素 RETAIN | LANG-11 |
| `strSub` | `(string, int, int) string` | 码元面子串（v0.10 增，D57 §一）：`strSub(s, from, count)` 按 **Unicode 码点**计 units（非字节——CJK 安全）；`from/count` 界内要求（`from >= 0`、`count >= 0`、`from+count <= 码点长`），越界 trap | LANG-11 |
| `strIndexOf` | `(string, string) int` | 子串首现**码元索引**（v0.10 增，D57 §一）；缺席得 `-1`；空针得 `0` | LANG-11 |
| `strSplit` | `(string, string) List<string>` | 分隔符切分（v0.10 增，D57 §一）：空分隔符 trap；连续分隔符产空串片；结果恒 ≥1 片 | LANG-11 |
| `imul` | `(int, int) int` | 64 位环绕乘（v0.14 增，D3 v0.14）：**非宿主调用**——codegen 直发 IMUL opcode（per-char 哈希内环不走宿主表分发）；`Math.imul` 仅借形态、32 位语义不继承；`*` 溢出 trap 语义分立（§6.13-3）；v0.26 起与 `wrapMul<int>` 相同（§6.21-21），保留不弃用 | LANG-15 |
| `wrapTo` | `wrapTo<T>(整数) T` | 截断转换：取低位按整数类型 `T` 的宽度与符号解释，不检查；类型实参必须写出（v0.23，§6.21-6；codegen 直发，不占 BuiltinId） | LANG-28 |
| `wrapAdd` | `wrapAdd<T>(T, T) T` | 按整数类型 `T`（含 `int`）的宽度环绕加：二补码取低位，不检查溢出；类型实参可省，由两个实参的公共类型推出；写出时两个实参须能无损扩宽到 `T`（v0.26，§6.21-21） | LANG-28 |
| `wrapSub` | `wrapSub<T>(T, T) T` | 环绕减，规则同 `wrapAdd`；无符号取二补码写 `wrapSub<T>(0, x)`（v0.26，§6.21-21） | LANG-28 |
| `wrapMul` | `wrapMul<T>(T, T) T` | 环绕乘，规则同 `wrapAdd`；`wrapMul<int>` 即 `imul`（v0.26，§6.21-21） | LANG-28 |
| `floatToBits` | `(float) int` | IEEE 双精度的 64 位模式（有符号 `int` 承载，最高位为符号位）；`f32` 实参先无损扩宽（v0.23；codegen 直发 cvt 种类 25，只换值标签，不占 BuiltinId） | LANG-27 |
| `floatFromBits` | `(int) float` | 由 64 位模式构造双精度值，任何位模式都合法（含 NaN 与无穷）（v0.23；codegen 直发 cvt 种类 26，不占 BuiltinId） | LANG-27 |
| `handleBits` | `(H) int` | 句柄的数值（v0.23，§6.8-10；codegen 直发，不占 BuiltinId） | LANG-27 |
| `handleFromBits` | `handleFromBits<H>(整数) H` | 由数值构造句柄（哨兵值如 `INVALID_HANDLE_VALUE`）；类型实参必须写出，实参为能无损扩宽到 `int` 的整数（v0.23，§6.8-10；不占 BuiltinId） | LANG-27 |
| `ffiLastError` | `() int` | 最近一次库函数调用后保存的系统错误码（Windows `GetLastError`、Linux errno；`from syscall` 不写它）（v0.23，§6.8-15；BuiltinId 60） | LANG-27 |
| `taskCancel` | `(Future<T>) void` | 协作式取消（§6.12-6；未启动/挂起任务当帧释放，已完成空操作） | LANG-10／RT-07 |
| `taskCancelled` | `() bool` | 当前任务取消旗标查询（协作检查点；main 同步体恒 false） | LANG-10／RT-07 |
| `taskAwaitAny` | `(List<Future<T>>) int \| Error` | 任一完成/取消→插入序最先者下标；全未决→挂起观察（§6.12-7） | LANG-10／RT-07 |
| `genNext` | `(Gen<T>) T?` | 取生成器的下一个产出值；已结束得 `None`，之后恒为 `None`；生成器运行中再取值 trap 4017（v0.22，§6.20-3；方法语法 `g.next()`；codegen 直发 `gen_next`，不占 BuiltinId） | LANG-26 |
| `listDir` | `(string) List<string> \| Error` | 列出目录里的条目名：不含 `.` 与 `..`，按字节序排序，不带条目类型（类型用 `entryKind` 取）；打不开得 `Error`（消息为“原因: 路径”），空路径得 invalid argument（V0.45 起，v0.30 补记） | BOOT-38 |
| `entryKind` | `(string) int` | 条目类型，不跟随符号链接：`0` 不可判（不存在、无权限、空路径；Linux 上 FIFO、设备、套接字也是 0）、`1` 文件、`2` 目录、`3` 符号链接（Windows 上为重解析点）（V0.47 起，v0.30 补记） | BOOT-42 |
| `writeFile` | `(string, string) void \| Error` | 写文本文件（覆盖写；UTF-8 字节直写）——readFile 对偶（V0.8-S1 增，D27 §一） | STD-03 |
| `writeFileBytes` | `(string, List<int>) void \| Error` | 写二进制文件（覆盖写）：元素逐字节直写，须 int 且 0..255——越界/非 int 得结构化 `Error`（校验先于写出，无部分写）；.tx 无 ≥0x80 单字节串构造通道的二进制写出承接（v0.16 增，LANG-17；P-1 小合同 selfhost-delimitation-v050 §三；readFile+byteAt 读侧对偶） | BOOT-47 |
| `bytesNew` | `(int) Bytes` | 长度 n 的全零 Bytes；n<0 trap（v0.18） | LANG-19 |
| `bytesLen` | `(Bytes) int` | 长度（`len(b)` 同义；方法 `b.len()`）（v0.18） | LANG-19 |
| `bytesGet` | `(Bytes, int) int?` | 读字节；越界 `None`（v0.18） | LANG-19 |
| `bytesSet` | `(Bytes, int, int) void` | 写字节；越界或值不在 0..255 trap（v0.18） | LANG-19 |
| `bytesPush` | `(Bytes, int) void` | 尾追加一字节；值不在 0..255 trap（v0.18） | LANG-19 |
| `bytesAppend` | `(Bytes, Bytes) void` | 把第二个的内容追加到第一个（可为同一对象）（v0.18） | LANG-19 |
| `bytesSlice` | `(Bytes, int, int) Bytes` | `bytesSlice(b, from, count)` 拷贝出新对象；越界 trap（v0.18） | LANG-19 |
| `bytesEqual` | `(Bytes, Bytes) bool` | 内容逐字节相等（v0.18） | LANG-19 |
| `bytesFromStr` | `(string) Bytes` | 字符串的 UTF-8 字节拷贝（v0.18） | LANG-19 |
| `bytesToStr` | `(Bytes) string \| Error` | 严格 UTF-8 校验后构造字符串（v0.18） | LANG-19 |
| `readFileBytes` | `(string) Bytes \| Error` | 读二进制文件（v0.18） | LANG-19 |
| `embedBytes` | `(字符串字面量) Bytes` | 编译期嵌入文件，见 §6.15-4；不占 BuiltinId（v0.18） | LANG-19 |
| `bytesAsList` | `(Bytes) List<u8>` | 零拷贝把 `Bytes` 当作 `List<u8>` 用：只改静态类型，返回同一个对象，经任一侧的修改（含 `listPop` 缩短长度）两侧互见；实参不是 `Bytes` 报 3002（v0.26，§6.15-7） | LANG-28 |
| `listAsBytes` | `(List<u8>) Bytes` | 零拷贝把 `List<u8>` 当作 `Bytes` 用：返回同一个对象；实参不是 `List<u8>` 报 3002，运行期核对不是紧凑的 `List<u8>` 时 trap 4010（v0.26，§6.15-7） | LANG-28 |
| `bytesCopy` | `(Bytes, int, Bytes, int, int) void` | `bytesCopy(dst, at, src, from, count)` 区间拷贝，同一对象、区间重叠均可（memmove）；位置或个数为负、越界 trap 4002，写冻结 trap 4022（v0.37，§6.15-8）。BuiltinId 77 | LANG-32 |
| `bytesFill` | `(Bytes, int, int, int) void` | `bytesFill(b, at, count, value)` 区间填充；越界或值不在 0..255 trap 4002，写冻结 trap 4022（v0.37）。BuiltinId 78 | LANG-32 |
| `bytesGetLE` | `bytesGetLE<T>(Bytes, int) T?` | 按小端读定宽数值（`T` 为 `i8`…`u64`、`int`、`f32`、`float`，须写出）；越界得 `None`（v0.37）。BuiltinId 79 | LANG-32 |
| `bytesGetBE` | `bytesGetBE<T>(Bytes, int) T?` | 按大端读，同 `bytesGetLE`（v0.37）。BuiltinId 79 | LANG-32 |
| `bytesSetLE` | `bytesSetLE<T>(Bytes, int, T) void` | 按小端写定宽数值，值须正是 `T`；越界 trap 4002，写冻结 trap 4022（v0.37）。BuiltinId 80 | LANG-32 |
| `bytesSetBE` | `bytesSetBE<T>(Bytes, int, T) void` | 按大端写，同 `bytesSetLE`（v0.37）。BuiltinId 80 | LANG-32 |
| `errAs` | `errAs<E>(Error) E?` | 取回错误 enum 种类：带 `E` 种类得该值，否则 `None`；类型实参必须显式写出（v0.19，§6.17-6） | LANG-21 |
| `errWrap` | `(Error, string) Error` | 包一层上下文，结果的 `cause` 为原错误（v0.19，§6.17-6） | LANG-21 |
| `makeDir` | `(string) void \| Error` | 创建单层目录（v0.17 增）：权限 `0777 & ~umask`（POSIX）；**已存在的目录视为成功**（幂等）；同名非目录条目得 `Error "invalid argument: <path>"`，父目录缺失得 `"no such file: <path>"`，无权限 `"access denied: <path>"`，空路径 `"invalid argument: "`；递归创建由标准库 `makeDirAll` 提供 | LANG-18 |
| `writeExecutable` | `(string, List<int>) void \| Error` | **工具链专用**（v0.17 增）：写出可执行文件——元素规则同 `writeFileBytes`（非 int 得 `"writeExecutable: 元素 -1 不是 int"`，越界得 `"writeExecutable: 元素 {v} 越界（须 0..255）"`；校验先于写出）；POSIX 先删同名旧文件再以 `0777 & ~umask` 新建（与 `ld` 一致），Windows 同 `writeFileBytes`。**语义检查默认拒绝**（3001 `writeExecutable 仅限工具链构建（需 --toolchain）`），仅编译选项 `--toolchain` 放行；编译器写出原生产物时使用 | LANG-18 |
| `hostTarget` | `() string` | **工具链专用**（v0.21 增，下同；未带 `--toolchain` 以 3001 `X 仅限工具链构建（需 --toolchain）` 拒绝）：程序的编译目标平台 `"windows"`／`"linux"`（由产物的目标决定，运行时不探测；macOS 产物在 macOS 适配 M1 实施后为 `"macos"`） | TOOL-81 |
| `hostArch` | `() string` | **工具链专用**（v0.35 增）：程序的编译目标架构 `"x86_64"` 或 `"aarch64"`（同 `hostTarget`，由产物的目标决定） | macOS 适配 M0b |
| `getEnv` | `(string) string?` | 读环境变量；未设置或名字不合法（空、含 `=` 或 NUL）得 `None`（Windows `GetEnvironmentVariableW`；Linux 读进程级环境表——初值为启动时的 envp，`std/os` 的 `setenv` 写入它；v0.24 起，原为只读启动时的 envp）；值中的非法 UTF-8 逐字节替换为 U+FFFD | TOOL-81 |
| `setEnv` | `(string, string) void \| Error` | **工具链专用**（v0.42 增）：设置环境变量，写 `getEnv` 读的同一张表（Windows `SetEnvironmentVariableW`；类 Unix 目标为运行时持锁的进程级表，同 `std/os` 的 `setenv`），此后起的子进程带上（v0.39）；名字为空、含 `=` 或 NUL，或值含 NUL 时得 `Error`（`invalid argument: <名字>`）；空值即设为空串（不是删除）；经外部函数调用的 C 代码在类 Unix 目标上用 `getenv` 看不到改动 | v052-gate-tx |
| `runProcess` | `(string, List<string>, string) int \| Error` | 启动子进程并等待结束：第一实参为程序路径，第二实参为参数（原样传递——Windows 按 MSVC 规则加引号，Linux `execve`），继承 stdin/stdout（父进程关着的子进程里也关着），此外本进程的文件、套接字、管道等一律不进子进程（v0.40：不论是否设了 CLOEXEC 或可继承标志，含经外部函数打开的），子进程的环境为本进程当前的环境（v0.39：类 Unix 目标取进程级环境表，含 `std/os` 的 `setenv`；Windows 为进程环境块）；第三实参非空时 stderr 重定向到该文件（新建或覆盖），空串表示继承。返回退出码（Windows 为进程退出码，含异常码；Linux 被信号终止得负的信号号）；程序不存在等启动失败得 `Error` | TOOL-81 |
| `tempDir` | `() string` | 临时目录（Windows `GetTempPathW`，去掉末尾分隔符、保留盘符根；类 Unix `$TMPDIR`，去掉末尾的 `/`、只剩根时保留（v0.35 补记 M3a；此前 Linux 原样返回），缺省 `/tmp`） | TOOL-81 |
| `removeFile` | `(string) void \| Error` | 删除文件；不存在或无权限得 `Error` | TOOL-81 |
| `selfExePath` | `() string` | 当前可执行文件的路径（Windows `GetModuleFileNameW`；Linux `/proc/self/exe`） | TOOL-81 |
| `monoNs` | `() int` | 单调时钟纳秒（起点任意，只用于计时；断言面禁用） | TOOL-81 |
| `stdinRead` | `(Bytes, int) int` | **工具链专用**（v0.27 增，下同；未带 `--toolchain` 以 3001 `X 仅限工具链构建（需 --toolchain）` 拒绝；用到它们的程序只能原生构建，字节码构建报错）：把本进程标准输入读进缓冲（至多 `len(buf)` 字节），返回读到的字节数；等满第二实参的毫秒数仍无数据得 0，到流尾得 −1；毫秒为负一直等、为 0 不等；缓冲长度为 0 时得 0。标准输入是控制台时按行读（就绪后等到整行）。缓冲是冻结的 `Bytes`（§6.22-8）时 trap 4022 | TOOL-103 |
| `stdoutWrite` | `(Bytes) int` | 把数据原样写到本进程标准输出（不补换行），写完才返回；返回字节数，读端已关闭得 −1（Linux、macOS 都不因 SIGPIPE 结束进程）。标准输出是控制台时同 `print` 按 UTF-16 写出 | TOOL-103 |
| `procSpawn` | `(string, List<string>, string, int) int \| Error` | 起子进程，返回句柄（正整数）：第一实参为程序路径（不经 shell、不按 PATH 查找），第二实参为参数（原样传递，规则同 `runProcess`），第三实参为工作目录（空串继承），第四实参为选项位——位 0 stdin 走管道（否则接空设备），位 1 建调试控制通道（其余位 trap 4010）。stdout、stderr 恒走管道；子进程只得到这三端与调试控制通道的两端，本进程其余描述符／句柄一律不继承（v0.40，同 `runProcess`）。子进程环境为本进程当前的环境（v0.39，同 `runProcess`）去掉 `TAIXU_DEBUG_CHANNEL`，建控制通道时换成本次的。启动失败得 `Error`：`no such file: <程序路径>`、`access denied: …`；程序路径为空、程序路径或参数含 NUL、不是可执行程序得 `invalid argument: <程序路径>`；工作目录无效或含 NUL 时报该目录；同时存在的子进程已满（附录 A）得 `io error: <程序路径>` | TOOL-103 |
| `procTryWrite` | `(int, int, Bytes) int` | 单次非阻塞写子进程 stdin（流 0）或控制通道（流 3）；实际已写字节、暂不可写／中断为 0，关闭或错误为 −1；每次最多 4096 字节。得 −1 时该流已被关闭，此后对它恒为 −1（同 `procWrite`）；Linux 写入后恢复文件状态标志或信号掩码失败也得 −1，这时可能已有字节写出，只能把该流当作已断开。句柄规则与 procWrite 相同；Windows 管道写端第一次成功切成非阻塞后保持非阻塞（`procWrite` 写之前切回阻塞），管道仍有未读数据、尚未切成非阻塞时得 0（未尝试写入） | TOOL-103 |
| `procWrite` | `(int, int, Bytes) int` | 把数据全部写入子进程的 stdin（流 0）或控制通道（流 3），写完才返回；返回字节数，对端已关闭或该流不存在得 −1（此后恒为 −1） | TOOL-103 |
| `procRead` | `(int, int, Bytes) int` | 不阻塞地读子进程的 stdout（流 1）、stderr（流 2）或控制通道（流 3）进缓冲；返回字节数，暂无数据得 0，流尾或该流不存在得 −1（此后恒为 −1）。缓冲是冻结的 `Bytes` 时 trap 4022 | TOOL-103 |
| `ioWait` | `(int, int, int) int` | 等第二实参（位集合）选中的来源之一可读或到流尾：位 0 本进程 stdin，位 1–3 句柄的 stdout、stderr、控制通道（只选位 0 时句柄可为 0；位集合超出 0..15 trap 4010）。返回就绪位的集合，等满第三实参的毫秒数得 0（负数一直等）；已读到流尾的流不计，选中的都已读完时立即得 0 | TOOL-103 |
| `procKill` | `(int) void` | 强制结束子进程（Windows 退出码 1；Linux SIGKILL，`procWait` 得 −9）；已退出时无动作 | TOOL-103 |
| `procWait` | `(int, int) int?` | 等子进程退出，返回退出码（同 `runProcess`：Linux 被信号终止为负的信号号）；Linux 子进程已被别处回收、退出状态不可知时得 −256（不是信号号，`procSpawnLimited` 的组内后代未确认清理）；等满毫秒数仍未退出得 `None`，负数一直等；可重复调用 | TOOL-103 |
| `procClose` | `(int) void` | 子进程仍在运行时先强制结束并回收，再关闭全部管道、释放句柄 | TOOL-103 |
| `currentDir` | `() string \| Error` | **工具链专用**（v0.28 增；未带 `--toolchain` 以 3001 拒绝，只能原生构建）：本进程当前工作目录的绝对路径（Windows `GetCurrentDirectoryW`，Linux `getcwd`）；不合法的 UTF-8 字节换成 U+FFFD。当前目录已被删除等失败得 `Error`：`no such file: .`、`access denied: .`、`out of memory: .`、`io error: .` | TOOL-103 |
| `fileSize` | `(string) int \| Error` | **工具链专用**（v0.29 增；未带 `--toolchain` 以 3001 拒绝，只能原生构建）：普通文件的字节数，只取元数据、不读内容；跟随符号链接（Linux `newfstatat`；Windows `CreateFileW` 以访问掩码 0 打开，`GetFileType` 判磁盘文件后 `GetFileInformationByHandle`）。失败得 `Error`，文案同 `readFile`：目录、设备等非普通文件与含 NUL 的路径得 `invalid argument: 路径`，不存在（含悬空链接）得 `no such file: 路径`，另有 `access denied`、`out of memory`、`io error` | TOOL-104 |
| `procSpawnLimited` | `(string, List<string>, string, int, int, int) int \| Error` | **工具链专用**（v0.32）：前四参同 procSpawn；内存字节数与 CPU 秒数均为正数，CPU 秒数≤922337203685，非法数值 trap 4010。Windows 创建挂起进程，成功设置并加入 Job 后才恢复：Job 总提交及逐进程提交上限、逐进程及 Job 累计**用户态** CPU 上限（`PerProcessUserTimeLimit`／`PerJobUserTimeLimit`，内核态时间不计）、最多 32 个活动进程、不允许脱离 Job、关闭 Job 终止其中全部进程；Linux 在 exec 前 `setpgid(0, 0)` 自成进程组，并设置继承的 RLIMIT_AS/RLIMIT_CPU（hard=soft；CPU 计用户态与内核态）与 RLIMIT_CORE=0——AS/CPU 是逐进程限制，每个后代各有一份，不是进程组总额；不限进程数、不设 PDEATHSIG（本进程异常退出时子进程不随之结束）；子进程不在终端的前台进程组，收不到终端发出的 Ctrl-C。macOS（v0.35 补记 M3a）：fork 后同 Linux 自成进程组并设 RLIMIT_CPU（超限 SIGXCPU，`procWait` 得 −24）与 RLIMIT_CORE=0；没有可用的地址空间限额，内存由全进程一个看守线程每 50 ms 按进程组累加物理足迹，超过即整组 SIGKILL（`procWait` 得 −9）——事后发现、峰值可短暂超出，不是分配失败；清理同 Linux；子进程环境另带回收 cookie `TAIXU_REAP_COOKIE`（v0.35 补记 M3b，宿主服务用来找回脱离进程树的后代）。设置失败返回 Error，绝不执行。procKill/procClose：Windows 终止整个 Job；Linux 在主进程尚未被回收时向它的进程组发 SIGKILL，procWait 回收已退出的主进程之前也先按组清理（以 WNOWAIT 保留主进程，避免 PID 复用误杀）；已用 `setsid`／`setpgid` 离开该进程组的后代不在清理范围内。**资源限额不是不可信代码隔离** | 安全边界 |
| `readLocalFileBytes` | `(string, string, int) Bytes \| Error` | **工具链专用**（v0.32）：本地根绝对路径、根内相对路径、最大内容字节数。按已验证句柄读取普通文件，拒绝符号链接、重解析／offline／recall 对象、绝对或 .. 相对路径、NUL、ADS 和超限／读取中增长；不支持的系统机制失败关闭，不回退普通读取。零长度返回空 Bytes，二进制无需 UTF-8；最大内容须在 0..INT_MAX−1，非法数值得 Error invalid argument。Windows 只接受盘符映射到本地硬盘卷（`\Device\HarddiskVolumeN`）的根，网络映射盘与 SUBST 拒绝，直接锚定该卷并逐组件持有句柄；Linux 根以 openat2 NO_SYMLINKS/NO_MAGICLINKS 打开，根内相对路径加 BENEATH/NO_XDEV（不跨挂载点）。原语本身不检查文件系统类型（Linux 的根可以位于网络或 FUSE 挂载上），显式根及祖先的挂载类型由调用服务启动策略核验；硬链接不检测。这些原语自身不代替服务的根授权 | 安全边界 |
| `localEntryKind` | `(string, string) int` | **工具链专用**（v0.32）：同一本地根／相对路径和句柄检查，0 拒绝／不存在／无法判定、1 普通文件、2 目录；不读取内容。路径字节数分别≤32760（还须满足宿主自身上限） | 安全边界 |
| `localFileSize` | `(string, string) int \| Error` | **工具链专用**（v0.32）：同一本地根／相对路径和句柄检查，仅查询普通文件大小，不读取内容；目录或拒绝时 Error | 安全边界 |
| `localListDir` | `(string, string) List<string> \| Error` | **工具链专用**（v0.35）：同一本地根／相对路径和句柄检查，列出目录项名（按字节排序，不含 `.`、`..`；含链接本身的名字但不跟随），相对路径为空时列根本身；不是目录、链接或重解析点、根外或无法核验时 Error。服务模式据此核对导入写法与磁盘是否一致（§6.1-4） | 安全边界 |
| `nowMs` | `() int` | 墙钟毫秒（宿主面观测）；**确定性纪律：e2e/教程断言面禁用**（输出随宿主墙钟变化） | STD-03 |
| `sleepMs` | `(int) Future<void>` | 定时器面（v0.9 增，D29 §一）：建 pending future，到期由调度泵置 done——**deadline 升序、同刻按登记序**（确定性红线时间维延伸）；钟源＝单调钟（墙钟禁用）；`await sleepMs(0)`＝下一调度轮到期（确定边界）；负值＝即 err future（`"sleep ms < 0"` 确定性错误通道）；**断言面只用 0ms/不到期两确定形态**；AOT 随异步面结构化拒绝（BuiltinId 25，D29 §一-4） | RT-08 |
| `assert` | `(bool, string) void` | 失败即测试失败 / 程序 panic | TEST-01 |
| `panic` | `(string) void` | 不可恢复，打印消息并退出非零 | — |
| `threadSpawn` | `(() => T) Thread<T>` | 在新线程里运行无参函数值，捕获深拷贝进新线程（v0.25，§6.22-1）；创建失败不 trap，join 时得错误值。BuiltinId 61 | LANG-30 |
| `threadJoin` | `(Thread<T>) T \| Error` | 等线程结束并取回结果（深拷贝回调用线程）；第二次 join 得错误值。BuiltinId 62 | LANG-30 |
| `sharedNew` | `(T) Shared<T>` | 对象图深拷贝进共享区（§6.22-7）；实参须可共享（3035；经函数值、接口值带入的 trap 4021）。BuiltinId 63 | LANG-30 |
| `sharedGet` | `(Shared<T>) T` | 取出冻结的对象图；值类型的根交一份拷贝（§6.22-8、§6.22-9）。BuiltinId 64 | LANG-30 |
| `atomicNew` | `(int) Atomic<int>` | 新的原子整数（§6.22-10）。BuiltinId 65 | LANG-30 |
| `atomicLoad` | `(Atomic<int>) int` | 读（seqcst）。BuiltinId 66 | LANG-30 |
| `atomicStore` | `(Atomic<int>, int) void` | 写（seqcst）。BuiltinId 67 | LANG-30 |
| `atomicAdd` | `(Atomic<int>, int) int` | 加并返回旧值（seqcst）；溢出 trap 4004、单元不变。BuiltinId 68 | LANG-30 |
| `atomicCas` | `(Atomic<int>, int, int) bool` | 当前值等于第二实参时换成第三实参，返回是否换成（seqcst）。BuiltinId 69 | LANG-30 |

**交给系统的串含 NUL（v0.35 安全修正）**：把串交给操作系统作路径、程序路径或进程实参的内建（`readFile`、`readFileBytes`、`writeFile`、`writeFileBytes`、`writeExecutable`、`makeDir`、`removeFile`、`listDir`、`runProcess` 及上表注明的工具链内建），串内含 NUL 字节时一律得 `Error "invalid argument: <路径>"`（`runProcess` 报程序路径），`entryKind` 得 `0`——不截断后照常操作（系统在 NUL 处截断，看到的是另一个路径）。同 Go 的 EINVAL、Rust 的 `NulError`、Python 的 ValueError。

**工具链进程句柄（v0.27）**：`procSpawn` 返回的句柄只在创建它的线程里有效。`procClose` 之后的、从未有效的、属于别的线程的句柄，以及不对的流号，在 `procWrite`、`procTryWrite`、`procRead`、`ioWait`、`procKill`、`procWait`、`procClose` 里 trap 4010（`bad_type`，文案写明原因）。没有 `procClose` 的句柄在进程退出时由操作系统回收。管道两端都要排干：子进程写满 stdout 或 stderr 的管道后会阻塞，调用方应以 `ioWait` 同时等两路。

**标准库内部内建（v0.24 补）**：以 `__rt` 开头的内建（如 `__rtHandleAdd`、`__rtEnvGet`、`__rtArgAt`）是原生运行时给标准库用的内部接口，只有标准库模块（模块文件在标准库根之下）可以调用；其余模块调用报 3001（文案同未定义函数并注明原因），用户声明的同名函数照常遮蔽。它们不属于语言面，签名与编号随实现变化，不列入上表（见 `docs/design/v052-threads-v0.1-20261015.md` §十六）。

`test "名称" { ... }` 块由 `taixu test` 枚举运行（TEST-01）。v0.21：原生 `taixu test` 每个 test 块在独立进程中运行（模块初始化每个 test 重跑，与 Zig CLI 每个 test 新建 VM 等效），trap 只判该 test 失败。

### 6.13 位运算（v0.14 新增；D3 v0.14；LANG-15）

1. **运算符与类型面**：双目 `&`（位与）、`|`（位或）、`^`（位异或）、`<<`（左移）、`>>`（**算术**右移——符号扩展）、`>>>`（**逻辑**右移——高位补零，hash/加密用例，TS 形态）；一元 `~`（位非）。操作数**恒 int**（64 位二补码）——float/bool 结构化拒绝（3002，无隐式转换、无 JS 掩码）；结果恒 int。**v0.26**：定宽整数的位运算与移位见 §6.21-15、§6.21-16（结果取公共类型或左侧类型，移位计数须在 0..宽度−1）。
2. **移位计数规则**：右操作数（移位计数）须落 **0..63**——界外（含负数）即结构化 trap `shift_count`（RuntimeCode 4016），**非 JS 掩码**（`x << (n & 63)` 的隐藏控制流永不发生——无隐藏控制流纪律 §2-4）。
3. **语义分立**：`*` 溢出 trap 不变；环绕乘经内建 `imul` 显式选入（§7——**64 位环绕语义**，`Math.imul` 仅借形态、其 32 位语义不继承）。`&`/`|`/`^`/`~`/移位无溢出面。
4. **VM/AOT 面**：新 opcode band=40/bor=41/bxor=42/bnot=43/shl=44/shr=45/sar=46/imul=47（追加编码值＝旧产物不含先例第 10 批）；AOT 双面发射（x86-64 基线指令内联直译＋交叉后端 riscv64/loongarch64 shim 慢路 `__tx_arith_slow`/`__tx_bnot`——语义单一事实源）；解释器/直译/慢路三路 trap 码与消息逐项同源。
5. **优先级（TS 同构）**：移位紧于比较、`&` > `^` > `|` 居比较与 `&&` 之间（§5 EBNF 链）——`1 << 2 < 5` ≡ `(1 << 2) < 5`、`2 + 3 << 1` ≡ `(2 + 3) << 1`。

### 6.15 Bytes 与编译期嵌入（v0.18 新增；LANG-19）

1. **表示**：`Bytes` 是 RC 堆对象，句柄语义；`Bytes` 自身的内建只增不减长度（无 pop/truncate）。`bytesXxx` 与 `listXxx` 语义一一对应，不得分叉。**v0.26 修订**（原文“将来引入定宽整数时，`List<u8>` 采用同一紧凑存储，`Bytes` 成为其别名”；定宽数值设计 §九-7 按推荐裁决为不做别名）：`Bytes` 与 `List<u8>` 是**两个类型**——`Bytes` 的接口照旧以 `int` 读写（`bytesGet` 得 `int?`，`bytesSet`/`bytesPush` 检查 0..255），`List<u8>` 的元素是 `u8`；运行期两者是同一种对象形态，可零拷贝互转（第 7 条）。做成真别名会让 `bytesGet` 改返回 `u8?`，现有代码里取出的字节相加就从 `int` 运算变成 `u8` 运算、超过 255 即 trap，故不采用。
2. **越界与取值**：`bytesGet` 越界得 `None`（同 `listGet`）；`bytesSet` 越界 trap（越界码 4002，同 `listSet`）；写入值不在 0..255 trap；`bytesNew` 长度为负 trap；`bytesSlice` 区间越界 trap。
3. **字符串互转**：`bytesFromStr` 拷贝字符串的 UTF-8 字节；`bytesToStr` 严格校验 UTF-8（拒绝过长编码、代理区与 > U+10FFFF），非法得 `Error "bytesToStr: 不是合法的 UTF-8"`。string 保持“合法 UTF-8”不变量，不提供原始字节构造字符串的通道。
4. **`embedBytes("路径")`**：参数必须是字符串字面量（否则 3002）；路径相对**调用所在源文件**的目录解析，也可为绝对路径；编译期读取，内容进入产物只读数据；文件不存在、不可读或超过 64MB 即编译错误 **3102**，不生成产物。每次求值返回一个**新的** `Bytes`（由只读数据拷贝），因为 Bytes 可变。
5. **文件**：`readFileBytes(path)` 读二进制文件得 `Bytes | Error`；`writeFileBytes`／`writeExecutable` 的内容参数同时接受 `List<int>` 与 `Bytes`（`Bytes` 直接写出，无逐元素检查）。
6. **零扰动**：不用本版新增面的程序零扰动；`.txc` 格式版本与金哈希不变。
7. **与 `List<u8>` 互转（v0.26 新增；LANG-28）**：`bytesAsList(b: Bytes): List<u8>` 与 `listAsBytes(l: List<u8>): Bytes` 只改静态类型，返回**同一个对象**（引用计数加一）——不是拷贝，也不是共用一块缓冲的两个对象。两边都是句柄语义：经任一侧的写入、追加、扩容与 `listPop`/`listRemoveAt`（`List<u8>` 一侧可以缩短长度）对另一侧立即可见；冻结（§6.22-8）两侧一致；放掉一侧不影响另一侧。经 `Bytes` 一侧读写照旧按 `int`（`bytesGet` 得 `int?`，`bytesSet`/`bytesPush` 检查 0..255），经 `List<u8>` 一侧按 `u8`。需要独立的副本时用 `bytesSlice(b, 0, len(b))`，或 `listNew` 后逐个推入。只收 `Bytes` 的位置（`writeFileBytes` 的内容参数、外部函数的 `Bytes` 形参、`bytesXxx` 内建）传 `List<u8>` 时先经 `listAsBytes`；要按 `u8` 逐个迭代 `Bytes` 用 `for (x in bytesAsList(b))`（`Bytes` 本身不可 for-in）。实参类型不符报 3002：`bytesAsList` 只收 `Bytes`，`listAsBytes` 只收 `List<u8>`（`List<int>` 等须逐个转换）。`listAsBytes` 在运行期再核对实参确是紧凑存放的 `List<u8>`（§6.21-22），否则 trap 4010 `bad_type`——静态类型为 `List<u8>` 的列表总是紧凑的，正常代码不会触发。
8. **批量操作（v0.37 新增；LANG-32）**：区间写法同 `bytesSlice(b, from, count)`，目的在前（同 C、Go、Rust）。
   - `bytesCopy(dst, at, src, from, count)`：把 `src` 的 `[from, from+count)` 拷到 `dst` 的 `[at, at+count)`；`dst` 与 `src` 可为同一对象，区间重叠时结果如同先拷出再写入。任一位置或个数为负、区间超出长度 trap 4002——不截断（Go 的 `copy` 静默取较短者，会把越界藏起来）。
   - `bytesFill(b, at, count, value)`：区间内每个字节写 `value`；越界或 `value` 不在 0..255 trap 4002。
   - `bytesGetLE<T>(b, at): T?` 与 `bytesGetBE<T>(b, at): T?`：从 `at` 起按小端／大端读 `T` 宽度的字节；`at` 为负或 `at + 宽度` 超出长度得 `None`（同 `bytesGet`，解析不可信输入时 `catch` 给缺省值即可）。有符号类型按宽度符号扩展；`f32`、`float` 按 IEEE 754 位型。
   - `bytesSetLE<T>(b, at, v)` 与 `bytesSetBE<T>(b, at, v)`：按小端／大端写 `v` 的 `T` 宽度字节；越界 trap 4002。
   - `T` 为 `i8 i16 i32 u8 u16 u32 u64`、`int`、`f32`、`float`，须显式写出（端序与宽度都不设缺省：DataView 缺省大端、字面量缺省 `int` 写 8 字节都是常见错）；`isize`／`usize` 不收——宽度随目标指针宽度而变，二进制格式须用显式宽度。写入的值须正是 `T`（窄类型先 `as`，字面量按 `T` 取型），否则 3002；不写类型实参、类型实参不合规也是 3002。
   - 写入冻结的 `Bytes`（§6.22-8）trap 4022；这些操作都不改变长度，外部调用借用中的 `Bytes`（§6.8-16）照常可写。

### 6.16 defer（v0.19 新增；LANG-20）

1. **语法**：`defer` 后随一个块，或一条表达式语句/赋值（视为只含这一条语句的块）。`defer` 是保留关键字。
2. **块作用域、后进先出**：离开声明 `defer` 的那个块时执行；同一块内多个 `defer` 按声明的逆序执行。离开多层块时由内向外逐层执行。
3. **循环**：循环体是块，**循环体里的 `defer` 在每一轮结束时执行**，不是整个循环结束后才执行——走到循环体末尾、`continue`、`break` 都先执行本轮已登记的 `defer`；C 风格 `for` 中在 `step` 之前执行，`for-in` 在取下一个元素之前执行。写在循环外的 `defer` 在外层块结束时执行一次。只执行已经执行到的 `defer` 语句所登记的。
4. **何时执行**：走到块尾、`return`、`break`/`continue` 离开该块、`?` 传播离开、`catch (e)` 块内 `return`。**何时不执行**：`panic`、`assert` 失败与运行期 trap（进程以非零退出码结束，资源由操作系统回收）。
5. **求值时机**：`defer` 体在离开时才求值，读取变量当时的值。`return expr` 先求出 `expr`，再执行 `defer`，最后返回；`defer` 改局部变量不影响已求出的返回值，经 `Ref` 修改的对象对调用方可见。
6. **体内限制**（编译错误 3026）：不得 `return`；不得用 `break`/`continue` 跳出 `defer` 体；不得使用 `?`（可能失败的调用用 `catch` 就地处理）；不得 `await`（v0.21：取消清理不能再挂起）。`test` 块与 `async` 函数（v0.21，§6.16-8）中可用。
7. **与所有权（D6）**：`defer` 在函数帧释放之前运行，它引用的局部都还活着；对象的死亡时点与 `Weak` 观察结果不变。实现为编译期展开，零新 opcode。
8. **`async` 函数（v0.21 新增；LANG-25）**：正常返回与错误返回同普通函数。任务**挂起中**（`await` 或 `taskAwaitAny` 未决）被 `taskCancel` 取消时，从挂起点按后进先出执行该点已登记的 `defer`，再把任务置为已取消，观察者得 `err("task cancelled")`；已被唤醒、尚在就绪队里的任务被取消同样执行。执行这些 `defer` 时当前任务是被取消者，`taskCancelled()` 为真；`defer` 体内可以再取消别的挂起任务（它的 `defer` 随即执行）。未启动就被取消的任务尚未登记任何 `defer`，不执行。实现：挂起点后的取消落点（编译期展开，零新 opcode），内核以取消模式进入挂起函数。

### 6.17 结构化错误（v0.19 新增；LANG-21）

1. **错误 enum**：`enum 名 extends Error { 变体(字段…) = 消息, … }`。每个变体**必须**给出消息：`"字面量"` 或 `` `模板串` ``；模板只能插值该变体自己的字段（字段类型限 int/float/bool/string/Error/错误 enum，按 `toStr` 插值）。缺消息、插值未知名字或不可转字符串的字段是编译错误 3027。除此之外它是普通 enum：`E.V(…)` 构造（不能 `new`，3027）、可存进变量与 struct 字段、按值比较、`switch` 须穷尽。
2. **失败联合 `T | E`**：失败侧可以是 `Error` 或一个错误 enum；`T` 不能是 `Error`、错误 enum、可选或失败联合（3028）；`T | 名字` 中的名字不是错误 enum 时报 3020。在返回 `T | E` 的函数里 `return E.V(…)` 即返回失败；此时 `return err(…)` 是编译错误 3028（会丢掉种类）。错误 enum 的值可以用在任何需要 `Error` 的位置（传参、赋值、`T | Error` 的失败侧）。
3. **`?` 传播**：被传播值的失败侧 `Es`、本函数的失败侧 `Ef`：`Es == Ef` 直接传播；`Ef` 是 `Error` 时自动放宽（具体种类随值保留，可用 `errAs` 取回）；其他组合是编译错误 3028（改用 `catch` 显式转换，或把返回类型的失败侧改为 `Error`）。不做隐式的跨种类转换。`T | E` 的值也可以直接赋给 `T | Error`。
4. **`catch`**：作用在 `T | E` 上时 `catch (e)` 的 `e` 类型为 `E`，可以 `switch` 穷尽处理；默认值形式沿用 §6.3-3（F 为 `None` 得 `T?`，F 为 `err(…)` 得 `T | Error`，F 为错误 enum `E2` 的值得 `T | E2`；`E?` 上的 `catch` 若 F 与成功侧同为 `E` 则只是取默认值）。
5. **message 与 cause**：错误 enum 值与 `Error` 都有 `.message`（错误 enum 为该变体消息渲染出的字符串）。变体中名为 `cause`、类型为 `Error`（或错误 enum）的字段是这个错误的原因；`e.cause`（`e` 为 `Error` 或错误 enum）类型为 `Error?`，没有原因时为 `None`。原因链在构造时一次成形、之后不可变。
6. **`errAs<E>(e: Error): E?`**：类型实参必须显式写出且为错误 enum（3029）；`e` 带有 `E` 种类时得到该值，否则得到 `None`。**`errWrap(cause: Error, message: string): Error`**：给普通错误包一层上下文，结果的 `cause` 为原错误。
7. **`toStr`**：普通 `err(…)` 仍为 `"Error: " + message`；错误 enum 值为 `"E.V: " + message`；有原因时依次追加 `"；原因：" + toStr(原因)`（`errWrap` 的结果以 `"Error: " + message` 开头）。
8. **相等**：错误 enum 之间按值比较（同普通 enum）；`Error` 之间仍按 message 比较（D3 §6.3-5 不变），放宽成 `Error` 后的错误也按 message 比较。
9. **`main`**：允许声明为 `function main(args: []string): int | Error`（失败侧也可以是错误 enum）。返回失败时把 `toStr(e)` 写到 stderr，退出码 1。
10. **`async`**：`async` 函数的失败侧暂只能是 `Error`（3028）。v0.21 注记：原生 async 已落地，但取消产生的是普通 `Error`（`err("task cancelled")`），放不进 `T | E`；错误 enum 失败侧须与取消语义一起设计，暂不支持。
11. **运行期表示**：失败值一律是 Error 对象；错误 enum 值是在构造时一次成形的“富 Error 对象”（message、种类名、类型号、变体载荷、原因），流入 `Error`、`?` 放宽、`catch` 绑定都不需要转换。不用本版新增面的程序零扰动。

### 6.18 接口与泛型约束（v0.20 新增；LANG-22）

1. **声明**：`interface 名<型参>? { 方法名(参数: 类型, …): 返回类型; … }`。成员只有方法签名，不写接收者；可 `export`；可带类型参数（类型参数暂不能带约束，3030）；方法名不得重复（3003）。`interface` 是保留关键字。
2. **实现关系（结构化）**：类型 `T` 实现接口 `I`，当且仅当对 `I` 的每个方法 `m(p1, …): R`，存在按方法语法约定名（§6.10-1：`T` 的类型名首字母小写＋方法名首字母大写；泛型 struct 实例取模板名）命名的函数，其首参类型与 `T` 相同，其余形参与返回类型逐个与 `I` 的声明相同；泛型函数按这个签名推断实例化。不写 `implements`。
   - **在哪里找**：先在检查发生处的模块作用域；找不到再到 `T` 的声明模块（struct/enum 的声明模块；内建类型的声明模块是 prelude，§6.19）。在声明模块里找时不要求 `export`——方法随类型。
   - 接口类型本身实现与它方法同名同签名的接口（因此 `T extends Shape` 可取 `T = Shape`）。
3. **接口值**：接口名可作普通类型（变量、形参、返回值、struct 字段、List 元素、可选与失败联合的成功侧）。在期望类型为接口 `I` 的位置（绑定标注、赋值、返回、实参、struct 字段、`listPush`/`listSet`/`listInsert`、闭包体）放入实现 `I` 的类型 `T` 的值时自动装入；`T` 不实现 `I` 时报 3030，指出缺的方法或签名差异。不同接口之间不转换。
4. **句柄语义**：接口值同 `Ref`、函数值，赋值与传参共享同一个对象。`T` 是值类型（struct/enum/定长数组）时在装入那一刻拷贝一份，之后与原值无关。接口值不能 `==`/`!=`（3030）、不能 `switch`、不能取字段、不能再包 `Ref`/`Weak`（3030）；本版不提供取回具体类型的手段。
5. **调用**：`v.m(args)` 动态分派到装入时确定的实现函数，实参按接口方法签名检查；接口里没有的方法报 3030。
6. **泛型约束**：`function f<T extends I>(…)`、`struct S<T extends I> {…}`。每个类型参数至多一个约束（需要多个时声明一个合并接口），约束必须是接口（3030），可以引用类型参数自身（`T extends Comparable<T>`）。
   - **调用点**：实例化前检查类型实参是否实现约束，不满足报 3030 并指出缺的方法；泛型 struct 实例化同样检查。
   - **定义处**：带约束的模板在定义处检查一次，与是否有人实例化无关。全部类型参数视为不透明类型：带约束的只能调用约束接口里的方法；所有类型参数都只能赋值、传参、作返回值、存进 `List` 或 struct 字段、`==`/`!=`。做其他运算（算术、取字段、调用约束之外的方法、传给要求具体类型的位置）报 3031，位置在模板源码处。定义处已报错的模板，其实例体不再重复报告。
   - 实例化时仍按实例检查一遍（同 v0.13），所以定义处检查只会多报、不会漏报。完全不带约束的模板保持 v0.13 行为（按实例检查）。
7. **运行期表示**（零新 opcode）：接口值是 `Ref` 指向的隐藏 struct，字段 0 为装入的值，其后依次为各方法的函数值（装入点按 `(T, I)` 一次确定）。调用取出函数值与装入的值，按函数值调用。单态化不变。

### 6.19 编译器自带 prelude：Map 与 Set（v0.20 新增；LANG-24）

1. **prelude**：编译器自带一个用太虚写的模块（仓库 `prelude/collections.tx`，构建编译器时嵌入，同 NRT 内核）。程序的任一模块源码出现它的标志名（`Map<`、`Set<`、`mapNew`、`setNew`、`Hashable`、`intHash` 等）时编译器载入它，它的导出名对每个模块都是预声明标识符；用户声明与显式导入优先（只补缺）。不用 prelude 的程序零扰动。
2. **`Hashable`**：`interface Hashable { hash(): int; }`。prelude 为 `int`、`string`、`bool`、`Bytes` 提供 `intHash`、`stringHash`、`boolHash`、`bytesHash`；`float` 不提供（NaN 与 ±0 不适合作键）。用户类型自己写 `pointHash(p: Point): int`。**v0.26**：prelude 为定宽整数提供 `i8Hash` `i16Hash` `i32Hash` `u8Hash` `u16Hash` `u32Hash` `u64Hash` `isizeHash` `usizeHash`（值转成 `int` 后同 `intHash`；`u64`/`usize` 先 `wrapTo<int>`），定宽整数因此实现 `Hashable`、可作 `Map`/`Set` 的键（方法语法 `x.hash()` 即 `u8Hash(x)` 等）；`f32` 不提供（同 `float`），`Map<f32, V>`、`Set<f32>` 报 3030，文案指出缺少 `f32Hash`。
3. **`Map<K, V>`**：`K` 须实现 `Hashable`（`mapNew` 等处检查，3030）；键的相等用 `==`，因此函数值、`Future`、接口值不能作键。操作：`mapNew(): Map<K, V>`（类型实参经期望类型推断，同 `listNew`）、`mapSet(m, k, v)`、`mapGet(m, k): V?`、`mapHas(m, k): bool`、`mapRemove(m, k): bool`（是否删除了）、`mapLen(m): int`、`mapKeys(m): List<K>`、`mapValues(m): List<V>`、`mapClear(m)`；均可用方法语法（`m.set(k, v)`、`m.get(k)`）。
4. **`Set<T>`**：`T` 须实现 `Hashable`。`setNew()`、`setAdd(s, x): bool`（是否新加入）、`setHas`、`setRemove(s, x): bool`、`setLen`、`setToList(s): List<T>`、`setClear`。
5. **迭代序＝插入序**：`for (k in m)` 迭代键，`for (k, v in m)` 迭代键值对，`for (x in s)` 迭代元素；覆盖已有键不改变位置，删除后再插入排在末尾。
6. **句柄语义**（同 `List`）：赋值与传参共享同一张表。键与值按值语义存入（struct 键、值存拷贝，§6.9-5）。
7. **实现**：插入序条目表＋开放寻址索引表；删除只标记，不存活条目过半时整表重建（被删条目的键与值在重建前仍被表持有）。单态化后无间接调用。
8. **与 stdlib**：`std/map_set` 的非泛型 `Map` 不受影响，导入它的模块照常使用它（显式导入优先）；第 4 轮迁移时统一。

### 6.20 生成器（v0.22 新增；LANG-26）

1. **声明与调用**：`function* f(参数): T { … }` 声明生成器函数，`T` 为**元素类型**（必须写出）。可 `export`、可带类型参数（按普通泛型函数单态化）。`async function*` 是语法错误；箭头与函数表达式形式的生成器没有对应文法。调用 `f(实参)` 求值实参、建生成器对象并返回 `Gen<T>`，**函数体一行都不执行**。生成器函数的类型就是普通函数类型 `(参数) => Gen<T>`：可作函数值传递、经函数值调用、实现接口方法（接口里写 `items(): Gen<int>;`），经任何途径调用都创建生成器——没有 async 那样的着色擦除（§6.12-1）。
2. **`Gen<T>`**：预声明的内建泛型句柄类型（§4.1、§4.2-11），当前模块有名为 `Gen` 的类型时以用户类型为准（v0.17 程序零扰动）。元素类型 `T` 不能是 `void`、可选类型、失败联合、`Error` 与错误 enum（3032）：前几种会让 `genNext` 的 `None` 有歧义，失败侧留给将来“可失败的生成器”（§8），以后放宽是向前兼容的；需要产出错误时包进 struct 或 enum。其余类型都可以（含 `Ref`、`List`、struct、函数值与 `Gen` 本身）。
3. **取值 `genNext(g): T?`**（方法语法 `g.next()` 同义）：
   - 未开始或挂起中：从开头或上次 `yield` 之后继续执行，到下一条 `yield v;` 暂停并得到 `v`；
   - 函数体执行完或遇到 `return;`：生成器结束，得 `None`；之后再取值恒得 `None`；
   - 生成器正在运行时再对它取值或关闭（体内直接或间接对自身取值）：运行期 trap 4017 `gen_running`「生成器运行中不能再取值或关闭」，退出码 3。
   - 与 TS 的差异：结果是 `T?` 而不是 `{ value, done }`（按 TS 习惯写 `r.done` 得类型错误，不会静默出错）；不能给 `next` 传值。
4. **`yield v;`**：只作语句，`v` 的类型须为 `T`；值类型元素产出的是拷贝，生成器之后修改自己的局部不影响已取出的值。只能直接写在生成器函数体内，可以在体内任意嵌套的 `if`/`while`/`for`/for-in 与 `catch` 块里；写在生成器内的箭头函数里报 3032。生成器函数体内（含其中的箭头函数）`yield` 恒为关键字，用作变量、参数等标识符报 3032；生成器之外 `yield` 仍是普通标识符（普通函数与 async 函数里写 `yield x;` 是语法错误）。
5. **for-in 与关闭**：`for (x in g)`，`g` 为 `Gen<T>`，`x` 为 `T`；双变量形态报 3022。解糖为外包一层块：`{ const #it = g; 〈关闭 #it 的出口动作〉; while (true) { 取下一个值，None 即 break；绑定 x；循环体 } }`。**提前离开循环**——`break`、`return`、`?` 传播出循环、所在 async 任务在循环内挂起时被取消（§6.12-6）——**即关闭生成器**，之后对它取值得 `None`（与 TS `for...of` 调用 `return()` 一致）。关闭发生在离开循环的那一刻，不是离开外层块时；`continue` 不关闭；正常取完后关闭是空操作；`panic`、`assert` 失败与运行期 trap 时不执行（同 §6.16-4）。关闭不执行生成器体里的任何代码，只释放它持有的值。出口动作与 `defer` 同样编译期展开，是编译器内部的，不受第 6 条“体内不能 `defer`”的限制。
6. **体内限制**：生成器函数体内不能 `?` 传播（3032；可能失败的调用用 `catch` 就地处理）、`return` 不能带值（3032；用 `return;` 结束）、不能直接调用 `taskAwaitAny`（3032；经其他函数间接调用时按 §6.12-10 在运行期报「await 执行于无任务上下文」）、不能 `await`（3024）、不能 `defer`（3026）。生成器里可以调用普通函数、创建与迭代别的生成器、递归调用自身（得到新的生成器）；async 函数里可以创建生成器并同步取值。
7. **生命周期与所有权（D6）**：生成器对象持有实参与挂起时的全部局部（RC 照常计数）。正常结束、被关闭、或最后一个引用释放（含未开始与挂起中）时释放它持有的值；**丢弃与关闭都不执行生成器体**（TS 对被丢弃的生成器同样不执行 `finally`）。`Gen` 与 `Future` 一样不可发送到其他线程（第 5 轮可发送判定）。
8. **诊断**：3032 统管生成器规则——元素类型、`yield` 的位置与作标识符、`?`、带值 `return`、直接调用 `taskAwaitAny`、`Gen` 的 `==`；`await` 沿用 3024，`defer` 沿用 3026，双变量 for-in 沿用 3022；运行期 4017 `gen_running`。
9. **原生实现**（仅太虚编译器与 NRT；Zig seed 冻结于 v0.17）：生成器函数编译为两个函数——原 fn 表序上的“创建函数”（实参移入生成器对象后返回）与追加的体函数（只经 `gen_next` 进入）。新操作码 48 `gen_new`、49 `yield`、50 `gen_next`、51 `gen_close`；`Gen` 的 `.txc` 类型种类 19；运行期值 tag 18。挂起是单帧快照（与 §6.12-5 同为 stackless）：`yield` 把产出值与整块值槽移入生成器对象（所有权转移，不增减 RC）；`gen_next` 在取值方帧之上开窗（越界 trap 4003）、移入快照并从恢复点继续。SEB1 在程序用到生成器且无诊断时追加 `GEN1` 节（生成器的 fn 表序）；冻结的 Zig CLI 遇 `Gen` 类型或该节即拒绝载入，提示改用原生编译器。不含生成器的程序，程序自身的代码不变。
10. **性能**：每次取值在调用栈窗口与生成器对象之间整块移入、移出值槽（按槽数线性，不增减 RC），适合一般用途；极热路径上的逐元素生成器（例如逐 token 的大型词法器）仍宜手写循环。

### 6.21 定宽数值（v0.23 新增第一步，v0.26 增第二步；LANG-28）

第 1–12 条为第一步（v0.23，第 3、5、8–12 条随 v0.26 修订），第 13–25 条为第二步（v0.26；实施设计见 `docs/design/v052-fixed-width-step2-v0.1-20261016.md`）。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）。**

1. **类型名**：`i8 i16 i32 u8 u16 u32 u64 isize usize f32`；`i64` 是 `int` 的别名、`f64` 是 `float` 的别名（诊断与类型显示写 `int`/`float`）。这些名字在类型位置优先于用户声明（同 `int`/`float`），不能再用作 struct、enum、interface 或类型参数的名字（3034）；作变量名、函数名等不受影响。`i`/`u` 后接数字的其余名字（`i3`、`u17`、`u128`…）整体保留：类型位置报 3034“暂不支持”，同样不能用作类型名（留给以后的 `i128`/`u128` 与位布局字段）。`isize`/`usize` 是独立类型，宽度为目标的指针宽度（现有目标均为 64 位）。
2. **默认不变**：字面量与未标注的推断仍得 `int`/`float`，不写定宽类型的代码零改动。
3. **字面量**：整数字面量有定宽期望类型（含可选、失败联合的内层与 `catch` 默认值）时取该类型，越界报 3034；负号紧贴字面量时与字面量一起取型（`-128` 可为 `i8`；`let f: float = -1` 一类写法随之放宽——`float` 的这一处 v0.23 未实现，v0.26 补上）。超过 i64 最大值的字面量：v0.23 报 1004，写 2^63 及以上的 `u64` 值用 `wrapTo<u64>(…)`（如 `wrapTo<u64>(-1)` 即 2^64−1，此写法仍然有效）；v0.26 起见第 20 条。浮点字面量期望 `f32` 时舍入到最近的 f32 值，超出 f32 有限范围报 3034；v0.26 起由十进制文本直接正确舍入到 f32（v0.23 的实现先舍入到 f64 再舍入到 f32，个别字面量会差一个 ulp），整数字面量取 `f32` 时同样一次舍入。v0.26 起二元运算里的字面量按另一侧取型，见第 19 条。
4. **无损扩宽**（只在赋值类位置：带类型的绑定初值、赋值、实参、返回值、字段初值与写入、`List` 元素、`catch` 默认值、下标与内建的 `int` 形参）：有符号 `i8→i16→i32→int`、`i32→isize→int`；无符号 `u8→u16→u32→u64`、`u32→usize→u64`；无符号到更宽的有符号 `u8→i16`、`u16→i32`、`u32→int`；`f32→float`。是否无损按所有目标判定（`isize`/`usize` 按 32..64 位），所以 `usize→int`、`u64→int` 须显式。整数与浮点之间维持现行：从不隐式。泛型实参推断不做扩宽（`i32` 与 `int` 冲突即报无法推断，可写显式类型实参）。
5. **`x as T`**：扩到任意两个数值类型之间（v0.22 只有 `int`↔`float`）：整数→整数越界 trap 4018 `narrow_range`（消息如“as u8 越界（须在 0..255）”）；整数→浮点舍入到最近；浮点→整数向零截断，越界与 NaN trap 4005（同现行）；`float→f32` 舍入，有限值溢出 trap。无损方向写 `as` 也合法。后缀 `as` 的优先级高于一元负号：`-3.9 as i8` 是对 `3.9 as i8` 的结果取负（v0.23 因定宽取负报 3034；v0.26 起有符号定宽类型可以取负，结合方式不变——`-128.0 as i8` 先把 `128.0` 转成 `i8` 而越界，`-3.9 as u8` 是无符号取负、报 3034），要写 `(-3.9) as i8`。与 TS 不同：TS 的 `as` 只是类型断言、没有运行期效果，太虚的 `as` 是数值转换且越界 trap。
6. **`wrapTo<T>(x)`**：`T` 为整数类型（类型实参必须写出），`x` 为任意整数：取低位按 `T` 的宽度与符号解释（二补码），不检查。用于位串与常量：`wrapTo<i32>(0x80004005)`（Win32 `E_FAIL`）、`wrapTo<int>(h)`／`wrapTo<u64>(n)`（`u64` 位串与 `int` 互转）。codegen 直发转换，不占内建编号。
7. **比较与相等**：`== != < <= > >=` 两侧须同型或有公共类型：一侧能无损扩宽到另一侧时取较宽者，否则取两侧都能无损扩宽到的最小类型（如 `i32` 与 `u32` 按 `int` 比，`f32` 与 `float` 按 `float` 比）；`u64`/`usize` 与有符号类型之间没有公共类型，报 3034 并提示显式转换。比较按数学值，无符号类型用无符号序。
8. **`toStr`** 接受全部定宽整数（`u64`/`usize` 按无符号十进制）；v0.26 起也接受 `f32`（第 18 条）。
9. **第一步禁止、第二步放开**：v0.23 以 3034 禁止（提示先转成 `int`/`float`）的定宽值算术（`+ - * / %`、一元负号）、位运算与移位、`toStr(f32)`，以及由接口一致性诊断（3030）拒绝的定宽 `Map`/`Set` 键，v0.26 起按第 13–24 条放开（原写 v0.24，容量附录与线程先后占用 v0.24、v0.25）。放开不改变已写程序的含义；唯一的例外是第 19 条的字面量对称取型对“左侧字面量、右侧定宽值”的比较（越界字面量改报 3034、与 `f32` 比较的浮点字面量先舍入到 f32），使它与右侧写字面量的写法一致。v0.26 仍报 3034 的：两侧没有公共类型、无符号类型取负、字面量越界、保留的类型名（第 25 条）。
10. **字段与容器**：struct 字段、`List` 元素、泛型实参都可为定宽类型；值槽里运行期复用 `int`/`float` 的标签与 8 字节载荷（`u64`/`usize` 以二补码位存放，`f32` 是能精确表示为 f32 的 `float`），定宽只在编译期区分。紧凑布局出现在外部结构体的封送缓冲（§6.8-11）与 v0.26 的紧凑列表（第 22 条）。
11. **用法分工**：计数、下标、累加用 `int`；定宽类型用于存储、互操作（外部函数签名与外部结构体，§6.8）与确实需要宽度语义的地方——避免 `u8 + u8` 一类在 255 处 trap 的写法（v0.26 起定宽算术越界即 trap 4004）。
12. **字节码**：SEB1 类型池增标签 21（另载种类码）；新操作码 `cvt`＝52（立即数为种类：1..24 收窄检查、`wrapTo`、`u64` 与浮点互转、`f32` 舍入；25/26 为 `floatToBits`/`floatFromBits`，§7）。**v0.26**：定宽运算（含环绕运算）、`toStr(f32)`、窄元素列表的创建与读写、`bytesAsList`/`listAsBytes` 使用新的字节码编码（实施设计 §八 2a、2c1、2c1b、2c2），冻结的 Zig 加载器遇到即拒绝——只有用到它们的程序才会产生；只用 `int`/`float` 的程序字节码逐字节不变。
13. **算术（v0.26）**：`+ - * / %` 两侧按第 7 条的公共类型计算，结果为该公共类型：`i32 + i32` 得 `i32`，`i32 + u32` 得 `int`，`u8 + i8` 得 `i16`，`isize + i32` 得 `isize`，`f32 + float` 得 `float`；没有公共类型（`u64`/`usize` 与有符号类型、定宽整数与浮点）报 3034 并提示先用 `as` 转换。整数结果越出类型范围 trap 4004 `int_overflow`，消息写明类型与运算（“i8 加法溢出”“u64 减法溢出”“isize 乘法溢出”），没有静默环绕——需要环绕时显式写第 21 条的环绕运算。
14. **除法与取余**：同 `int` 现行——整数除法向零截断，余数与被除数同号；除数为 0 trap 4001（“除数为零”／“模运算除数为零”）；有符号类型的最小值 / −1 trap 4004（“i16 除法溢出”），最小值 % −1 得 0。无符号类型用无符号除法与取余（`u64` 的 2^64−1 / 7 按无符号计算）。`f32` 与 `float` 一样不支持 `%`（3002）。
15. **位运算**：`& | ^` 按公共类型、结果同型（结果不会越出公共类型的范围）；没有公共类型报 3034，浮点作操作数报 3002。一元 `~` 只用于整数类型（`f32` 报 3002），在类型宽度内取反、结果同型（`x` 为 0 时：`x: u8` 的 `~x` 得 255，`x: i8` 的得 −1）。
16. **移位**：`<< >> >>>` 的结果取**左侧**的类型；计数可为任一整数类型（`int` 或定宽整数；计数字面量按 `int` 取型）；运行期计数须在 0..宽度−1（`i16` 为 0..15，`int` 仍为 0..63），否则 trap 4016 `shift_count`（“移位计数越界（i16 须 0..15）”）。`<<` 舍去超出类型宽度的位（有符号类型按宽度的二补码解释结果，不 trap）；`>>` 对有符号类型为算术右移、对无符号类型为逻辑右移；`>>>` 在类型宽度内做逻辑右移、结果按原类型解释（`i8` 的 −1 >>> 1 得 127、−128 >>> 1 得 64；无符号类型的 `>>>` 与 `>>` 相同；`int` 同 §6.13）。左侧为 `int`、计数为定宽整数时结果仍为 `int`。操作数为浮点报 3002。（对照：Rust、Go、Swift 的移位计数都可为任一整数类型；要求两侧同型会使 `x << n`（`x: u8`，`n: int`）无从书写。）
17. **一元负号**：有符号整数与 `f32` 取负，结果同型；有符号最小值取负 trap 4004（“i32 取负溢出”）。无符号类型取负报 3034，提示写 `wrapSub<T>(0, x)` 取二补码（同 Rust：`-x` 对无符号值除 0 外必然越界，写出来几乎总是错误）。负号紧贴字面量时随字面量取型（第 3 条）。
18. **`f32` 运算与格式化**：`+ - * /` 每步舍入到 f32（以 f64 计算后舍入，结果与原生单精度运算相同）；溢出得 ±inf、除以零得 ±inf 或 NaN，不 trap（同 `float`；与 `float` → `f32` 的 `as` 不同，后者有限值溢出 trap）。`toStr(f32)` 取 1..9 位有效数字中最短的、按 f32 舍入能回到原值的十进制，格式同 `toStr(float)`（不用指数记法）。非有限值（`float` 与 `f32` 相同）打印为 `inf`、`-inf`、`nan`：任何 NaN 都打印 `nan`、不带符号（v0.36）。无效运算（`0.0 / 0.0`、`inf - inf` 等）产生的 NaN 的符号位与载荷由架构决定——x86 缺省置符号位、arm64 不置，IEEE 754 不作规定（Rust 同样写明不保证）；`floatToBits` 如实给出这些位，程序不应依赖。
19. **字面量对称取型**：二元运算（算术、比较、与或异或）一侧是数值字面量（含紧贴的负号）而另一侧不是时，字面量取另一侧的类型，左右对称：`x + 1` 与 `1 + x`（`x: u8`）都按 `u8` 计算，越界报 3034（`s + 300`，`s: u8`）。另一侧为 `float` 时，算术与比较里整数字面量取 `float`（`1 + x` 由 3002 放宽为合法；`x + 1` 本就合法）；与或异或不放宽。两侧都是字面量时维持 `int`/`float`。移位的计数字面量按 `int` 取型（第 16 条）。不用定宽类型的程序，本条只放宽（诊断与产物不变）。v0.23 起写下的定宽**比较**里，左侧字面量原先按缺省类型参与第 7 条的公共类型比较，现在取右侧的类型，与右侧写字面量（`s < 300`、`f > 0.1`）一致；由此改变的只有两种情形：越出右侧类型范围的字面量改报 3034（`300 < s`，`s: u8`：v0.23 按 `int` 比较、合法，现报“整数字面量 300 超出 u8 范围”），以及与 `f32` 比较的浮点字面量先舍入到 f32（`0.1 < f`，`f: f32` 由 `0.1` 初始化：v0.23 把 `f` 扩成 `float` 比较得 `true`，现按 `f32` 比较得 `false`）。这是对称规则的应有之义——两种写法现在与右侧写字面量的 `s > 300`、`f > 0.1` 结果一致——不作兼容处理。**迁移**：原意是按更宽的类型比较时写出转换，`300 < s as int`、`0.1 < f as float`（`as` 优先级高于比较）。
20. **`u64`/`usize` 大字面量**：`int` 上限（2^63−1）以上、2^64−1 以内的整数字面量（十进制与 `0x`/`0o`/`0b` 四种写法）在期望类型为 `u64`/`usize` 时合法（含第 19 条按另一侧取型）。其余位置——期望类型不是这两者、负号紧贴这样的字面量、定长数组长度、`from syscall` 编号——报 1004，位置在字面量末尾，文案同词法（“整数字面量超出 int（64 位有符号）范围: …”）。超过 2^64−1 的仍由词法报 1004。
21. **环绕运算** `wrapAdd<T>(a, b)`、`wrapSub<T>(a, b)`、`wrapMul<T>(a, b)`：`T` 为整数类型（含 `int`），按 `T` 的宽度做二补码环绕——取低位，有符号类型按宽度符号扩展——不检查溢出（`wrapAdd<u8>(200, 100)` 得 44，`wrapSub<u32>(0, 1)` 得 4294967295）。类型实参可省：由两个实参按公共类型推出（字面量取另一侧的类型，两侧都是字面量时为 `int`）；写出时两个实参须能无损扩宽到 `T`。实参不是整数、类型实参不是整数类型、两个实参没有公共类型、类型实参多于一个都报 3034。`wrapMul<int>(a, b)` 与 `imul(a, b)` 相同，`imul` 保留不弃用。
22. **紧凑列表**（存储方式，语义不变）：`List<T>` 的 `T` 为 `i8 i16 i32 u8 u16 u32` 或 `f32` 时，元素按 1、2、4 字节紧凑存放（`f32` 存单精度，读出即该 `f32` 值）。存放方式由创建处的静态类型决定——`listNew()` 的期望类型，泛型在单态化后按具体类型（结构体字段初值同样），`bytesAsList` 的结果同样是紧凑的 `List<u8>`；全部 `listXxx` 内建、for-in、发送与 `Shared`（§6.22，冻结后写入同样 trap 4022）的行为与其他列表相同。其余元素类型（含 `int`、`float`、`bool`、`u64`、`isize`、`usize`）每个元素仍占 8 字节或一个值槽。
23. **`Bytes` 与 `List<u8>`**：两个类型、同一种对象形态，`bytesAsList`／`listAsBytes` 零拷贝互转、返回同一个对象（§6.15-1、§6.15-7）；`Bytes` 的接口照旧以 `int` 读写；实参类型不符报 3002，`listAsBytes` 的运行期核对失败 trap 4010。
24. **`Hashable`**：定宽整数类型由 prelude 提供 `i8Hash`…`usizeHash`，可作 `Map`/`Set` 的键；`f32` 不能（3030，§6.19-2）。
25. **诊断**（`taixu explain` 文案以 `bootstrap/selfhost/explain.tx` 为准）：

    | 码 | 名 | 解释（`taixu explain`） | 本节的触发处 |
    |---|---|---|---|
    | 1004 | `bad_number` | 数字字面量格式或范围非法（修复建议：超过 int 上限、不超过 2^64−1 的整数字面量只能用在期望 u64/usize 的位置） | 第 20 条 |
    | 3002 | `type_mismatch` | 类型不匹配 | `f32` 取余、`~f32`、浮点移位或位运算；`bytesAsList`/`listAsBytes` 实参类型不符（第 23 条） |
    | 3030 | `constraint_unmet` | 类型不满足接口或泛型约束，或接口值用法不合法 | `f32` 作 `Map`/`Set` 的键（第 24 条） |
    | 3034 | `fixed_width` | 定宽数值的用法不合法：字面量越界、两侧没有公共类型、无符号取负，或使用了保留的类型名 | 第 1、3、7、13、15、17、19、21 条 |
    | 4001 | `div_by_zero` | 除数为零 | 第 14 条 |
    | 4004 | `int_overflow` | 整数算术溢出（int 与定宽整数，消息写明类型） | 第 13、14、17 条 |
    | 4010 | `bad_type` | 运行期类型不符合指令期待 | `listAsBytes` 的运行期核对（第 23 条；正常代码不会触发） |
    | 4016 | `shift_count` | 移位计数不在 0..宽度−1（int 为 0..63） | 第 16 条 |
    | 4018 | `narrow_range` | as 收窄越界 | 第 5 条 |

### 6.22 线程、发送与共享（v0.25 新增；LANG-30）

设计与对照见 `docs/design/v052-threads-v0.1-20261015.md`（并发模型见 `v052-concurrency-model-v0.1-20261014.md`）。**仅太虚编译器与 NRT 实现（Zig seed 冻结于 v0.17）。**

1. **线程**：`threadSpawn(f: () => T): Thread<T>` 在新的系统线程里运行 `f`。`f` 是无参、非 `async`、非生成器的函数值——顶层函数名、箭头函数，或存在变量、字段、容器里的函数值；`T` 可为 `void`。`threadJoin(t: Thread<T>): T | Error` 等线程结束并取回结果；`f` 的结果是失败联合时，错误原样作为 `threadJoin` 的错误返回。同一个线程第二次 join、或线程创建失败（系统资源不足）时，`threadJoin` 得到错误值，不 trap。放掉 `Thread` 句柄不影响线程：它照常运行到结束；`main` 返回后，进程等全部线程结束再退出。
2. **隔离与发送**：每个线程有自己的对象图，跨线程只有发送与 `Shared`。`f` 的捕获在 `threadSpawn` 时、结果在线程结束时深拷贝跨线程（结构化克隆：图内的共享与环保持原样；之后双方修改各自的那一份，互不可见）。模块级 `let` 每线程一份：线程启动时按模块依赖序把全部模块初始化一遍，初始化里的副作用每个线程各发生一次，新线程里的模块变量从初值开始。
3. **可发送**（编译期，只看类型）：数值、`bool`、`string`、`Bytes`、`Error`、`List`、定长数组与切片、`Ref<T>`、struct、enum、可选与失败联合，在成分都可发送时可发送；`Shared<T>` 可发送（发送的是句柄）。不可发送：`Weak<T>`（只观察本线程的对象）、`Future<T>`（任务属于本线程的执行器）、`Gen<T>`、`Thread<T>`、平台句柄（`declare type`），以及 `Atomic<int>`（只能经 `Shared` 跨线程）。发送点——`threadSpawn` 的实参是箭头函数时它的捕获、线程函数的结果类型、`sharedNew` 的实参——的静态类型里出现不可发送的成分（含经 struct 字段、enum 变体字段、`List`／数组／切片元素、`Ref`、可选、失败联合间接含有）报 3035 `not_sendable`，文案指出成分与路径（如“捕获的 `cache` 的字段 `owner` 是 `Weak<Node>`”）。
4. **擦除类型**：函数值与接口值静态算作可发送；它们装着的捕获、具体类型在闭包成形、接口装入处按同一规则算好，发送时核对，不可发送即在发送点 trap 4021 `unsendable`（`threadSpawn` 的调用处，或线程结束时的结果发送）。是否 trap 只取决于发送的值，与线程调度无关。
5. **线程里的 trap**：任一线程 trap 即打印诊断并终止整个进程（退出码 3）；多个线程同时 trap 时只输出第一个。需要隔离失败时让线程函数返回 `T | Error`。
6. **执行器**：每个线程一个确定性执行器，`async`/`await` 与定时器只在本线程内调度，任务不跨线程迁移；线程函数返回后先跑完本线程的就绪任务再结束。
7. **`Shared<T>`**：`sharedNew(v: T): Shared<T>` 把 `v` 可达的对象图深拷贝进进程级的共享区，原对象不受影响、照常可写。实参须可共享——同可发送，另允许 `Atomic<int>`——否则报 3035；经函数值、接口值带入的不可共享成分在 `sharedNew` 处 trap 4021。`sharedGet(s: Shared<T>): T` 取出共享的对象图，读不加锁。`Shared` 句柄可以随捕获、结果发送，也可以嵌进别的 `sharedNew`；发送句柄不拷贝内容。
8. **深度不可变**：`sharedGet` 取出的对象图（以及从中取出的任何对象）是冻结的。字段赋值、下标赋值、`listPush`/`listPop`/`listSet`/`listInsert`/`listRemoveAt`、`bytesSet`/`bytesPush`/`bytesAppend`（`Map`、`Set` 的写随之）、把冻结的外部结构体以 `Ref` 传给外部函数、把冻结的 `Bytes`（含经 `listAsBytes` 得到的冻结对象）作外部函数或系统调用的实参，都 trap 4022 `frozen_write`，文案指出操作与对象种类。（v0.33 勘误：v0.26 曾注记“冻结的 `Bytes` 作外部函数实参时不检查”，实现自 5839304 起已在交给 C 之前检查；即使 C 接口只读，也须先用 `bytesSlice` 拷出一份再传。）字符串拼接照常产生新串。要修改就构造一份新的对象；可变的共享状态放进 `Atomic<int>`（以后的 `Mutex<T>`）。
9. **同一性与值类型**：同一线程里，`Shared` 句柄活着时 `sharedGet` 每次返回同一个根（`Ref` 根之间 `==` 为真）；不同线程各自得到自己的那一份（跨线程的同一性本来不可观察）。所有句柄放掉之后再收到同一个共享区，得到的是新的一份。根是值类型（struct 值、enum 值、定长数组）时，`sharedGet` 按值语义交一份拷贝：拷贝归调用方、可写，其中的句柄类成分（`Ref`、`List`、`Bytes` 等）仍指向冻结的对象。大数据零拷贝用 `Shared<Ref<T>>` 或 `Shared<List<…>>`：字符串、`Bytes` 与数值 `List`（v0.26 起含紧凑列表）的数据各线程共用一份。
10. **`Atomic<int>`**：`atomicNew(v: int): Atomic<int>`；`atomicLoad(a): int`、`atomicStore(a, v): void`、`atomicAdd(a, d): int`（返回旧值；溢出同 `+` trap 4004，单元不变）、`atomicCas(a, expect, new): bool`（当前值等于 `expect` 时换成 `new`，返回是否换成），内存序都是 seqcst。`Atomic` 不冻结（自带同步）；放进 `sharedNew` 的对象图后，各线程取得的 `Atomic` 指向同一个值单元——`sharedNew` 拷贝的是单元的当前值，原对象里的 `Atomic` 与共享区里的是两个单元。`Atomic<T>` 目前只有 `T = int`（其余报 3020）；内存序参数以后作为纯增量开放。
11. **比较与文本**：`Thread`、`Shared`、`Atomic` 都是句柄，不可 `==`（3002）、不可 `toStr`。
12. **平台**：Windows `CreateThread`；Linux 静态产物 `clone`（自管 8 MiB 栈，低端保护页）；Linux 动态产物（调用了 `from "库"` 的外部函数）`pthread_create`。线程栈 8 MiB。`ffiLastError` 与回调窗口每线程一份（§6.8-14、§6.8-15）。
13. **字节码**：内建 BuiltinId 61..69（§7）；TXC 类型种类 20 `Thread`、21 `Shared`、22 `Atomic`（追加值），只出现在原生产物里。

### 6.14 字符字面量（v0.15 新增；D3 v0.15；LANG-16）

1. **形态与值**：'x' ＝ int 码点字面量——值为该单字节字符的码点（'a'＝97）；词法定界＋转义面校验＋值解码，**解析期即解糖为 int 字面量节点**（raw 保留原文形态）——sema/codegen/AOT/VM 零新面，bytecode 零新 opcode（金哈希恒等由零扰动亲证）。
2. **转义集**：\n（10）/ \t（9）/ \r（13）/ \\（92）/ \'（39）/ \0（0）；\u{...} 为**评估位**（结构化拒绝，登记不实施——需要时按尾段追加先例随单落位）。
3. **结构化拒绝**（诊断码 1005 bad_char_lit，1xxx 词法段尾段追加）：空字面量（''）/多字符（'ab'）/多字节（'太'）/未知转义（'\q'）/\u 评估位拒绝；未闭合复用 1002（与模板串同款）。多字节拒绝口径＝S1 字符串为 UTF-8 字节串（§7 byteAt），字符字面量对齐**字节**语义——码点字面量需求随 std/utf8 实战评估。
4. **byteAt 锚**：'a' 的动机面＝`byteAt(s, i) == 97` 魔数可读性——惯用形 `byteAt(s, i) catch 0 == 'a'`（byteAt 返回 T?，越界 None）；存量 `== 97` 形魔数按判据式（仅字节比较面）重写小批随单。

## 8. 明确排除清单

**S1 排除（规划于 S2+，第一阶段内补齐）**：~~闭包与箭头函数~~（**v0.7 起以单一箭头形式入 S1，§6.11**——引用捕获、闭包相等、`function` 表达式形式仍排除）、用户泛型/编译期参数、~~`interface`~~（**v0.20 起入 S1，§6.18**）、可选链 `?.`、~~空值合并 `??`~~（**v0.12 起入 S1，修订 v0.12 第 1 项**）、解构赋值（**v0.12 起声明位的 List 形入 S1，修订 v0.12 第 2 项；表达式位与剩余形态仍排除**）、~~字符串插值~~（**v0.6 起以模板串形态入 S1，§6.10-3**）、字符串索引（**语法**形态 `s[i]` 仍排除属 S2；V0.3 起以 `byteAt` 内建函数形态提供逐字节访问——自举层能力，见 §7）、switch 字面量模式与块臂（复杂 case 逻辑拆辅助函数）、切片操作内建（追加/子切片，stdlib；**v0.5 注记：动态序列需求由 `List<T>` 内建承载**）、**Map/Set 等集合类型（v0.20 注记：泛型 `Map<K, V>`/`Set<T>` 由编译器自带 prelude 提供，§6.19；2026-09-03 明确推迟；**v0.5 注记：V0.7 起以 stdlib `.tx` 形态交付于 List 内建之上（D20 §四：`mapNew/mapGet/...`／`setNew/setAdd/...` 为普通导出函数，迭代序＝插入序），仍不进语言面——语言面容器内建冻结为 List 六件**；**v0.6 注记：`for (k, v in m)` 的 Map 迭代经 `mapKeys/mapGet` 解糖，Map 本体仍为 stdlib 类型**）**、`Ref<T>` 到 `T` 形参的自动解引用（避免隐式深拷贝，需要时显式 `unref` 或设计函数接收 `Ref`）、~~定宽数值类型（O4，随 V0.2 FFI）~~（**v0.23 起第一步入 S1，§6.21**——定宽算术、位运算与移位、`f32` 运算与格式化、紧凑容器、`u64` 大字面量随 O4 第二步；**v0.26 起第二步入 S1，§6.21-13 起**）、`Ptr<T>` 与 FFI 全集（O1；**v0.4 注记：§6.8 `declare function` 是宿主函数调用面的最小前奏，非 FFI 全集**——任意外部符号链接、指针类型、结构体封送仍随 O1 期设计；**v0.23 注记：FFI 入 S1（§6.8-7 起：系统库与系统调用、句柄、外部结构体、回调）；原始指针 `Ptr<T>` 与 `unsafe` 仍排除，留给裸机轨道单独设计**）、`dynamic`（O5）、`is` 类型判断（switch 已覆盖）、~~方法语法~~（**v0.6 起书写形态入 S1，§6.10-1**；**v0.20 起接口值的动态分派入 S1，§6.18**；类、继承、方法表、`this`、重载仍永久排除）。**v0.6 排除增补**：嵌套模板串（插值段内模板）、string 码点迭代（v0.9 评估）、for-in 自定义迭代器协议（可迭代面冻结于 §6.10-2）、`?.`/`??` 与糖形组合（v0.9）。**v0.7 排除增补**：引用捕获（by-ref）、被捕获名赋值、闭包相等、`function` 表达式、函数重载/方法表（§6.11-7）。**v0.8 排除增补**：真线程/抢占式并发（RT-06 设计定稿、实现 V1.0+ 候选——V0.9 协作式单线程面，AGENTS §3 并发定档）；`Future<Future<T>>` 嵌套；async 函数指针/函数类型的 async 变体（fn 类型无着色位——**着色跟随被调表达式**：直接调用 async 函数/async 箭头调用表达式即 spawn；经普通 fn 类型转发调用＝着色擦除、同步执行，体内 await 即结构化 trap「await 执行于无任务上下文」；类型同一性不比较着色位——async 值可赋给同签名 fn 类型＝宽化擦除）；TaskGroup/Pool（V0.9-A2 库层交付，不进语言核心，D26 §四）；`async` 属性/装饰器形态；select/超时通道语法（stdlib 演进面）。**v0.22 排除增补**：异步生成器 `async function*`（将来另设类型，不复用 `Gen`）；`yield*` 委托；`yield` 作表达式与向生成器传值 `next(v)`；生成器的 `throw()`/`return(v)`；箭头与函数表达式形式的生成器；通用迭代器协议（用户类型自定义可迭代——将来可定义为“有返回 `Gen<T>` 的 `iter` 方法即可迭代”）；生成器内的 `defer` 与失败传播、可失败的生成器（失败结束迭代）；`Gen` 的相等比较（§6.20）。**v0.23 排除增补**：~~定宽值的算术、位运算与移位、`toStr(f32)`、定宽 `Map`/`Set` 键、超过 i64 最大值的 `u64` 字面量（O4 第二步）~~（**v0.26 起放开，§6.21-13 起**）；任意位宽整数（`u3`/`u17`，名字已保留，随位布局 struct 另行设计）、`i128`/`u128`；FFI 的按值传递或返回结构体、联合与位域、~~可变参数函数~~（**v0.36 起以变参参数入 S1，§6.8-21**；调用处任意个数与类型的变参仍排除）、指向指针的指针、闭包回调、懒装载（缺库时返回错误）、`string?`/`wstr?` 返回（空指针现得 `""`）、Linux 上的宽字符串、JS 宿主种类（§6.8-12、§6.8-17）。**v0.26 排除增补**：`float`/`f32` 的 `%`（浮点取余另议）；饱和运算、SIMD 向量类型、`f16`/`bf16`、`i128`/`u128`、位域与紧凑 struct 布局（定宽数值设计 §七）。

**永久排除（语言承诺）**：`==` 隐式类型转换、`var` 提升与重复声明、`this` 与原型链、异常作主控制流、switch fallthrough、运算符重载、隐式数值转换、None 与 Error 的隐式互转、显式解引用操作符与指针算术。

## 9. 示例

> **全景示例**：`docs/design/D3-s1-syntax-tour-v0.1.tx` 覆盖性演示 S1 全部语法特性，是本节的评审观察材料；D3 定稿后转为 TEST-02 首个正向守护用例（v0.2 修订后须补 Weak 段落并同步更新 skill 副本）。以下为最小示例。

```text
// 闭包与函数类型（v0.7；§6.11）
const add: (int, int) => int = (a: int, b: int): int => a + b;   // 表达式体
const log: (string) => void  = (s: string): void => { print(s); }; // 块体
function makeAdder(n: int): (int) => int {
    return (x: int): int => x + n;     // 捕获＝值快照（n 入 env），闭包体内只读
}
const apply = (f: (int) => int, v: int): int => f(v);  // 函数作值/传参（句柄语义）
```

```text
// 语法糖三件（v0.6）：方法语法 / for-in / 字符串插值
function total(nums: List<int>): int {
    let acc: int = 0;
    for (x in nums) {                  // for-in：List 下标序
        acc = acc + x;
    }
    return acc;
}

export // 异步与任务运行时（v0.8；§6.12）
async function fetchScore(id: int): int {
    if (id < 0) { return err("bad id"); }
    return id * 10;
}

async function joinTwo(a: int, b: int): int {
    const fa = fetchScore(a);
    const fb = fetchScore(b);          // spawn 子任务，不经 await 即并发
    const va = (await fa)?;            // T | Error；? 原样传播
    const vb = await fb catch -1;      // 或就地处理错误
    return va + vb;
}

function main(args: []string): int {
    const j = joinTwo(1, 2);
    const r = referee(j);
    return 0;                          // main 返回后调度泵跑完任务（§6.12-4）
}

async function referee(f: Future<int>): int {
    const l: List<Future<int>> = listNew();
    listPush(l, f);
    const i = taskAwaitAny(l) catch -1;
    const v = await f catch (e) {
        print(e.message);
        return -1;
    };
    print(toStr(v));
    return v;
}

function main(args: []string): int {
    const nums: List<int> = listNew();
    nums.push(3);                      // 方法语法 ≡ listPush(nums, 3)
    nums.push(4);
    print(`total=${total(nums)} len=${nums.len()}`);
    // print("total=" + toStr(total(nums)) + " len=" + toStr(listLen(nums)))
    for (b in "AB") {                  // string 字节序（65, 66）
        print(toStr(b));
    }
    return 0;
}
```

```text
// examples/hello.tx —— PoC 目标（LANG-06）
export function main(args: []string): int {
    print("Hello, 太虚!");
    return 0;
}
```

```text
// 自举目标风格示例：AST（Ref 堆引用）+ 两类失败通道 + 桥接
enum Expr {
    Num(v: int),
    Add(left: Ref<Expr>, right: Ref<Expr>),
}

function mkAdd(l: Ref<Expr>, r: Ref<Expr>): Ref<Expr> {
    return new Expr.Add(l, r);
}

function size(e: Ref<Expr>): int {
    return switch (e) {                   // switch 对 Ref 自动解引用
        case Num(v) => 8,
        case Add(l, r) => 1 + size(l) + size(r),
    };
}

function firstByte(s: string): int? {     // 预期内的缺失：可选
    if (len(s) == 0) { return None; }
    return len(s);
}

function loadSource(path: string): int | Error {   // 异常故障：错误联合
    const text = readFile(path)?;         // 传播 Error
    if (len(text) == 0) {
        return err("empty source");
    }
    return len(text);
}

export function main(args: []string): int {
    const n = loadSource("app.tx") catch (e) {
        print("load failed: " + e.message);
        return 1;
    };
    const fb = firstByte("taixu") catch 0;   // T? 降级为默认值（桥接为 T | Error 用 catch err(...)）
    print("loaded " + toStr(n) + " bytes, first at " + toStr(fb));
    return 0;
}
```

```text
// List 容器示例（v0.5 新增，§6.9）
export function main(args: []string): int {
    const xs: List<int> = listNew();          // T 经期望类型推断
    listPush(xs, 10);
    listPush(xs, 20);
    const head = listGet(xs, 0) catch -1;     // 在界得 10
    const oob = listGet(xs, 99) catch -1;     // 越界得 None → catch -1
    const top = listPop(xs) catch -1;         // 尾弹出 20（所有权移交）
    print(toStr(listLen(xs) + head + oob + top));   // 2 + 10 + (-1) + 20 = 31
    return 0;
}
```

```text
// 弱引用示例（v0.2 新增，§6.7）
struct Node { name: string }

function main(args: []string): int {
    const r: Ref<Node> = new Node { name: "taixu" };
    const w: Weak<Node> = weak(r);
    const a = deref(w) catch { name: "" };   // 目标存活：得 Node 值拷贝
    print(a.name);                            // taixu
    return 0;
}
// 目标释放后 deref(w) 得 None——由 `--mem-report` 与守护用例验证（MEM-03）
```

## 10. 评审决策记录与开放问题

v0.1 已定（2026-09-03）：

1. ~~单一失败通道 vs 分离类型~~ → **B 方案**（§2.1 记录理由）；
2. ~~catch 形式~~ → 默认值表达式与 `(e)` 绑定块，统一类型规则 §6.3-3；
3. ~~错误丢弃~~ → `T?` / `T | Error` 作为语句被丢弃即编译错误（§4.2-7）；
4. ~~TS 优先修订包~~ → 用户全部采纳：`function` + `:` 返回类型、`catch (e)` 括号形式、`case Pat =>` 臂、裸对象字面量为主推构造形式、`->` 移除；
5. ~~struct 字面量与 import 形式~~ → 裸字面量 + 类型名兜底；TS 式命名导入；
6. ~~堆引用拼写与解引用~~ → **`Ref<T>` 方案**（用户确认，歧义检查通过：尖括号仅存于类型文法；S1 无 `>>` 运算符故嵌套无碍；类型不出现在表达式位置故无比较符歧义；命名遮蔽按 TS 全局名政策）。`mut` 关键字与 `*mut` 移除，`.*` 操作符移除，自动解引用全覆盖，`Ref` 对象共享可变（TS object 心智）；FFI 裸指针 V0.2 预留 `Ptr<T>`；
7. ~~enum 变体构造的前导点~~ → **移除**（用户指出违反 TS 优先）：表达式位限定名 `Expr.Add(l, r)`（TS 命名空间/静态调用同形，后缀链解析 + sema 定夺，零新增文法）；模式位裸名 `case Add(l, r)`；堆分配 `new Expr.Add(l, r)`；
8. ~~强制分号~~ → **用户确认保留**（2026-09-03）；
9. ~~数组/切片字面量缺失~~ → 编写全景示例时发现的规范缺口，本版补入（§5 EBNF `array_lit`、§6.6；`[N]T` 值语义 / `[]T` 共享语义、`len` 支持数组）；
10. ~~enum/数组相等语义、catch 块值规则、const 深层冻结~~ → 补入 §6.5-3（enum 按 tag+字段值；数组按内容、切片按同一性）、§6.3-3（catch 块须发散或 void 语境）、§4.2-3（const 对值类型深层冻结，Ref 例外）；
11. ~~导入说明符三级规则~~ → 用户确认（2026-09-03）：相对路径（S1 唯一形式）／裸标识符（标准库预留，V0.2 激活）／其他（包管理期），见 §6.1；
12. ~~集合类型缺失~~ → 用户确认（2026-09-03）：Map/Set 推迟至 V0.2 标准库（`Map<K,V>`/`Set<T>`，与 `dynamic`、结构化数据字面量同期设计），见 §8；
13. ~~`test` 块、关键字全集（22 个）、全景示例三处刻意约束~~ → 用户评审通过（2026-09-03）。

v0.2 修订（2026-09-04，评审通过同日）：

14. ~~`Weak<T>` 语言面~~ → 按 **D6 §5 定案**引入（V0.2 范围确认后的最小增量）：`Weak<T>` 预声明泛型 + `weak()` / `deref()` 内建；**`deref` 返回 `T?`（值拷贝语义，与 `unref` 同心智）而非 `Ref<T>?`**——句柄入帧窗口会被 RETAIN 从而延长目标生命周期，违反"弱引用不拥有目标"的定义，故取值拷贝形态（D6 §5"不 RETAIN 目标"的直接推论）；`==` 按盒同一性、深度比较不穿透（D6 §3 不变式）；不提供回调/finalizer；循环残留语义如实引用 D6 §6。关键字集不变（`Weak`/`weak`/`deref` 均为预声明标识符，可遮蔽）。

v0.3 修订（2026-09-04，评审通过同日）：

15. ~~自举前置能力~~ → **两个内建函数、零新语法**（D11 §2 缺口分析结论；评审通过 2026-09-04）：`byteAt(s, i): int?`（越界 `None` 显式可判，优于 trap——词法扫描的 EOF 判定天然用 `T?`）与 `utf8Encode(cp): string | Error`（非法码点走错误联合——与语言失败模型一致）；标注"自举层内建"（面向用太虚写太虚的场景，应用层优先 `readFile`/切片等高层形态，S2 提供码点迭代后重估）；**字符串索引语法（`s[i]`）仍排除**——内建形态是更小的语言面增量且语义显式。

v0.4 修订（2026-09-09，V0.6 规划包；评审通过后走同步链）：

16. ~~外部函数声明~~ → **`declare function`（§6.8，TS 词汇接缝）最小面**：GUI 语言绑定（GUI-07/D18 §六）所需——封送四标量类型＋string 借用、声明序分配宿主 id（callbi 64+ 段，D4 v0.2）、运行期缺失结构化 trap；`Ptr<T>`/FFI 全集仍排除（§8 注记）；关键字集不变——`declare` 为**上下文关键字**（§3，顶层声明位 + 后随 `function` 才识别；词法/自举词法表零改动），TEST-02 守护同步扩充。

v0.5 修订（2026-09-09，V0.7 规划包；随执行包落地后走同步链）：

17. ~~容器类型缺失~~ → **`List<T>` 预声明泛型＋六内建**（§6.9；D20 §二，LANG-07）：句柄语义、元素冻结面（int/float/bool/string/Ref/Weak/List）、`listNew` 期望类型推断、读 `None` 写 trap 的越界二分、RC 计数四规则；**callbi 13–18 段内追加，`.txc` 格式零变更**（金哈希守护）；Map/Set/字符串工具/JSON 均为 stdlib `.tx` 形态不进语言面（§8 注记）；结构化字面量语法糖（`Json.Obj{...}` 类）推迟 V0.8 按真实用例评估。
19. **闭包与函数类型入 S1**（§4.1/§4.2-9/§5/§6.5-3/§6.11；D25 §一–五，LANG-09；用户 V0.9 执行授权）：捕获＝**创建时值快照**（值类型 clone、句柄共享）＋闭包体内只读（3023）——与 RC 帧窗口所有权制零纠缠、AI 可静态推理；调用约定扩展＝env 形态 R0 保留（零新 opcode、`TyKind.fn=16`＋TXFNT 尾节，既有产物零扰动）；`.ret`/unwind 的 R0 释放规则统一为「一律 releaseSlot」（永生哨兵空操作——旧程序逐字节不受影响）；双形态（env/fnv）并存调用收敛；List 元素面扩函数类型。**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。
23. **错误链打印可读化入 S1**（§7 `toStr` 面扩 `Error`；LANG-12，D57 续行；V0.22 规划包评审执行一体授权，2026-09-16）：`toStr(e)`（e 为 `Error` 值）⇒ `"Error: " + e.message`——错误链打印从 `print(e.message)` 两段式收敛为值级可读渲染；插值段 `${e}` 经 toStr 解糖同享（§6.10-3 段类型面随扩）。语义与 interp／AOT shim 双实现逐字节同源；既有程序零扰动（原 `toStr(err)` 为编译期 3002 拒绝，无行为兼容面）。**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。
18. ~~方法语法／for-in／字符串插值~~ → **语法糖三件入 S1**（§6.10；D22 §一，LANG-08；TS 优先原则：三者皆有 TS 对应物——方法调用、for-of、模板串）：方法语法＝**纯函数调用糖**（约定名映射 → 鸭子方法直击，解析序冻结于 §6.10-1；零对象系统语义）；for-in＝**计数循环解糖**（可迭代面冻结：List/string 字节序/数组/切片/Map 键值经 mapKeys/mapGet——插入序红线；发散 catch 收口句柄容器失败通道）；插值＝**拼接链解糖**（`toStr` 面扩 `string` 恒等——插值段统一 toStr 化的实现前提）。**三者解糖为零 .txc 格式变更**（金哈希 `806dd58c` 守护预期恒等；糖形与手写形态指令流逐位对照单测锁定）；`in` 为上下文关键字、模板串为词法新记号——两者各自镜像自举 lexer/parser（diff-parser 全仓守护覆盖糖形源）。

澄清记录（2026-09-03，均无规范变更）：

- **`?` 传播的类型标注**（用户问询后接受）：`expr?` 是"解包或传播"算子，结果类型为成功侧 `T`——失败时当场从函数返回、后续代码不执行，故 `const total: int = strictLen? + ...;` 中 `total` 必为 `int`，标注 `int?` 反而是类型错误。可见性由行内 `?`（传播点）与函数签名（传播通道）两处保证，类比 TS 的 `await`；
- **int 单一 64 位默认、不做 int→long 扩容**：64 位 CPU 寄存器/栈槽本就按 64 位处理标量，窄类型在"值流动中"省不了内存；真正省内存的场景（大数组、字段布局、二进制序列化）由 V0.2 定宽类型（u8/i32/i64/f64）限定承担；"只有一个 int"从根上消除了扩容格子（C 系 int/long/size_t 移植性 bug 温床），并使 sema/字节码更小（自举友好）、AI 生成零转换决策。Go 为同模型先例；
- **`expr catch None`**：catch 后跟"失败时顶上的值"，其类型决定整体类型（成功侧 ∪ F 类型）——`catch 0` 得 `T`、`catch None` 得 `T?`、`catch err(...)` 得 `T | Error`。`catch None` 是 §6.3-4 桥接二的显式拼写（被调方的故障在调用方问题域中即缺失），低频、类型可见、零新增机制。
20. **异步语言面与任务运行时入 S1**（§3/§4.1/§4.2-10/§5/§6.5-3/§6.12/§7；D26 §一–三/§五，LANG-10＋RT-07 运行时段；用户 V0.9 执行授权）：`async`/`await` 为**上下文关键字**（declare/in 同模式，保留字 22 个不变）；着色规则＝声明写成功侧 T、调用得 `Future<T>`、await 产出 `T | Error`（任务内 err 原样穿透，天然接 ?/catch）；spawn＝惰性入队 FIFO（启动序＝可观测执行序）；调度器＝同步栈排空后的 FIFO 泵；挂起＝单帧快照（stackless 不变式，恢复重执行挂起点）；取消即确定性清理（挂起窗口逐槽 release）＋观察者得 `err("task cancelled")`；`taskAwaitAny` 插入序裁决；结构化出口双守护（死锁①/退出 pending②，退出码 3，错误出口同守）；AOT 对 Op 38/39 结构化拒绝（红线 5）；`TyKind.future=17` 追加编码值＋BuiltinId 20–22 尾加（`.txc` 兼容，金哈希 `806dd58c` 不变）；List 元素面扩 Future。**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。

21. **定时器面与库层超时入 S1**（§3/§6.12-8/§6.12-9/§7；D29 §一–三，RT-08；用户 V0.10 执行授权，评审定档随行）：`sleepMs`＝BuiltinId 25 尾加（"追加编码值，旧产物不含"先例第 7 批——`.txc` 兼容，金哈希 `806dd58c` 不变，无 sleepMs 程序零扰动）；到期轮＝泵每轮先于就绪任务收割（deadline 升序/同刻登记序，钟源恒单调钟）；0ms/负值两确定边界；断言面只用 0ms/不到期两确定形态（沿 nowMs 从严）；已完成任务关联定时器收割空操作＋泵出口排水空操作（零滞留）；**timeout 单态定档**（stdlib 库面 `Future<int>`——S1 无用户泛型函数，竞速走包装 runner 同构化，泛型函数维持排除）；`taskGroupCancel` 登记序传播、cancel 后 drain 必达零 pending；AOT 拒绝门覆盖不变（登记文本提及 BuiltinId 25）。**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。
22. **语言核心回补批入 S1**（§3/§7；D57 §一，LANG-11；V0.21 规划包评审执行一体授权，2026-09-16）：五内建＝BuiltinId 26–30 尾加（"追加编码值，旧产物不含"先例第 8 批——`.txc` 兼容，金哈希 `3873a6a8` 恒等，无新内建程序零扰动）；List 删除/插入补齐实战最常撞面（G1 feedback #2：todo 删行整表重建样板的根除）；string 子串族走**码元语义**（G1 feedback #3：byteAt 逐字节重建在 CJK 场景拆多字节的根除——for-in 字符串维持字节序不变，码元访问走 strSub/strIndexOf）；错误读取面复核＝`e.message` 字段访问在案（教材化推广，toStr 拒绝 Error 维持——不增设冗余内建）；诊断防护＝`or`/`and` 误写就近诊断＋恢复解析（G1 feedback #4 级联漂移根除）＋结构化诊断随附 `taixu explain <code>` 对照行；`taixu init` 单一模板名实参就近提示（G1 feedback #5）。**运行期所有权**：listRemoveAt 表失权即 RELEASE（void 无人接管）；listInsert RETAIN（表持独立所有权）——D6 §3 计数点纪律逐条对账。**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。

---

## 附录 A 实现限制（v0.24 新增）

上限表保留两列供历史比较：现行原生链（原生编译器内部 TXC v6，64 位指令字）与已退场的 Zig 链（归档 seed、`--frontend=zig`；TXC v4）。后者不是当前可选后端，日常容量门只验证原生实现。超限一律结构化拒绝或运行期 trap，不得静默生成错误代码（守护门：`scripts/test-capacity-limits.py`；评估与依据见 `docs/design/v052-capacity-limits-review-v0.1-20261016.md`）。上限只会放宽（放宽总是兼容的），收紧须改本附录并升版。

| 项 | 原生链 | Zig 链 | 超限 |
|---|---|---|---|
| 单函数寄存器（局部、临时与调用实参窗口） | 65535（编号 0–65534） | 256 | 编译期 3101 |
| 函数、函数类型、箭头函数的参数 | 65534 | 254 | 编译期 3011 |
| 外部函数（`declare function … from`）的参数 | 254 | 254 | 编译期 3011 |
| 单函数常量 | 2^32 | 65536 | 编译期 3101 |
| 单函数跳转距离 | ±2^31 条指令 | ±32767 条指令 | 编译期 3100 |
| struct 字段、单个 enum 变体的载荷字段 | 32767 | 127 | 编译期 3020 |
| 模块顶层绑定（`let`/`const`） | 32768 | 128 | 编译期 3020 |
| 闭包捕获 | 32765 | 125 | 编译期 3020 |
| 接口方法 | 32766 | 126 | 编译期 3020 |
| enum 变体 | 65536 | 65536 | 编译期 3020 |
| 每程序外部函数（`declare function … from`） | 61440 | 192 | 编译期 3101 |
| 泛型实例化嵌套 | 256 层 | 256 层（`--frontend=zig` 为每程序 512 个实例） | 编译期 3020 |
| 单个泛型实例的类型参数规模（展开树节点） | 2^20 | 2^20 | 编译期 3020 |
| 值语义克隆、比较的嵌套深度 | 1024 | 1024 | 运行期 4007 |
| 对象链释放深度 | 不限（超过 8192 层转为迭代释放） | 不限 | — |
| 每线程值栈 | 16,776,960 槽（按需提交） | — | 运行期 4003 |
| 原生调用深度 | 本线程实际栈（主线程与工作线程 8 MiB；Linux 主线程按 `RLIMIT_STACK`，至多 64 MiB）减 256 KiB 余量，小帧递归约 8 万层 | — | 运行期 4003 |
| 引用计数 | 到顶即永生：不再计数、不再释放（只泄漏该对象，不越界） | 同 | — |
| `Shared` 共享区的引用计数（v0.25） | 64 位原子计数，越过 2^62 即饱和、永不归还（只泄漏该共享区） | — | — |
| 线程数 | 受系统资源限制；每个线程预留 8 MiB 机器栈与 256 MiB 地址空间（值栈与线程状态，按需提交） | — | 创建失败（含新线程建不起自己的区域，v0.34）时 `threadJoin` 得错误值 |
| 运行期内存（v0.34 写明） | 受进程可用内存限制 | — | 致命：stderr 为 `taixu: 内存不足`、退出码 3，含启动时取参数表；有明示错误值的操作（`readFile`、`readFileBytes`、线程创建）除外 |
| `readFile`、`readFileBytes` 单次读取 | 不设上限（受可用内存限制；分配失败得错误值） | 64 MiB | 错误值 |
| `writeFile`、`writeFileBytes`、`print`、`eprint` 单次写出 | 不设上限（逐块写出） | Windows 单次 4 GiB 以下 | — |
| `embedBytes` 单个文件 | 64 MiB | 64 MiB | 编译期诊断 |
| 同时存在的工具链子进程（`procSpawn`，v0.27） | 64 个 | — | `procSpawn` 得错误值 |
| 定长数组 `[N]T` 的长度 | 1–65535 | 同 | 编译期 3020 |
| 切片元素数 | 受源码规模限制：S1 的切片只来自字面量与 `main` 的实参表（S2 加入运行期按长度构造切片的接口时放宽到 64 位） | 同 | — |

## 修订 v0.13（V0.38-G1 泛型单态化；LANG-14——2026-09-23 随单）

**新增（§4.1/§5/§6.13 泛型——单态化语义）**：

1. **声明位类型参数**（TS 优先）：`function name<T, U>(params): ret { … }` 与 `struct Name<A, B> { fields }`。类型参数名为普通标识符，声明域＝本声明体内（参数/返回/体类型位）；**enum 不收类型参数**（排除清单维持——首切面按需演进）。带类型参数的声明为**模板**：不直接参与常规符号解析调用面（只可经实例化消费）。
2. **类型位泛型名**：`Name<int, string>`——命名类型后随 `<` 类型实参表（逗号分隔、逐个完整类型原子）。实参个数须与模板型参数一致（结构化拒绝）；预声明泛型（Ref/Weak/List/Future）特判面不变。
3. **调用位**：`f(args)` 命中泛型模板时**单态化实例化**——型参统一三通道（顺序固定）：①显式类型实参 `f<int>(args)`（postfix 平衡前瞻：`<…>` 配对且后随 `(` 方按类型实参解析，否则恒为比较运算——零歧义保守面）；②实参型统一推断（形参类型中出现型参者逐位对齐实参静态型——含 `List<T>`/`Ref<T>`/`T?` 结构件）；③期望返回型收窄（返回型含型参且调用位有期望型——listNew 期望传播同款）。三通道后仍有未解型参＝结构化拒绝（"无法推断类型参数 T"）。
4. **实例化语义**：实例＝模板 AST 深拷贝＋型参替换为实参型＋**mangled 名 `name#T1,T2`**（`#` 非合法标识符首字符，永不与源码标识符碰撞；typeNames 规范拼写）。实例作为普通函数登记 fn_table 尾段（闭包体同款）→ sema 常规检查/codegen 常规发射——**零新 opcode、.txc 零格式变更**。同（模板，型参表）重复实例经 memo 去重（同实例复用——单一定义点）；**实例化嵌套不超过 256 层、单个实例的类型参数按展开树计不超过 2^20 个类型节点**（超限结构化拒绝 3020——实例爆炸护栏；不限每程序实例总数——v0.24，此前为每程序 512 个；冻结的 Zig 前端 `--frontend=zig` 仍按 512）；先登记后查体＝递归/互递归泛型闭环；泛型 struct 实例＝StructInfo 骨架先行（同法闭环）＋字段型替换。**金哈希纪律：无泛型程序零扰动**（模板不进产物、实例路径仅泛型调用触发）。
5. **方法糖/鸭子调用命中泛型**：`recv.method(args)` 解糖（§6.10-1）经约定名→方法名直击两路，命中泛型模板即走实例化（接收者前插实参参与推断）——`xs.mapU(f)`、`p.mag()` 对泛型 `mapU`/`mag` 同语义。
6. **失败通道/异步泛型**：实例化后的签名检查与普通函数同面（T 可解为 `T?`/`T | Error` 实参型；async 泛型按着色规则）。

**§8 排除清单注记更新**："用户泛型/编译期参数"（**v0.13 摘除——函数/struct 单态化在册**；enum 泛型/泛型闭包/关联类型/where 约束/变参泛型维持排除——登记按需演进）。

**TEST-02 正负守护**：正例＝恒等函数/Pair 容器/高阶 cmp/递归泛型/方法糖命中/memo 同实例（同型参两调用点 fn_idx 相同）；负例＝型参个数不符/无法推断/实例超限/泛型 enum 拒绝。e2e 36-generics（interp/AOT 双路）。

**镜像面**：parser.tx/ast_bin 引导侧滞后——**泛型文件 diff-parser 受控排除（登记 v0.39 首位）**；txlex/zig 生产路不受影响。render/ast_bin 引导版扩展（K.tgeneric=62/K.tyargs=63/K.tparams=64——追加编码值旧产物不含先例第 9 批）。

**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（本版随执行单走完，副本亲核）。

---

## 修订 v0.12（V0.37-L1 语言还账批；LANG-13——2026-09-23 随单）

**新增（§6.12 糖批——两件落地＋一件分期）**：

1. **`??` None/Error 合并**（表达式全域）：`a ?? b` ≡ `a catch b`——与 catch 默认值**同构**的 TS 拼写糖（None 与 Error 均合并——§2"推广到 Error"纲领落点；右结合：`a ?? b ?? c` ≡ `a catch (b catch c)`）；操作数须 `T?`/`T | Error`（同 catch 约束）。**零新 token**（parser 双 `?` 前瞻解糖为 catch_e AST——sema/codegen/ast_bin/render 零触碰）；fmt 规范形 `a ?? b`（2026-10-25 起；此前排成 `a?? b`，见兼容变更 `tool.fmt-generic-angles`）。
2. **解构声明**（声明位：块内；顶层受 3020 字面量约束自然限制；for 头/export 不支持）：`const (a, b) = e;`——List 元组惯用法。解糖（§6.10 for-in 先例同法）：`const #dt<N> = e;`（RHS 单次求值）＋每名 `const a = listGet(#dt<N>, i) catch None;`——**元素型 `T?`（越界→None）**；`let` 位同形（重赋值面 T?）。糖产物纯既有 AST/内建。
3. **`?.` 可选链——分期登记（§8）**：表达式位短路需控制流面（S1 无 if/三元表达式）；**定向误写诊断**就地落位（miswrittenOr 先例——"`?.` 不在 S1（v0.12 分期）——用 catch 默认值或 ?? 合并；短路链 v0.38 评估"）。v0.38 评估位：闭包内建通道（`optMap(v: T?, f): U?`——箭头体即短路体）设计候选。

**§8 排除清单注记更新**："可选链 `?.`"（分期 v0.38）、"空值合并 `??`"（**v0.12 摘除**）、"解构赋值"（**v0.12 部分摘除——声明位 List 形；表达式位/剩余形态维持排除**）。TEST-02 正例＋解糖等价锚随单（compiler.zig）；e2e 35-sugar-l1（.tx/.out）。同步链四副本随走。

## 修订 v0.30（补记 eprint、listDir、entryKind——2026-10-19 随单）

- **依据**：第 6 轮 6d-3b 给 LSP 建内建表（`docs/design/v052-round6-lsp-mcp-v0.1-20261019.md` §四-5），以本规范 §7 为准与 semamirror 的 `builtinOf` 逐条核对，发现这三个用户可写的内建在 §3、§7 都没有列出。`eprint`、`listDir` 随 V0.45 段二续谱缺口批（BOOT-38）加入，`entryKind` 随 V0.47 fmt 目录递归批（BOOT-42，D95 §四 v0.2 裁决）加入，实现、用例与诊断都已在册，只是规范漏记。
- **§3**：预声明标识符补记 `eprint`、`listDir`、`entryKind`。
- **§7**：内建表补 `eprint`、`listDir`、`entryKind` 三行，写法照现行实现（原生运行时 `nrt_text.tx`、`nrt_sys.tx`，semamirror 的检查分支）。
- **不变**：语法与语义零改动，编译器与产物不变。

## 修订 v0.29（工具链内建 fileSize；TOOL-104——2026-10-19 随单）

- **依据**：`docs/design/v052-round6-lsp-mcp-v0.1-20261019.md` §三-4 改定（LSP 检查 `embedBytes` 按用户裁决 §十一-6 只查存在与大小、不读内容；原定的按目标文件 FFI 因冻结的 Zig 解析器不认 FFI 声明、编译器源码不能写而改为内建）。签名与失败语义对照 Rust `fs::metadata`、Go `os.Stat`、Python `os.stat`：跟随符号链接，取不到时返回错误，所以为 `int | Error`。
- **§3**：预声明标识符增 `fileSize`（工具链专用）。
- **§7**：内建表增 `fileSize` 一行。
- **实现面**：原生编译器与 NRT；例程随工具链例程整组只在程序用到工具链内建时进产物，内核不变；字节码构建用到时报错、不出产物。用例 `tests/nrt-cases/file-size.tx`（两个目标：空文件、多字节内容、目录、设备、不存在、含 NUL 的路径、删除后），反例 `proc-bad-notoolchain`（3001）。
- **不变**：其余语法与语义；不用任何工具链内建的程序产物逐字节不变（用到工具链内建的程序因整组例程多出约 1.5 KB）。

## 修订 v0.28（工具链内建 currentDir；TOOL-103 增补——2026-10-18 随单）

- **依据**：`docs/design/v052-round6-debug-v0.1-20261018.md` §四-3（调试会话按 VM 的规则把模块路径给成绝对路径，需要当前目录）、§四-6（签名与失败语义；对照 Go `os.Getwd`、Rust `env::current_dir`，当前目录被删除时会失败，所以返回 `string | Error`）。
- **§3**：预声明标识符增 `currentDir`（工具链专用）。
- **§7**：内建表增 `currentDir` 一行。
- **实现面**：原生编译器与 NRT；例程只在程序用到时进产物，内核不变；字节码构建用到时报错、不出产物。用例 `tests/nrt-cases/proc-cwd.tx`（两个目标）、`proc-cwd-gone.tx`（Linux，当前目录被删除后得 `no such file: .`），反例 `proc-bad-notoolchain`（3001）。
- **不变**：其余语法与语义；不用它的程序产物逐字节不变。

## 修订 v0.27（工具链进程与标准输入内建；TOOL-103——2026-10-18 随单）

- **依据**：`docs/design/v052-round6-debug-v0.1-20261018.md` §三-1（工具链专用内建，同 `runProcess`、`writeExecutable` 先例）、§四-6（接口；实施前对照修订：读入调用方给的 `Bytes`、读写以返回码报字节数／超时／流尾、`ioWait` 用位集合选来源、补 `stdoutWrite`）。
- **§3**：预声明标识符增九个工具链专用内建。
- **§7**：内建表增 `stdinRead`、`stdoutWrite`、`procSpawn`、`procWrite`、`procRead`、`ioWait`、`procKill`、`procWait`、`procClose` 九行；增“工具链进程句柄”一段（句柄只在创建它的线程里有效，失效句柄与不对的流号 trap 4010）。
- **附录 A**：同时存在的工具链子进程 64 个。
- **运行期码**：4010 用于失效句柄、不对的流号与选项位、`ioWait` 的来源位越界；4022 用于读入冻结的 `Bytes`。
- **实现面**：原生编译器与 NRT；例程只在程序用到时进产物，内核不变；字节码构建用到时报错、不出产物。用例 `tests/nrt-cases/proc-pipe.tx`（两个目标，含 `--debug`、`--leak-check` 构建）与三个反例（3001、4010、4022）。
- **不变**：其余语法与语义；不用这组内建的程序产物逐字节不变。

## 修订 v0.26（定宽数值第二步；LANG-28——2026-10-18 随单）

- **依据**：`docs/design/v052-fixed-width-step2-v0.1-20261016.md`（定稿）与 `docs/design/v052-fixed-width-numerics-v0.1-20261015.md` §四“第二步”、§九。用户 2026-10-15：定宽设计七问按推荐（其中 §九-6 第二步提供 `wrapAdd`/`wrapSub`/`wrapMul`，§九-7 `Bytes` 与 `List<u8>` 两个类型、可零拷贝互转）；2026-10-16：实施设计五问按推荐（无符号取负 3034、有符号 `>>>` 在类型宽度内逻辑右移、`float`/`f32` 维持不支持 `%`、互转写法 `bytesAsList`/`listAsBytes` 且返回同一个对象、`List<int>`/`List<float>` 维持按首个元素选布局），并要求复核性能与安全（该文 §七：互转是同一个对象而非共用缓冲、紧凑写入依赖“定宽值总在范围内”的不变式、`int`/`float` 程序零扰动）。按判定套路自定（该文 §八 2a）：移位结果取左侧类型、计数为任一整数类型（对照 Rust、Go、Swift）；大字面量四种进制一并放开；负号紧贴整数字面量随期望取 `float`（第一步设计已写、实现补上）。原定 v0.24、v0.25，容量附录与线程先后占用后顺延。
- **§3、§4.1、§4.2-1**：预声明标识符五个与 prelude 哈希九个；整数字面量注记；定宽数值、容器、字节三行注记；字面量对称取型。
- **§6.8-12**：`Bytes` 实参——长度为 0 时数据区指针可能是空指针；`List<u8>` 先经 `listAsBytes`。
- **§6.13-1**：定宽整数的位运算与移位指向 §6.21。
- **§6.15-1、§6.15-7**：修订“`Bytes` 成为 `List<u8>` 的别名”的说法——两个类型、同一种对象形态，`Bytes` 的接口照旧返回 `int`；`bytesAsList`/`listAsBytes` 零拷贝互转、返回同一个对象（新条；实参类型不符 3002，`listAsBytes` 运行期核对失败 4010）。
- **§6.19-2**：定宽整数实现 `Hashable`（`i8Hash`…`usizeHash`；`u64`/`usize` 经 `wrapTo<int>`），`f32` 不提供（`Map<f32, V>` 报 3030 并指出缺 `f32Hash`）。
- **§6.21**：第 3、5、8–12 条修订（大字面量与 f32 字面量舍入、`as` 与取负的结合、第一步禁止项的放开与唯一例外、紧凑列表、字节码兼容）；第 13–25 条新增（算术、除法与取余、位运算、移位、一元负号、`f32` 运算与 `toStr(f32)`、字面量对称取型（含 v0.23 定宽比较的两处可见变化与迁移写法）、`u64` 大字面量、环绕运算、紧凑列表、`Bytes` 与 `List<u8>`、`Hashable`、诊断表）。
- **§6.22-8、§6.22-9**：冻结的 `Bytes` 作外部函数实参不检查（既有行为，登记于工具链缺口 TOOL-90）；数值 `List` 的共享注记含紧凑列表。
- **§7**：`toStr` 扩 `f32`；`imul` 注记；增 `wrapAdd`、`wrapSub`、`wrapMul`、`bytesAsList`、`listAsBytes` 五行。
- **§8**：O4 注记；v0.23 排除增补里的定宽项划去；v0.26 排除增补（浮点取余、饱和运算、SIMD、`f16`/`bf16`、`i128`/`u128`、位域）。
- **实现面**（详见实施设计 §八）：2a 前端解禁与对称取型、大字面量改由语义检查按期望类型判定、环绕运算，定宽运算的字节码与运行期例程，`f32` 字面量由十进制直接正确舍入；2b 定宽运算内联发射（定宽整数运算快约 4.5 倍）；2c1 紧凑列表；2c1b 紧凑列表读写内联（`List<u8>` 读写循环快于 `List<int>`，内存约为六分之一）；2c3 prelude 九个哈希函数与定宽类型的方法名派生；2c2 `Bytes` 改用紧凑 `List<u8>` 的对象形态（纯 `Bytes` 访问循环慢约 3–5%），FFI 窄整数返回值只取低位并按种类扩展的用例。lexer、parser、semamirror 的 `.txc` 重烘；frontend-probe 的 v0.17 诊断逐字节不变。
- **运行期码与诊断**：4004 的消息写明类型与运算（“i8 加法溢出”“i32 取负溢出”等）；4016 写明类型的宽度（“移位计数越界（i16 须 0..15）”）；4001 文案同 `int`；3034 增“没有公共类型的算术／位运算”“无符号取负”“环绕运算的实参与类型实参”；3002 增 `bytesAsList`/`listAsBytes` 实参；3030 对 `f32` 键指出缺 `f32Hash`；4010 用于 `listAsBytes` 的运行期核对。超过 int 上限的整数字面量在不期望 `u64`/`usize` 的位置仍报 1004（字面量末尾、文案同前），但改由语义检查（数组长度与 `from syscall` 编号由语法分析）报出，可与同一文件的其他诊断一并给出。`taixu explain` 的 1004、3034、4004、4016 条目已更新（§6.21-25）。
- **用例**：`tests/nrt-cases/` 下 fw2-arith、fw2-f32、fw2-inline、fw2-list（期望值由 `scripts/fixtures/fw2-oracle.py` 独立生成）；运行期 trap fw2-trap-add、fw2-trap-sub、fw2-trap-mul、fw2-trap-div、fw2-trap-neg、fw2-trap-isize、fw2-trap-zero、fw2-trap-shift、fw2-trap-loop、fw2-trap-shiftneg；编译期拒绝 fw2-bad-ops、fw2-bad-biglen、fw2-hash-bad-f32；紧凑列表 fw2-list-frozen；哈希 fw2-hash；互转 fw2-bytes-list；FFI 窄整数返回 ffi-win-narrow-ret、ffi-linux-narrow-ret。第一步的 fw-bad-ops（断言算术与 `toStr(f32)` 报 3034）随禁令解除删去。
- **不变**：其余语法与语义（§6.21-19 所列两处除外）；只用 `int`/`float` 的程序字节码逐字节不变、性能不变（D102 合同）；Zig 链的诊断文本逐字节不变。用到定宽类型的既有程序（fw-basic、ffi-*、thread-basic、thread-win-ffi）产物因定宽负字面量折叠与 f32 字面量舍入改变，运行结果不变。

## 修订 v0.25（线程、发送与共享；LANG-30——2026-10-17 随单）

- **依据**：`docs/design/v052-threads-v0.1-20261015.md`（定稿）。用户 2026-10-16：八问按推荐（运行时冻结、平台原生线程局部存储、线程里 trap 终止进程、每线程全局变量按依赖序初始化、发送序列化、Linux 无 libc 用 clone 有 libc 用 pthread、重复 join 得错误值、只给 seqcst 的 `Atomic<int>`）；擦除类型按推荐“运行期按表核对”。第 4 步的实现选择（共享走“每线程物化、叶子载荷借用”，值类型的根交拷贝，`atomicAdd` 溢出 trap）按用户授权的判定套路自定，见该文 §二十一、§二十四。
- **§3、§4.1、§4.2**：预声明类型名 `Thread`、`Shared`、`Atomic` 与九个内建；类型清单三行；句柄规则一条。
- **§6.5-3**：`Thread`、`Shared`、`Atomic` 不可比较（3002）。
- **§6.8**：回调只能由发起这次外部调用的太虚线程在该调用期间调用（4020 文案随之更正）；`ffiLastError` 每线程一份。
- **§6.22**（新节）：线程、隔离与发送、可发送判定（3035）、擦除类型的运行期核对（4021）、线程里的 trap、每线程执行器、`Shared<T>`、深度不可变（4022）、同一性与值类型、`Atomic<int>`、平台与字节码。
- **§7**：内建表增九行（BuiltinId 61..69）。
- **附录 A**：增共享区引用计数、线程数两行。
- **运行期码**：4021 `unsendable`、4022 `frozen_write`；编译期 3035 `not_sendable`（`taixu explain` 各有条目）。
- **用例**：`tests/nrt-cases/` 下 thread-basic、thread-many、thread-trap、thread-bad、thread-linux-dynamic、thread-win-ffi、thread-send、thread-send-erased、thread-send-bad、thread-send-trap-weak、thread-send-trap-handle、thread-send-trap-result；shared-basic、shared-atomic、shared-atomic-overflow、shared-churn、shared-frozen-field、shared-frozen-list、shared-frozen-bytes、shared-frozen-index、shared-trap-erased、shared-trap-atomic、shared-bad、shared-linux-dynamic。
- **不变**：其余语法与语义；不用线程的程序的产物与性能不变（D102 合同）；Zig 链的诊断文本逐字节不变。

## 修订 v0.24（容量与编码上限；LANG-29——2026-10-16 随单）

- **依据**：`docs/design/v052-capacity-limits-review-v0.1-20261016.md`（定稿）。用户 2026-10-16 裁决：泛型实例化嵌套深度 256＋单个实例类型规模 2^20、取消每程序总数；引用计数保持 32 位、到顶即永生。其余（指令格式改为 64 位定长字、字段划分、实施顺序）按用户授权的判定套路自行判定。
- **§5 泛型实例化**：每程序实例数上限 512 改为嵌套 256 层＋单实例类型规模 2^20；实测线性展开 512 层 1.6 秒，类型规模指数膨胀的程序原先 50 秒后编译器内存耗尽，现在不到 1 秒给出 3020。
- **附录 A 实现限制**：新增；原生链的字段号、模块顶层绑定、闭包捕获、接口方法、外部函数、单函数常量与跳转距离随 TXC v6 放宽，Zig 链维持 v4 上限（由前端按 SEIN 宽格式标志区分）。
- **附录 A 同日放宽（容量批 C3b）**：原生链单函数寄存器 256 → 65535（编号 0–65534，后端“无槽”哨兵由 255 改为 65535）；函数、函数类型、箭头函数的参数 254 → 65534；外部函数参数仍为 254。放宽是兼容的修订，不另升版；Zig 链不变。
- **附录 A 同日放宽（容量批 C4b）**：原生链 `readFile`／`readFileBytes` 去掉 64 MiB 上限（分配失败仍得错误值）；新增“单次写出”一行（Windows 原先单次写，≥4 GiB 时文件写出半截、标准输出静默截断）。兼容的修订，不另升版。
- **附录 A 同日更正（容量批 C4c）**：“数组、切片元素数 2^32−1、超出时截断”一行拆为两行。定长数组长度在编译期限为 1–65535（3020，原本如此）；S1 的切片只来自字面量与 `main` 的实参表，元素数受源码规模限制，到不了 2^32。原先登记的截断缺陷在 S1 不可达。运行时对象的元素数字段仍为 32 位，S2 加入运行期按长度构造切片的接口时同步放宽到 64 位。纯文档更正。
- **附录 A 同日放宽（线程 2d）**：每线程值栈 1,048,576 槽 → 16,776,960 槽（按需提交）；新增“原生调用深度”一行（原先固定预算约为实际栈的五分之一）。兼容的修订，不另升版。
- **§7 同日补充（线程 2c）**：新增“标准库内部内建”一段——`__rt` 前缀的内建只供标准库模块，其余模块调用报 3001；`getEnv` 在 Linux 改读进程级环境表（初值为启动时的 envp，`std/os` 的 `setenv` 写入它），名字含 `=` 或 NUL 时也得 `None`，值中的非法 UTF-8 逐字节替换为 U+FFFD。兼容的修订，不另升版。
- **不变**：其余语法与语义；Zig 链的诊断文本逐字节不变。

## 修订 v0.23（去 Zig 第 5 轮（上）定宽数值第一步与 FFI；LANG-27／LANG-28——2026-10-15 随单）

**新增与修订**：§3 定宽类型名与外部声明用词、整数字面量取型；§4.1 数值行与“定宽数值”“句柄”“外部结构体”三行；§4.2-1；§5 `top_decl`、`extern_decl`、`extern_from`、`extern_type_decl`、`base` 注记；§6.1-4 按目标选择文件；§6.5-3 注记；§6.8 第 7–20 条（FFI）；§6.21 定宽数值（新节）；§7 `wrapTo`、`floatToBits`、`floatFromBits`、`handleBits`、`handleFromBits`、`ffiLastError` 与 `toStr` 注记；§8 O4／O1 注记与 v0.23 排除增补。

**实现面**：词法零改动；AST 新增节点 72（外部函数的 `from` 子句）、73 `declare type`、74 `declare struct`；sema 序数 59 `wrapTo`、60 `handleBits`、61 `handleFromBits`、62 `ffiLastError`（运行期 BuiltinId 60）、63 `floatToBits`、64 `floatFromBits`；semamirror 类型池与 SEB1 的定宽类型 21（另载种类码）与句柄类型 22，SEB1 在 `GEN1` 之后、`LVE1` 之前追加 `FFI1` 节（程序含带 `from` 的外部函数且无诊断时）；操作码 `cvt`＝52（种类 1..26）；`.txc` 尾节 `TXFFI` 与格式版本 5；NRT 调用桩（`bootstrap/selfhost/ffi_stubs.tx`）、回调蹦床（`ffi_callbacks.tx`）、错误码槽与内核导出（`nrt_ffi.tx`）、Linux 动态 ELF（`link_elf_objects.tx`）；运行期码 4018 `narrow_range`、4019 `ffi_arg`、4020 `ffi_callback`；诊断码 3033（FFI 与按目标文件）、3034（定宽数值）。parser.txc、semamirror.txc 重烘，frontend-probe 的 v0.17 诊断逐字节不变。**Zig seed 冻结于 v0.17，不实现本版**。实施记录见两份设计稿的实施记录各节（定宽数值设计稿 §四与提交 b4b2940；FFI 设计稿 §十二–§十五）。

**用例**：`tests/nrt-cases/` 下 fw-basic、fw-narrow、fw-f2i、fw-f32、fw-unsigned、fw-bad-ops、fw-bad-types；ffi-front、ffi-handle-names、ffi-bad-decls、ffi-bad-from、ffi-bad-syntax、ffi-bad-use、ffi-variant-mismatch；Windows 专属 ffi-win-call、ffi-win-cstr-nul、ffi-win-syscall、ffi-win-struct、ffi-win-callback、ffi-win-callback-thread、ffi-win-wstr-empty；Linux 专属 ffi-linux-syscall、ffi-linux-lib、ffi-linux-struct、ffi-linux-static、ffi-linux-wstr、ffi-linux-callback-thread、ffi-linux-callback-outside；float-bits、float-bits-bad。标准库平台层（taixu-stdlib 的 `*_sys_windows.tx`／`*_sys_linux.tx` 与 std/math 纯太虚实现）是 FFI 的第一个大型使用者。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.22（去 Zig 第 4b 轮生成器；LANG-26——2026-10-15 随单）

**新增与修订**：§3 `function*` 与关键字 `yield`、预声明 `Gen`／`genNext`；§4.1 “生成器”行；§4.2-11；§5 `function_decl`、`base`、`yield_stmt`；§6.5-3 注记；§6.10-2 注记；§6.20 生成器（新节）；§7 `genNext`；§8 v0.22 排除增补。

**实现面**：词法零改动（`*` 复用乘号记号，`yield` 由解析器按所在函数识别，lexer.txc 不变）；AST-BIN 函数节点的 async 位取 2 表示 `function*`，`yield` 语句为节点 71；sema 序数 57 `genNext`、58 内部关闭（for-in 解糖专用，用户不可写）；semamirror 类型池与 SEB1 的类型种类 20，SEB1 在 `IFC1` 之后、`LVE1` 之前追加带载荷的 `GEN1` 节；`.txc` 类型种类 19；操作码 48–51；NRT 生成器运行时（`bootstrap/selfhost/nrt_gen.tx`：Obj72 kind 12、payload kind 9，值 tag 18）；运行期码 4017 `gen_running`；诊断码 3032。parser.txc、semamirror.txc 重烘，frontend-probe 对照集逐字节一致：守护负例 `tests/cases/guard-bad-generator.tx` 的输入改为 `async function * gen`（两个前端仍报“期望 标识符，遇到 '*'”），“不可迭代”诊断原文保持 v0.17 不变（提示里不列 `Gen`）。**Zig seed 冻结于 v0.17，不实现本版**。

**用例**：`tests/nrt-cases/` 下 gen-basic、gen-close、gen-more、gen-generic（.out），gen-reentry（.err，运行期 trap），gen-bad-yield、gen-bad-elem、gen-bad-fail、gen-bad-defer、gen-bad-eq（.err）。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.21（去 Zig 第 4 轮原生异步、原生 run/test 与太虚版 fmt；LANG-25／TOOL-81／TOOL-82——2026-10-14 随单）

**新增与修订**：§3 预声明工具链系统内建；§4.1 Future 行注记；§6.12-6/8/9 修订、§6.12-10 原生实现；§6.16-6 修订、§6.16-8 async 函数中的 `defer`；§6.17-10 措辞；§7 七个工具链系统内建、`test` 块注记。

**实现面**：sema 序数 50..56、BuiltinId 53..59（`host_target`..`mono_ns`，须小于 extern 基址 64）；NRT 内核新增任务运行时（任务表、FIFO 就绪队、Obj72 future、定时器、调度泵、四个任务内建）与系统内建（Windows 直调 kernel32，Linux 只用 syscall）；可挂起函数保守发射（不做帧内分配、帧缓冲、借用约定与叶内联）与恢复入口，取消落点登记在 ProgramContext（不进 `.txc`）；诊断 3026 增“defer 体内不能 await”、去掉“async 函数内暂不支持 defer”；3028（async 失败侧为错误 enum）的提示改为“取消产生的是普通 Error，错误 enum 失败侧须与取消语义一起设计”。工具链：原生 `taixu run`（文件之后的参数全部交给程序）、`taixu test`（测试表、每个 test 一个进程）、`taixu fmt`（与 Zig fmt 逐字节一致，`bootstrap/selfhost/fmt.tx`）。自举链增一级 `bootstrap/STAGE1`＝339040d（当前编译器源码使用 S0 不认识的系统内建）。**Zig seed 冻结于 v0.17**，VM 只修两处缺陷（§6.12-8、§6.12-10）。

**用例**：`tests/nrt-cases/` 下 async-sleep-wait、async-defer-cancel、sys-proc（.out），test-blocks（.test），defer-bad-await、sys-bad-notoolchain、errors-bad-async（.err）；`tests/cases/async-trap-erased-await`、`async-stress-growth`；原有 18 个 async 用例中 16 个进入 NRT 差分（2 个 `--mem-report` 为 VM 专属）。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.20（去 Zig 第 3 轮接口、泛型约束与泛型 Map/Set；LANG-22／LANG-23／LANG-24——2026-10-14 随单）

**新增**：§3 关键字 `interface`、`extends` 扩到类型参数、prelude 导出名入预声明标识符；§4.1 “接口”“Map/Set”两行与 List 行注记；§4.2-2 注记；§5 `interface_decl`、`type_params`；§6.9-5 List 值类型元素；§6.10-1/2 补充；§6.18 接口与泛型约束、§6.19 prelude Map/Set（新节）；§8 注记。

**实现面**：记号 `interface`＝74（尾段追加）；AST-BIN 节点 68 接口声明、69 带约束的类型参数表；诊断码 3030（实现关系、约束、接口值的使用限制）、3031（带约束模板的定义处检查）；SEB1 程序含接口方法调用时追加 `IFC1` 节；接口值在类型池记为 `Ref<隐藏 struct>`；codegen 对 `List` 值类型元素在存取处插入既有 clone 指令。零新 opcode。prelude 源码在仓库 `prelude/collections.tx`（不在任何与冻结 Zig 前端对照的语料目录里），由 `bootstrap/taixu.tx` 经 `embedBytes` 嵌入。程序用到 `List` 值类型元素时 SEB1 追加无载荷的 `LVE1` 标记节（只有太虚 codegen 在存取处插拷贝，Zig CLI 的加载器遇到未知尾节即拒绝）。**Zig seed 冻结于 v0.17，不实现本版**。守卫 `tests/cases/guard-bad-list-elem.tx` 的输入由 struct 元素改为仍不收的切片元素（两个前端诊断仍逐字节一致，frontend-probe 对照集不变）。

**用例**：`tests/nrt-cases/` 下 iface-values、iface-generics、iface-modules、clone-ref-field、list-values、map-set、prelude-shadow（.out）与 iface-bad-*（9 个）、map-bad-*（2 个）（.err）。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.19（去 Zig 第 2 轮 defer 与结构化错误；LANG-20／LANG-21——2026-10-14 随单）

**新增**：§3 关键字 `defer`、上下文关键字 `extends`、预声明 `errAs`／`errWrap`；§4.1 “错误 enum”行，错误与失败联合行修订；§4.2-8 放宽规则；§5 `defer_stmt`、`enum_decl` 扩展；§6.3-6；§6.16 `defer`、§6.17 结构化错误（新节）；§7 入口 `int | Error`、`errAs`/`errWrap` 两行、`toStr` 扩错误 enum。

**实现面**：记号 `defer`＝73（尾段追加）；AST-BIN 节点 65 `defer`、66 错误 enum 声明、67 `T | 错误enum`；诊断码 3026（defer 误用）、3027（错误 enum 声明与构造）、3028（失败侧类型与传播）、3029（`errAs` 类型实参）；sema 序数与 BuiltinId 48 `errAs`、49 `errWrap`，codegen 内部 BuiltinId 50 `errRich`、51 `errPayload`、52 `errCause`；SEB1 类型 kind 19（错误 enum）与程序含错误 enum 时追加的 `ERR1` 节；NRT 富 Error 对象（struct 形容器，五个值）与 `__tx_bi_err_*`、`__tx_nrt_err_text`。`defer` 编译期展开，零新 opcode。**Zig seed 冻结于 v0.17，不实现本版**；Zig 内嵌 `lexer/parser/semamirror.txc` 随源码重烘，v0.17 程序的诊断与产物逐字节不变（frontend-probe）。

**用例**：`tests/nrt-cases/` 下 defer-basic、errors-enum、errors-main-ok（.out）与 defer-bad-*（5 个）、errors-bad-*（7 个）、errors-main-fail（.err）。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.18（V0.52 Bytes 类型与编译期嵌入；LANG-19——2026-10-13 随单）

**新增**：§4.1 `Bytes` 行；§6.15（新节）；§7 内建表 12 行（`bytesNew/bytesLen/bytesGet/bytesSet/bytesPush/bytesAppend/bytesSlice/bytesEqual/bytesFromStr/bytesToStr/readFileBytes/embedBytes`）；`len` 定义域加 `Bytes`；`writeFileBytes`／`writeExecutable` 内容参数加 `Bytes`。

**实现面**：sema 序数 36..47、BuiltinId 37..47（`embedBytes` 编译期降为字符串常量＋`bytesFromStr`）、SEB1 类型 tag 17、TXC TyKind 18、值 tag 17（NRT 内核的 `__tx_is_heapref` 表加 17，非 NRT 内核不变）；NRT 内建 `__tx_bi_bytes_*`／`__tx_bi_read_file_bytes`（`nrt_bytes.tx`）；Bytes 对象复用字符串对象形（ptr@32、len@40、cap@48）。**Zig seed 冻结于 v0.17，不实现本版**（自举策略见设计稿 §二）；Zig 内嵌 `semamirror.txc` 随源码重烘。

**用例**：`tests/nrt-cases/` 下 bytes-basic、bytes-embed-module（.out）与 bytes-oob、bytes-range、bytes-sema、bytes-embed-missing（.err）。

**同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.17（V0.52 目录创建与工具链可执行写出内建；LANG-18——2026-10-13 随单）

**新增（§7）**：

1. **`makeDir(path: string): void | Error`**——单层建目录，已存在的目录视为成功；错误沿用文件内建 `"{status}: {path}"` 格式。递归版 `makeDirAll` 由 taixu-stdlib 以 `.tx` 提供（Go `os.Mkdir`/`MkdirAll`、Rust `create_dir`/`create_dir_all` 同构分层）。
2. **`writeExecutable(path: string, data: List<int>): void | Error`**——工具链专用：普通程序写出的文件（含 `writeFileBytes`）不带执行位，只有显式 `--toolchain` 构建的程序（编译器自身）可以写出可执行文件。门控是编译期默认拒绝，不是沙箱。
3. **实现面**：BuiltinId 35/36 尾段追加；interp／AOT shim `__tx_bi_make_dir`、`__tx_bi_write_executable`／NRT 同名内建（Windows `CreateDirectoryW`，Linux `mkdirat`/`unlinkat`+`openat 0777`）；Zig CLI `run|build|test|aot --toolchain`（`run` 仅识别入口文件之前的开关）与 tx CLI `taixu build --toolchain`；SEIN 开关开启时头部为 `SEIN1 2 <flags> <n>`，关闭时保持 `SEIN1 1 <n>`。
4. **零扰动**：不用新内建的程序零扰动；`.txc` 格式与金哈希不变。
5. **同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`。

---

## 修订 v0.16（V0.50-S5-① 二进制字节写出内建；LANG-17——2026-10-03 随单）

**新增（§7）**：

1. **`writeFileBytes(path: string, data: List<int>): void | Error`**——二进制字节面覆盖写：元素须 int 且 0..255，越界/非 int 得结构化 Error（校验先于写出，无部分写）；与 `readFile`＋`byteAt` 构成二进制读写对偶。动机＝.tx 无 ≥0x80 单字节串构造通道（utf8Encode 拒、无原始字节转义——taixu-stdlib bytes.tx 头注在册），自举编译器汇编器/PE 链接器/.txc 序列化的二进制写出唯一通道（D102 §二"OS 文件与原始字节 I/O"白名单执行/事实原语）。
2. **实现面**：BuiltinId 34 尾段追加（追加只能尾加纪律）；interp callbi 分派＋AOT shim `__tx_bi_write_file_bytes` 双臂同源（tx_file_write_all）；verifyProgram callbi id 上界随尾位；EMITMIRROR 记录构造通用路面级扩面（通用路 id 无关——零新发射形态）。
3. **零扰动**：无该内建程序零扰动——五门随批；`--frontend` 双态 .txc 恒等（含新内建程序亲证）。
4. **同步链**：llm-guide → `skill/taixu/` 与 `.zcode/skills/taixu-s1/`（随单走完）。

---

## 修订 v0.15（V0.41-M0-② 字符字面量；LANG-16——2026-09-24 随单）

**新增（§3/§5/§6.14）**：

1. **词法面**：单引号字符字面量（zig lexer＋bootstrap/lexer.tx 生产 .tx 词法器双改＋kindId 72 尾段——drift 锚随单刷新：embed 双件重入库）；单引号由非法字符（1001）转为引入符＝零冲突加法（存量合法程序零变化）。
2. **语义面**：§6.14 四条（int 码点/转义集六件＋\u 评估位/结构化拒绝 1005 四形＋未闭合 1002/解析期 int 节点解糖）。byteAt 锚惯用形与存量魔数重写判据落册。
3. **零扰动证明**：解析期解糖不触 bytecode 面——金哈希 69b4e54b 恒等亲证；TEST-02 守护＝guard-ok-char（正例＋byteAt 锚）＋guard-bad-char-empty/multi/escape/u/unterminated（负例 e2e）；diff-lexer/diff-parser 全量 0 different（词法级负例的 cmdParse 对称件随单：parser.tx main 首错行 `! line col code` 口径与引导侧字节对齐——潜伏不对称顺带闭合）。
4. **同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（随单走完，副本亲核）。

---
## 修订 v0.14（V0.39-G0 位运算批＋重写清偿；LANG-15——2026-09-23 随单）

**新增（§3/§5/§6.13/§7——弧 G1-b 六条约束落地不走样）**：

1. **词法面**：`& ^ ~` 三 token 变更（zig lexer＋bootstrap/lexer.tx 生产 .tx 词法器双改＋kindId 68/69/71 尾段——drift 锚随单刷新）；位移 token 零变更（解析器邻接组合，§5 注记 6——当初排除理由所保护的词法极简原封不动；与 TS 对比＝形式取 TS、机制取更简者）。
2. **语义面**：§6.13 五条（int 专属/移位 0..63 trap 非 JS 掩码/`>>` 算术·`>>>` 逻辑钉死/imul 64 位环绕与 `*` trap 分立/优先级 TS 同构）。RuntimeCode 4016 `shift_count`。
3. **VM/AOT**：Op 40–47 追加编码值（旧产物不含先例第 10 批）；interp `bitArith`；AOT x86-64 内联直译（and/or/xor/imul r64←r/m64＋shl/shr/sar r64,cl＋not r64——编码锚 14/15 GAS 真值常驻）＋交叉后端 shim 慢路（`__tx_arith_slow` 位运算臂＋`__tx_bnot` 微臂）＋qemu-e2e 交叉位运算段（37-bitops 入三目标负载集）。
4. **wrapping 面**：`imul` 内建（BuiltinId 31 尾段追加——**非宿主调用**评审钉死）；32 位环绕家族（wrapping add/sub/shl 等）评估落册＝**维持不实施**（现状无用例、显式选入面 YAGNI——需要时按本批先例尾段追加）。
5. **重写清偿（弧 G1-c）**：std/random splitmix64 复归（序列变更＝行为变更如实登记＋新 golden 锚＋README 注记）；std/map_set 位混合哈希（迭代序＝插入序红线亲证不动）；taixu-gui modHas 位测试（行为恒等＋golden 守护）；std/hash SHA-256 位运算原生重写（算术位分解退役——FIPS 向量复锚＋A/B 呈报）；std/strconv 评估结论落册（parseInt 维持除法形——有符号溢出检测位运算无益）。
6. **TEST-02 正负守护**：正例＝37-bitops e2e（全谱＋优先级＋嵌套泛型×位移同文件锚＋`f<int>(x) << 2`/`a << (b+1)`/空白形三锚）；负例＝guard-bad-bitop-float（float & 拒绝）、guard-bad-bitnot-bool（`~bool` 拒绝）、`a > > b` 带空白形结构化拒绝、移位 64 界外 trap。

**附带评估位两件（评审补掉单——判定落册）**：①`?.` optMap 闭包通道：维持不实施——`?.` 已有定向误写诊断面，optMap 闭包通道与 `catch` 默认值/`??` 表达力重叠（`v catch d` 覆盖缺省场景；映射场景 `match` 面外）——再评估触发＝真实反馈出现表达力缺口；②字符串索引糖 `s[i]`：维持不实施——S1 字符串为 UTF-8 字节串，`byteAt(s, i)` 已承载且返回 `T?`（越界 None），糖形 `s[i]` 语义（字节/码点？越界 trap/None？）存在二义性——再评估触发＝码点迭代 std/utf8 实战反馈。两件均如实登记不排 V0.40（除非触发）。

**同步链**：llm-guide → `skill/taixu/`（含 syntax-tour 副本）与 `.zcode/skills/taixu-s1/`（随单走完，副本亲核）。

---

## 修订 v0.31（核心 Zig 与宿主 extern 退场——2026-10-21 随单）

**v0.31 现行边界（2026-10-21，第 6 轮 6e）**：当前核心、运行时和 CLI 由太虚原生实现，公开 VM 与 `.txc` 产物退场。模块级 `declare function` 必须有 `from "库"` 或 `from syscall 编号`；无 `from` 的宿主 extern 报 3033，即使未调用也报错。接口内的方法签名不是宿主 extern，不受影响。带 `from` 的既有 FFI、类型和 ABI 不变。GUI 引擎外移后的空窗已由用户在 2026-10-18 接受，当前不提供 desktop/gui 模板或内置 GUI 宿主。原生 `build/run/test`、格式化、调试、DAP、LSP、MCP 与源码汇编清单保留；`build --bytecode` 报用法错误 64。旧版 Zig/VM 描述只解释历史，不构成当前可用能力。

依据：`v052-round6-gui-tools-v0.1-20261016.md` §七、v0.2 的 GUI 外部库裁决及 `v052-round6-cleanup-v0.1-20261020.md`。本次落实既定清退范围，不放宽语义或安全边界。同步 test 仍不启动异步队列，退出释放队列所有权以便泄漏检查。
