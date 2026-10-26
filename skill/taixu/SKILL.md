---
name: taixu
description: 太虚（Taixu）语言开发规范与上手指南。在创建、编辑、评审 .tx 源文件，或构建/运行/测试太虚项目时使用。涵盖 S1 语法铁律、与 TypeScript 的差异、失败通道模型、值语义、生成错误自检与 CLI 工具协议。
---

**v0.34 FFI 借用（2026-10-23）**：`Bytes`（含经 `bytesAsList`／`listAsBytes` 的 `List<u8>` 别名）交给外部函数的借用期内，任何改变长度的操作（`bytesPush`、非空的 `bytesAppend`、`listPush`、`listInsert`、非空表的 `listPop`、`listRemoveAt`）一律 trap 4024，与当时的容量无关；原地读写（`bytesGet`／`bytesSet`／`listGet`／`listSet`）、空追加与空表弹出照常。取代 v0.33 按容量的判定（同 Python `bytearray` 存在导出时拒绝改长）。

**v0.33 安全边界（2026-10-23）**：新增工具链专用 `procTryWrite(int, int, Bytes): int`：单次非阻塞、每次最多 4096 字节、可部分写，暂不可写／中断或模式暂不可切换得 0，关闭／错误得 −1（得 −1 时该流已关闭，此后恒为 −1）；保留 `procWrite` 写满语义。FFI 借用 Bytes 期间，别名（含 List<u8>）可原地读写，不能请求超出当前容量、会迁移数据区的增长（trap 4024；v0.34 收紧为借用期内任何改变长度的操作）；嵌套与重复实参正确解除借用，正常返回后可增长。冻结 Bytes 仍在交给 C 前 trap 4022。C 保留返回后指针或外部 longjmp 不受保护。RC 最高位为永生，Windows 回调保存完整 XMM6–15。

**v0.32 工具链原语（2026-10-23）**：新增 `procSpawnLimited(exe, args, cwd, opts, memoryBytes, cpuSeconds): int | Error`、`readLocalFileBytes(root, relative, maxBytes): Bytes | Error`、`localEntryKind(root, relative): int` 和 `localFileSize(root, relative): int | Error`，均仅 `--toolchain`。进程限额须在执行前成功设置；Windows Job 总提交／累计用户态 CPU（内核态时间不计）与 Linux 继承的逐进程 AS／CPU 硬限额（每个后代各一份，不限进程数，后代可用 setsid 脱离进程组清理）作用范围不同，均不是不可信代码沙箱。文件原语按同一已核验句柄读取／查询，拒绝符号链接与重解析点、绝对或 .. 相对路径，超限或无法验证即拒绝；Windows 原语只接受盘符映射到本地硬盘卷的根（网络映射盘、SUBST 拒绝），Linux 原语本身不查文件系统类型、根内不跨挂载点，网络／FUSE 挂载由服务层启动时按挂载表拒绝；硬链接不检测。显式根授权由服务启动策略落实。二进制读取返回 Bytes，源码自行严格转 UTF-8。普通应用生成代码不调用这些原语。

# 太虚（Taixu）语言开发 Skill

自包含的太虚语言官方知识包。**先读本文件与 `examples/syntax-tour.tx`，再动手写 `.tx`**——不要凭 TS/Rust 肌肉记忆直接生成。

**安装**：将本目录（`skill/taixu/`）复制到你的 Agent 技能目录（ZCode：`~/.zcode/skills/`；Claude Code：`.claude/skills/`；其它 Agent 按其技能机制注册）。

> 工具链版本 0.3.0-beta.1（首次公测）。当前规范 D3 v0.42；命令以 `taixu capabilities` 为准。

> 开始生成代码前查询 taixu capabilities --format=json，核对 toolchain_version、diagnostic_version 与 schemas；version_gate 只是内部关口。遇到版本差异，用 taixu changes --format=json 查看迁移示例；applicability=unavailable 表示尚无自动修复。当前不提供 edition/API 版本门。

**v0.31 现行边界（2026-10-21）**：编译器、运行时和 CLI 全部由太虚原生实现，直接生成原生可执行文件。模块级 `declare function` 必须有 `from "库"` 或 `from syscall 编号`；无 `from` 的宿主 extern 报 3033，即使未调用也报错。接口内的方法签名不是宿主 extern，不受影响。带 `from` 的既有 FFI、类型和 ABI 不变。GUI 引擎外移后的空窗已由用户在 2026-10-18 接受，当前不提供 desktop/gui 模板或内置 GUI 宿主。原生 `build/run/test`、格式化、调试、DAP、LSP、MCP 与源码汇编清单保留；`build --bytecode` 报用法错误 64。


## 1. 一句话定位

TS 的皮（function / let / const / import / 花括号）、静态类型朴实子集、**值语义显式**（struct 拷贝、堆引用要 `new`）、**错误是值不是异常**（`T | Error` + `?` 传播）。源文件扩展名 `.tx`。

## 2. 十条铁律（违反即编译错误或语义错误）

1. **强制分号**；块用 `{}`，4 空格缩进，字符串一律双引号；
2. 无 `var` / `this` / 原型链 / `==` 隐式转换（`==` 只用于同类型）；
3. **无异常**：可失败函数返回 `T | Error`，调用处用 `?` 传播或 `catch` 处理；
4. `switch` 是**表达式**、**无穿透**（无 break）、对 enum **必须穷尽**或含 `else =>` 臂；
5. 堆分配只有 `new`，类型写 `Ref<T>`；`p.x` 自动解引用；
6. `T?`（缺失，值 `None`）与 `T | Error`（故障，`err("msg")`）**是两个类型、不隐式互转**，跨通道用 `catch` 显式桥接；
7. `int`（64 位）与 `float`（f64）不隐式转换，用 `as`；v0.23 的定宽类型（`i32`、`u8`…）只往无损方向自动扩宽，收窄用 `as`（越界 trap）；v0.26 起定宽算术越界即 trap（环绕要显式写 `wrapAdd` 等）——计数、下标、累加一律用 `int`；
8. **失败值不得丢弃**：`readFile(p);` 裸写是编译错误，必须 `?` / `catch` / 绑定 / 传参；
9. `const` 对值类型**深层冻结**；唯一例外 `Ref`：const 只锁重绑，对象字段可改（同 TS const object）；
10. 对象字面量 `{ x: 1 }` 需上下文类型（`const p: Point = { x: 1 }`），无上下文写 `Point { x: 1 }`。

## 3. 与 TS 差异速查表

| 主题 | TypeScript | 太虚 | 备注 |
|---|---|---|---|
| 函数 | `function f(a: number): number {}` | `function f(a: int): int {}` | 形式几乎相同 |
| 数字 | `number` | `int` / `float` 分离；v0.23 另有定宽 `i8`…`u64`、`isize`/`usize`、`f32` | 互转必须 `as`——它是数值转换（越界 trap），不是 TS 的类型断言 |
| 空 | `null` / `undefined` 无处不在 | `T?` 唯一入口，`None` 唯一空值 | 非可选类型保证非空 |
| 错误 | `throw` + `try/catch` 语句 | `T \| Error` + `?` / `catch` **表达式** | 见 §4 |
| 空值合并 | `a ?? b` | `a ?? b`（v0.12 起＝catch 同构糖，None/Error 均合并） | 同形 |
| switch | 语句、fallthrough、break | 表达式、`case Pat =>`、穷尽检查 | 仅限 enum scrutinee |
| 对象 | 引用语义 | struct **值语义**；`Ref<T>` 才是引用 | `new` 产生 `Ref` |
| 相等 | 原始按值、对象按引用 | 值类型按内容；`Ref` / 切片按同一性 | 心智一致 |
| 数组 | `Array` 引用 | `[N]T` 值拷贝；`[]T` 切片共享；`List<T>` 动态序列（六内建） | 字面量 `[1, 2, 3]` |
| 导入 | `from "pkg"` | `from "./相对路径"`；`from "std/模块"`（标准库） | 扩展名省略 |
| 枚举 | enum 无载荷 / 判别联合 | enum 变体**可带字段**（和类型） | 构造 `Expr.Add(l, r)`，模式 `case Add(l, r)` |

## 4. 失败通道模型（最易错处，重点）

