# 太虚工具链兼容变更

工具链版本：0.3.0-beta.1。自动修复尚未实现。

## lang.literal-left-fixed-width

分类：2；层：lang；首次开发线：0.2.15-dev；实际实现：D3 v0.26。

原写法：

~~~tx
const s: u8 = 1; print(toStr(300 < s));
~~~

保留旧行为的迁移：

~~~tx
const s: u8 = 1; print(toStr(300 < s as int));
~~~

将右侧定宽值显式转换为 int，以保留旧比较类型。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#621-定宽数值v023-新增第一步v026-增第二步)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.literal-left-f32

分类：3；层：lang；首次开发线：0.2.15-dev；实际实现：D3 v0.26。

原写法：

~~~tx
const f: f32 = 0.1; print(toStr(0.1 < f));
~~~

保留旧行为的迁移：

~~~tx
const f: f32 = 0.1; print(toStr(0.1 < f as float));
~~~

将右侧 f32 值显式转换为 float，以保留旧比较精度。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#621-定宽数值v023-新增第一步v026-增第二步)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.local-service-trust

分类：3；层：tool；首次开发线：0.2.24-dev；实际实现：工具安全边界 v0.2。

原写法：

~~~tx
taixu mcp
~~~

保留旧行为的迁移：

~~~tx
taixu mcp --workspace <本地项目> --trust-workspace --request-timeout-ms 60000
~~~

由启动操作者配置本地工作区与库根；仅在明确信任的工作区启用运行和调试。同批的其他可见变化：LSP 也只读操作者授权的本地根，不带 --workspace 时只读当前目录，按目录上溯发现的标准库、GUI 库不在已授权根内时不再可读，须以 --library-root 或 TAIXU_STDLIB／TAIXU_GUI_ROOT 授权（taixu lsp --workspace <本地项目> --library-root <标准库 src>），与可执行文件同目录分发的 taixu-stdlib/src、taixu-gui/src 自动授权；MCP 工具的 timeout_ms 超过启动上限（--request-timeout-ms，缺省 10000）时由截断到 60000 改为按参数错误拒绝；调试会话 ID 由 1、2… 改为“worker 代数.序号”形式的不透明字符串，须原样回传；debug_step、debug_continue 在请求时限先到时暂停程序（stopped，reason pause），会话保留。需要更长的请求时由操作者加 --request-timeout-ms（1..1800000）。自动应用等级：unavailable。