```text
function loadSource(path: string): string | Error {
    const text = readFile(path)?;          // ? = 解包或传播：失败则本函数立即返回该 Error
    if (len(text) == 0) { return err("empty source"); }
    return text;
}

// ? 的结果类型是成功侧 T —— const n: int = loadSource(p) catch 0;  n 是 int，不是 int?
// catch F 的 F 是"失败时顶上的值"，整体类型 = 成功侧 ∪ F 的类型：
//   risky() catch 0            → T          （默认值）
//   opt()    catch None        → T?         （缺失降级）
//   opt()    catch err("...")  → T | Error  （缺失升级）
// catch (e) { ... } 块：非 void 语境必须经 return/panic 发散（块没有隐式返回值）
```

**禁止**：`try` / `throw` / 语句式 `catch`；`?` 结果标注为 `T?`；跨通道隐式转换。

**v0.19：defer 与结构化错误**：

```text
enum ConfigError extends Error {                        // 错误 enum：每个变体必须带消息
    Missing(path: string) = `找不到配置文件 ${path}`,   // 模板只能插值本变体字段
    Io(path: string, cause: Error) = `读取 ${path} 失败`, // 名为 cause 的 Error 字段＝原因
    Empty = "配置为空",
}

function load(path: string): Config | ConfigError {    // 失败侧可以是错误 enum
    const f = open(path) catch (e) { return ConfigError.Io(path, e); };
    defer { close(f); }                                 // 离开本块时执行（后进先出）
    if (empty(f)) { return ConfigError.Empty; }         // return 变体即失败；这里不能 return err(...)
    return parse(f);
}

// 调用方：catch (e) 中 e 是 ConfigError，可 switch 穷尽；? 只能同种传播或放宽成 Error
// errAs<ConfigError>(e) 从 Error 取回种类（得 ConfigError?）；errWrap(e, "上下文") 包一层；e.cause: Error?
// toStr(e) → "ConfigError.Missing: 找不到配置文件 a.conf"，有原因时追加 "；原因：…"
// main 可返回 int | Error：失败时 toStr(e) 写 stderr，退出码 1
```

- `defer` 是块作用域：循环体里的 `defer` 每一轮结束都会执行；体内不能 `return`、`?`、`await`，不能用 `break`/`continue` 跳出；`async` 函数内可用（v0.21，见下）。

**v0.20：接口、泛型约束、List 值类型元素与 Map/Set**：

```text
interface Shape {                        // 只有方法签名，不写接收者
    area(): float;
    name(): string;
}
struct Circle { r: float }
function circleArea(c: Circle): float { return 3.14 * c.r * c.r; }  // 约定名：类型名小写开头＋方法名
function circleName(c: Circle): string { return "circle"; }         // Circle 自动实现 Shape（不写 implements）

const shapes: List<Shape> = listNew();
listPush(shapes, Circle { r: 1.0 });     // 期望类型是接口时自动装入（值类型在装入时拷贝）
for (s in shapes) { print(s.name()); }   // 接口值调用＝动态分派

function maxOf<T extends Comparable<T>>(xs: List<T>, first: T): T { … }  // 每个类型参数至多一个接口约束
// 带约束的模板在定义处按约束检查：对 T 只能调用约束里的方法（及赋值、传参、存储、==），否则 3031

const ages: Map<string, int> = mapNew(); // 编译器自带 prelude：Map/Set，键须实现 Hashable
ages.set("li", 30);
const a = ages.get("li") catch 0;        // get 得 V?
for (k, v in ages) { … }                 // 插入序
const seen: Set<int> = setNew();
seen.add(3);                             // 得 bool：是否新加入
function pointHash(p: Point): int { return p.x * 31 + p.y; }        // 自定义键：写 xxxHash
```

- 接口值是共享句柄（同 `Ref`）：不能 `==`、不能 `switch`、不能取字段、不能再包 `Ref`；没有取回具体类型的手段。
- `List` 元素可以是 struct/enum/定长数组：存入、取出都拷贝；要原地修改就存 `Ref<T>`。
- `float` 不能作 Map 键（没有 `floatHash`）；函数值、接口值也不能作键。
- 实现函数写在类型声明所在的模块即可（不需要 export），泛型代码与其他模块都能找到。
- 跨错误种类不会隐式转换：`?` 从 `T | A` 传播到返回 `U | B` 的函数是编译错误，用 `catch` 显式转换或把失败侧改为 `Error`。

**v0.21：async 中的 defer、原生 run/test/fmt 与工具链系统内建**：

```text
async function copyOut(path: string): int {
    const f = open(path) catch (e) { return -1; };
    defer { close(f); }                  // 正常返回、错误返回、挂起中被取消，都会执行
    await sleepMs(0) catch (e) { return -1; };
    return 0;
}
```

- 任务挂起中（`await`、`taskAwaitAny` 未决）被 `taskCancel` 取消时，从挂起点按后进先出执行已登记的 `defer`，观察者得 `err("task cancelled")`；这时 `taskCancelled()` 为真。未启动就被取消的任务不执行任何 `defer`。`defer` 体内不能 `await`（3026）。
- 就绪队空、但还有未到期的定时器时，调度泵等到最早的定时器到期再继续，不判死锁（单任务 `await sleepMs(50)` 可以正常结束）。
- 同一 `Future` 可以多次 `await`（v0.38），每次得同一结果（与 future 共享：列表、结构体等引用类型得同一对象）；已取消的每次得 `err("task cancelled")`。`threadJoin` 不同：每个线程只能 join 一次，第二次得 `Error`。
- `async` 函数的失败侧仍只能是 `Error`（错误 enum 失败侧 3028）。
- 原生 `taixu run <file.tx> [程序参数...]`：文件之后的参数全部交给程序；`taixu test` 每个 test 块一个进程；`taixu fmt [--write|--check]` 由原生实现，以冻结兼容夹具和幂等性检查验证；规范形里泛型尖括号紧贴（`List<int>`、`Map<string, List<int>>`、`wrapTo<i32>(x)`，2026-10-25 起，此前排成 `List< int>`），`??` 两侧一空格（`a ?? -1`，此前排成 `a?? - 1`）。
- `hostTarget` `getEnv` `setEnv`（v0.42） `runProcess` `tempDir` `removeFile` `selfExePath` `monoNs`，以及 v0.27 的进程与标准输入内建 `stdinRead` `stdoutWrite` `procSpawn` `procWrite` `procRead` `ioWait` `procKill` `procWait` `procClose`、v0.28 的 `currentDir`、v0.29 的 `fileSize`、v0.32 的 `procSpawnLimited` `readLocalFileBytes` `localEntryKind` `localFileSize`、v0.33 的 `procTryWrite` 与 v0.35 的 `hostArch` `localListDir`，是**工具链专用**内建（编译器自身使用，仅 `--toolchain` 构建可用，否则 3001）——生成应用代码时不要使用。
- 以 `__rt` 开头的名字是标准库的内部内建（D3 v0.24 §7），只有标准库模块能调用，用户代码调用报 3001——生成应用代码时不要使用。

**v0.22：生成器 `function*`／`yield`／`Gen<T>`**：

```text
function* range(from: int, to: int): int {   // 声明写元素类型 T；调用得 Gen<int>
    let i = from;
    while (i < to) {
        yield i;                              // 只作语句；暂停并产出 i
        i = i + 1;
    }
}                                             // 走到末尾或 return; 即结束

const g = range(0, 3);                        // 只建生成器，函数体一行都不执行
const a = genNext(g) catch -1;                // genNext(g)／g.next() 得 T?：这里是 0
for (x in g) {                                // 接着取 1、2；None 即结束
    if (x == 1) { break; }                    // 提前离开（break/return/?）即关闭生成器
}
print(toStr(genNext(g) catch -1));            // 已关闭：None → -1

interface Source { items(): Gen<int>; }       // 生成器函数的类型就是 (参数) => Gen<T>
const make: (int, int) => Gen<int> = range;   // 经函数值、接口调用同样只建生成器
```

- `genNext(g)` 得 `T?`，不是 TS 的 `{ value, done }`；取完之后恒为 `None`；不能给 `next` 传值；`yield` 只作语句（不能写 `const v = yield x;`）。
- `yield` 只能直接写在生成器函数体内（生成器里的箭头函数中不行）；生成器体内 `yield` 是关键字，不能作变量名（生成器之外仍是普通标识符）。
- 元素类型不能是 `void`、`T?`、`T | Error`、`Error` 或错误 enum（3032）——要产出错误就包进 struct 或 enum。
- 生成器体内不能 `?`（用 `catch` 就地处理）、`return` 不能带值（3032）、不能 `await`（3024）、不能 `defer`（3026）、不能直接调用 `taskAwaitAny`（3032）。
- `Gen<T>` 是句柄：赋值、传参、存进 struct 字段或 `List` 都共享同一个生成器与进度；不能 `==`（3032）；for-in 只收单变量。
- 生成器运行中再对它自身取值是运行期 trap（4017，退出码 3）。丢弃或关闭生成器不执行生成器体，只释放它持有的值。
- 每次取值整块搬移生成器的局部变量，适合一般用途；极热路径上的逐元素迭代仍宜手写循环。
- 没有 `async function*`、`yield*`、箭头生成器与自定义迭代器协议。

**v0.23：定宽数值（第一步）**：

```text
const port: u16 = 8080;                  // 字面量按期望类型取型；越界即编译错误 3034
const n: int = port;                     // 无损方向自动扩宽：u16 → int
const b: u8 = (n % 256) as u8;           // 收窄必须 as；越界运行期 trap 4018
const all: u64 = wrapTo<u64>(-1);        // 2^64−1：取低位重解释用 wrapTo，不检查
const hr: i32 = wrapTo<i32>(0x80004005); // Win32 HRESULT 一类位串
const next: int = port as int + 1;       // 计数、累加用 int：先转 int 再算
print(toStr(port) + " " + toStr(all));   // toStr 接受定宽整数（u64 按无符号）
```

- 类型名 `i8 i16 i32 u8 u16 u32 u64 isize usize f32`；`i64`/`f64` 就是 `int`/`float`。这些名字与 `i3`、`u128` 一类 `i`/`u`＋数字的名字不能再作 struct/enum/interface/类型参数名（3034）。
- 默认不变：字面量和未标注推断仍是 `int`/`float`。**计数、下标、累加用 `int`**；定宽只用于存储、外部函数签名与确实需要宽度的地方。
- 自动扩宽只在赋值类位置（带类型的绑定、赋值、实参、返回、字段、`List` 元素、`catch` 默认值）、只往无损方向：`u8→u16→u32→u64`、`i8→i16→i32→int`、`u32→int`、`f32→float` 等；`u64`/`usize` → `int` 要写 `as`；整数与浮点之间永远要 `as`。
- `as` 是**数值转换**，不是 TS 的类型断言：收窄越界 trap 4018；浮点转整数向零截断，越界与 NaN trap 4005。`as` 优先级高于一元负号：写 `(-3.9) as i8`，不要写 `-3.9 as i8`。
- `wrapTo<T>(x)` 取低位、不检查——只用于位串与常量，类型实参必须写出。
- `u64`/`usize` 与有符号类型不能直接比较（3034）。v0.23 禁止的定宽算术、位运算与移位、`toStr(f32)`、超过 i64 最大值的 `u64` 字面量，v0.26 起都可以写，见下文 v0.26 段。

**v0.23：FFI——调用系统库与系统调用**（原生编译器支持）。**应用代码不要手写绑定**：文件、目录、环境变量、网络、数学等标准库已覆盖的能力一律用标准库；签名写错（参数个数、宽度）会悄悄破坏内存、运行时查不出。外部声明只写在标准库与平台层模块里，写法见 §8.1。

**v0.25：线程、发送与共享**：

```text
struct Config { name: string, sizes: List<int> }
struct Stats { hits: Atomic<int>, best: Atomic<int> }

function work(cfg: Shared<Ref<Config>>, st: Shared<Ref<Stats>>, id: int): int {
    const c = sharedGet(cfg);                 // 冻结的只读对象图：读不加锁
    const s = sharedGet(st);
    atomicAdd(s.hits, listLen(c.sizes));      // 可变的共享状态只能放 Atomic<int>
    return id;
}

const cfg = sharedNew(new Config { name: "a", sizes: sizes });   // 深拷贝进共享区，原对象照常可写
const st = sharedNew(new Stats { hits: atomicNew(0), best: atomicNew(-1) });
const ts: List<Thread<int>> = listNew();
let i = 0;
while (i < 4) {
    const id = i;                             // 捕获在 threadSpawn 时深拷贝进新线程
    listPush(ts, threadSpawn((): int => work(cfg, st, id)));
    i = i + 1;
}
for (t in ts) { print(toStr(threadJoin(t) catch -1)); }        // join 得 T | Error
print(toStr(atomicLoad(sharedGet(st).hits)));
```

- **隔离**：每个线程有自己的对象图。`threadSpawn` 深拷贝函数的捕获，`threadJoin` 深拷贝回结果（图内共享与环保持）；之后双方改各自的那一份，互不可见。模块级 `let` 每线程一份，新线程从初值开始（模块初始化里的副作用每个线程各发生一次）。
- `threadSpawn` 收无参、非 `async`、非生成器的函数值；`threadJoin(t)` 得 `T | Error`——同一线程第二次 join、线程创建失败都得错误值；线程函数自己返回 `T | Error` 时错误原样带回。放掉 `Thread` 句柄线程照常跑完；`main` 返回后进程等全部线程结束。
- **不可发送**（3035，指出成分与路径）：`Weak`、`Future`、`Gen`、`Thread`、`declare type` 句柄，以及 `Atomic<int>`（只能放进 `sharedNew`）。函数值、接口值静态放行，里面装的捕获或具体类型不可发送时在发送点 trap 4021。
- **`Shared<T>` 深度不可变**：从 `sharedGet` 取出的对象写字段、写下标、`listPush`/`listSet`/`listPop`/`listInsert`/`listRemoveAt`、`bytesSet`/`bytesPush`/`bytesAppend`，以及把冻结的 `Bytes` 或外部结构体 `Ref` 交给外部函数，都 trap 4022——要改就新建一份。`Shared` 句柄本身可以发送（不拷贝内容）、可以嵌进别的 `sharedNew`。
- 同一线程里句柄活着时 `sharedGet` 返回同一个根；根是 struct 值、enum 值、定长数组时交一份拷贝（可写，里面的 `Ref`/`List` 仍冻结）。大数据要零拷贝就用 `Shared<Ref<T>>` 或 `Shared<List<…>>`。
- `Atomic<int>` 只有 `atomicNew`/`atomicLoad`/`atomicStore`/`atomicAdd`（返回旧值，溢出 trap 4004）/`atomicCas`（返回是否换成），内存序都是 seqcst；`Atomic<float>` 等不存在（3020）。`sharedNew` 拷贝的是值单元的当前值：原对象里的 `Atomic` 与共享区里的是两个单元。
- `Thread`、`Shared`、`Atomic` 都不能 `==`（3002）、不能 `toStr`。任一线程 trap 即终止整个进程（退出码 3）；要隔离失败就让线程函数返回 `T | Error`。
- 没有 `Mutex`、条件变量、通道、线程池（以后的标准库）；`async`/`await` 只在本线程内调度，任务不跨线程。

**v0.26：定宽数值第二步——算术、位运算、`f32` 运算与紧凑存储**：

```text
const a: u8 = 200;
const b: u8 = 50;
const s = a + b;                          // u8 + u8 得 u8：250；超过 255 即 trap 4004「u8 加法溢出」
const w = wrapAdd(a, a);                  // 144：要环绕就显式写 wrapAdd/wrapSub/wrapMul
const total: int = a as int + 300;        // 计数、累加仍用 int
const i: i32 = -7;
const u: u32 = 3;
const mixed = i * u;                      // 按公共类型计算：i32 与 u32 得 int（-21）
const k = 1 + b;                          // 字面量取另一侧的类型（左右对称）：u8
const fnv: u64 = 14695981039346656037;    // 期望 u64/usize 时字面量可到 2^64−1
const hi = fnv >> 32;                     // 移位结果取左侧类型；无符号 >> 是逻辑右移
const f: f32 = 0.1;
print(toStr(f * 3.0));                    // f32 每步舍入到 f32；toStr(f32) 取最短往返：0.3
print(toStr(0.0 / 0.0));                  // 非有限值打印 inf、-inf、nan；任何 NaN 都是 nan（不带符号，v0.36）

const buf: List<u8> = listNew();          // 窄元素 List 每个元素占 1 字节，用法不变
listPush(buf, 0x7f);
const raw = listAsBytes(buf);             // 零拷贝：raw 与 buf 是同一个对象，改一边另一边可见
const seen: Set<u32> = setNew();          // 定宽整数可作 Map/Set 的键
```