规范：[D3](../../docs/design/v052-tool-security-boundaries-v0.3-20261023.md)；[AI 指南](../../docs/ai/llm-guide-s1.md#本地工具服务的信任与预算)。

## lang.ffi-borrow-length

分类：3；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.34。

原写法：

~~~tx
let buf: Bytes = bytesNew(16);
function more(): void { bytesPush(buf, 1); } // 作回调交给 fill(buf, more)，借用期内被调用
~~~

保留旧行为的迁移：

~~~tx
let pending: List<u8> = listNew();
function more(): void { listPush(pending, 1); } // fill(buf, more) 返回后再 bytesAppend(buf, listAsBytes(pending))
~~~

外部函数借用 Bytes（含 List<u8> 别名）期间只做原地读写；要增减长度就先记在独立的列表里，等外部调用返回后再改。v0.33 按容量判定，不超出容量的追加与任何缩短照常进行；v0.34 起一律 trap 4024。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#68-外部函数与原生-ffiv031)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.import-case

分类：2；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.35。

原写法：

~~~tx
import { parse } from "./Parser"; // 磁盘上为 parser.tx
~~~

保留旧行为的迁移：

~~~tx
import { parse } from "./parser";
~~~

按诊断给出的磁盘写法改导入说明符（目录名与文件名都须与磁盘逐字节一致）；在大小写不敏感的 Windows、macOS 上原先能编，到 Linux 本就编不过。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#61-模块与导入导入说明符三级规则)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.target-file-unix

分类：3；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.35。

原写法：

~~~tx
// x.tx 与 x_unix.tx 是两个无关的模块，import { f } from "./x" 总是 x.tx
~~~

保留旧行为的迁移：

~~~tx
// Linux、macOS 上 import { f } from "./x" 选 x_unix.tx；无关的模块改名为 x_posix_helpers.tx 等
~~~

已有 x_unix.tx、x_macos.tx 或 x_<系统>_<架构>.tx 这样命名的普通模块时改名；它们现在是按目标选择的文件，导入 ./x 时在对应目标上代替 x.tx（导出面不一致时报 3033）。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#61-模块与导入导入说明符三级规则)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.ffi-cstr-string

分类：2；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.36。

原写法：

~~~tx
declare function strlen(s: cstr): usize from "libc.so.6";
~~~

保留旧行为的迁移：

~~~tx
declare function strlen(s: string): usize from "libc.so.6";
~~~

外部函数签名里的 cstr 改写成 string：签名顶层的 string 即 UTF-8、NUL 结尾的 C 串，封送与原 cstr 相同（含 NUL 字节 trap 4019，返回空指针得空串）；wstr 不变。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#68-外部函数与原生-ffiv031)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.ffi-known-variadic

分类：2；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.36。

原写法：

~~~tx
declare function fcntl(fd: i32, cmd: i32, arg: int): i32 from "libc.so.6";
~~~

保留旧行为的迁移：

~~~tx
declare function fcntl(fd: i32, cmd: i32, ...arg: int): i32 from "libc.so.6";
~~~

系统 C 库里的常见 C 变参函数（open、openat、fcntl、ioctl、snprintf 等）按 C 原型声明：定参个数同原型，变参位置写成变参参数 ...名: 类型。此前写成全定参在 x86 上碰巧能用，在 Apple arm64 上被调方从栈上读到垃圾。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#68-外部函数与原生-ffiv031)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.float-nan-text

分类：3；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.36。

原写法：

~~~tx
const z = 0.0; print(toStr(z / z)); // x86 上曾打印 -nan
~~~

保留旧行为的迁移：

~~~tx
const z = 0.0; print(toStr(floatToBits(z / z) < 0)); // 打印 nan 不再带符号；要符号位就直接读
~~~

toStr 对任何 NaN 都打印 nan、不带符号（此前 x86 上无效运算的 NaN 打印 -nan）。无效运算产生的 NaN 的符号由架构决定，程序不应依赖；确需符号位时用 floatToBits 读最高位。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#621-定宽数值v023-新增第一步v026-增第二步)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.future-await-repeat

分类：3；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.38。

原写法：

~~~tx
const f = work(); const a = await f catch 0; const b = await f catch 0; // 曾经 b 是 None，toStr(b) trap
~~~

保留旧行为的迁移：

~~~tx
const f = work(); const a = await f catch 0; const b = await f catch 0; // 现在 b == a；旧行为里第二次 await 没有合法用法，无需迁移
~~~

同一 Future 可以多次 await，每次得同一结果（结果与 future 共享，同 JS/TS、C#、Kotlin、Swift、Dart）；此前结果移给第一个 await，第二次起得到不合类型的 None，一用即 trap bad_type。已取消的 future 每次 await 都得 err("task cancelled")。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#612-异步与任务运行时v08-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.child-env-current

分类：3；层：tool；首次开发线：0.2.24-dev；实际实现：D3 v0.39。

原写法：

~~~tx
setenv("MODE", "ci") catch panic(""); runProcess("/bin/sh", ["-c", "echo $MODE"], "") catch -1; // Linux、macOS 曾打印启动时的值
~~~

保留旧行为的迁移：

~~~tx
setenv("MODE", "ci") catch panic(""); runProcess("/bin/sh", ["-c", "echo $MODE"], "") catch -1; // 现在三个目标都打印 ci
~~~

工具链 runProcess、procSpawn、procSpawnLimited 的子进程继承本进程当前的环境（类 Unix 目标为进程级环境表，含 std/os 的 setenv；Windows 为进程环境块），同 Go、Python、Rust、Node；此前 Linux、macOS 传启动时的 envp，setenv 之后起的子进程看不到改动。要给子进程启动时的值，先把变量改回再起子进程。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.child-handles-std-only

分类：3；层：tool；首次开发线：0.2.24-dev；实际实现：D3 v0.40。

原写法：

~~~tx
const s = tcpListen(8080) catch -1; runProcess("cmd.exe", ["/c", "start /b server.exe"], "") catch -1; tcpClose(s); // Windows 上后台进程曾拿着 8080
~~~

保留旧行为的迁移：

~~~tx
const s = tcpListen(8080) catch -1; runProcess("cmd.exe", ["/c", "start /b server.exe"], "") catch -1; tcpClose(s); // 子进程只拿到标准三件，8080 随 tcpClose 释放
~~~

工具链 runProcess、procSpawn、procSpawnLimited 的子进程只得到标准输入、输出、错误（procSpawn 另加调试控制通道），本进程其余的文件、套接字、管道一律不继承，不论是否设了 CLOEXEC 或可继承标志（同 Python 的 close_fds、Go 的句柄白名单）；此前 Windows 继承全部可继承句柄，Linux、macOS 只靠 CLOEXEC。要交给子进程的数据经标准输入输出、文件或命令行参数传递。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.fmt-generic-angles

分类：3；层：tool；首次开发线：0.2.24-dev；实际实现：fmt 规范形修订 v0.1。

原写法：

~~~tx
const m: Map < string, List< int>> = mapNew();
~~~

保留旧行为的迁移：

~~~tx
const m: Map<string, List<int>> = mapNew();
~~~

taixu fmt 的规范形里泛型尖括号紧贴：List<int>、Map<string, List<int>>、wrapTo<i32>(x)、struct Box<T>（同 Rust、TypeScript、Java、C# 的格式化器）；此前只认 Ref／Weak／List 且开括号后留空格（List< int>），其余泛型排成比较运算（Map < string, int >）。`??` 两侧一空格、其后的负号紧贴（a ?? -1，原为 a?? - 1）。生成器声明的星号紧贴 function（function* 名，原为 function * 名）。只改空白，语义不变；旧排法的代码在 fmt --check 下报需要格式化，fmt --write 一次即可。自动应用等级：unavailable。

规范：[D3](../../docs/design/v052-fmt-generic-angles-v0.1-20261025.md)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.argv-valid-utf8

分类：3；层：lang；首次开发线：0.2.24-dev；实际实现：D3 v0.41。

原写法：

~~~tx
./prog $'\xff'  # Linux、macOS 上 main 拿到含坏字节的 string，strSub 对它 trap 4010
~~~

保留旧行为的迁移：

~~~tx
./prog $'\xff'  # 入口拒绝：taixu aot: Linux argv acquisition failed: InvalidUnicode，退出码 64
~~~

命令行参数不是合法 UTF-8（Windows 为不成对的 UTF-16 代理）时进程在入口拒绝启动：stderr 含 argv acquisition failed: InvalidUnicode，退出码 64（同 Rust 的 env::args()；Windows 自 2026-10-07 起即如此）。此前 Linux、macOS 把参数字节原样做成 string，破坏 string 恒为合法 UTF-8 的不变量。调用方须以合法 UTF-8 传参；std/os 的 args() 与 main(args) 逐项相同（保留空参数）。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.syscall-number-x86_64

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.47。

原写法：

~~~tx
declare function socket(d: i32, t: i32, p: i32): i32 from syscall 41;  // 编给 aarch64：3033
~~~

保留旧行为的迁移：

~~~tx
declare function socket(d: i32, t: i32, p: i32): i32 from syscall "socket";  // 各架构都对
~~~

from syscall 的数字写明是 x86_64 的系统调用号：编给 aarch64、loongarch64、riscv64 时，写在普通文件（含 x_linux.tx）里的数字声明在第一次调用时报 3033（同一个号在别的架构是别的调用，如 41 在 aarch64 是 pivot_root）。改写成名字 from syscall "socket"（按目标架构查内核号表），或把声明放进 x_linux_<架构>.tx 写该架构的号。只影响新目标，x86_64 上不变。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#68-外部函数与原生-ffiv031)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.catch-exit-not-stmt-exit

分类：5；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.49。

原写法：

~~~tx
function g(x: int): int { const n: int = f(x) catch (e) { return 0; }; print(toStr(n)); } // 曾经编译通过
~~~

保留旧行为的迁移：

~~~tx
function g(x: int): int { const n: int = f(x) catch (e) { return 0; }; print(toStr(n)); return n; }
~~~

补上漏写的 return。catch (e) 块离开（return、panic 等）只是失败路径离开，成功路径照常往下走；此前检查把它当成整条语句离开，函数漏写 return 时漏报 3012，运行时取到空值（bad_type）。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#63-失败可选错误传播与处理)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.generic-body-outside-loop

分类：5；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.49。

原写法：

~~~tx
function first<T>(xs: List<T>, d: T): T { break; return listTryGet(xs, 0) ?? d; } // 在 while 里调用时曾经编译通过
~~~

保留旧行为的迁移：

~~~tx
function first<T>(xs: List<T>, d: T): T { return listTryGet(xs, 0) ?? d; }
~~~

去掉泛型函数体顶层（不在任何循环里）的 break／continue。泛型函数在循环里首次实例化时，检查曾带着外层的循环层数，这样的 break 被放过、生成的跳转没有目标。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#64-控制流)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.no-increment-decrement

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.49。

原写法：

~~~tx
const y = --x; const z = a--b; // 曾经是两次取负与减负
~~~

保留旧行为的迁移：

~~~tx
const y = -(-x); const z = a - -b;
~~~

太虚没有 ++／--（D3 v0.49 §8）：零空白相邻的两个 + 或两个 - 一律给定向诊断。要自增自减写 i += 1／i -= 1；确实要两次取负写 -(-x) 或 - -x。taixu fmt 对含零空白相邻 ++／-- 的文件同样拒绝格式化。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#5-语法ebnf)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.optional-chain

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.50。

原写法：

~~~tx
function first(xs: List<P>): string? { const n = listGet(xs, 0)?.name; print("之后"); return n; } // 曾经：表为空时在 ?. 处返回 None，不打印
~~~

保留旧行为的迁移：

~~~tx
function first(xs: List<P>): string? { const n = (listGet(xs, 0)?).name; print("之后"); return n; }
~~~

x?.f 现在是可选链（D3 v0.50 §6.3-9）：x 为 None 时整个表达式得 None、继续往下执行，结果类型为 F?；原先的含义是“传播后取字段”（x 为 None 时当前函数立即返回 None），要保留原义写 (x?).f。结果类型从 F 变为 F? 的位置大多会编译报错（如赋给 F 的绑定）；只在结果本来就能放进 F? 的位置（返回 F? 的函数、F? 形参、?? 的左侧）语义静默改变。编译器、标准库、GUI 与全部用例里 x?.f 为 0 处。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#63-失败可选错误传播与处理)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.none-narrowing

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.50。

原写法：

~~~tx
if (x != None) { const y = x; print(toStr(y ?? 0)); } // y 曾是 int?
~~~

保留旧行为的迁移：

~~~tx
if (x != None) { const y = x; print(toStr(y)); } // 或 const y: int? = x;
~~~

const 局部与参数在判过 None 的区域里收窄为 T（D3 v0.50 §6.3-8）。对该变量本身照写 catch、??、后缀 ?、== None 仍按 T? 取，旧写法照常可编；但在区域里由它推断的绑定（const y = x;）现在是 T，之后再对 y 写 y ?? d、y == None 等会报 3002。去掉多余的 ?? 与判空，或给绑定写明类型 const y: T? = x。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#63-失败可选错误传播与处理)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.catch-binding-scope

分类：5；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.50。

原写法：

~~~tx
if (c) { const r = f() catch (e) { return 0; }; const n = r; } return n; // 曾经编译通过
~~~

保留旧行为的迁移：

~~~tx
let n: int = 0; if (c) { const r = f() catch (e) { return 0; }; n = r; } return n;
~~~

块形 catch (e) 的作用域原先少弹一层（D3 v0.50 §6.3-3 修正）：catch 表达式之后在同一块里声明的名字逃出所在块，块外仍可见、外层同名声明误报 3003。现在块结束即不可见；块外用到块内名字的写法报 3001，把声明挪到块外。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#63-失败可选错误传播与处理)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## runtime.windows-pe-hardening

分类：4；层：runtime；首次开发线：0.3.0-beta.1；实际实现：x86_64-windows PE 规范化 v0.1（P1 DEP、P2 ASLR）。

原写法：

~~~tx
declare function jit_run(code: Bytes, n: i32): i32 from "oldjit.dll" // 外部库直接执行传入的数据缓冲区，曾能运行；程序总装在 0x400000
~~~

保留旧行为的迁移：

~~~tx
declare function jit_run(code: Bytes, n: i32): i32 from "oldjit.dll" // 开启 DEP 后在 jit_run 里访问违例：库须先把代码放进可执行页；加载地址每次由系统选定
~~~

x86_64-windows 产物开启 DEP 与 ASLR（NX_COMPAT、DYNAMIC_BASE、HIGH_ENTROPY_VA，首选基址 0x140000000、带基址重定位，同 Go、Rust、Zig、MSVC 的缺省；aarch64-windows 原本就开）。DEP：数据页、堆与栈不可执行——太虚程序不受影响（运行时申请的内存一律可读写不可执行，代码只在映像的 .text 里）；经 FFI 调用的外部库若把可写不可执行的内存当代码执行（自带跳板、旧式 JIT），会触发访问违例，须由该库自己经 VirtualAlloc／VirtualProtect 取得可执行页。ASLR：程序每次启动的加载地址由系统选定，太虚代码看不到地址、不受影响；外部库收到的回调函数指针与数据指针每次运行不同，不能保存成常量或跨进程使用。自动应用等级：unavailable。

规范：[D3](../../docs/design/v052-pe-x64-normalize-v0.1-20261103.md)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.list-index-trap

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.51。

原写法：

~~~tx
const c = byteAt(src, pos) ?? 0; const first: int? = listGet(xs, 0);
~~~

保留旧行为的迁移：

~~~tx
const c = byteTryAt(src, pos) ?? 0; const first: int? = listTryGet(xs, 0);
~~~

listGet、listPop、byteAt 越界（空表）即 trap 4002、返回 T（D3 v0.51 §6.9-2；越界是编程错误）。对结果写 ?? 默认值、catch 默认值或后缀 ? 的旧写法编译报错并给改写：可能越界改用 listTryGet／listTryPop／byteTryAt（越界或空表得 None，与原先完全相同；机械替换即保持行为），下标确定在界内就去掉默认值。结果原本直接放进 T? 的位置（T? 绑定、返回 T? 的函数、T? 形参）照常编译，越界时由得 None 变为 trap——这类位置要可能越界的语义请同样改用 Try 版。--toolchain 构建（编译器与构建脚本，由种子 0.3.0-beta.1 编译）在种子换代前沿用原先的语义。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#69-list-容器v05-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.dead-copy-write

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.51。

原写法：

~~~tx
let t = listGet(todos, i); t.done = true; // 改的是拷贝，todos 里的元素不变
~~~

保留旧行为的迁移：

~~~tx
let t = listGet(todos, i); t.done = true; listSet(todos, i, t);
~~~

struct 类型的 let 局部是一份拷贝：对它的字段写入之后若该变量再未被读取、传出、返回或被闭包捕获，这次写入不可能有作用，现在报 3036（D3 v0.51 §6.4-5）。多是想改 List 或 Map 里的元素：改完用 listSet／mapSet 写回，或把元素改存 Ref<T>；确实多余的写入删掉。编译器、标准库与 GUI 的全部源码核对过没有这种写法。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#64-控制流)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.str-builtins-byte-names

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.52。

原写法：

~~~tx
const head = strSub(s, 0, 4); const at = strByteIndexOf(s, ":");
~~~

保留旧行为的迁移：

~~~tx
const head = slice(s, 0, 4); const at = strFind(s, ":"); // import { slice } from "std/utf8"
~~~

字符串的整数位置与长度一律按字节（D3 v0.52 §7）：strSub、strIndexOf（按码点）与 strByteSub、strByteIndexOf（v0.44 的字节面）移除，普通构建里调用报 3001 并给改写，逐字等价。按字节：strByteSub(s, a, n) 改 strSlice(s, a, a + n)（第三实参由字节数改为终点），strByteIndexOf 改 strFind；按码点：strSub(s, a, n) 改 std/utf8 的 slice(s, a, a + n)，strIndexOf 改 std/utf8 的 find。--toolchain 构建（编译器与构建脚本，由种子 0.3.0-beta.1 编译）在种子换代前照收、语义不变。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.str-for-in-codepoint

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.52。

原写法：

~~~tx
for (b in s) { sum = sum + b; } // v0.51：逐字节
~~~

保留旧行为的迁移：

~~~tx
let i = 0; while (i < len(s)) { sum = sum + byteAt(s, i); i = i + 1; }
~~~

for (c in s) 改为逐字符产出码点（D3 v0.52 §6.10-2），此前逐字节：ASCII 串结果不变，含非 ASCII 字符的串迭代次数与取值都变，没有诊断。要逐字节改写为 while 加 byteAt；要字符的位置用 for (i, c in s)，i 为该字符首字节的字节偏移，可直接交给 strSlice。编译器、标准库与 GUI 的全部源码按三个目标核对过，没有依赖逐字节迭代的写法。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#610-语法糖三件v06-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.compare-eval-order

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.52。

原写法：

~~~tx
if (next() > limit()) { … } // v0.51：先调 limit()
~~~

保留旧行为的迁移：

~~~tx
const lim = limit(); if (next() > lim) { … } // 要先调 limit() 就显式写出
~~~

a > b、a >= b 改为先求值 a、再求值 b（D3 §6.5-1 表达式自左向右求值）；此前编译器把它们当作 b < a、b <= a 发码，连求值顺序一起对调，先求值右侧。只有两侧都有副作用且依赖先后时结果不同；要右侧先算就先把它存进局部。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#65-求值顺序与相等)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.main-entry

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
async function main(args: []string): int { const v = (await load())?; return v; }
~~~

保留旧行为的迁移：

~~~tx
function main(args: []string): int { const v = await load() catch 1; return v; }
~~~

main 是纯入口（D3 v0.53 §7）：任一模块顶层的 main 不能调用（含递归）、取值或导入，报 3010／3008；async function main 报 3010，main 体内可直接 await。原先这些写法都能编译（async main 运行到未决 await 即 trap）；现有代码里调用、导入 main 的为 0 处。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.entry-await

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
function main(args: []string): int { const f = report(); return 0; } // v0.52：main 返回后 report 照常运行
~~~

保留旧行为的迁移：

~~~tx
function main(args: []string): int { const f = report(); const r = await f; return 0; }
~~~

main、test 块与 threadSpawn 实参位置的箭头是同步入口，体内可直接 await（D3 v0.53 §6.12-4）；入口返回即取消未完成的任务（挂起中的先执行已登记的 defer），不再像 v0.52 那样在 main 返回后由调度泵跑完全部剩余任务，退出 pending 守护与 trap 文案后的未决任务数一并删去。依赖“main 返回后任务照常跑完”的程序没有诊断，任务的输出与副作用会消失：在 main 里 await 要它完成的 Future。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#612-异步与任务运行时v08-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.future-discard

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
job(); // v0.52：合法，发后即忘
~~~

保留旧行为的迁移：

~~~tx
detach job(); // 或 const r = await job();
~~~

把 Future 当表达式语句丢掉是编译错误（D3 v0.53 §6.12-11）：直接的 async 调用、返回 Future 的普通函数、sleepMs、for 更新段都算。要发后即忘写 detach 语句（上下文关键字，只在语句开头且后随标识符时识别）；要结果就 await。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#612-异步与任务运行时v08-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.await-arrow-scope

分类：2；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
async function f(): int { const g = (): int => { return await h() catch 0; }; return g(); }
~~~

保留旧行为的迁移：

~~~tx
async function f(): int { const g = async (): int => { return await h() catch 0; }; return await g() catch 0; }
~~~

await 的资格按函数／箭头各自判定，嵌套的普通箭头不继承外层（D3 v0.53 §6.12-2）：此前嵌套在 async 函数里的非 async 箭头能写 await、能过检查，运行到未决的 await 才 trap「await 执行于无任务上下文」。把箭头声明成 async 并 await 它的结果，或把 await 移到外层函数体里。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#612-异步与任务运行时v08-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## lang.threads-end-with-main

分类：3；层：lang；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
function main(args: []string): int { const t = threadSpawn(work); return 0; } // v0.52：进程等 work 跑完
~~~

保留旧行为的迁移：

~~~tx
function main(args: []string): int { const t = threadSpawn(work); threadJoin(t) catch (e) { print(e.message); }; return 0; }
~~~

main 返回即结束进程，未 join 的线程随之终止、不执行它们的 defer（D3 v0.53 §6.22-1，同 C、Rust、Go）；此前进程等全部线程结束再退出。没有诊断：要线程跑完就 threadJoin 它。线程函数返回时也改为取消本线程未完成的任务（§6.22-6）。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#622-线程发送与共享v025-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## std.task-generic

分类：2；层：std；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
const tg = taskGroupNew(); taskGroupAdd(tg, job());
~~~

保留旧行为的迁移：

~~~tx
const tg = taskGroupNew<int>(); taskGroupAdd(tg, job());
~~~

std/task、std/pool 对结果类型泛型（D3 v0.53 §6.12-9）：TaskGroup<T>、Pool<T>、timeout<T>(f: Future<T>, ms): T | Error；无参的 taskGroupNew、poolNew、poolDefault 推不出类型，报 3002，须写 taskGroupNew<int>() 或由期望类型推出；写在签名里的 Ref<TaskGroup>、Ref<Pool> 改为 Ref<TaskGroup<int>>、Ref<Pool<int>>。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#612-异步与任务运行时v08-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## std.map-set-retire

分类：2；层：std；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
import { mapNew, mapSet, objToMap } from "std/map_set"; const m = mapNew();
~~~

保留旧行为的迁移：

~~~tx
import { objToMap, Json } from "std/json"; const m: Map<string, Ref<Json>> = mapNew();
~~~

std/map_set 的非泛型 Map（值恒为 Ref<Json>）退役（D3 v0.53 §6.19-8）：导入它报 3008 并提示改用 prelude 的 Map<K, V>、Set<T>（不必导入）；objToMap／mapToObj 移入 std/json，改用 Map<string, Ref<Json>>（objToMap 返回 Map<string, Ref<Json>>?）。mapSetInt／mapGetInt 之类的 int 键版本用 Map<int, V>。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#619-编译器自带-preludemap-与-setv020-新增)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.capabilities-no-version-gate

分类：3；层：tool；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
taixu capabilities --format=json // 曾有 capabilities.version_gate（内部关口号）
~~~

保留旧行为的迁移：

~~~tx
taixu capabilities --format=json // 按 capabilities.toolchain_version 判断版本
~~~

taixu capabilities 不再输出内部关口号：JSON 去掉 capabilities.version_gate，文本去掉 version_gate= 一项。它从来不是产品版本；比较版本用 toolchain_version、diagnostic_version 与 schemas。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。

## tool.capabilities-retired-list

分类：3；层：tool；首次开发线：0.3.0-beta.1；实际实现：D3 v0.53。

原写法：

~~~tx
taixu capabilities --format=json // capabilities.retired 曾以早期引导前端开头
~~~

保留旧行为的迁移：

~~~tx
taixu capabilities --format=json // capabilities.retired 为 bytecode,vm,gui-host
~~~

taixu capabilities 的退场列表不再列早期的引导前端：JSON 的 capabilities.retired 为 bytecode,vm,gui-host，文本末行只说旧 aot 命令与公开 VM 已退场。该前端从未随公开发行版提供。自动应用等级：unavailable。

规范：[D3](../../docs/design/D3-s1-language-spec-v0.53.md#7-内建函数与程序入口)；[AI 指南](../../docs/ai/llm-guide-s1.md#工具链兼容变更)。