- 算术 `+ - * / %` 与位运算 `& | ^` 按公共类型：一侧能无损扩宽到另一侧取较宽者，否则取两侧都能扩到的最小类型（`u8 + i8` 得 `i16`，`i32 + u32` 得 `int`）。`u64`/`usize` 与有符号类型、定宽整数与浮点之间没有公共类型（3034，先 `as`）。
- 整数越界一律 trap 4004（消息写明类型）；除零 4001；有符号最小值 / −1 trap，% −1 得 0；无符号用无符号除法。**没有静默环绕**——哈希、校验和、随机数一类要环绕的写 `wrapAdd`/`wrapSub`/`wrapMul`（类型实参可省，由两个实参推出；`wrapMul<int>` 即 `imul`）。
- 无符号值不能取负（3034）：要二补码写 `wrapSub<u32>(0, x)`。`~` 只用于整数，在类型宽度内取反。
- 移位结果取左侧类型，计数可为任一整数类型、须在 0..宽度−1（`u8` 为 0..7），否则 trap 4016；`<<` 舍去超出宽度的位；`>>` 对有符号是算术右移、对无符号是逻辑右移；`>>>` 在类型宽度内逻辑右移（`i8` 的 −1 >>> 1 得 127）。
- 字面量取另一侧的类型、左右对称：越出该类型的范围报 3034（`s + 300`、`300 < s`，`s: u8`）；与 `f32` 比较的浮点字面量按 f32 取值。`1 + x`（`x: float`）现在也合法。v0.23 写下的字面量在左的定宽比较因此有两处变化：`300 < s` 由合法变为 3034，`0.1 < f`（`f: f32` 由 `0.1` 初始化）由 `true` 变为 `false`——与右侧写字面量一致；要按更宽的类型比较就写 `300 < s as int`、`0.1 < f as float`。
- `f32` 运算每步舍入到 f32，溢出得 inf、不 trap；`f32` 与 `float` 都不支持 `%`（3002）。
- 期望类型为 `u64`/`usize` 时整数字面量可到 2^64−1（`0x`/`0o`/`0b` 也行）；其余位置超过 int 上限仍报 1004。
- `List<T>`（`T` 为 `i8`…`u32` 或 `f32`）按元素宽度紧凑存放，行为与其他 `List` 完全相同；大批字节、像素、样本用 `List<u8>`/`List<f32>`。
- `Bytes` 与 `List<u8>` 是两个类型（`bytesGet` 照旧得 `int?`）。`bytesAsList(b)`／`listAsBytes(l)` 零拷贝互转，**得到的是同一个对象**：改一边另一边可见（`listPop` 缩短长度也是）；要独立副本用 `bytesSlice(b, 0, len(b))`。只收 `Bytes` 的地方（`writeFileBytes`、外部函数）先 `listAsBytes`；`listAsBytes` 只收 `List<u8>`（`List<int>` 报 3002）；按 `u8` 遍历 `Bytes` 写 `for (x in bytesAsList(b))`。
- 定宽整数可作 `Map`/`Set` 的键（prelude 的 `i8Hash`…`usizeHash`，`x.hash()` 可用）；`f32` 同 `float`，不能作键（`Map<f32, V>` 报 3030）。
- 用法分工不变：**计数、下标、累加用 `int`**——`u8` 累加在 255 处就会 trap。

## 5. 内存与值语义（TS 心智映射）

| 太虚概念 | TS 对应物 | 行为 |
|---|---|---|
| struct 值（`Point`） | 原始类型 | 赋值拷贝、按内容比较、const 深冻结 |
| `Ref<T>`（`new` 产生） | object / class 实例 | 共享、字段可变、按同一性比较 |
| `Weak<T>`（`weak(r)` 产生） | （无对应；WeakRef 语义近似） | **不拥有目标**：`deref(w)` 得 `T?`（存活→值拷贝，已死→`None`）；按盒同一性；不延长生命周期 |
| `[N]T` 定宽数组 | （无对应，值语义） | 拷贝、按内容比较 |
| `[]T` 切片 | Array | 共享底层、按同一性比较 |
| `List<T>`（`listNew` 产生） | `Array` 动态序列 | 句柄共享、按同一性比较；操作仅六内建（无索引语法；方法**书写语法** v0.6 可用，见 §4.1 指南）；越界读 `None`、写 trap |
| `Gen<T>`（v0.22，`function*` 调用产生） | Generator 对象 | 句柄共享（同一进度）；不可 `==`；取完、关闭或丢弃时释放持有的值 |
| 句柄（v0.23，`declare type H;`） | 不透明指针 | 只能比较与传回外部函数；`handleBits(h)`／`handleFromBits<H>(v)` 取或造数值；`H?` 的 `None` 即空指针 |
| 外部结构体（v0.23，`declare struct`） | C struct | 值类型（拷贝、按字段比较）；只以 `Ref<S>` 传给外部函数，调用前拷入、调用后拷回 |
| `Shared<T>`（v0.25，`sharedNew` 产生） | （无对应；近似 `Object.freeze` 后跨 Worker 共享） | 句柄可发送；`sharedGet` 取出的对象图深度不可变（写入 trap 4022）；值类型的根交拷贝 |
| `Atomic<int>`（v0.25，`atomicNew` 产生） | `Atomics` 作用于 `SharedArrayBuffer` | 只经五个内建读写（seqcst）；只能放进 `sharedNew` 跨线程 |

- 递归类型必须经 `Ref` 间接：`enum Expr { Add(left: Ref<Expr>, right: Ref<Expr>) }`；
- `unref(r)` 从 `Ref` 整体取值拷贝；`Ref` 传给 `T` 形参**不会**自动解引用；
- `weak(r)` 只接受 `Ref<T>`；`deref(w)` 是 **unref 或 None**——需要长期持有就直接持有 `Ref`，Weak 只做观察（目标死没死）；
- enum 变体构造用**限定名** `Expr.Add(l, r)`（禁止前导点）；switch 模式用**裸名** `case Add(l, r) =>`。
- 引用计数**不回收强引用环**（`Ref` 经字段或 `List` 指回自己、闭包捕获装着它自己的 `List`）：反向的引用（子指父、观察者指回主体）写成 `Weak<T>`。怀疑成环时用 `--leak-check` 构建运行（见 §9 前的“泄漏检查”一条）。

## 6. S1 没有的东西（不要生成）

`?.`（分期）、字符串索引、类/继承/`this`/重载/`implements`（v0.20 起 `interface` 与接口值的动态分派可用）、`is`、动态类型、嵌套模板串、string 码点迭代。**`??`/解构声明（v0.12 可用）**：`a ?? b`≡`a catch b`；`const (a, b) = listExpr;` 元素型 `T?`。**用户泛型（v0.13 可用——单态化）**：`function id<T>(x: T): T`／`struct Pair<A, B>`；型参三通道（实参推断/期望型/显式 `f<int>(…)`）；每型参组合一实例（v0.24：实例化嵌套 ≤256 层、单实例类型规模 ≤2^20，不限总数；容量上限见 D3 附录 A）；enum 泛型/泛型闭包/where 不在面。替代方案：动态序列→`List<T>` 六内建（元素面含函数类型，v0.7）；Map/Set→v0.20 起编译器自带 prelude 的 `Map<K, V>`/`Set<T>`（直接用；标准库 `std/map_set` 仍可显式导入）。**位运算（v0.14 可用）**：`& | ^ ~ << >> >>>` 恒 int 操作数（float/bool 拒绝；v0.26 起也可为定宽整数，见 §4 的 v0.26 段）；移位计数 0..63 界外运行期 trap（非 JS 掩码；定宽整数为 0..宽度−1）；`>>`＝算术右移、`>>>`＝逻辑右移；`imul(a, b)` 64 位环绕乘（与 `*` 溢出 trap 分立）；优先级 `+ -` > 移位 > 比较 > `&` > `^` > `|` > `&&`。**字符字面量（v0.15 可用——LANG-16）**：'a' ＝ int 码点（97）——byteAt(s, i) == 'a' 即字节比较；转义集 '\n '\t '\r '\\ '\' '\0'；空/多字符/多字节/\u{...}（评估位）结构化拒绝（诊断 1005）。**闭包与函数类型（v0.7 可用）**：唯一箭头形式 `(a: T): R => 体`（参数/返回必须显式）；捕获＝值快照、闭包体内被捕获名只读（赋值即编译错误）；句柄共享要可变状态持 `Ref`；函数类型 `(T,…) => R` 可入标注位/struct 字段/List 元素；`obj.field(args)` 直调 fn 类型字段；函数值不可 `==`。**v0.6 语法糖（可用）**：方法语法 `nums.push(v)`≡`listPush(nums, v)`（约定名→同名用户函数→错误，解析序冻结）；for-in `for (x in it)`（List/字符串字节序/数组/切片/Map 键值——插入序红线）；插值 `` `n=${x}` ``≡`"n=" + toStr(x)`（`toStr` 面扩 string 恒等；D3 v0.11 增 Error 可读化；不支持嵌套模板、转义 `` ` `` 与 `\$`）。**异步与任务运行时（v0.8 可用）**：`async function f(): T` 声明写成功侧 T，调用即 spawn 得 `Future<T>`；`await` 仅在 async 函数/async 箭头体内（普通函数/main 内＝编译错误 3024），操作数须 `Future<T>`（3025），产出 `T | Error` 接 `?`/`catch`；调度＝确定性 FIFO 泵（main 同步体后跑任务，无微任务）；`taskCancel(fut)` 未决任务立即清理、await 得 `err("task cancelled")`、`taskCancelled()` 协作旗标、`taskAwaitAny(list)` 插入序裁决；main 返回后仍有未决任务→trap 退出码 3（必须 join 或显式取消）；`Future` 不可 `==`、不可嵌套；**定时器与库层超时（v0.9 可用）**：`sleepMs(ms): Future<void>`——到期泵置 done，deadline 升序/同刻登记序；`await sleepMs(0)`＝下一轮到期；负值＝err future（"sleep ms < 0"）；断言面只用 0ms/不到期两确定形态；`timeout(f, ms)`（std/task，单态 Future<int>）失败者取消→`err("timeout")`；`taskGroupCancel(tg)` 登记序传播、cancel 后 drain 必达零 pending。**语言核心回补（D3 v0.10 起可用）**：`listRemoveAt(l, i)`/`listInsert(l, i, v)` 补 List 删除/插入（越界 trap；`i==len` 即尾加）；`strSub(s, from, count)`/`strIndexOf(s, needle)`/`strSplit(s, sep)` 走**码元语义**（Unicode 码点计 units——CJK 安全；for-in 字符串仍字节序不变）；错误消息读取＝`e.message` 字段访问（catch 绑定值——错误打印写作 `print(e.message)`；D3 v0.11 起 `toStr(err)` ⇒ `"Error: <message>"`）。**定宽与 FFI 排除（v0.23）**：~~定宽值的算术与位运算~~（v0.26 已有）、`u3` 一类任意位宽、`i128`/`u128`、原始指针 `Ptr<T>` 与 `unsafe`、按值传递结构体、联合与位域、调用处任意个数的 C 变参（变参须在声明里写成 `...名: 类型`）、闭包作回调、懒装载动态库。**线程内排除（v0.25）**：`Mutex`、条件变量、通道、线程池、并行 map/reduce（以后的标准库）；原子内存序参数；除 `Atomic<int>` 以外的共享可变状态。**定宽排除（v0.26）**：`float`/`f32` 的 `%`、饱和运算、SIMD 向量类型、`f16`/`bf16`。

## 7. 常见生成错误自检清单

1. 写了 `try/catch` → 改 `?` / `catch` 表达式；
2. `?` 结果标注成 `T?` → 标 `T`；
3. `new Point(1, 2)` 构造器调用 → `new Point { x: 1, y: 2 }`；
4. `.Add(l, r)` 前导点 → `Expr.Add(l, r)`；
5. 字符串一律双引号；单引号是字符字面量（int 码点，如 'a'）——不能装字符串；
6. `var`、跨类型 `==`、`number` → `let/const`、同类型比较、`int/float`；
7. 丢弃失败值 → 必须 `?`/`catch`/绑定；
8. switch 漏变体且无 `else` → 穷尽检查会拒绝；
9. switch 臂里写语句块 → 臂只能是表达式，复杂逻辑拆辅助函数；
10. 忘分号、忘 `export`（默认模块私有）；
11. 写了 `?.`/逗号表达式 → 判空 / 拆语句（`??` 可用，同 `catch` 默认值）；
12. 联合写成 `T | 非 Error` → 只有 `T | Error` 合法；
13. 模板串段内再写反引号/`${` → 转义或拆段；
14. 线程之间共享可变的东西（全局变量、同一个 `Ref`/`List`）→ 线程只拿到深拷贝，改动对方看不到；只读大数据用 `sharedNew`/`sharedGet`，计数一类的可变状态放 `Atomic<int>`；从 `sharedGet` 取出的对象不能写（trap 4022）；
15. 在 `u8`/`u32` 等定宽值上写哈希、校验和、随机数，默认算术越界会 trap 4004 → 要环绕的地方写 `wrapAdd`/`wrapSub`/`wrapMul`；对无符号值取负 → `wrapSub<T>(0, x)`；计数与累加改用 `int`。

## 7.1 TS 边界速查（类 TS 非 TS，边界显式）

| TS 肌肉记忆 | 太虚事实 | 编译器反应 |
|---|---|---|
| `1 == "1"` | 跨类型 `==` 不存在 | 3002 `不能比较 int 与 string` |
| `if (list)` | 无 truthiness，条件必须 bool | 3014 `if 条件必须是 bool` |
| `let x = null` | 只有 `None`，且仅 `T?` 上下文 | 3001 `未定义符号 'null'` |
| `"n=" + 1` | 无隐式拼接：`` `n=${1}` `` 或 `toStr` | 3002 `算术须为同种数值或 string 拼接` |
| `a < b < c` | 比较不可连写 | 2001 `比较运算不能连写` |
| 嵌套模板串 | 仅单层插值；段内一个表达式 | 1003 `插值段内不支持嵌套模板串` |
| `(1, 2)` | 无逗号表达式 | 2002 `期望 ')'，遇到 ','` |
| `a ?? b` | v0.12 起有：与 `a catch b` 同义，None 与 Error 都换成右侧的值 | 无（合法） |
| `a?.b` | 无 `?.`；`T?` 判空后直用 | 3006 `传播 None，但当前函数不返回 T?` |
| `@dec` / `async function*` / `: any` / `implements` | 均不在面（v0.20 起 `interface` 可用，实现关系是结构化的；v0.22 起普通生成器 `function*` 可用，见 §4） | 1001/2002/3001/通用语法错 |
| `it.next().done` | `genNext(g)`／`g.next()` 得 `T?`，取完为 `None`；没有 `{ value, done }` | 3015 `对 int? 取字段 'done'` |
| `int \| string` | 联合唯一合法形态 `T \| Error` | 3020 `联合类型右侧必须是 Error` |
| 保留字全局禁用 | `declare`/`in`/`async`/`await` 为上下文关键字，其余场合可作标识符；`yield` 只在生成器体内是关键字（v0.22）；`from`/`syscall`/`type` 只在外部声明里有意义、`i8`…`f32` 与 `i`/`u`＋数字在类型位置保留（v0.23） | 表随语言版本冻结升版 |
| 函数不是值 | **是值**：箭头单一形式，参数/返回显式（`(int) => int`）；捕获＝值快照、闭包内只读 | 随 D3 v0.7 生效 |
| async 隐式排队 | **显式着色**：`async/await`；`await` 仅 async 体内；调度序＝FIFO 启动序，无微任务 | 已生效（D3 v0.8） |

每行都有自动化负例锁定（`tests/cases/guard-*` 用例）。

## 8. 内建函数、入口与工具协议

| 内建 | 签名 | 说明 |
|---|---|---|
| `print` | `(string) void` | 输出一行 |
| `eprint` | `(string) void` | 输出一行到标准错误（原样，不加前缀；v0.30 补记） |
| `err` | `(string) Error` | 构造错误 |
| `readFile` | `(string) string \| Error` | 读文本文件 |
| `listDir` | `(string) List<string> \| Error` | 目录里的条目名（不含 `.`、`..`，按字节序排序，不带类型）；打不开得 Error（v0.30 补记） |
| `entryKind` | `(string) int` | 条目类型，不跟随符号链接：0 不可判、1 文件、2 目录、3 符号链接（v0.30 补记） |
| `len` | `(string \| []T \| [N]T) int` | 长度（字符串为字节） |
| `toStr` | `(int \| float \| bool \| string \| Error) string` | 转字符串（Error ⇒ "Error: msg"；错误 enum ⇒ "E.V: msg"，有原因时追加 "；原因：…"，v0.19；定宽整数，`u64` 按无符号，v0.23；`f32` 取最短往返，v0.26） |
| `wrapTo` | `wrapTo<T>(整数) T` | 取低位按整数类型 `T` 重解释，不检查；类型实参必须写出（v0.23） |
| `wrapAdd`/`wrapSub`/`wrapMul` | `wrapAdd<T>(T, T) T` 等 | 按整数类型 `T`（含 `int`）的宽度环绕加、减、乘，不检查溢出；类型实参可省，由两个实参的公共类型推出；`wrapMul<int>` 即 `imul`（v0.26） |
| `floatToBits`/`floatFromBits` | `(float) int`／`(int) float` | 双精度与 64 位模式互转（最高位为符号位；任何位模式都能构造，含 NaN）——写数学库一类位操作用（v0.23） |
| `unref` | `(Ref<T>) T` | 从堆引用取值拷贝 |
| `weak` | `(Ref<T>) Weak<T>` | 构造弱引用（不拥有目标） |
| `deref` | `(Weak<T>) T?` | 目标存活得值拷贝、已死得 `None` |
| `byteAt` | `(string, int) int?` | **自举层**：源码逐字节访问（越界 `None`） |
| `utf8Encode` | `(int) string \| Error` | **自举层**：码点→UTF-8 串（非法码点 `Error`） |
| `listNew` | `listNew<T>(): List<T>` | 空表；`T` 经期望类型推断（`let l: List<int> = listNew();`），无期望编译错误 |
| `listPush` | `(List<T>, T) void` | 尾追加 |
| `listPop` | `(List<T>) T?` | 尾弹出（空表 `None`） |
| `listGet` | `(List<T>, int) T?` | 索引读（越界 `None`） |
| `listSet` | `(List<T>, int, T) void` | 索引写（越界 trap） |
| `listLen` | `(List<T>) int` | 长度 |
| `taskCancel` | `(Future<T>) void` | 协作式取消（未决任务立即清理；已完成空操作） |
| `taskCancelled` | `() bool` | 当前任务取消旗标（main 同步体恒 false） |
| `taskAwaitAny` | `(List<Future<T>>) int \| Error` | 插入序最先完成/取消者下标；全未决挂起等待 |
| `threadSpawn`/`threadJoin` | `(() => T) Thread<T>`／`(Thread<T>) T \| Error` | 在新线程里运行无参函数值（捕获深拷贝进去）；join 取回结果，第二次 join 或创建失败得错误值（v0.25） |
| `sharedNew`/`sharedGet` | `(T) Shared<T>`／`(Shared<T>) T` | 对象图深拷贝进共享区；取出冻结的只读对象图（写入 trap 4022；值类型的根交拷贝）（v0.25） |
| `atomicNew`/`atomicLoad`/`atomicStore`/`atomicAdd`/`atomicCas` | `(int) Atomic<int>`、`(Atomic<int>) int`、`(Atomic<int>, int) void`、`(Atomic<int>, int) int`、`(Atomic<int>, int, int) bool` | 原子整数，seqcst；`atomicAdd` 返回旧值、溢出 trap 4004；`atomicCas` 返回是否换成（v0.25） |
| `genNext` | `(Gen<T>) T?` | 取生成器的下一个值；取完得 `None` 且之后恒为 `None`；方法语法 `g.next()`（v0.22） |
| `sleepMs` | `(int) Future<void>` | 定时器：0ms＝下一轮到期；负值＝err future；断言面只用 0ms/不到期两形态 |
| `writeFileBytes` | `(string, List<int>) void \| Error` | 写二进制文件（覆盖写）：元素须 int 且 0..255，越界/非 int `Error`（无部分写）——汇编器/序列化级字节写出通道（v0.16） |
| `bytesNew`/`bytesLen`/`bytesGet`/`bytesSet`/`bytesPush`/`bytesAppend` | 见右 | **Bytes（v0.18）**：可变字节序列，句柄语义（同 List）；`bytesGet` 越界 `None`；`bytesSet`/`bytesPush` 越界或值不在 0..255 trap；方法语法 `b.push(x)` 可用 |
| `errAs`/`errWrap` | 见右 | `errAs<E>(e): E?` 从 Error 取回错误 enum 种类（类型实参必须写出）；`errWrap(e, "上下文"): Error` 包一层，`cause` 为原错误（v0.19）；仅原生编译器支持 |
| `bytesSlice`/`bytesEqual`/`bytesFromStr`/`bytesToStr`/`readFileBytes`/`embedBytes` | 见右 | 切片拷贝；`bytesEqual` 比内容；`bytesToStr` 严格 UTF-8 得 `string \| Error`；`readFileBytes(path)`；`embedBytes("相对路径")` 编译期嵌入（v0.18） |
| `bytesAsList`/`listAsBytes` | `(Bytes) List<u8>`／`(List<u8>) Bytes` | 零拷贝互转：返回同一个对象，改一边另一边可见；要独立副本用 `bytesSlice`（v0.26） |
| `bytesCopy`/`bytesFill`/`bytesGetLE`/`bytesGetBE`/`bytesSetLE`/`bytesSetBE` | 见右 | v0.37 批量操作：`bytesCopy(dst, at, src, from, count)` 区间拷贝（同一对象、区间重叠均可）；`bytesFill(b, at, count, value)`；`bytesGetLE<u32>(b, at)` 按小端读定宽数值得 `T?`（越界 `None`，解析不可信输入用 `catch` 给缺省值）；`bytesSetBE<u16>(b, at, v)` 按大端写。`T` 为 `i8`…`u64`、`int`、`f32`、`float`，必须写出；不收 `isize`/`usize`；写入的值须正是 `T`（窄类型先 `as`）。拷贝、填充、写入越界 trap 4002，写冻结的 `Bytes` trap 4022。逐字节循环或声明 `memcpy` 传地址的写法改用这些内建 |
| `makeDir` | `(string) void \| Error` | 创建单层目录，已存在的目录视为成功；递归用标准库 `makeDirAll`（v0.17） |
| `writeExecutable` | `(string, List<int>) void \| Error` | 工具链专用（仅 `--toolchain` 构建可用，否则 3001）：写出带执行位的可执行文件；普通程序不要使用（v0.17） |
| `hostTarget`/`hostArch`/`getEnv`/`setEnv`/`runProcess`/`tempDir`/`removeFile`/`selfExePath`/`monoNs` | 见右 | **工具链专用**（v0.21；`hostArch` 为 v0.35，`setEnv` 为 v0.42）：目标平台名、目标架构名、读环境变量（`string?`）、设环境变量（`setEnv(名, 值)`，`void \| Error`；写同一张表，此后起的子进程带上；名字为空、含 `=` 或 NUL，或值含 NUL 得 `Error`）、启动子进程并等待（`int \| Error`；子进程（`runProcess`、`procSpawn`）继承本进程当前的环境（v0.39，含 `std/os` 的 `setenv`），只得到标准三件（v0.40，本进程其余文件、套接字不进子进程））、临时目录、删文件（`void \| Error`）、自身路径、单调纳秒；未带 `--toolchain` 即编译错误 3001——生成应用代码时不要使用 |
| `stdinRead`/`stdoutWrite`/`procSpawn`/`procWrite`/`procRead`/`ioWait`/`procKill`/`procWait`/`procClose` | 见右 | **工具链专用**（v0.27）：读本进程 stdin 进 `Bytes`、原样写 stdout；起子进程（参数数组、不经 shell、stdout/stderr 走管道，`int \| Error` 句柄），读写其管道（返回字节数，暂无数据 0、流尾 −1）、等就绪（位集合）、结束、等退出码（`int?`；Linux 被信号终止为负的信号号，子进程已被别处回收、状态不可知为 −256）、关闭；未带 `--toolchain` 即编译错误 3001，只能原生构建——生成应用代码时不要使用 |
| `currentDir` | `() string \| Error` | **工具链专用**（v0.28）：本进程当前工作目录的绝对路径；当前目录已被删除等失败得 `Error`（`no such file: .` 等）；未带 `--toolchain` 即编译错误 3001，只能原生构建——生成应用代码时不要使用 |
| `fileSize` | `(string) int \| Error` | **工具链专用**（v0.29）：普通文件的字节数，只取元数据、不读内容，跟随符号链接；目录、设备等非普通文件得 `invalid argument: 路径`，不存在得 `no such file: 路径`；未带 `--toolchain` 即编译错误 3001，只能原生构建——生成应用代码时不要使用 |
| `procSpawnLimited` | `(string, List<string>, string, int, int, int) int \| Error` | **工具链专用**（v0.32）：前四参同 `procSpawn`，另给内存字节数与 CPU 秒数（正整数）作系统硬限额，执行前设置、设置失败得 `Error` 且不执行；Windows 为 Job（总提交与逐进程提交、累计用户态 CPU、最多 32 个进程），Linux 为逐进程 RLIMIT_AS／RLIMIT_CPU 并自成进程组；macOS 为 RLIMIT_CPU 并自成进程组，内存由看守线程按组的物理足迹事后执行（超过即整组 SIGKILL，`procWait` 得 −9，不是分配失败）；限额不是沙箱；未带 `--toolchain` 即编译错误 3001——生成应用代码时不要使用 |
| `procTryWrite` | `(int, int, Bytes) int` | **工具链专用**（v0.33）：单次非阻塞写子进程 stdin（流 0）或控制通道（流 3），每次最多 4096 字节；返回已写字节，暂不可写或中断得 0，关闭或错误得 −1（之后该流恒为 −1）；`procWrite` 仍写满才返回；未带 `--toolchain` 即编译错误 3001——生成应用代码时不要使用 |
| `readLocalFileBytes`/`localEntryKind`/`localFileSize`/`localListDir` | `(string, string, int) Bytes \| Error`／`(string, string) int`／`(string, string) int \| Error`／`(string, string) List<string> \| Error` | **工具链专用**（v0.32；`localListDir` 为 v0.35）：第一实参为本地根的绝对路径、第二实参为根内相对路径，经句柄读取普通文件（第三实参为最大字节数）、查类型（0 拒绝或不存在、1 文件、2 目录）、查大小、列目录项名（按字节排序，不含 `.` `..`）；拒绝符号链接与重解析点、绝对或 `..` 相对路径、含 NUL 的路径，超限或无法核验即拒绝；未带 `--toolchain` 即编译错误 3001——生成应用代码时不要使用 |
| `assert` | `(bool, string) void` | 断言 |
| `panic` | `(string) void` | 不可恢复退出 |

交给系统的路径、程序路径与进程实参不得含 NUL 字节：`readFile`、`writeFile`、`makeDir`、`removeFile`、`listDir`、`runProcess` 等一律得 `invalid argument` 错误（`entryKind` 得 0），不会截断成别的路径（D3 v0.35 安全修正）。

## 8.1 外部函数（declare function；D3 v0.36）

模块级外部函数必须写 `declare function 名(参数: 类型): 类型 from "库";` 或 `from syscall 编号;`。无 `from` 的旧宿主声明报 3033；不要生成 GUI 宿主函数或 desktop/gui 模板。接口内方法签名保持原样。

**v0.23：FFI 外部函数（带 `from`；原生编译器）**——给标准库与平台层写绑定用，应用代码用标准库：

```text
// clock_windows.tx（按目标文件：Windows 构建时代替 clock.tx；依次找 x_<系统>_<架构>、x_<系统>、类 Unix 的 x_unix、x；兄弟文件导出面须一致，否则 3033；导入写法须与磁盘逐字节一致，大小写不同即 3008）
declare type HANDLE;                                                   // 不透明句柄（可 export）
declare struct FILETIME { lo: u32, hi: u32 }                           // C 布局的外部结构体
declare function GetCurrentProcessId(): u32 from "kernel32";
declare function GetSystemTimeAsFileTime(ft: Ref<FILETIME>): void from "kernel32";

export function pid(): int { return GetCurrentProcessId(); }           // u32 → int 无损扩宽
export function unixSeconds(): int {
    const ft = new FILETIME { lo: 0, hi: 0 };
    GetSystemTimeAsFileTime(ft);                                       // 调用前拷入、调用后拷回
    return (ft.hi as int * 4294967296 + ft.lo as int) / 10000000 - 11644473600;  // 定宽先 as int 再算
}

// clock_linux.tx
declare struct Timespec { sec: int, nsec: int }
declare function getpid(): i32 from syscall 39;                        // 直接系统调用：产物静态、无 libc
declare function clock_gettime(clock: i32, ts: Ref<Timespec>): i32 from syscall 228;  // 失败返回 -errno

export function pid(): int { return getpid(); }
export function unixSeconds(): int {
    const ts = new Timespec { sec: 0, nsec: 0 };
    clock_gettime(0, ts);
    return ts.sec;
}
```

规则：
1. `from "库"`（Windows DLL 名可省 `.dll`；Linux 写 `"libc.so.6"` 等全名；macOS 写 dylib 安装名，如 `"/usr/lib/libz.1.dylib"`，libSystem 简写 `"libSystem"`，其他写法报 3033）或 Linux 的 `from syscall 编号`（macOS 上报 3033）；带 `from` 的外部函数只在本模块可见、不可 `export`——平台差异包进本模块的导出函数，放在 `x_windows.tx`／`x_linux.tx` 按目标文件里（`from "./x"` 导入时原生编译器按 `--target` 选用，兄弟文件导出面必须一致）。
2. 签名只用封送类型：定宽数值与 `int`/`float`、`bool`、`string`（即 UTF-8、NUL 结尾的 C 串）/`wstr`（UTF-16，仅 Windows 的 W 系 API）、`Bytes`（只作参数：输出缓冲）、`declare type` 句柄与 `H?`（`None` 即空指针）、`Ref<外部结构体>`（拷入拷出）、回调函数类型；返回另可 `void`。没有 `cstr`（v0.36 并入 `string`，写了报 3033）；`List`、可选、失败联合、按值结构体都不行。Win32 `BOOL` 写 `i32`。
3. 实参按一般规则检查：`int` 变量传给 `i32` 形参要 `as i32`；字面量自动取型。
4. 句柄只能比较与传回外部函数；要数值或哨兵（`INVALID_HANDLE_VALUE`）用 `handleBits(h)`／`handleFromBits<H>(-1)`。
5. `string`/`wstr` 实参含 NUL 字节即 trap 4019；返回空指针得 `""`；`Bytes` 数据区只在调用期间有效，C 端不得保留任何指针。冻结的 `Bytes`（从 `sharedGet` 取出的）交给 C 之前即 trap 4022，C 接口只读也一样，先 `bytesSlice` 拷出一份再传；调用期间回调经该 `Bytes`（或它的 `List<u8>` 别名）改变长度（push、append、insert、pop、removeAt）即 trap 4024，与容量无关——借用期内只做原地读写，要增减长度就改用独立缓冲或等调用返回；长度为 0 的 `Bytes` 的指针不可解引用、也不保证是空指针（新建的空 `Bytes` 是哨兵地址 1），C 端一律以长度为准。
6. 回调实参只能是顶层非泛型函数名、签名与形参逐一相同；只在外部调用期间、本线程上被调用（否则 trap 4020）。
7. 错误码：库函数调用后读 `ffiLastError()`（GetLastError／errno）；`from syscall` 的返回值本身就是 `-errno`。
8. C 变参函数（v0.36）：变参位置写成带 `...` 的参数，排在全部定参之后——`declare function openat(fd: i32, path: string, flags: i32, ...mode: u32): i32 from "libSystem";`、`declare function fcntl(fd: i32, cmd: i32, ...arg: int): i32 from "libc.so.6";`。定参个数必须同 C 原型：`open`／`openat`／`fcntl`／`ioctl`／`snprintf` 等常见变参函数写成全定参报 3033（Apple arm64 上变参经栈传，写错会读到垃圾）。窄整数、`bool`、`f32` 不用手写转换（编译器按 C 规则提升）；`from syscall` 与回调不能用变参。
9. Linux 调用了 `from "库"` 的产物动态链接 glibc（构建报告会写明；`--static` 要求静态）；只用 `from syscall` 的保持静态。macOS 产物总经 libSystem 动态链接；x86_64 上从 libSystem 导入的 `stat`、`readdir` 等按 Apple 头文件改名链接（`$INODE64` 版），结构按 64 位 inode 布局声明。只声明不调用的外部函数不检查、不链接。

- 入口：`function main(args: []string): int`（args 不含程序名、逐项为命令行原文；参数不是合法 UTF-8 时进程以退出码 64 拒绝启动，v0.41；`std/os` 的 `args()` 与它逐项相同；v0.19 起也可返回 `int | Error`，失败时 stderr 输出 `toStr(e)`、退出码 1）；测试块 `test "名称" { ... }` 由 `taixu test` 运行（原生 `taixu test` 每个 test 块一个进程）；
- CLI：`taixu run <file.tx> [程序参数...]` / `taixu build <file.tx> --out <目录>` / `taixu test <file.tx>` / `taixu fmt [--write|--check] <file.tx|目录>`；退出码 `0` 成功 / `1` 编译失败 / `2` 测试失败 / `3` 运行时错误 / `64` 用法错误；`--format=json` 输出带 `schema` 字段的结构化结果，供自动修复消费：诊断写到 stderr，形如 `{"schema":"diag/1","diags":[{"severity":"error","code":3002,"msg":"…","module":"main.tx","line":3,"col":5}]}`（编译诊断与运行期 trap 同一 schema）；`taixu test` 的结果写到 stdout，形如 `{"schema":"tests/1","tests":[…],"summary":{"total":1,"pass":1,"fail":0}}`；支持的 schema 以 `taixu capabilities --format=json` 的 `schemas` 为准。
- 堆检查（原生 `taixu build`/`run`/`test` 的 `--heap-check`）：链接检测内核——页堆（每块独立映射与保护页：越界写、释放后使用、重复与非法释放）加对象号隔离（释放的对象号先入隔离环、不立即复用，此间的过度释放落在计数为 0 的号码上）；查出即以 4012（`[runtime:corrupt_code]`，消息以 `heapcheck:` 开头或为“引用计数下溢”）终止、退出码 3。很慢、耗内存，只供调试与测试；可与 `--leak-check`、`--debug` 同用，与 `--link-object` 互斥。安全代码不会越界或过度释放，它查的是运行时缺陷与不安全的外部函数用法。
- 泄漏检查（原生 `taixu build`/`run`/`test` 的 `--leak-check`）：查引用计数成环。程序正常退出时（trap 不查）放开模块级变量后扫描仍存活的对象，有则向 stderr 按种类与分配位置汇总报告（运行期码 4023 `leak`，不是 trap），进程退出码改为 23；`taixu test --leak-check` 下留下环的 test 块判失败。报告形如：
  ```text
  taixu: [runtime:leak] 退出时仍有 2 个对象存活（引用计数成环，或运行期少放了计数）：
    1 个 Ref，分配于 cycle.tx:13:27
    1 个 List，分配于 cycle.tx:13:56
  ```
  修法：把反向引用改成 `Weak<T>`，或在退出前断环（字段置 `None`、清空 `List`）；位置指向标准库或 prelude 内部时看调用它的那一行。`taixu explain 4023` 有同样的说明。不带该选项的产物不变。

## 9. 完整示例

`examples/syntax-tour.tx`——约 300 行覆盖 S1 全部语法特性，作为少样本参考优先阅读。D3 v0.12：`a ?? b`＝`catch 默认值`同构糖（None/Error 均合并）；`const (a, b) = listExpr;` 解构（元素型 `T?` 越界→None）；`?.` 不在 S1（定向诊断）。

太虚目前尚未发布；不为旧开发快照保留语义、命令、输出或诊断编号的向后兼容层。根据当前 capabilities、规范与 schema 生成代码；变更记录用于开发追踪，正式发布前再确定冻结基线。

## 维护说明

本包由太虚核心仓派生（`docs/ai/llm-guide-s1.md` ← D3 语言规范），随语言版本演进；来源与最新版见官方仓库（taixu.icu）。

## 本地工具服务的信任与预算

LSP/MCP 以启动操作者的本地根为边界：不带 --workspace 时为当前目录；可重复 --workspace 和 --library-root，显式 TAIXU_STDLIB/TAIXU_GUI_ROOT 也属于库根。这些旗标不改变cwd或模块搜索规则；path相对启动目录，自动化宜传绝对路径。服务模式下，与编译器可执行文件同目录的 stdlib/src（发布包布局；也认 taixu-stdlib/src）、taixu-gui/src存在且可核验时自动授权；按目录上溯发现的同级仓只有位于已授权根内时才会被找到，否则须以 --library-root 或 TAIXU_STDLIB／TAIXU_GUI_ROOT 显式授权。TAIXU_STDLIB／TAIXU_GUI_ROOT 指向无法核验的目录时只在 stderr 告警、不授权；--workspace、--library-root 无法核验时拒绝启动（退出 64）。WSL 的 /mnt/<盘符>（9p／drvfs）以及虚拟机、Docker Desktop 的共享目录（virtiofs、fuse.grpcfuse、fakeowner）属主机共享挂载，缺省拒绝；启动操作者可用可重复的 --allow-mount-type <类型> 逐类开启（作用于全部根，MCP 转给 worker），discover 的 security 与 LSP initialize 的 taixuFileAccess 以 host_share_mount_types 报告已开启的类型；网络 9p（trans=tcp／rdma）与 nfs、cifs 等网络文件系统开启也拒绝。网络路径（Linux 按启动时读取的挂载表核验根及其祖先，Windows 只接受本地硬盘卷）、设备等非普通文件与根之下的符号链接／重解析点拒绝；启动操作者给的根本身经符号链接、目录联接或 SUBST 盘符时（如 macOS 的 /tmp、/var），启动时解析为真实路径一次，核验对真实路径做，roots 报真实根、root_aliases 报写法与真实根，请求里按写法给的路径改写到真实根；硬链接不检测；initialize/workspaceFolders 与 RPC 参数不能授权新根。库只读不表示代码可安全执行。

LSP 资源预算（initialize 结果的 experimental.taixuResources 宣告实际值；initializationOptions.taixuResources 只能收紧）：打开文档至多 1024 个、合计 256 MiB、单文档 32 MiB，一条 didChange 的 range 编辑按编辑前全文字节累计至多 64 MiB；超出的文档按未打开处理（分析取磁盘内容），发 code 为 document_budget／change_budget 的说明诊断（source 为 taixu-lsp），其上的请求回 -32803（data.reason 同码），全文变更回到预算内或重新打开即恢复。全部入口的程序源码至多 32 MiB（超出的入口发 analysis_budget，此时 rename 拒绝、references 附 logMessage），检查缓存只留最近使用、累计至多 4 MiB 的入口（其余下次冷查，结果相同）。单文件诊断至多 1000 条、1 MiB（末条 code 为 diagnostics_truncated），回复至多 32 MiB（否则 -32803 response_too_large）。去抖 300 ms、最长等待 2 s；$/cancelRequest 在同步分析的入口之间生效（回 -32800），单个入口的检查不中断。写 stdout 阻塞：客户端停读时服务停住、不再读入，读端关闭即退出（码 3）。

MCP compile/disasm 默认可用，run/test/debug_start 须启动时 --trust-workspace 且入口在工作区。缺少已验证系统沙箱时拒绝不可信执行。worker 在 READY 前查询实际 OS 限额：默认4096MiB、CPU60秒为 worker 生命周期累计（Windows 只计用户态，按 Job 与逐进程；Linux 逐进程，计用户态与内核态）、墙钟10000ms为每请求；--worker-memory-mib 上限1048576，--worker-cpu-s 上限86400，--request-timeout-ms 上限1800000，均为正整数。timeout_ms 只能在操作者上限内缩短，超出明确拒绝。进程限额不是网络／文件沙箱，没有磁盘配额；Windows Job聚合、Linux逐进程地址空间／CPU范围由 server/discover 明示。

tool-error/1 的 reason/retryable、termination_requested、reaped、worker_exit_code（未知为null）、artifacts_cleaned/cleanup_pending_paths、process_cleanup_pending/process_cleanup_error 与 stderr_truncated/stderr_text_bytes 供编排；不要把请求超时当作编译诊断。worker失败后调试会话失效，必须重新 debug_start，使用回复的完整不透明 session ID（形如“代数.序号”，不要自行拼写）。debug_step/debug_continue 的 timeout_s（缺省60秒）是程序运行时限，到点结束程序；请求剩余时限先到时 worker 暂停程序，以 stopped（reason pause）照常回复，会话保留，可再次 debug_continue（程序卡在外部调用或睡眠里、暂停 300 ms 内未落地时才结束程序，exited 的 reason 为 request_budget）。父墙钟覆盖 worker启动、输入写入、编译、执行和worker回复读取；客户端stdout仍阻塞，client_stdout_write_nonblocking=false明确披露。临时产物最多64槽、保守清理未知内容；清理未完成时须处理报告中的路径。

服务源文件上限32MiB，二进制embed上限64MiB，协议预检32768结构token；通用JSON解析器语义不变。限制与完整设计见 docs/design/v052-tool-security-boundaries-v0.3-20261023.md。Linux父进程核验subreaper并收割孤儿后代，以WNOWAIT保留受管理worker退出码；不可把reaped单独解释为全部清理完成。FFI调用期借出的Bytes超出当前容量地扩容报4024，冻结的Bytes交给C前报4022；原地修改仍须遵守冻结语义，C不得保留调用返回后的指针。
