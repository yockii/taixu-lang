# D3 S1 语言规范 v0.53

各版的兼容变更见 `docs/ai/toolchain-changes.md`（由编译器的变更登记生成）。


## 1. S1 的定位

S1 是 Stage 1 自举子集：编译器实现的语法白名单（即要求的正式版），须同时满足两个目标：

1. **PoC 目标**：能编译运行 Hello World，具备五项能力（模块导入、函数、结构体、显式错误返回、基础字符串）；
2. **自举目标**：用 S1 能写出词法分析器、语法分析器、语义检查器与字节码生成器（闭环要求）。

冻结管理：本文档即白名单正式版；任何扩展 = 修订本文档（评审）+ 守护用例同步。S1 ⊂ "第一阶段"：闭包、泛型等第一阶段特性排至 S2（见 §8），与需求清单范围一致。

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
- **关键字（S1 全集，v0.19 增 `defer` 为 23 个；v0.20 增 `interface` 为 24 个）**：`export function struct enum let const if else while for break continue return switch case test import true false new catch as defer interface`；**上下文关键字（v0.4 增 `declare`；v0.6 增 `in`；v0.8 增 `async`/`await`；v0.19 增 `extends`——`enum 名` 之后识别，§6.17；v0.20 起也在类型参数名之后识别为约束引入符，§6.18-6；v0.22 增 `yield`）**：`declare`——仅顶层声明位且后随 `function` 时识别为外部函数声明引入符（§6.8），其余位置恒为普通标识符；`in`——仅 `for (IDENT [, IDENT] in expr)` 形态位识别为 for-in 迭代引入符（§6.10-2），其余位置恒为普通标识符；`async`——仅顶层声明位且后随 `function`（§6.12-1）或表达式位且后随箭头头形态（§6.12-2）时识别，其余位置恒为普通标识符；`await`——仅一元表达式位且后随表达式起始记号时识别为前缀 await（§6.12-4），其余位置恒为普通标识符（词法零改动，自举词法表零同步）；`yield`（v0.22）——生成器函数（`function*`，§6.20）体内（含其中的箭头函数）恒为关键字、不能作标识符，其余位置恒为普通标识符；`detach`（v0.53）——仅语句开头且后随标识符时识别为 detach 语句引入符（§6.12-11），其余位置恒为普通标识符（`detach(x);` 仍是普通调用）；`function` 后紧跟的 `*` 是生成器声明标记（v0.22；词法零改动：`*` 复用乘号记号，`yield` 由解析器按所在函数识别）；
- **预声明标识符（非关键字）**：`Ref`、`Weak`、`List`（v0.5 增；内建泛型类型）、`Future`（v0.8 增；内建泛型类型，§6.12）、`Error`（类型）、`None`（缺席值）、`err` `print` `readFile` `len` `toStr` `unref` `weak` `deref` `byteAt` `utf8Encode` `listNew` `listPush` `listPop` `listGet` `listSet` `listLen`（v0.5 增六件容器内建） `assert` `panic` `taskCancel` `taskCancelled` `taskAwaitAny`（v0.8 增三件任务内建，见 §7） `sleepMs`（v0.9 增定时器内建，见 §7） `listRemoveAt` `listInsert` `strSub` `strIndexOf` `strSplit`（v0.10 增五件回补内建，见 §7） `strByteSub` `strByteIndexOf`（v0.44 增字符串的字节面，见 §7；v0.52 起这两个与 `strSub`、`strIndexOf` 只在种子换代前的 `--toolchain` 构建里收） `strSlice` `strFind` `strCompare`（v0.52 增，见 §7） `listTryGet` `listTryPop` `byteTryAt` `range`（v0.51 增，见 §7） `imul`（v0.14 增环绕乘内建，见 §7） `writeFileBytes`（v0.16 增二进制字节写出内建，见 §7） `makeDir` `writeExecutable`（v0.17 增目录创建与工具链可执行写出内建，见 §7；后者仅 `--toolchain` 构建可用） `errAs` `errWrap`（v0.19 增结构化错误内建，见 §6.17 与 §7）；v0.20 增编译器自带 prelude 的导出名 `Hashable` `Map` `Set` `mapNew` `mapSet` `mapGet` `mapHas` `mapRemove` `mapLen` `mapKeys` `mapValues` `mapClear` `setNew` `setAdd` `setHas` `setRemove` `setLen` `setToList` `setClear` `mapAt`（v0.51） `intHash` `stringHash` `boolHash` `bytesHash`（§6.19；用户声明与显式导入优先）；v0.21 增工具链系统内建 `hostTarget` `getEnv` `runProcess` `tempDir` `removeFile` `selfExePath` `monoNs`（§7；仅 `--toolchain` 构建可用；v0.42 增 `setEnv`）；v0.22 增 `Gen`（内建泛型句柄类型，§6.20；当前模块有同名类型时以用户类型为准）与 `genNext`（§7）；v0.27 增工具链进程与标准输入内建 `stdinRead` `stdoutWrite` `procSpawn` `procWrite` `procRead` `ioWait` `procKill` `procWait` `procClose`（§7；仅 `--toolchain` 构建可用）；v0.28 增工具链内建 `currentDir`（§7；仅 `--toolchain` 构建可用）；v0.29 增工具链内建 `fileSize`（§7；仅 `--toolchain` 构建可用）。预声明标识符可被用户声明遮蔽（同 TS 全局名政策）； v0.32 工具链专用：`procSpawnLimited` `readLocalFileBytes` `localEntryKind` `localFileSize`；v0.33 增 `procTryWrite`；v0.35 增 `hostArch`、`localListDir`。
- **定宽数值类型名与外部声明用词（v0.23）**：预声明类型名 `i8 i16 i32 u8 u16 u32 u64 isize usize f32`，`i64`/`f64` 是 `int`/`float` 的别名；它们在类型位置优先于用户声明，`i`/`u` 后接数字的其余名字（`i3`、`u128`…）在类型位置保留（§6.21-1）。外部函数声明的 `from "库"`／`from syscall 编号` 子句与 `declare type`／`declare struct` 按标识符形态识别（同 `import … from`；`type`、`syscall` 不是关键字）；`wstr` 只在带 `from` 的外部函数签名顶层与外部结构体的字段识别为类型名（§6.8-11、12；字段自 v0.43；v0.36 起原 `cstr` 并入 `string`，写 `cstr` 报 3033）。预声明标识符增 `wrapTo`（§6.21-6）、`handleBits` `handleFromBits` `ffiLastError`（§6.8）、`floatToBits` `floatFromBits`（§7）；
- **线程（v0.25）**：预声明类型名 `Thread`、`Shared`、`Atomic`（内建泛型句柄类型，§6.22；当前模块有同名类型时以用户类型为准）；预声明标识符增 `threadSpawn` `threadJoin` `sharedNew` `sharedGet` `atomicNew` `atomicLoad` `atomicStore` `atomicAdd` `atomicCas`（§6.22、§7）；
- **补记（v0.30）**：预声明标识符 `eprint`、`listDir`与 `entryKind`一直漏列，补上（§7）；
- **定宽数值第二步（v0.26）**：预声明标识符增 `wrapAdd` `wrapSub` `wrapMul`（§6.21-21）、`bytesAsList` `listAsBytes`（§6.15-7）；prelude 导出名增 `i8Hash` `i16Hash` `i32Hash` `u8Hash` `u16Hash` `u32Hash` `u64Hash` `isizeHash` `usizeHash`（§6.19-2）；
- **`Bytes` 批量操作（v0.37）**：预声明标识符增 `bytesCopy` `bytesFill` `bytesGetLE` `bytesGetBE` `bytesSetLE` `bytesSetBE`（§6.15-8）；
- **整数字面量**：十进制、`0x`/`0o`/`0b` 前缀，`_` 作分隔（`1_000_000`）；类型默认 `int`；v0.23 起有定宽期望类型时取该类型（§6.21-3）；v0.26 起二元运算一侧为字面量时取另一侧的类型（§6.21-19），期望型为 `u64`/`usize` 时可到 2^64−1（§6.21-20）；
- **浮点字面量**：含小数点或指数（`1.5`、`1e3`、`3.14e-2`）；类型 `float`；
- **字符串字面量**：双引号；转义支持 `\n \t \r \\ \" \0 \u{XXXX}`；相邻字符串字面量**不**自动拼接；
- **字符字面量（v0.15 增）**：单引号包裹单个字符，值为其码点（int；'a'＝97，v0.52 起含多字节字符，'太'＝22826）；转义集 \n \t \r \\ \' \0（\u{...} 为评估位——结构化拒绝）；空/多字符/未知转义/未闭合带位置诊断；kindId 72（尾段追加）；语义见 §6.14；
- **模板串字面量（v0.6 增）**：反引号包裹（`` `a=${expr} b` ``）；`${`…`}` 为插值段（嵌套深度 1 冻结：段内不支持模板串、不支持未配对的 `)`/`]` 越出段界）；转义集＝字符串转义 ∪ `` \` ``（反引号）与 `\$`（字面 `$`）；裸 `$` 非段首时为字面；段内字面部分禁止裸换行（未闭合诊断）。词法只定界（模板串为单一记号），段切分与解糖在解析期完成（§6.10-3）；
- **运算符与标点**：`+ - * / % == != < <= > >= && || ! ? = => . , ; : ( ) { } [ ] |`；v0.14 增位运算 `& ^ ~` 三 token（单字符、词法无歧义——`&` 与 `&&` 最大匹配不冲突：S1 无一元 `&`；`|` 复用既有 token，类型联合/按位或按文法位置分派）；**位移 `<< >> >>>` 词法器零变更**——`<` `>` 分离发射不动，组合上移解析器（§5 注记 6）：嵌套 `Ref<Ref<T>>` 词法无碍的既有性质原样保留（类型位逐个消费 `>` 永不组合）。

## 4. 类型系统

### 4.1 类型清单（S1）

| 类别 | 类型 | 说明 |
|---|---|---|
| 数值 | `int`、`float` | `int` 为 64 位有符号；`float` 为 f64；字面量与未标注推断的默认数值类型。v0.23 起 `i64`、`f64` 是它们的别名 |
| 定宽数值（v0.23） | `i8 i16 i32 u8 u16 u32 u64 isize usize f32` | 一般类型：可声明变量、作字段、`List` 元素、泛型实参与外部函数签名；赋值类位置无损扩宽，收窄须 `as`（越界 trap 4018）或 `wrapTo<T>`；v0.23 只有类型、字面量、转换、比较与 `toStr`（整数），**v0.26 起**算术、位运算与移位按公共类型计算，整数溢出 trap 4004（环绕须显式写 `wrapAdd`/`wrapSub`/`wrapMul`），`f32` 运算逐步舍入到 f32，`toStr(f32)` 可用；`isize`/`usize` 宽度为目标指针宽度；运行期复用 `int`/`float` 的表示（`List` 的窄元素紧凑存放），语义见 §6.21。 |
| 布尔 | `bool` | 条件表达式只接受 `bool` |
| 字符串 | `string` | 不可变 UTF-8 字节串，值语义（定实现策略） |
| 空 | `void` | 仅作函数返回类型 |
| 错误 | `Error` | 内建结构：`{ message: string }`；作为失败联合的失败侧出现，也可作普通值存储与传递；v0.19 起另有只读的 `cause: Error?`（§6.17-5） |
| 可选 | `T?` | `T` 或缺席 `None`；表达"预期内的缺失" |
| 失败联合 | `T \| Error`、`T \| E` | 表达"操作可能故障"；v0.19 起失败侧可以是错误 enum `E`（§6.17-2） |
| 结构 | `struct` 声明 | 名义类型，值语义（≈ TS 原始类型的行为） |
| 变体 | `enum` 声明 | 名义和类型（tagged union），变体可带字段（≈ TS 判别联合的语义等价物） |
| 错误 enum（v0.19） | `enum E extends Error` 声明 | 每个变体带消息的 enum：值同时是 `Error`（可流入 `Error` 位置），按值比较、`switch` 须穷尽；不能用 `new` 构造；语义见 §6.17。 |
| 序列 | `[]T`、`[N]T` | 切片（长度 + 指针）、定宽数组 |
| 堆引用 | `Ref<T>` | RC 管理的安全堆句柄（`new` 产生，≈ TS object 的行为）；不支持指针算术；**FFI 裸指针（O1）将用独立拼写（预留 `Ptr<T>`），不与 `Ref` 混用** |
| 弱引用（v0.2） | `Weak<T>` | 不拥有目标的观察句柄（`weak()` 产生）：不延长目标生命周期、`deref` 目标已死得 `None`；`T` 为被指向类型（实际限 struct/enum，由 `weak()` 的参数类型约束）；语义详见 §6.7 与 |
| 容器（v0.5） | `List<T>` | 预声明泛型堆容器（RC 堆对象）：**句柄语义**（赋值/传参共享底层存储，对齐 `[]T`）；元素类型冻结面＝`int/float/bool/string/Ref<T>/Weak<T>/List`（嵌套）——**v0.20 起元素面放开到 struct/enum/定长数组与接口值（值类型按值语义存取，§6.9-5；仅太虚编译器与 NRT）**；**v0.51 起元素可为 `T?`（§6.9-5）**；**v0.7 注记：元素面扩函数类型；v0.8 注记：元素面扩 `Future<T>`（`taskAwaitAny` 形参面）**；**v0.26 注记：元素为 8/16/32 位整数或 `f32` 时按元素宽度紧凑存放（实现说明，语义不变，§6.21-22）；`List<u8>` 与 `Bytes` 可零拷贝互转（§6.15-7）**；操作仅经 §7 六内建（`listNew/listPush/listPop/listGet/listSet/listLen`），无方法、无索引语法；语义详见 §6.9 |
| 函数（v0.7） | `(T, …) => R` | 函数类型：**句柄语义**（赋值/传参零 clone）；不可 `==`（§6.5-3）；struct 字段/List 元素可承载（GUI 回调主用例）；不入 extern 面（§6.8 类型面冻结不变）；字节码 `TyKind.fn=16`＋TXFNT 可选尾节——**零新 opcode**（既有产物零扰动）；语义详见 §6.11 |
| 字节（v0.18） | `Bytes` | 可变、可增长的字节序列（元素 0..255，紧凑存储）：**句柄语义**（同 `List<T>`：赋值/传参/存字段共享底层存储；`==` 比较句柄同一性，内容比较用 `bytesEqual`）；可作 struct 字段与 `List` 元素；不入 extern 面；操作经 §7 `bytesXxx` 内建、方法语法（`b.push(x)` 解为 `bytesPush(b, x)`）与 `len`；无字面量，编译期嵌入见 §6.15。**v0.26**：与 `List<u8>` 是两个类型、运行期是同一种对象形态，`bytesAsList`／`listAsBytes` 零拷贝互转（返回同一个对象，§6.15-7）。 |
| 句柄（v0.23） | `declare type H;` 声明 | 指针宽度的名义不透明句柄（§6.8-10）：只来自外部函数的返回值、输出参数或 `handleFromBits<H>`；能传回外部函数、同一句柄类型之间 `==`/`!=`，不能算术、不能 `as`、不能与 `int` 隐式转换（`handleBits` 取数值）；`H?` 可空（空指针为 `None`）；可作 `List` 元素；可 `export`。**仅太虚编译器与 NRT 实现** |
| 外部内存区（v0.48） | `FfiMem` | 一段外部内存的（起点、大小、可写否）（§6.8-22）：只由 `ffiMem`／`ffiMemOf` 创建，只作局部变量与参数，不能作返回类型、结构体字段、集合元素、模块级变量与泛型实参，不能比较、不能发送到其他线程 |
| 外部结构体（v0.23） | `declare struct S { … }` 声明 | C 布局的值类型 struct（§6.8-11）：字段只能是定宽数值、`int`/`float`、句柄、定长数组 `[N]标量`、其他外部结构体，以及（v0.43）`string`/`wstr` 与回调函数类型（只由太虚写给 C）、（v0.46）`Bytes`（数据区指针，C 端可写）；太虚侧读写同普通 struct；以 `Ref<S>` 传给外部函数（调用前拷入、调用后拷回），v0.45 起也可按值传与返回（§6.8-11、12）；可 `export`。**仅太虚编译器与 NRT 实现** |
| 接口（v0.20） | `interface` 声明 | 只含方法签名的类型：结构化实现（不写 `implements`），可作泛型约束（`T extends I`），也可作值的类型——接口值是共享句柄、动态分派；语义见 §6.18。 |
| 映射与集合（v0.20） | `Map<K, V>`、`Set<T>` | 编译器自带 prelude 中的泛型类型（§6.19）：键须实现 `Hashable`，插入序迭代，句柄语义（同 `List`） |
| Future（v0.8） | `Future<T>` | 异步句柄：`async` 函数调用的值形态（调用即 spawn，§6.12-3）；**句柄语义**（赋值/传参零 clone）；不可 `==`（§6.5-3）；不可嵌套 `Future<Future<T>>`；`await` 的操作数类型（§6.12-4）；字节码 `TyKind.future=17`（追加编码值先例，旧产物不含）；v0.21 起太虚编译器与 NRT 原生实现（§6.12-10）；v0.53：async 函数声明 `T | Error` 时调用同样得 `Future<T>`（§6.12-1） |
| 生成器（v0.22） | `Gen<T>` | 惰性产出 `T` 值的句柄（§6.20）：生成器函数（`function*`）调用的结果；**句柄语义**（赋值/传参共享同一个生成器与进度）；可存进变量、struct 字段与 `List`，可作参数与返回值；不可 `==`（§6.5-3）；`T` 不能是 `void`、可选、失败联合、`Error` 与错误 enum（§6.20-2）；字节码 `TyKind` 19（追加值）。 |
| 线程（v0.25） | `Thread<T>` | `threadSpawn` 返回的线程句柄（§6.22-1）：`threadJoin` 取回结果 `T`；**句柄语义**；不可 `==`、不可发送（只能由建它的线程 join）；放掉句柄不影响线程运行。 |
| 共享（v0.25） | `Shared<T>` | 跨线程共享的只读对象图（§6.22-7）：`sharedNew` 深拷贝进共享区，`sharedGet` 取出冻结的对象图；**句柄语义**；可发送（发送的是句柄，不拷贝内容）；不可 `==`。**仅太虚编译器与 NRT 实现** |
| 原子（v0.25） | `Atomic<int>` | 原子整数（§6.22-10）：五个内建操作，内存序 seqcst；**句柄语义**；不冻结；只能经 `Shared` 跨线程（直接发送报 3035）；不可 `==`；`Atomic<T>` 目前只有 `T = int`。**仅太虚编译器与 NRT 实现** |

### 4.2 类型规则

1. **无隐式数值转换**：`int` ↔ `float` 需显式 `as`；**v0.23**：定宽数值在赋值类位置按无损方向自动扩宽（§6.21-4），收窄与整数↔浮点仍须 `as`；整型字面量在期望 `int` 处取 `int`，浮点字面量在期望 `float` 处取 `float`，无期望时分别取默认类型；**v0.26**：二元运算一侧为数值字面量时，字面量取另一侧的类型、左右对称（§6.21-19），`1 + x`（`x: float`）因此合法；
2. 名义类型之间互不相容；struct 无子类型。**v0.20**：接口（§6.18）按结构化实现关系检查，值装入接口值是显式期望类型位置上的转换，不是子类型；接口之间不转换；
3. `let` 为可变绑定、`const` 为不可变绑定（不可变性对值类型**深层传递至字段**）；函数参数绑定不可变。**唯一例外是 `Ref<T>`：`const` 只约束绑定不可重绑，其堆对象字段仍可变**——对齐 TS object 心智（`const` 绑定 ≠ 对象冻结）；`Weak<T>` 绑定同此例外不适用——`Weak` 是句柄且不可解引用写目标，`const`/`let` 仅区别可否重绑；
4. **switch 穷尽性**：对 enum 的 switch 必须覆盖全部变体或含 `else =>` 臂；
5. **返回路径检查**：声明非 `void` 返回类型的函数，所有控制流路径必须 `return` 或经 `?` 传播离开；返回 `void | Error`（或 `void | E`）的函数走到末尾即成功，不受此限（v0.49，§6.3-7）；
6. 局部绑定可省略类型标注，但类型必须可静态确定（推断失败即编译错误，不做最右猜测）；
7. **失败值不得丢弃**：类型为 `T?` 或 `T | Error` 的表达式作为表达式语句被丢弃，是编译错误——必须 `?` 传播、`catch` 处理、绑定或传参；
8. **失败通道不混用**：`?` 传播要求外层函数返回类型的联合成员与被传播值的失败侧一致（`None` 侧对应 `U?`，`Error` 侧对应 `U | Error`）；跨通道需用 `catch` 显式转换（§6.3-4）。**v0.19**：失败侧为错误 enum `E` 时，外层失败侧须同为 `E`，或为 `Error`（自动放宽，具体种类保留，可用 `errAs` 取回）；其他组合是编译错误（§6.17-3）；
9. **函数值句柄语义（v0.7）**：函数类型值赋值/传参/存容器均复制句柄不 clone（对齐 `Ref` 心智）；函数值不可比较（§6.5-3）；语义详见 §6.11。
10. **Future 句柄语义（v0.8）**：`Future<T>` 值赋值/传参/存容器均复制句柄不 clone；不可比较（§6.5-3）；把 Future 当表达式语句丢掉是编译错误 3005（v0.53；发后即忘写 `detach`，§6.12-11）；不可嵌套。语义详见 §6.12。
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
extern_from  = "from" ( STRING | "syscall" ( INT_LIT | STRING ) ) ;  (* v0.23：FFI，§6.8-7；from/syscall 按标识符形态识别；v0.47：syscall 后可写名字 *)
extern_type_decl = "declare" "type" IDENT ";"                 (* v0.23：不透明句柄，§6.8-10 *)
             | "declare" "struct" IDENT "{" field { "," field } [","] "}" ;  (* v0.23：外部结构体，§6.8-11 *)
import_decl  = "import" "{" import_spec { "," import_spec } [","] } "from" STRING ";" ;
import_spec  = IDENT [ "as" IDENT ] ;  (* v0.51：导入后改名，§6.1-6 *)

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
             | defer_stmt | yield_stmt | detach_stmt ;
defer_stmt   = "defer" ( block | assign_stmt | expr_stmt ) ;   (* v0.19，§6.16 *)
yield_stmt   = "yield" expr ";" ;                              (* v0.22：只直接出现在生成器函数体内，§6.20-4 *)
detach_stmt  = "detach" expr ";" ;                             (* v0.53：上下文关键字，语句开头且后随标识符时识别；须为结果是 Future 的调用，§6.12-11 *)
assign_stmt  = lvalue assign_op expr ";" ;
assign_op    = "=" | "+=" | "-=" | "*=" | "/=" | "%=" | "&=" | "|=" | "^=" ;   (* v0.49 复合赋值，§6.5-4；见语法注记 7 *)
lvalue       = postfix_base { "." IDENT | "[" expr "]" } ;
if_stmt      = "if" "(" expr ")" block [ "else" ( if_stmt | block ) ] ;
while_stmt   = "while" "(" expr ")" block ;
for_stmt     = "for" "(" [ for_init ] ";" [ expr ] ";" [ for_update ] ")" block ;
for_init     = for_binding | lvalue assign_op expr ;
for_binding  = ( "let" | "const" ) IDENT [ ":" type ] "=" expr ;
for_update   = lvalue assign_op expr | expr ;
for_in_stmt  = "for" "(" IDENT { "," IDENT } "in" expr ")" block ;(* v0.6：变量 1–2 个；`in` 上下文关键字，见 §3 *)
return_stmt  = "return" [ expr ] ";" ;
expr_stmt    = expr ";" ;

(* 表达式——优先级自高到低：后缀(含 as) > 一元 > * / % > + - > 移位(v0.14) > 比较 > & (v0.14) > ^ (v0.14) > | (v0.14) > && > || > catch > 三元(v0.50) *)
expr         = cond_expr ;
cond_expr    = catch_expr [ "?" expr ":" cond_expr ] ;   (* v0.50 三元，右结合；后缀位 `?` 的消歧见语法注记 8 *)
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
             | "." IDENT | "?" "." IDENT | "?" | "as" base } ;  (* v0.50："?" "." 起可选链（§6.3-9），其后的调用、下标、字段接在链上 *)
arg_list     = expr { "," expr } [","] ;
primary      = INT_LIT | CHAR_LIT | FLOAT_LIT | STRING_LIT | TEMPLATE_LIT | "true" | "false" | "None"
             | IDENT | "(" expr ")" | arrow_fn
             | struct_lit | array_lit | new_expr | switch_expr ;
arrow_fn     = [ "async" ] "(" [ param { "," param } ] ")" ":" type "=>" ( expr | block ) ;  (* v0.7；v0.8 增 async 前缀——仅后随箭头头形态时识别 *)
param        = IDENT ":" type ;
(* v0.6：TEMPLATE_LIT 解糖为拼接链（§6.10-3），AST 无独立节点 *)
(* v0.15：CHAR_LIT 解糖为 int 字面量节点（§6.14），AST 无独立节点 *)
struct_lit   = [ IDENT ] "{" [ ( field_init | spread ) { "," field_init } [","] ] "}" ;
spread       = "..." expr ;  (* v0.51：struct 更新表达式，只能写在最前，§6.2-1 *)
array_lit    = "[" [ expr { "," expr } [","] ] "]" ;
variant_ctor = IDENT "." IDENT "(" [ arg_list ] ")" ;
new_expr     = "new" ( struct_lit | variant_ctor ) ;
field_init   = IDENT ":" expr ;
switch_expr  = "switch" "(" expr ")" "{" switch_arm { "," switch_arm } [","] "}" ;
switch_arm   = "case" switch_pat "=>" expr ;
switch_pat   = IDENT [ "(" [ IDENT { "," IDENT } [","] ] ")" ] | "else" ;
```

**运算符优先级与结合性**：后缀（调用/索引/字段/`?`/`as`）> 一元（`- ! ~`）> `* / %` > `+ -` > 移位（v0.14）> 比较 > `&` > `^` > `|` > `&&` > `||` > `catch`（含 `??`）> 三元 `? :`（v0.50，右结合）（v0.14 位运算五级与移位层插入——TS 同构）；双目运算均左结合；`=` 不是表达式（赋值只能是语句）。

**语法注记**：

1. **struct 字面量**：期望类型已知时用裸对象字面量 `{ x: 1, y: 2 }`（TS 形式，主推）；无上下文时用类型名前缀 `Point { x: 1, y: 2 }` 兜底。裸字面量不得作为语句首记号（需括号包裹或出现在绑定/参数中）——与 JS 对对象字面量的限制一致；
2. **enum 变体构造**：表达式位置用限定名 `Expr.Add(l, r)`（TS 命名空间/静态调用同形）。它由普通后缀链（标识符 + 字段访问 + 调用）解析，sema 定夺：接收者标识符为作用域内 enum 类型名时为变体构造（v0.6 前的唯一解）；否则为**方法语法**糖形（§6.10-1，v0.6 增）——零新增文法；模式位置用裸名 `case Add(l, r) =>`（模式文法独立，无歧义）；

4. **箭头头消歧（v0.7）**：表达式位置 `(` 后为 `)` 或 `IDENT :` 时按 `arrow_fn` 解析，否则按分组表达式——单 token 前瞻即唯一（分组表达式内不容 `:`）。参数/返回类型必须显式（与 `function` 声明同面），`(x) => x` 形态（省类型）是编译错误。词法零新增（`=>` 已在 switch 臂文法）；自举 parser.tx 同单镜像（diff-parser 全仓守护覆盖箭头源）。
3. 花括号构造与语句块无歧义（块只出现在语句/声明位置，且 if/while/for 的条件带括号）；`lvalue` 限定为名字起头的后缀链，排除字面量与调用结果被赋值。
5. **await 上下文消歧（v0.8）**：`await` 后随表达式起始记号（标识符/字面量/`(` `[` `{`/`-` `!`/`new`/`switch`/`true`/`false`）时识别为前缀 await，否则恒为普通标识符；操作数含后缀链（`await f().x` ＝ `await (f().x)`，与 TS 一致）；await 右结合可叠缀。`async` 箭头同理：`async` 后随箭头头形态（§5 注记 4）才识别。

6. **位移邻接组合与泛型实参互操作（v0.14）**：`<<`/`>>`/`>>>` 不新增词法 token——表达式二元运算符位遇**零空白邻接**（同行且位置区间相接亲证）的两个 `>` 组合为 `>>`、三个为 `>>>`，两个 `<` 组合为 `<<`；带空白的 `a > > b` 不组合（沿比较/期望表达式路径给结构化诊断——C++ 惯例）。**排除理由所保护的词法极简原封不动**：与 TS 机制对比，TS 扫描器合并 `>>` 系 token、解析器在类型实参位回调拆分；Taixu 从不合并故无需拆分——解析器只在表达式位组合，无歧义的结构保证＝D3 既有不变量"类型不出现在表达式位置"（类型实参位逐个消费 `>` 永不组合、表达式二元位才组合——两态文法位置不相交）。**`<<`×显式类型实参互操作＝串联定序**：调用位平衡前瞻（v0.13）成功即消费泛型实参、失败回落比较位；比较位操作数才遇移位层邻接组合——`f<int>(x) << 2`／`a << (b+1)`／空白形 `f < int > (x)` 三形锚入。

7. **复合赋值运算符（v0.49）**：`+ - * / % & | ^` 与紧随其后的 `=` **零空白邻接**（同行且相接）时组成复合赋值运算符，词法器零变更（同注记 6 的移位组合）；只出现在 `assign_stmt`、`for_init`、`for_update` 的运算符位，表达式各层见到这种形态即停。带空白的 `a + = b` 不是复合赋值。`++`／`--` 不是太虚的运算符（§8），前缀与后缀写法都给定向诊断“写 i += 1”；带空白的 `- -x` 仍是两次取负。`taixu fmt` 同样拒绝格式化含零空白相邻 `++`／`--` 的文件，两个负号之间的空格原样保留。

8. **后缀 `?` 的三种读法与可选链（v0.50）**：后缀位的 `?` 之后是 `?` 即 `??`；是 `.` 即可选链 `?.`（`.` 不能开始表达式，不会是三元）；否则其后一个记号能开始表达式（标识符、字面量、`(` `[` `{`、`-` `!` `~`、`new`、`switch`），且从那里向后在同一层（括号配平）先遇到 `:`——而不是 `,` `;`、本层的闭括号或文件尾——即为三元的 `?`，后缀链在此结束，交给 `cond_expr`；其余是传播。所以 `c ? x? : y`、`c ? x? + 1 : y` 的 then 支照常传播；`c ? x? - 1 : y` 里 `-` 能开始表达式，读作嵌套三元而缺 `:`（2002），要传播后再减写 `(x?) - 1`——误读总会多出一个缺 `:` 的三元，不会静默改义。条件本身以后缀 `?` 结尾须加括号 `(x?) ? a : b`（写 `x? ? a : b` 报 2001）。三元的条件是 catch 层：`a ?? b ? x : y` 即 `(a ?? b) ? x : y`（同 TS）。词法不变：`?.` 是 `?`、`.` 两个记号，与中间的空白无关；“传播后取字段”写 `(x?).f`。`taixu fmt` 给三元的 `?` 与配对的 `:` 两侧各一空格，`?.` 紧贴。

## 6. 语义规则

### 6.1 模块与导入（导入说明符三级规则）

1. 文件即模块。导入说明符按形式分三级：
   - **相对路径**（`./`、`../` 开头）：相对当前文件的 `.tx` 文件（扩展名省略）——**S1 唯一可解析形式**：`import { a, b } from "./sub/token";`
   - **裸标识符**（如 `"io"`）：标准库命名空间，为标准库（taixu-stdlib 新仓）预留——S1 期为编译错误（"标准库未提供"），语法保留以避免破坏语法（负例守护）：`import { open } from "io";`
   - **其他形式**（绝对路径、包名路径等）：包管理议题，S1 一律编译错误；
2. 顶层声明默认模块私有，`export` 后才可被导入；
3. 循环导入是编译错误；导入路径不存在或导出符号缺失是编译错误。
4. **按目标选择文件（v0.23；v0.35 增两档）**：原生编译器解析导入 `./x`（`../x`、`std/x`、`gui/x` 同法）时，依次找同目录的 `x_<系统>_<架构>.tx`、`x_<系统>.tx`、`x_unix.tx`（只对类 Unix 目标：linux、macos）、`x.tx`，用第一个存在的（系统名 `windows`、`linux`、`macos`；架构名 `x86_64`、`aarch64`、`loongarch64`、`riscv64`（v0.47）；目标由 `--target` 决定，缺省为宿主），模块显示名随之为 `./x_windows` 等，诊断里看得出用的是哪个文件。`unix` 后缀对应 Go 的 `//go:build unix` 约束——太虚不引入布尔条件编译，所以做成文件后缀；不提供只按架构的后缀。同一组兄弟文件（`x.tx` 与上述各后缀中存在的，编辑器里未保存的也算）的导出面须一致——名字、种类与按书写文本比较的签名，参数名不计——每次构建都比对全部存在的兄弟文件，缺失或不同报 3033（位置为第一处导入），在一个平台上就能发现另一个平台的不一致。用途：平台相关的实现（外部函数声明与库名）放在按目标文件里；公共实现可放在 `x.tx`，也可只提供导出面一致的目标文件。
5. **导入写法须与磁盘逐字节一致（v0.35）**：导入说明符里写出的每段目录名与最终选中的文件名（含目标后缀与 `.tx`），都必须在其所在目录的列举结果里逐字节出现，否则报 3008（“导入 `./x` 的写法与磁盘不一致：`a` 在磁盘上为 `A`”）；三个宿主同一规则——大小写不敏感的 Windows、macOS 上按别的写法也能打开，到 Linux 就打不开，现在一律报同一条（同 TypeScript `forceConsistentCasingInFileNames`）。逐字节比较、不做 Unicode 规范化（NTFS 与 ext4 都对规范化敏感）。按目标选文件时，只差大小写的文件不算这个名字的目标文件，兄弟导出面比对同理。编辑器里打开的文件以编辑器给的写法为准。
6. **导入改名（v0.51）**：`import { truncate as cut } from "std/text";`——按被导入模块里的名字查找与核对 `export`，在本模块里以新名使用（类型、函数、常量同法）；新名与本模块声明或其他导入撞名报 3003，原名在本模块里不可见。不能导入 `main`，也不能借 `as` 改名出 `main`（3008；v0.53 起直接导入也拒绝，`main` 是程序入口，§7）。同一名字可分别导入两次、各取一个新名。

### 6.2 struct、enum 与 new

1. **struct 字面量**（值语义）：期望类型已知时 `const p: Point = { x: 1, y: 2 };`；无上下文时 `Point { x: 1, y: 2 }`。字段必须全量初始化（缺字段 = 编译错误，多字段同样）。**更新表达式（v0.51）**：`{ ...t, done: true }`、`Todo { ...t, done: true }`、`new Todo { ...t, id: 2 }`——展开源 `t` 须是同一 struct 的值（`Ref<T>` 先 `unref`，别的类型报 3002），只能写在最前、只能一个；先求值展开源，未显式给出的字段从它逐个拷贝（值类型字段拷贝，`List`、`Ref` 等句柄字段与原值共享同一对象，同赋值），再按书写序求值并写入显式给出的字段；显式字段不能重复（3017），没有上下文类型的裸字面量取展开源的类型。同 TS 的对象展开、Rust 的 `..t`、Kotlin 的 `copy()`；
2. **堆分配**：`new Point { x: 1, y: 2 }`（struct，产生 `Ref<Point>`）与 `new Expr.Add(l, r)`（enum，产生 `Ref<Expr>`）；`new` 是唯一堆分配入口；
3. **enum 变体构造**（值语义）：`Expr.Add(l, r)`（限定名，见 §5 语法注记 2）；
4. **自动解引用**：`p.x`、`p[i]`、对 `p` 的赋值、switch 的 scrutinee，在 `p: Ref<T>` 时自动解引用（链式逐层）；**语言无显式解引用操作符**，整体取值拷贝用内建 `unref`（§7）；
5. 字段访问 `.`；**书写层面的方法语法**见 §6.10-1（v0.6 增，纯调用糖，零对象系统语义——本条 v0.5 前"方法不存在"指无虚分派/无 this 的语义层，仍成立）。

### 6.3 失败：可选、错误、传播与处理

1. **构造**：`err("message")` 构造 `Error`；`None` 是 `T?` 的缺席值（不是 Error）；
2. **传播 `expr?`**：`expr` 为 `T | Error` 时，失败则当前函数立即返回该 `Error`（要求外层返回 `U | Error`）；`expr` 为 `T?` 时，缺席则返回 `None`（要求外层返回 `U?`）。跨通道传播是编译错误，需先 `catch` 显式转换；
3. **处理 `expr catch F`**：`F` 为默认值表达式或 `(e)` 绑定块（TS 词汇的括号形式）。统一类型规则：**整体类型 = 成功侧类型 ∪ F 的类型**——F 为 `T` 得 `T`；F 为 `None` 得 `T?`（把故障降级为缺席）；F 为 `err(...)` 得 `T | Error`（把缺席升级为故障）；**(e)** 形式仅对 `T | Error` 合法，`e: Error` 恒有 `message`。**块形式的值规则**（无隐式块值，同 TS 函数体必须显式 return）：成功侧非 `void` 时，`(e)` 块必须离开——`return`、`panic`，或（v0.49）在循环里以 `break`／`continue` 跳出 catch 外层的那层循环，跨越的 `defer` 照常执行；块里另开的循环、闭包体里的 `break`／`continue` 不算——此时表达式类型取成功侧。块离开只是失败路径离开，成功路径照常往下走，所在语句不因此算作离开（v0.49 修正：原实现算作离开，函数漏写 `return` 时漏报 3012）；成功侧为 `void` 时块作语句序列执行。`(e)` 绑定与块内的声明只在块内可见（v0.50 修正：原实现少弹一层作用域，catch 表达式之后在同一块里声明的名字逃出所在块、块外同名声明误报重复）。**发散默认值（v0.6 增）**：默认值表达式若恒发散（`panic(...)`），不参与上列类型并集——整体类型取成功侧（`catch panic("impossible")` 是"此路不可达"的显式拼写，for-in 解糖依此收口句柄容器的失败通道，§6.10-2）；
4. **显式桥接**：`opt catch err("missing")`（`T?` → `T | Error`）、`risky catch None`（`T | Error` → `T?`）都合法且类型在签名处可见；
5. `Error` 可像普通值一样存储与比较（按 message 相等）；`T?` 之间及与 `None` 比较按值相等（`None` 仅等于 `None`）。
6. **结构化错误（v0.19）**：失败侧可以是错误 enum（`T | E`），`catch (e)` 中 `e: E`；`?` 的同种传播与放宽、`errAs`／`errWrap`／`cause` 见 §6.17。本节其余规则对 `T | E` 同样成立（F 为 `E` 的值得 `T | E`）。
7. **成功出口（v0.49）**：返回 `void | Error`（或 `void | E`）的函数与闭包里，`return;` 即成功返回，走到末尾同样成功（同 `void` 函数，§4.2-5）；`async function f(): void` 的函数体面为 `void | Error`（§6.12-1），体内的 `return;` 同理。
8. **None 收窄（v0.50）**：类型为 `T?` 的不可变绑定——`const` 局部、参数，以及 catch 绑定、模式绑定、闭包捕获的副本——在下列区域里类型为 `T`：`if` 条件含 `x != None` 时为真的分支、`&&` 的右侧；含 `x == None` 时为假的分支（`else`）、`||` 的右侧；`!` 取反；三元的两支同 `if`；`if` 的某一支必离开（`return`、`break`、`continue`、`panic`，或其中的 `if`／`else` 各支都离开）时，`if` 语句之后直到所在块结束取另一支的收窄（`if (x == None) { return; }` 之后 `x` 为 `T`；else-if 链同理）。在收窄区域里创建的闭包，捕获的副本同样收窄。`let` 绑定与字段不收窄（可能被改写）。收窄区域里对该变量照写 `x catch d`、`x ?? d`、`x?`、`x == None`、`x != None` 仍按声明类型 `T?` 取，旧写法照常可编；其余位置按 `T`，由它推断的绑定（`const y = x;`）也是 `T`（兼容变更 `lang.none-narrowing`）。`T?` 与 `T` 运行期同一值槽，收窄零代价。
9. **可选链 `a?.b`（v0.50）**：`a` 须为 `T?`（`T | Error` 报 3006：先用 `catch` 转成 `T?`，或写 `(a?).b` 传播后再取；不是可选值报 3002：直接写 `.`）。`a` 为 `None` 时整条链得 `None`，链上其后的字段、下标、调用与方法糖都不求值（同 TS 的短路：`a?.b.c` 在 `a` 为 `None` 时 `.c` 也不求值）；否则以 `a` 的值（类型 `T`）求链。结果：链的值为 `U` 得 `U?`，已是 `U?` 仍是 `U?`（可选不嵌套），为 `void` 得 `void`（`a?.m();` 是条件调用，作语句不报 3005）；链的值不能是 `T | Error`（3002）。传播 `?`、`??`、`catch`、`as` 与三元作用于整条链；`?.` 链不能作赋值目标（3016）；`a?.b?.c` 逐段短路。`a` 按声明类型取（收窄区域里照写 `x?.f` 不报错）。原先 `x?.f` 的含义是“传播后取字段”，现写 `(x?).f`（兼容变更 `lang.optional-chain`）。

### 6.4 控制流

1. `if` 条件必须 `bool`；`else` 必须显式；悬空 else 归最近 if（块语法本身消歧）；
2. `while`、C 风格 `for`；`break` / `continue` 仅对最近的循环生效；
3. `switch` 是表达式（有值），`case` 臂间**不穿透**（无 TS 的 fallthrough 与 `break` 遗忘问题）；解构绑定的变体字段在臂表达式内只读；对 enum 的 switch 必须穷尽或含 `else =>` 臂。**臂类型（v0.49）**：各臂类型相同即为 switch 的类型；不同时，有期望类型（返回值、带类型的绑定、实参等）且各臂都能赋给它，取期望类型；没有期望类型时，`T` 臂与 `Error` 臂合成 `T | Error`、与同一错误 enum `E` 的臂合成 `T | E`，`T` 臂与 `None` 臂合成 `T?`（`T?` 臂、`T | Error` 臂按其成功侧计）；其余组合（如 `int` 与 `string`、`None` 与 `Error` 并存、两种错误 enum）报 3018。`None` 臂以期望类型 `T?` 或其他臂的 `T` 为上下文（写在最前也行）。
4. **三元 `c ? a : b`（v0.50）**：`c` 须为 `bool`（3014，无真值转换）；只求值选中的一支。两支按期望类型检查，没有期望类型时 else 支以 then 支的类型为提示；类型合成同 switch 臂（第 3 条）：相同即取之，有期望类型且两支都能赋给它取期望类型，否则 `T` 与 `None` 合成 `T?`、`T` 与 `Error` 合成 `T | Error`、与同一错误 enum 合成 `T | E`，其余报 3018；`None` 支以期望类型或另一支的 `T` 为上下文（写在前面也行）。一支发散（`panic`）时取另一支的类型。两支的 None 收窄同 `if`（§6.3-8）。文法与 `?` 的消歧见 §5 与语法注记 8。
5. **对 struct 拷贝的无效写入（v0.51）**：struct 是值，struct 类型的 `let` 局部里是一份拷贝（取自 `listGet`、`mapGet`、函数返回、for-in 元素或字面量）。对它的字段写入——含只经值类型（struct 字段、定长数组元素）走到的位置，如 `t.inner.v = 1`、`t.arr[0] = 1`——之后，若该变量在任何路径上都再未被读取、传出、返回或被闭包捕获，这次写入不可能有作用，报 3036 `dead_copy_write`（“要改原处用 `listSet`／`mapSet` 写回，或改存 `Ref<T>`；多余的写入删掉”）。经句柄（`Ref`、`List`、切片等）走到的写入改的是共享对象，不在此列；整体赋值 `t = …` 之前的字段写入同样无效。判定按控制流：循环里的写入被下一轮读到不算；`defer` 体读取的变量不报（出口处读取）；闭包在创建处读取它捕获的变量（之后的写入闭包看不见，照报）。只查非泛型函数体（泛型模板与实例不查），参数与 `const` 本就不可写。同 Go 生态 SA4005、Swift“written to, but never read”的判定，太虚定为编译错误（兼容变更 `lang.dead-copy-write`）。

### 6.5 求值顺序与相等

1. 表达式求值从左到右（含比较运算的两侧；v0.51 及以前 `>`、`>=` 先求值右侧，v0.52 修正）；`&&` / `||` 短路；
2. `string` 不可变；`+` 产生新串；
3. **`==` / `!=` 语义**：数值与布尔按值；string 按 UTF-8 内容；struct 按字段值；enum 按 tag 与字段值（递归）；`Ref<T>` 按同一性（对齐 TS `===` 对 object 的行为）；`[N]T` 按内容；`[]T` 按同一性（对齐 TS 数组 `===` 的行为）；`Weak<T>` 按盒同一性、**不比较目标**（见 §6.7-4）；`List<T>` 按**句柄同一性**（同 `Ref`/`[]T` 心智，§6.9-6；逐元素比较拆用户函数）；`T?` 见 §6.3-5；**函数类型不可比较（v0.7）**——操作数为函数类型即编译错误（TS 的函数比较是无底洞）。**Future 不可比较（v0.8）**——操作数为 `Future<T>` 即编译错误（完成与否用 `taskAwaitAny`/`await` 表达）。**Gen 不可比较（v0.22）**——操作数为 `Gen<T>` 即编译错误 3032（生成器是句柄，没有相等性面，同 Future；§6.20-8）。**v0.23**：定宽数值按公共类型比较（§6.21-7）；句柄只在同一句柄类型之间按指针值比较（§6.8-10）。 **Thread、Shared、Atomic 不可比较（v0.25）**——操作数为三者之一即编译错误 3002（它们是句柄，没有相等性面；§6.22-11）。
4. **复合赋值（v0.49）**：`a op= b`（`op` 为 `+ - * / % & | ^`）等同 `a = a op b`，但左值只求值一次——下标里有调用等须求值的部分先求值暂存，再读、算、写回，求值顺序同 `a = a op b` 从左到右；类型、溢出与 `const` 规则照普通赋值与对应运算（`string` 的 `+=` 即拼接，定宽数值照 §6.21）。`for` 的初始化段、更新段里插不进暂存，下标只能是名字、字面量或字段链，否则 3016。
5. **字符串大小比较（v0.52）**：两个 `string` 可用 `< <= > >=` 比较，按 UTF-8 字节的字典序——逐字节比，先遇到较小字节的在前，一方是另一方的前缀时短的在前。UTF-8 下字节序即码点序（同 Go、Rust；不同于 JS 按 UTF-16 码元比，后者对 U+E000 以上与增补平面的字符次序不同）。不做区域化排序（拼音、大小写折叠等交给库）。`strCompare(a, b)` 得 -1／0／1（§7）。可空的 `string?` 先解开；`string` 与别的类型比较报 3002。

### 6.6 数组、切片与字面量

1. **字面量** `[a, b, c]`：期望类型为 `[N]T` 时校验元素数；期望类型为 `[]T` 时构造新切片；期望类型为 `List<T>`（含 `List<T>?`）时构造新 List（v0.51：`listNew` 后按书写序逐个 `listPush`，值类型元素存拷贝，`[]` 得空表）；无期望类型时推断为 `[N]T`（元素类型须一致）；
2. **`[N]T` 是值类型**：赋值与传参整体拷贝，按内容比较；
3. **`[]T` 是共享句柄**：赋值共享底层存储（对齐 TS 数组行为）；S1 不提供索引读写以外的切片操作（追加、子切片等属 S2 标准库）；

### 6.7 弱引用（v0.2 新增）

1. **构造**：`weak(r)`，`r: Ref<T>` —— 产生 `Weak<T>`（`T` 为 `r` 的指向类型）。`weak` 只接受 `Ref<...>` 实参，传其他类型为编译错误；
2. **解引用**：`deref(w)`，`w: Weak<T>` —— 返回 `T?`：目标存活时等价于对目标 `unref`（**值拷贝**，不产生新 `Ref`、不延长目标生命周期）；目标已释放时返回 `None`。因此弱引用观察的是"目标死没死 + 死前最后状态的拷贝"，**不是**长期共享句柄——需要长期持有请直接持有 `Ref<T>`；
3. **生命周期与计数**：`Weak<T>` 不 RETAIN 目标、不阻止目标释放（`--mem-report` 与守护用例验证该语义）；`Weak` 盒自身是 RC 堆对象（可存字段/传参/入数组，随普通规则计数）；
4. **相等**：`w1 == w2` 按 Weak 盒**自身同一性**（同 `Ref` 的句柄心智），不比较目标内容——深度相等比较不穿透 Weak（不变式：EQ 深比较不触碰计数）；
5. **不提供**：弱引用回调 / finalizer（V1.0 前不纳入）；循环中的目标在循环收集器（选型）落地前不会被回收，属已记录的残留而非 Weak 语义缺口。

### 6.8 外部函数与原生 FFI（v0.31）

模块级外部函数必须带 `from "库"` 或 `from syscall 编号`，无函数体且返回类型标注必填。无 `from` 的旧宿主 extern 已退场，一律在语义阶段报 3033，即使未调用也报错；接口内的方法签名不属于宿主 extern。当前不提供宿主注册表或 GUI 绑定。

旧版第 1–6 条的宿主 id、封送映射及 4013 运行期绑定规则只见于历史 D3 v0.30 §6.8，不再构成现行合同。下面沿用原生 FFI 的既有条款号，保留引用锚；类型、ABI 与调用期间有效的封送规则不变。

7. **声明**：`declare function 名(参数): 返回 from "库";`——Windows 写 DLL 名（可省 `.dll`，如 `"kernel32"`），Linux 写共享库全名（如 `"libc.so.6"`）；Linux 另有 `from syscall 编号;`：直接发系统调用、不经任何库，编号 0..4095，产物保持静态、无 libc。**编号是 x86_64 的号（v0.47）**：编给 x86_64 照旧；编给别的架构（aarch64、loongarch64、riscv64）时只许写在该架构的 `x_linux_<架构>.tx` 文件里（写该架构的号），否则第一次调用时报 3033 并指出它在 x86_64 上的名字。**名字形态（v0.47）**：`from syscall "openat";` 按编译目标的架构查编译器内置的内核号表（来自 Linux 源码），同一份源码在各架构上都对；目标上没有这个调用报 3033 并给出替代（通用表架构没有 `open`、`poll`、`access`、`dup2`、`pipe`、`fork`、`getrlimit`、`renameat`（loongarch64、riscv64）等旧形态，分别改 `openat`、`ppoll`、`faccessat2`、`dup3`、`pipe2`、`clone`、`prlimit64`、`renameat2`），不是 Linux 系统调用名的报 3033。名字在表里不等于运行的内核有它（如 `statx` 要 4.11），运行时照旧得 `-ENOSYS`。号表里有、但该架构的最低内核上没有、别的架构又自古就有而容易照搬的调用按没有处理（v0.47 补记）：目前只有 loongarch64 的 `fstat`、`newfstatat`（5.19～6.10 的内核没有，6.11 才补回；glibc、musl、Go 在龙芯上都只用 `statx`），报 3033 并提示 `statx`；确要用的写进 `x_linux_loongarch64.tx` 用数字形态。结构布局与常量同样随架构变（x86_64 的 `struct stat` 与通用表不同，aarch64 的 `O_DIRECTORY` 与 x86_64 不同）：优先用与架构无关的调用（`statx` 的结构各架构相同），依赖布局与常量的放进 `x_linux_<架构>.tx`。macOS 不提供稳定的系统调用接口（Go 曾因直接发系统调用在系统更新后失效），以 macOS 为目标时被调用的 `from syscall` 报 3033，改经 `from "libSystem"` 声明并放进 `_macos` 或 `_unix` 文件（v0.35）。macOS 的库名写 dylib 的安装名：`/` 开头的绝对路径（如 `"/usr/lib/libz.1.dylib"`），或 `@executable_path/`、`@loader_path/` 开头的装载路径，libSystem 可简写为 `"libSystem"`；交叉链接不读目标平台的库文件，所以不按短名搜索，其他写法以 macOS 为目标时报 3033（v0.35 补记 M2）。x86_64 macOS 上从 libSystem 导入的部分函数按 Apple 头文件改名链接（stat 与目录族的 `$INODE64`、`realpath$DARWIN_EXTSN`、`select$1050` 等），太虚里声明的 `stat` 与 C 程序得到同一个函数，结构按 64 位 inode 布局声明。`from`、`syscall`、`type` 按标识符形态识别，不新增关键字。
8. **可见性与导出**：FFI 外部函数只在声明它的模块可见，不可 `export`（2001）——平台差异封装在模块自己的导出函数里；各模块可以各自声明同一个 C 函数；同一模块内重复声明或与本模块的声明、导入重名报 3003。`declare type`、`declare struct` 是类型，可以 `export`。
9. **只检查被调用的**：目标检查、调用桩与导入只针对程序实际调用的外部函数；只声明不调用的不检查、不链接，共用文件里可以放另一目标的声明（同 Go、Zig：引用到才报）。目标检查（`from syscall` 只用于 Linux、`wstr` 只用于 Windows）在语义分析里于第一次调用时做，诊断落在声明处，检查命令与编辑器即可看到（v0.35；此前只在构建尾部）；同名符号来自不同库、macOS 上与运行时经 libSystem 导入的符号同名却来自别的库（v0.35 补记 M2），以及 `--static` 的限制属于链接层，仍在构建时报。平台相关的声明通常放在按目标文件里（§6.1-4）。
10. **句柄**：`declare type H;` 声明指针宽度的名义不透明句柄：只能来自外部函数的返回值或输出参数，能传回外部函数、同一句柄类型之间 `==`/`!=`，不能算术、不能 `as`（3013，提示用 `handleBits`/`handleFromBits<H>`）、不能与 `int` 隐式转换。需要数值或哨兵值（如 `INVALID_HANDLE_VALUE`）时用 `handleBits(h): int` 与 `handleFromBits<H>(v): H`（`v` 为能无损扩宽到 `int` 的整数）。外部函数返回 `H?` 时空指针得 `None`，参数为 `H?` 时可传 `None`。不同模块的同名句柄是不同类型。句柄不可发送到其他线程（随线程落地生效）。
11. **外部结构体**：`declare struct S { a: i32, b: [4]u16 }` 按 C 布局（声明序、C 对齐规则；x86_64 上 `isize`/`usize`/句柄 8 字节）：字段只能是定宽数值、`int`/`float`、句柄、定长数组 `[N]标量`、其他外部结构体，以及 `string`、`wstr`、回调函数类型（v0.43，见下）与 `Bytes`（v0.46，见下）——`bool` 不在其内（Win32 `BOOL` 写 `i32`），字段不收可空句柄（空句柄写 `handleFromBits<H>(0)`）；至少一个字段，不收类型参数，不能按值包含自身（3033）。太虚侧是普通值类型 struct，读写规则同一般代码。以 `Ref<S>` 传给外部函数时：调用前按布局拷入临时 C 缓冲（填充清零），调用后拷回字段（输入输出两用）；同一个 `Ref` 作两个实参时各拷一份，按参数顺序拷回、后者覆盖前者。**按值（v0.45）**：`S` 本身作参数时同样拷入临时缓冲、按目标平台 C 编译器的调用约定传，调用后不拷回（C 端改的是它自己的副本；冻结的结构体也可按值传，不 trap 4022）；作返回类型时得到一个新的 `S` 值，字段按布局从 C 返回的字节读出。按值返回的结构体（含嵌套的）不能有 `string`、`wstr` 或回调字段（3033，理由同拷回跳过它们）。调用约定：Win64 只看大小——1、2、4、8 字节按同宽整数传（浮点成员也走整数寄存器）、在 RAX 返回，其余由调用方拷进 16 字节对齐的临时内存按引用传、返回经隐藏的首个指针实参；SysV x86_64 大于 16 字节的经栈传、经隐藏指针返回，不大于 16 字节的按八字节分类（有整数成员即 INTEGER，否则 SSE），各用下一个整数或 XMM 寄存器、返回在 RAX／RDX 与 XMM0／XMM1；Apple arm64 叶子全为同一种浮点的 1～4 个成员（同构浮点聚合）每成员一个 V 寄存器，其余不大于 16 字节的占一到两个整数寄存器，更大的按指针传、经 X8 返回。寄存器类的按值结构体因剩余寄存器不够要整体改走栈的调用（SysV 的不大于 16 字节结构体、arm64 的同构浮点聚合与小结构体）暂不支持，第一次调用时按目标报 3033（系统库里找不到这类函数核对 ABI）。按值的外部结构体不能用在 `from syscall`、C 变参参数与回调签名里（3033）。**回调与串字段（v0.43）**：三者在 C 布局里都是一个指针（8 字节、8 字节对齐），只由太虚写给 C——拷入时 `string` 字段拷进临时缓冲并补 NUL、`wstr` 字段转成 NUL 结尾的 UTF-16（串内含 NUL 字节 trap 4019，缓冲只在本次调用期间有效，C 端不得保留指针或写入），回调字段换成该（函数, 签名）的 C 入口（第 14 条）；拷回时跳过它们，太虚侧保持调用前的值（太虚不能把 C 指针读回成串或函数）。回调字段的参数与返回限制、它的值（结构体字面量与字段赋值里须是顶层的非泛型、非 `async` 函数名，类型与字段一致）同回调实参，否则 3033；`wstr` 字段只用于 Windows 目标（目标检查同 `wstr` 参数）。用不到的指针字段（如可为空的串）声明为 `usize` 写 0。**`Bytes` 字段（v0.46）**：C 布局里是数据区指针，规则同 `Bytes` 参数——只在本次调用期间有效、C 端可以写入（输出缓冲），长度由调用方另放字段；冻结的 trap 4022，调用期间被借用、改变长度 trap 4024（第 16 条），同一个 `Bytes` 出现在多个字段或实参时按后进先出恢复；拷回时跳过（C 写的是它自己的数据区）。含 `string`、`wstr`、`Bytes` 或回调字段（含嵌套的）的外部结构体不能按值返回（3033）。
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
   | `S`（外部结构体，v0.45） | `S`（按值） | 参数与返回；参数拷入、不拷回，返回得新值，按目标调用约定，见第 11 条 |
   | 函数类型 | C 函数指针 | 只作参数（回调），见第 14 条 |
   | `void` | — | 只作返回 |

   签名顶层的 `string` 即 C 串（v0.36）：太虚的 `string` 本身就是 UTF-8，不另设类型名（同 Swift 的 `String` 实参、Kotlin/Native 的 `String` 自动桥接）；`string` 在签名以外照旧是普通字符串，回调签名不收字符串（第 14 条）。`wstr` 只在带 `from` 的外部函数签名顶层与外部结构体的字段（第 11 条，v0.43）识别（太虚侧即 `string`），用在别处报 3033 并提示改用 `string`；UTF-16 是另一种表示，编译器不按函数名（如 W 后缀）推断。写 `cstr` 报 3033“`cstr` 已并入 `string`”（v0.36）。其余形态——`Ref<普通 struct>`、`List`、可选（句柄除外）、失败联合、返回 `Bytes`、联合与位域、指向指针的指针——报 3033，文案按形态给出改法；C 变参见第 21 条。
13. **`from syscall` 的额外限制**：最多 6 个参数；参数只收整数、`bool`、句柄、`string`、`Bytes`、`Ref<外部结构体>`（无浮点、`wstr`、回调、按值的外部结构体，也不收变参参数——系统调用的实参个数固定，第 21 条）；返回只能是整数类型或 `void`，返回值本身就是 `-errno`；Windows 目标调用 `from syscall` 的外部函数报 3033。
14. **回调**：参数可为函数类型（如 `cmp: (usize, usize) => i32`），其参数与返回只收标量、`bool`、句柄（不收按值的外部结构体，v0.45），不能 `async`。实参必须是顶层的非泛型、非 `async`、非生成器函数名，签名与形参完全一致（不做扩宽），否则 3033——闭包与函数值变量都不行；外部结构体的回调字段（第 11 条，v0.43）同此规则。编译器为每个（函数, 回调签名）生成 C ABI 蹦床。回调只能在一次外部函数调用期间、由发起这次调用的太虚线程调用（如 `qsort` 的比较函数、`EnumWindows`；v0.25 起各线程各自的外部调用期间都可以），否则运行期 trap 4020；回调里可以再调外部函数（嵌套与重入安全）。需要上下文时用 C 接口自带的 userdata 传 `int`，自己查表。
15. **错误码**：每次库函数调用后立即保存系统错误码（Windows `GetLastError`、Linux 经 libc 的 errno），`ffiLastError(): int` 读取本线程最近一次库函数调用留下的值（v0.25 起每线程一份）；`from syscall` 不写它（返回值本身就是 `-errno`）。标准库包装层把它们转成返回码或 `T | Error`。
16. **生存期与安全契约**：所有临时缓冲（`string`、`wstr`、外部结构体及其串字段）只在本次调用期间有效；外部结构体 `Bytes` 字段（v0.46）的数据区同 `Bytes` 实参。`Bytes` 实参（含经 `listAsBytes` 的 `List<u8>`）交给外部函数或系统调用之前做冻结检查，冻结的 trap 4022（§6.22-8；冻结的外部结构体以 `Ref` 传入同样 trap 4022，第 11 条）。调用期间该 `Bytes` 被临时借用，数据区与长度都不变：回调经该 `Bytes` 或其 `List<u8>` 别名做任何改变长度的操作（`bytesPush`、非空的 `bytesAppend`、`listPush`、`listInsert`、非空表的 `listPop`、`listRemoveAt`），在修改之前 trap 4024；原地读写（`bytesGet`、`bytesSet`、`listGet`、`listSet`）与不改变长度的调用（空的 `bytesAppend`、空表的 `listPop`）照常（v0.34；v0.33 只拒绝超出当前容量的增长，是否 trap 取决于当时的容量）。调用正常返回后借用解除，嵌套调用与同一对象作多个实参时按后进先出恢复。C 端在调用返回后保留指针、外部异常或 longjmp 越过太虚帧，不在这一安全合同之内。太虚堆不暴露给 C：没有原始指针类型，C 端拿到的只有临时缓冲或 `Bytes` 数据区。C 端保留指针、越界写缓冲、签名写错（参数个数、宽度、调用约定）属于调用方契约违反，规范写明、运行时查不出——**标准库已覆盖的能力不得手写绑定**，外部声明集中在标准库与平台层。
17. **链接与产物形态**：两个目标都按符号名导入，交叉链接不需要目标平台的库文件。Windows 经 PE 导入表（每个库一个导入目录项）；两个被调用的声明给同一个名字写了不同的库报 3033。Linux 目标调用了 `from "库"` 的外部函数时产物动态链接（解释器 `/lib64/ld-linux-x86-64.so.2`，DT_NEEDED 按声明的库名首次出现序并补 `libc.so.6`，立即绑定），经 libc 的 `exit` 退出（C 侧 stdio 照常刷新），只支持 glibc；只用 `from syscall` 或不用 FFI 的产物保持静态、无 libc。库缺失时进程起不来、由系统报告（没有懒装载）。
18. **`--static` 与构建报告**：`taixu build`/`run` 接受 `--static`：Linux 目标调用了 `from "库"` 的外部函数报 3033 并指出声明处；Windows 产物不受影响。程序调用了外部函数时，构建在产物行后多一行报告：Windows“外部库 kernel32.dll（模块）、…”，Linux 动态“动态链接 库（模块）、…；解释器 …”，只用 `from syscall` 为“静态产物（外部函数均为 from syscall）”；不用外部函数的构建输出不变。程序创建了外部内存区（第 22 条，v0.48）时再多一行“外部内存区：模块（n 处）、…”，列出调用 `ffiMem`／`ffiMemOf` 的模块。
19. **运行期码**：4019 `ffi_arg`（`string`/`wstr` 实参含 NUL 字节）、4020 `ffi_callback`（回调不在外部调用期间，或调用它的线程不是发起这次外部调用的太虚线程）、4022 `frozen_write`（冻结的 `Bytes` 或冻结的外部结构体 `Ref` 作外部函数实参，第 16 条）、4024 `ffi_buffer_borrow`（v0.33 起；v0.34 起为调用期间经 `Bytes` 或其 `List<u8>` 别名改变长度，第 16 条；诊断带调用处的源码位置）、4025 `ffi_mem`（v0.48，第 22 条：外部内存区越界读写、写只读区、区已结束、负大小、同时存活的区超过上限）；退出码 3。
20. **内部编码与历史产物**：原生编译器内部以 TXC IR 表示外部函数调用；它不是公开 `.txc` 产物，也没有 VM 执行入口。旧 v4/v5 产物、`TXFFI` 与冻结加载器的兼容规则仅见历史 D3 v0.30；当前容量按附录 A 的原生 TXC v6 列。
21. **C 变参参数（v0.36）**：C 变参函数在声明里把变参位置写成带 `...` 前缀的参数，读作“变参位置上的这种类型”：

   ```
   declare function openat(fd: i32, path: string, flags: i32, ...mode: u32): i32 from "libSystem";
   declare function snprintf(buf: Bytes, size: usize, format: string, ...value: float, ...count: int): i32 from "libc.so.6";
   ```

   - **位置**：变参参数排在全部定参之后、至少一个定参（2002）；一个声明可有多个变参参数，每个都写 `...`（三个相邻的点；fmt 让它紧贴参数名）。
   - **类型**：同定参可用的封送类型（第 12 条：整数、定宽整数、`bool`、`float`、`f32`、句柄、`string`、`wstr`、`Bytes`、`Ref<外部结构体>`）；回调与按值的外部结构体（v0.45）不能作变参参数（3033）；`from syscall` 不收变参参数（3033）。
   - **调用**：照常逐个传实参，个数与类型按声明核对（同定参），没有调用处推断。一个 C 函数在一个模块里只有一种声明：同一函数要以不同的变参调用，须是同一套类型（printf 族通常用不上，太虚有自己的格式化）。
   - **默认实参提升**：编译器在变参位置做 C 的默认实参提升，不要求手写转换（同 C 编译器）——`i8`、`i16` 符号扩展，`u8`、`u16`、`bool` 零扩展到 C 的 `int`；`f32` 转成 `double`；其余整数、指针类按本身的宽度扩展，各占一个 8 字节变参槽。
   - **调用约定**：x86_64 的 SysV 与 Win64 约定下变参与定参同样传（SysV 的 `al` 计入变参浮点个数；Win64 前四个位置上的变参浮点同时放进 xmm 与对应的通用寄存器）；Apple arm64 上变参一律经栈传、各占 8 字节，从定参的栈区之后按 8 字节对齐起——变参函数声明成定参在 x86 上碰巧能用、在 Apple arm64 上读到垃圾，故须如实声明。
   - **已知变参函数的核对**：对系统 C 库（`libc.so.6`、`libSystem`、`ucrtbase`、`msvcrt`、`user32`，不分大小写，可带 `.dll`）的声明，下列 C 变参函数的定参个数须等于表中个数，多出的参数须写成变参参数，否则 3033（文案给出改写，如“第 4 个参数起写成变参参数，如 `...mode: u32`”）：`printf 1`、`fprintf 2`、`dprintf 2`、`sprintf 2`、`snprintf 3`、`_snprintf 3`、`asprintf 2`、`scanf 1`、`fscanf 2`、`sscanf 2`、`open 2`、`open64 2`、`openat 3`、`openat64 3`、`fcntl 2`、`fcntl64 2`、`ioctl 2`、`execl 2`、`execlp 2`、`execle 2`、`syslog 2`、`prctl 1`、`semctl 3`、`sem_open 2`、`mq_open 2`、`err 2`、`errx 2`、`warn 1`、`warnx 1`、`_open 2`、`wsprintfA 2`、`wsprintfW 2`。不在表里的库函数由声明者负责，与 C 原型不一致的声明属第 16 条的契约违反。这张表是太虚的补充：Rust、Zig 有头文件或手写原型可依，太虚的外部声明是唯一来源。

22. **外部内存区（v0.48）**：C 接口交来的内存地址（`WM_GETMINMAXINFO` 的 `lParam`、剪贴板 `GlobalLock` 的结果等）经外部内存区读写，不另设原始指针类型：
   - **类型**：内建不透明类型 `FfiMem`，即一段外部内存的（起点、大小、可写否）。只作局部变量与参数；不能作返回类型、结构体字段（含外部结构体）、`List` 等集合的元素、模块级变量的类型与泛型实参，不能比较、打印，不能发送到其他线程（同句柄），违者 3033。`handleBits`、`handleFromBits` 不收它。
   - **创建（唯一的不安全步骤）**：`ffiMem(address: int, size: int, writable: bool): FfiMem | Error` 给地址定大小；`ffiMemOf<S>(address: int, writable: bool): FfiMem | Error` 的大小取外部结构体 `S` 的布局大小，`S` 含 `string`、`wstr`、`Bytes` 或回调字段（含嵌套的）报 3033。创建时核验地址：地址为 0、整段里有未映射（Windows 未提交）、不可读（`PAGE_NOACCESS`、`PAGE_GUARD`、`PROT_NONE`）或 `writable` 时不可写的部分，得 `Error`；Linux 上可写区只核验可读。`size` 为负 trap 4025；`size` 为 0 得空区（不核验，不能读写任何字节）。句柄形态的地址先经 `handleBits` 取整数，`isize`／`usize` 按一般规则转成 `int`。
   - **门槛**：`ffiMem`、`ffiMemOf` 只许出现在本身声明了 `from` 外部函数的模块里，否则 3033（外部内存区属平台层，同第 16 条“外部声明集中在标准库与平台层”）；不许出现在 `async` 函数与生成器里（3033，挂起会越过区的生存期）。读写函数与 `FfiMem` 参数不受门槛限制。
   - **读写**（按区的大小查界，越界 trap 4025，诊断写出区的大小与请求的范围；写只读区 trap 4025）：`ffiMemRead(m, offset, n): Bytes` 拷成新的 `Bytes`；`ffiMemReadInto(m, offset, dst: Bytes, dstOffset, n): void` 拷进已有的 `Bytes`（`dst` 冻结 trap 4022，`dst` 一侧越界同 `bytesCopy`）；`ffiMemWrite(m, offset, src: Bytes): void`；`ffiMemGet<S>(m, offset): S`、`ffiMemSet<S>(m, offset, v: S): void` 按外部结构体布局读写（`S` 的限制同 `ffiMemOf`，字段编解码同第 11 条的拷入拷回，偏移不要求对齐）；`ffiMemUtf16(m, offset): string`、`ffiMemUtf8(m, offset): string` 从 `offset` 读到第一个 NUL 或区尾为止（UTF-16 按两字节的码元读，区尾的单个字节忽略），坏代理与非法 UTF-8 替换为 U+FFFD。
   - **生存期**：区在创建它的函数返回时结束（含经 `?` 与 `catch` 的提前返回；在延迟块之后），也可提前 `ffiMemEnd(m): void`（重复结束无效果）。结束之后经它或它的副本读写 trap 4025。同一线程同时存活的区至多 64 个，超出 trap 4025。C 端让地址失效之前须先结束区（如 `GlobalUnlock` 之前），这一点仍是调用方契约（第 16 条）；区的大小写错（大于 C 端的实际缓冲）同样查不出，只能把错误限制在声明的大小之内。

   ```
   const m = ffiMemOf<MINMAXINFO>(lp as int, true) catch (e) { return 0; };
   let info = ffiMemGet<MINMAXINFO>(m, 0);
   info.minTrackX = 320;
   ffiMemSet<MINMAXINFO>(m, 0, info);
   ```

### 6.9 List 容器（v0.5 新增）

1. **构造**：`listNew<T>()`——空表；元素类型 `T` 经**期望类型推断**（同 `[]T` 字面量规则：`let l: List<int> = listNew();`），无期望为编译错误（编译器不定 `T`，负例）；
2. **操作**（仅此六件，无方法、无索引语法——语言面最小化红线）：
   - `listPush(l, v): void`——尾追加；
   - `listPop(l): T`——尾弹出，空表 **trap**（4002，v0.51；此前得 `None`）；
   - `listGet(l, i): T`——索引读，越界 **trap**（4002，v0.51；此前得 `None`）——越界是编程错误，同 Python、Swift、C#、Go、Java 的下标与 Rust `v[i]`、Kotlin `list[i]`；
   - `listTryGet(l, i): T?`、`listTryPop(l): T?`（v0.51）——“可能越界／可能为空”时用：越界或空表得 `None`（即 v0.50 的 `listGet`／`listPop`），同 Kotlin `getOrNull`、Rust `get`。元素本身可空（`List<T?>`）时越界与空元素无法区分，报 3002 定向拒绝：先比 `listLen` 再用 `listGet`；
   - `listSet(l, i, v): void`——索引写，越界 **trap**（越界码 4002——写入位置的丢失是程序错误而非预期缺席）；
   - `listLen(l): int`——长度；
3. **句柄语义**：`List<T>` 是 RC 堆对象；赋值/传参/存字段共享底层存储（对齐 `Ref`/`[]T`）；`==` 按句柄同一性（§6.5-3）；
4. **计数规则**（帧窗口所有权制的常规延伸，`--mem-report` 守护）：`listPush` 对元素 RETAIN（表持独立所有权）；`listPop` 将元素所有权自表**移交**结果槽（不 RETAIN）；`listGet` 为共享读 RETAIN（表仍持有）；`listSet` RETAIN 新值、RELEASE 旧值；表对象释放时级联释放全部元素；
5. **元素类型面**：`int/float/bool/string/Ref<T>/Weak<T>/List`（嵌套）、函数类型、`Future<T>`、`Bytes`；**v0.20 起放开到 struct/enum（错误 enum 除外）/定长数组与接口值**——值类型元素按值语义（与 `const q = p` 同一规则）：`listPush`/`listSet`/`listInsert` 存入拷贝，`listGet` 取出拷贝（修改取出的值不影响表内元素），`listPop`/`listRemoveAt` 把元素移交出来（不拷贝）；要在表内原地修改，存 `Ref<T>`。**v0.51 起元素可为 `T?`**（`listGet` 越界 trap，取到的 `None` 只能是元素本身；`listPop` 同理）；失败联合 `T | Error` 仍不收（错误要先处理，需要“结果列表”时放进 struct 或用 `List<Future<T>>`，3020）。**v0.20 的放开**：用到值类型元素的程序，SEB1 追加 `LVE1` 标记节。
6. **无驻留特例**：表与其元素全程常规计数（无永生哨兵），循环 push/pop 零滞留由 `--mem-report` 用例守护。

### 6.10 语法糖三件（v0.6 新增）

**总则**：三件全部是**书写层面的糖**，解糖为既有指令面——**零 .txc 格式变更**（金哈希 `806dd58c` 守护预期恒等；等价性由指令流逐位对照单测锁定）；不引入对象系统、虚分派、`this` 或新内建。

1. **方法语法 `recv.name(args)`**：解析为普通后缀链（接收者 + 字段访问 + 调用），sema 检查期解糖为纯函数调用——**解析序（冻结）**：
   - 接收者为作用域内 enum 类型名 → 变体构造（§5 注记 2，v0.6 前语义不变）；
   - 否则按接收者**静态类型**归一化 `TypeName_methodName`，经**驼峰约定映射**（`List_push`→`listPush`、`Map_getInt`→`mapGetInt`、`string_trim`→`stringTrim`）得目标函数名，按普通调用同一解析序（作用域 → extern → 内建）查找——内建/库函数经此路命中（`l.push(v)`≡`listPush(l, v)`、`m.get(k)`≡`mapGet(m, k)`）；
   - 约定名未命中 → 以**方法名直击作用域**（鸭子方法：首个实参为接收者的用户函数，`p.magnitude()`≡`magnitude(p)`——零新语义，参数与返回类型沿普通函数调用诊断）；
   - 两路皆未命中 → 既有 `undefined_symbol`（3001，消息含接收者类型与方法名）；
   - **不进语言核心**：无 method 表、无 `this`、无重载——它是书写的糖（§8"方法语法"排除条目由本条取代：书写形态入 S1，类、继承、`this`、重载仍永久排除）。
   - **v0.20 补充**：泛型 struct 实例按模板名映射（`Box<int>` 上的 `b.get()` 找 `boxGet`）；接收者为接口值时是动态分派（§6.18-5）；泛型实例体内按上述顺序找不到时，回落到接收者类型的声明模块（§6.18-2）。
2. **for-in 迭代 `for (x in e)` / `for (k, v in e)`**（`in` 为上下文关键字，§3）：检查期解糖为**计数循环**——与今日手写完全同构（`const #it = e;` 绑定 → `for (let #i = 0; #i < len(#it); #i += 1 形态)` → 体首元素绑定）；合成名以 `#` 前缀承载（非合法用户标识符，永不碰撞）。**可迭代面（冻结）**：`List<T>`（元素，`listLen`/`listGet`）、`string`（**v0.52 起逐字符产出码点**（`int`），见下；v0.51 及以前逐字节）、数组 `[N]T` 与切片 `[]T`（下标序，`len`/索引）；`Map`（stdlib 类型：单变量迭代**键**、双变量迭代键值对，经 `mapKeys`/`mapGet`——**插入序红线**由 stdlib 守护；`mapKeys`/`mapGet` 未导入时报定向 `undefined_symbol`）；**v0.20**：prelude 的泛型 `Map<K, V>` 同此规则，`Set<T>` 单变量迭代元素（经 `setToList`），均为插入序（§6.19）。取元素由循环界保证在界（v0.50 及以前写作“越界得 `None` ＋发散 catch 收口”，§6.3-3；v0.51 起元素可空的 List 用越界即 trap 的 `listGet` 取元素，`None` 照常是元素；值可空的 Map 用 `mapAt` 取值）；`break`/`continue` 沿用最近循环语义。可迭代面之外报 `not_iterable`（3022）。**双变量形态**：`Map`（键, 值）；**v0.51** 起 `List`、数组、切片为（下标, 元素），`for (i, x in xs)` 的 `i` 为 `int`；`Set`、`Gen` 只收单变量（3022）。**字符串（v0.52）**：`for (c in s)` 按 UTF-8 逐字符解码，`c` 为码点；`for (i, c in s)` 的 `i` 为该字符首字节的字节偏移（同 Go 的 `for i, r := range s`），可直接交给 `strSlice`、`byteAt`；`continue` 同样前进到下一个字符。解糖为 `for (let #i = 0; #i < len(#it); #i = #strCpEnd(#it, #i)) { const c = #strCpAt(#it, #i); 体 }`（`#` 名为内部内建）。要逐字节写 `while`＋`byteAt`。此前逐字节产出——非 ASCII 串的迭代次数与取值都变（兼容变更 `lang.str-for-in-codepoint`），ASCII 串结果不变。**区间（v0.51）**：`for (i in range(n))` 即 `0..n-1`，`for (i in range(a, b))` 即 `a..b-1`（`b <= a` 时不执行）——解糖为计数循环、不分配；实参须为 `int`，各求值一次、自左向右；只写一个循环变量（3022）。预声明的 `range` 只用作 for-in 的迭代对象（别处报 3002）；从 `std/collections` 导入的 `range(lo, hi)` 在 for-in 里同样不分配。**v0.22**：`Gen<T>` 可迭代（单变量，元素为 `T`；双变量报 3022），逐个取值、`None` 即结束；提前离开循环即关闭生成器（§6.20-5）。
3. **字符串插值 `` `a=${x} b` ``**：解析期解糖为**拼接链**——字面段成为字符串字面量（raw＝引号包裹的模板内原文切片，转义原样保留——渲染红线不破），表达式段统一 `toStr` 化后以 `+` 左结合串联（与手写 `"a=" + toStr(x)` 逐位同构；空字面段不入链）。**段类型面**＝`toStr` 之面（v0.6 起 `int/float/bool/string`，v0.11 增 `Error`——错误值可读化，§7）——`Ref`/`List` 等面外类型沿 `toStr` 既有诊断报错；段内不支持嵌套模板（词法结构化拒绝）。无插值段的模板串 ≡ 普通字符串字面量。

### 6.11 闭包与函数类型（v0.7 新增）

1. **单一书写形式**：只有箭头形式 `arrow_fn`；`function` 保留声明位，不引入 function 表达式。参数与返回类型必须显式，零推断；类型位函数类型的参数只写类型不写名。
2. **捕获＝创建时值快照**：值类型（int/float/bool/struct/enum/定宽数组）创建时 clone 进 env；句柄类型（`Ref`/string/切片/`List`/函数）复制句柄。**闭包体内被捕获名只读**——赋值即编译错误（`closure_capture_readonly`，3023）；要可变状态就持 `Ref`（穿过捕获句柄写字段合法）。无引用捕获（by-ref 不入面）。
3. **捕获解析**：闭包体内自由标识符按最近绑定解析——外层局部/形参 → 捕获；顶层绑定 → 不捕获（经 env 的定义模块状态基座直连）；遮蔽规则与块作用域一致。嵌套闭包对外层捕获名的引用＝再次捕获。绑定名在自身初始化器内不可见（禁自指，与既有 const 一致）——递归走普通函数或经参数传递。
4. **双形态收敛**：**箭头字面量恒 env 形态**（闭包值＝env 对象引用，字段布局冻结：f0＝函数位 fnv、f1＝定义模块状态引用、f2..＝捕获快照——普通 RC 对象，全部计数点走既有机制零特判）；**具名函数值恒 fnv 形态**（`const f: (int) => int = helper;` 直接装载函数索引）。调用点双臂收敛：`.call` 对 fnv 与 env 形态统一 R0 语义（fnv 路径改写模块状态、env 路径保留 env 引用随槽移交被调帧——同槽同账目形态）。`.ret`/trap 卸载的 R0 释放规则自 v0.7 起为「R0 一律 releaseSlot」（模块状态永生哨兵＝空操作——旧程序逐字节不受影响；金哈希 `806dd58c` 不变）。
5. **调用约定边界注记**：「ref 且 f0 为 fnv」是 VM 层闭包调用的 **ABI 契约**，不是用户可依赖的语言特性——sema 把关调用点 callee 必须为函数类型；文档/教程/llm-guide 一律不得将「可调用 struct」当特性叙述。
6. **方法解析序增补（§6.10-1 序的中位插入）**：变体构造 → **fn 类型字段直调**（`obj.onClick(e)`——字段存在且类型为函数类型时接管；GUI 回调存字段主用例）→ 约定名 → 同名用户函数 → 编译错误。fn 类型字段为 v0.7 新面，与既有程序零交叠。
7. **显式不做**：引用捕获/被捕获可变绑定、闭包与 `Weak` 的组合糖、函数重载/方法表（`this`）、函数类型泛型参数（高阶内建按用例另立）、闭包相等、`function` 表达式形式。

### 6.12 异步与任务运行时（v0.8 新增）

1. **着色规则**：`async function f(): T` 的 `T` 为**成功侧**类型；直接调用 `f(x)` **启动任务**（§6.12-3）并返回 `Future<T>`；`async` 箭头（§5 `arrow_fn`）同面。着色跟随**被调表达式**——经普通 fn 类型绑定/形参转发调用＝着色擦除（同步执行；体内 await 即结构化 trap）；类型同一性不比较着色位（sig 面无 async 位，async 值→同签名 fn 类型＝宽化）。声明体面＝`T | Error`：体内 `return v`（T）与 `return err(...)` 皆合法，`?`/`catch` 照常作用；missing_return 按书面返回判定（void 的 async 观察者函数不因体面包装误报）。**v0.53**：声明 `T | Error`（失败侧为 Error）与声明 `T` 等价，调用都得 `Future<T>`，两种写法都收（此前得写不出的 `Future<T | Error>`，放不进 `List<Future<T>>`）。
2. **await**：`await fut` 只能写在 async 函数体与 async 箭头体内，以及同步入口——`main` 体、`test` 块体、直接写在 `threadSpawn(…)` 实参位置的箭头体（v0.53，§6.12-4）——内，其余位置报 3024。资格按函数／箭头各自判定，嵌套的普通箭头不继承外层（v0.53 起；此前实现按累计深度放行，嵌套在 async 函数里的普通箭头能过检查、运行到未决的 await 才 trap，登记 `lang.await-arrow-scope`）；操作数须为 `Future<T>`（3025）；产出 `T | Error`（任务内错误原样穿透联合通道）——await 天然接 `?`/`catch`，零新增通道语义。**v0.38**：同一 `Future` 可多次 `await`（同一任务里先后、或多个任务各自），每次得同一结果——结果与 future 共享（引用类型得同一对象，值类型得相同的值），future 释放时连带放掉它那一份；已取消的每次得 `err("task cancelled")`。
3. **spawn＝惰性入队**：调用 async 函数建任务＋Future 对象后**入 FIFO 就绪队即返回**——不急切执行；启动序＝FIFO 序＝可观测执行序。async 体内调用 async 函数＝spawn 子任务（不经 await 即并发）。即“调用＝排队”：要等到有人 `await`（同步入口里的 await 运行调度泵，或任务在 await 处让出）时才按先来后到执行（保留惰性 FIFO）。
4. **调度器＝同步入口 await 里的 FIFO 泵（v0.53 改）**：`main`、`test` 块与直接写在 `threadSpawn` 实参位置的箭头是**同步入口**，始终同步执行，体内可直接 `await`：所等的 future 未完成时，在入口帧之上就地运行调度泵——每轮先收割到期定时器，再按开轮时的就绪队快照 FIFO 运行任务（跑至完成或再次挂起）；就绪队空时有定时器就等到最早到期，没有即死锁（§6.12-8）——直到它完成，入口取得结果继续执行；`taskAwaitAny` 在入口里同样就地运行泵。入口不能被调用（§7），泵不会重入。全序确定：完成序、await 唤醒序、`taskAwaitAny` 裁决序全部由 FIFO＋观察集插入序决定。**入口返回即结束**：入口返回（含以 `Error` 或非零退出码返回）时先执行自己的 `defer`，再取消全部未完成的任务——按任务表序逐个取消，挂起中的按 §6.12-6 先执行已登记的 `defer`，取消过程中新建的任务同样取消——然后排空定时器；`main` 随即结束进程（未 join 的线程随之终止，§6.22-1），线程入口随即结束本线程。trap、`panic` 照旧立即结束进程，不执行 `defer`。不设同步阻塞等待（`taskWait`／`block_on`）：普通同步函数要等待就声明成 async。v0.52 及以前：main 同步体跑完后泵跑完全部剩余任务，main 体内 await 报 3024（登记 `lang.entry-await`）。
5. **挂起＝单帧快照（stackless 不变式）**：`await` 未决时本帧窗口值整体移入快照＋pc 存档→弹帧回调度器；future 完成后任务恢复并**重执行挂起点**。不变式：async 帧链恒单帧——await 仅在 async 体内词法面；async→async 经 Future 解耦，快照/恢复无需帧链序列化。
6. **取消即确定性清理**：`taskCancel(f)` 对未决任务置取消——**未启动/挂起中的任务当帧立即释放**（窗口值逐槽 release，局部/捕获 RC 确定性回收）；已完成者空操作；await 被取消的 future 得 `err("task cancelled")` 走联合通道；任务体内 `taskCancelled()` 为协作检查点旗标查询（同步入口里恒 false）。**v0.21**：挂起中（含已被唤醒、尚在就绪队里）的任务若在挂起点已登记 `defer`，先按后进先出执行这些 `defer` 再释放（§6.16-8）。
7. **taskAwaitAny**：`(List<Future<T>>) int | Error`——按 List 插入序扫描，有已完成/已取消者立即返回其下标；全未决→挂起登记观察集，任一完成即唤醒重扫（重新按插入序取最先完成者，确定性裁决）；空列表得 `Error`。
8. **定时器面（v0.9 增）**：`sleepMs(ms)` 建 pending future＋登记一次性定时器；调度泵每轮**先于就绪任务**收割到期定时器（deadline 升序、同刻按登记序——与 FIFO 全序共同构成确定性时间维度）；钟源恒单调钟。到期＝future 置 done（空结果，await 得 void）并唤醒观察者（观察集插入序）；任务已完成后关联的定时器到期**收割为空操作**；泵出口未到期定时器**排水为空操作**（仅释放引用——零滞留，库层超时依赖此条）。**只剩定时器时等待到期（v0.21）**：就绪队空、仍有未决任务且有未到期定时器时，调度泵等待到最早的 deadline 再收割（Windows `Sleep`、Linux `nanosleep`，毫秒向上取整），不判死锁；没有未决任务时泵出口照旧排水。唤醒序仍只由 deadline 序决定。`await sleepMs(0)`＝下一调度轮到期（确定边界）；负值＝即 err future。**确定性纪律（沿 nowMs 从严）**：e2e/断言面只允许 0ms（立即到期）与不到期（任务先完成）两类确定形态，真实中间时长只入手工冒烟面。
9. **库层超时与选择通道（v0.9 注记）**：超时/组级取消/异构选择均为 **stdlib 纯 .tx 库面**（std/task），语言核心零增量——`timeout(f, ms)`（当时单态 `Future<int>`——S1 尚无用户泛型函数；**v0.53 起 `std/task`、`std/pool` 对结果类型泛型**：`TaskGroup<T>`、`timeout<T>(f: Future<T>, ms): T | Error`，无参的 `taskGroupNew<T>()`、`poolNew<T>(n)` 须写类型，登记 `std.task-generic`）；竞速走"包装 runner 同构化"：taskAwaitAny 只收同构 `List<Future<T>>`，异构 future 经 async 包装统一为同型标记值（下标携带来源）后裁决——f 先完成透传（定时器事后收割空操作）、定时器先到期 taskCancel(f)（失败者取消，§6.12-6 语义复用）→ `err("timeout")`；组级取消 `taskGroupCancel`（登记序遍历逐 taskCancel；**cancel 后 drain 必达零 pending**——结构化闭环）。select 语法面（`select { case … }`）维持排除（§8）。
8. **死锁守护（trap 4014，退出码 3）**：同步入口等待中，就绪队空、也没有未到期定时器（v0.21），而所等的 future 仍未完成（await 等待环）——位置为入口里的这条 await。**v0.53 起“退出 pending”守护退场**：入口返回时未完成的任务按 §6.12-4 取消，不是错误；trap 文案不再附“另有 N 个未决任务”。（v0.52 及以前为双守护：①死锁——泵就绪排空而仍有未决任务；②退出 pending——全部可推进任务完成后仍有未决任务，main 经 panic／非零退出码退出时同样检查并附未决计数。）
9. **实现边界**：当前原生 `build/run/test` 支持异步面（§6.12-10），并经普通、调试和泄漏检查产物验证。
10. **原生实现（v0.21 新增）**：太虚编译器与 NRT 实现本节全部规则，执行序、唤醒序、`taskAwaitAny` 裁决序与出口 trap（消息文本、退出码 3；v0.53 起只剩死锁一种）与解释器逐项一致，NRT 差分逐字节对照。只有任务的基帧能挂起：着色经函数值擦除后在任务内同步调用的 async 函数，遇到未决 `await`（或 `taskAwaitAny`）按 trap「await 执行于无任务上下文」结束（解释器原先把嵌套帧当任务帧挂起、随后以 `corrupt_code` 结束，同修）。不含 `async` 的程序产物不变。v0.53：同步入口里的 await／taskAwaitAny 由运行时按帧窗口判定——入口函数的帧开在入口窗口，未决时就地运行泵；擦除着色后同步调用的 async 函数帧在入口之上，仍按「await 执行于无任务上下文」trap。
11. **丢弃 Future 与 detach（v0.53）**：类型为 `Future<T>` 的值当表达式语句丢掉（含 for 更新段）报 3005——直接的 async 调用、返回 Future 的普通函数、`sleepMs(…)` 都算（此前合法，登记 `lang.future-discard`）。要发后即忘写语句 `detach 调用;`：`detach` 是上下文关键字，只在语句开头且后随标识符时识别（`detach(x);`、`detach = 1;` 照旧是普通表达式），其后须是结果为 `Future<T>` 的函数调用（含方法简写），否则报 3005 并提示“普通函数要并行请声明 async，或用 threadSpawn”。语义＝求值调用、放掉句柄；任务照常排队，入口返回时若仍未完成同样被取消。绑定（`const f = job();`）不受影响。以 `Error` 结束却无人 `await` 的任务静默，退出码不变——调用方不取结果即放弃错误（同 Rust、Go 的错误值）；取消不算失败。

## 7. 内建函数与程序入口

**入口**：`function main(args: []string): int`（返回 `void` 视为返回 0；v0.19 起也可返回 `int | Error`，失败时 stderr 输出 `toStr(e)`、退出码 1，§6.17-9）；`args` 为命令行参数（不含程序名），逐项为命令行原文、空参数保留；参数不是合法 UTF-8（Windows 为不成对的 UTF-16 代理）时进程在入口拒绝启动——stderr `taixu aot: <平台> argv acquisition failed: InvalidUnicode`，退出码 64，`main` 不运行（v0.41；string 恒为合法 UTF-8）。**v0.53**：`main` 是纯入口——任一模块顶层名为 `main` 的函数都不能被调用（含递归、方法简写）、取值（含作 `threadSpawn` 实参、FFI 回调）或导入（§6.1-6），报 3010／3008（同 C++）；`async function main` 报 3010（main 体内可直接 await，§6.12-4）；`export function main` 仍合法。登记 `lang.main-entry`。

| 内建 | 签名 | 说明 |
|---|---|---|
| `print` | `(string) void` | 标准输出一行（INFO 级日志通道） |
| `eprint` | `(string) void` | 标准错误输出一行（原样写出、末尾加换行，不加前缀；与 `print` 对偶）（v0.30 补记） |
| `err` | `(string) Error` | 构造 Error |
| `readFile` | `(string) string \| Error` | 读文本文件（UTF-8） |
| `len` | `(string \| []T \| [N]T \| Bytes) int` | 长度（字符串与 Bytes 为字节数；Bytes 自 v0.18） |
| `toStr` | `(int \| float \| bool \| string \| Error) string` | 转字符串（v0.6：`string` 为恒等——插值解糖统一 toStr 化，§6.10-3；v0.11：`Error` ⇒ `"Error: " + message`——错误链打印可读化，v0.19：错误 enum 值 ⇒ `"E.V: " + message`，有原因时追加 `"；原因：" + toStr(原因)`，§6.17-7；v0.23：全部定宽整数，`u64`/`usize` 按无符号十进制，§6.21-8；v0.26：`f32`，按 f32 的最短往返表示，§6.21-18；v0.36：任何 NaN 打印 `nan`，不带符号） |
| `unref` | `(Ref<T>) T` | 从堆引用整体取值拷贝（唯一显式"解引用"形式） |
| `weak` | `(Ref<T>) Weak<T>` | 构造弱引用（不拥有目标；§6.7） |
| `deref` | `(Weak<T>) T?` | 解引用弱引用：目标存活得值拷贝、已死得 `None`（§6.7） |
| `byteAt` | `(string, int) int` | **自举层内建**：逐字节访问——在界得 UTF-8 字节值（0–255）；**v0.51 起越界 trap 4002**（此前得 `None`） |
| `byteTryAt` | `(string, int) int?` | 逐字节访问（v0.51 增）：越界得 `None`（即 v0.50 的 `byteAt`），扫描到串尾的循环用 |
| `utf8Encode` | `(int) string \| Error` | **自举层内建**：合法 Unicode 码点（≤0x10FFFF 且非代理区段）得 UTF-8 编码串，否则得 `Error` |
| `listNew` | `listNew<T>(): List<T>` | 空表；`T` 经期望类型推断，无期望编译错误（§6.9-1） |
| `listPush` | `(List<T>, T) void` | 尾追加（元素 RETAIN；§6.9-4） |
| `listPop` | `(List<T>) T` | 尾弹出（所有权移交）；**v0.51 起空表 trap 4002**（此前得 `None`） |
| `listTryPop` | `(List<T>) T?` | 尾弹出（v0.51 增）：空表得 `None`（即 v0.50 的 `listPop`）；元素可空的 List 报 3002 |
| `listGet` | `(List<T>, int) T` | 索引读；**v0.51 起越界 trap 4002**（此前得 `None`） |
| `listTryGet` | `(List<T>, int) T?` | 索引读（v0.51 增）：越界得 `None`（即 v0.50 的 `listGet`）；元素可空的 List 报 3002 |
| `listSet` | `(List<T>, int, T) void` | 索引写（越界 trap；RETAIN 新/RELEASE 旧） |
| `listLen` | `(List<T>) int` | 长度 |
| `listRemoveAt` | `(List<T>, int) void` | 索引删除（v0.10 增）：越界 trap（越界码）；被删元素所有权自表移交后**无人接管**——堆引用即 RELEASE |
| `listInsert` | `(List<T>, int, T) void` | 插入（v0.10 增）：`i==len` 即尾加；越界 trap（`0 <= i <= len`）；元素 RETAIN |
| `strSub` | `(string, int, int) string` | **v0.52 移除**：普通构建报 3001，按码点取子串改写为 `std/utf8` 的 `slice(s, a, a + n)`，按字节用 `strSlice`；种子换代前的 `--toolchain` 构建照收、语义不变。原说明：码元面子串（v0.10 增）：`strSub(s, from, count)` 按 **Unicode 码点**计 units（非字节——CJK 安全）；`from/count` 界内要求（`from >= 0`、`count >= 0`、`from+count <= 码点长`），越界 trap |
| `strIndexOf` | `(string, string) int` | **v0.52 移除**：普通构建报 3001，改写为 `std/utf8` 的 `find`（码点下标）或 `strFind`（字节下标）；`--toolchain` 构建照收。原说明：子串首现**码元索引**（v0.10 增）；缺席得 `-1`；空针得 `0` |
| `strSplit` | `(string, string) List<string>` | 分隔符切分（v0.10 增）：空分隔符 trap；连续分隔符产空串片；结果恒 ≥1 片 |
| `strSlice` | `(string, int, int) string` | 字节区间子串（v0.52 增）：`strSlice(s, from, to)` 取字节区间 `[from, to)` 的拷贝；`from < 0`、`to < from`、`to > len(s)` trap 4002，任一端落在 UTF-8 多字节序列中间（该处是续字节 `10xxxxxx`）同样 trap 4002，结果因此恒为合法 UTF-8（同 Rust `&s[a..b]`）。**字符串的整数位置与长度一律按字节**（`len`、`byteAt`、本函数、`strFind`）；按码点的切片在 `std/utf8.slice` |
| `strFind` | `(string, string) int` | 子串首现的**字节下标**（v0.52 增）；缺席得 `-1`；空针得 `0`。匹配处恒在字符边界上，结果可直接交给 `strSlice`；码点下标用 `std/utf8.find` |
| `strCompare` | `(string, string) int` | 按 UTF-8 字节字典序（即码点序）比较（v0.52 增）：`a` 在前得 `-1`、相等得 `0`、在后得 `1`；`string` 的 `< <= > >=` 与之一致（§6.5-5） |
| `strByteSub` | `(string, int, int) string` | **v0.52 移除**：普通构建报 3001，改写为 `strSlice(s, a, a + n)`（第三实参由字节数改为终点）；`--toolchain` 构建照收。原说明：字节面子串（v0.44 增）：`strByteSub(s, from, count)` 取字节区间 `[from, from+count)` 的拷贝；`from`、`count` 为负或 `from+count > len(s)` trap 4002；区间任一端落在 UTF-8 多字节序列中间（该处是续字节 `10xxxxxx`）同样 trap 4002，结果因此恒为合法 UTF-8。**字符串的长度与下标默认按字节**（`len`、`byteAt`、本函数、`strByteIndexOf`），`strSub`／`strIndexOf` 是码点面，两面的数不能混用 |
| `range` | `(int[, int]) for-in` | 区间（v0.51 增）：只作 for-in 的迭代对象，`range(n)` 即 `0..n-1`、`range(a, b)` 即 `a..b-1`，解糖为计数循环、不分配（§6.10-2）；别处报 3002 |
| `strByteIndexOf` | `(string, string) int` | **v0.52 移除**：普通构建报 3001，改写为 `strFind`（同义）；`--toolchain` 构建照收。原说明：子串首现的**字节下标**（v0.44 增）；缺席得 `-1`；空针得 `0`。两者都是合法 UTF-8，匹配处恒在字符边界上，结果可直接交给 `strByteSub` |
| `imul` | `(int, int) int` | 64 位环绕乘（v0.14 增，D3 v0.14）：**非宿主调用**——codegen 直发 IMUL opcode（per-char 哈希内环不走宿主表分发）；`Math.imul` 仅借形态、32 位语义不继承；`*` 溢出 trap 语义分立（§6.13-3）；v0.26 起与 `wrapMul<int>` 相同（§6.21-21），保留不弃用 |
| `wrapTo` | `wrapTo<T>(整数) T` | 截断转换：取低位按整数类型 `T` 的宽度与符号解释，不检查；类型实参必须写出（v0.23，§6.21-6；codegen 直发，不占 BuiltinId） |
| `wrapAdd` | `wrapAdd<T>(T, T) T` | 按整数类型 `T`（含 `int`）的宽度环绕加：二补码取低位，不检查溢出；类型实参可省，由两个实参的公共类型推出；写出时两个实参须能无损扩宽到 `T`（v0.26，§6.21-21） |
| `wrapSub` | `wrapSub<T>(T, T) T` | 环绕减，规则同 `wrapAdd`；无符号取二补码写 `wrapSub<T>(0, x)`（v0.26，§6.21-21） |
| `wrapMul` | `wrapMul<T>(T, T) T` | 环绕乘，规则同 `wrapAdd`；`wrapMul<int>` 即 `imul`（v0.26，§6.21-21） |
| `floatToBits` | `(float) int` | IEEE 双精度的 64 位模式（有符号 `int` 承载，最高位为符号位）；`f32` 实参先无损扩宽（v0.23；codegen 直发 cvt 种类 25，只换值标签，不占 BuiltinId） |
| `floatFromBits` | `(int) float` | 由 64 位模式构造双精度值，任何位模式都合法（含 NaN 与无穷）（v0.23；codegen 直发 cvt 种类 26，不占 BuiltinId） |
| `handleBits` | `(H) int` | 句柄的数值（v0.23，§6.8-10；codegen 直发，不占 BuiltinId） |
| `handleFromBits` | `handleFromBits<H>(整数) H` | 由数值构造句柄（哨兵值如 `INVALID_HANDLE_VALUE`）；类型实参必须写出，实参为能无损扩宽到 `int` 的整数（v0.23，§6.8-10；不占 BuiltinId） |
| `ffiLastError` | `() int` | 最近一次库函数调用后保存的系统错误码（Windows `GetLastError`、Linux errno；`from syscall` 不写它）（v0.23，§6.8-15；BuiltinId 60） |
| `ffiMem` | `(int, int, bool) FfiMem \| Error` | 给外部地址定大小，创建时核验（v0.48，§6.8-22；只许声明了 `from` 外部函数的模块） |
| `ffiMemOf` | `ffiMemOf<S>(int, bool) FfiMem \| Error` | 同 `ffiMem`，大小取外部结构体 `S` 的布局大小（v0.48，§6.8-22） |
| `ffiMemRead` | `(FfiMem, int, int) Bytes` | 从区里拷出若干字节（v0.48，§6.8-22；越界 trap 4025） |
| `ffiMemReadInto` | `(FfiMem, int, Bytes, int, int) void` | 从区里拷进已有的 `Bytes`（v0.48，§6.8-22） |
| `ffiMemWrite` | `(FfiMem, int, Bytes) void` | 把字节写进可写区（v0.48，§6.8-22） |
| `ffiMemGet` | `ffiMemGet<S>(FfiMem, int) S` | 按外部结构体布局读（v0.48，§6.8-22） |
| `ffiMemSet` | `ffiMemSet<S>(FfiMem, int, S) void` | 按外部结构体布局写（v0.48，§6.8-22） |
| `ffiMemUtf16` | `(FfiMem, int) string` | 读 UTF-16 串到 NUL 或区尾（v0.48，§6.8-22） |
| `ffiMemUtf8` | `(FfiMem, int) string` | 读 UTF-8 串到 NUL 或区尾（v0.48，§6.8-22） |
| `ffiMemEnd` | `(FfiMem) void` | 提前结束区（v0.48，§6.8-22） |
| `taskCancel` | `(Future<T>) void` | 协作式取消（§6.12-6；未启动/挂起任务当帧释放，已完成空操作） |
| `taskCancelled` | `() bool` | 当前任务取消旗标查询（协作检查点；同步入口里恒 false） |
| `taskAwaitAny` | `(List<Future<T>>) int \| Error` | 任一完成/取消→插入序最先者下标；全未决→挂起观察（§6.12-7） |
| `genNext` | `(Gen<T>) T?` | 取生成器的下一个产出值；已结束得 `None`，之后恒为 `None`；生成器运行中再取值 trap 4017（v0.22，§6.20-3；方法语法 `g.next()`；codegen 直发 `gen_next`，不占 BuiltinId） |
| `listDir` | `(string) List<string> \| Error` | 列出目录里的条目名：不含 `.` 与 `..`，按字节序排序，不带条目类型（类型用 `entryKind` 取）；打不开得 `Error`（消息为“原因: 路径”），空路径得 invalid argument（v0.30 补记） |
| `entryKind` | `(string) int` | 条目类型，不跟随符号链接：`0` 不可判（不存在、无权限、空路径；Linux 上 FIFO、设备、套接字也是 0）、`1` 文件、`2` 目录、`3` 符号链接（Windows 上为重解析点）（v0.30 补记） |
| `writeFile` | `(string, string) void \| Error` | 写文本文件（覆盖写；UTF-8 字节直写）——readFile 对偶（增） |
| `writeFileBytes` | `(string, List<int>) void \| Error` | 写二进制文件（覆盖写）：元素逐字节直写，须 int 且 0..255——越界/非 int 得结构化 `Error`（校验先于写出，无部分写）；.tx 无 ≥0x80 单字节串构造通道的二进制写出承接（v0.16 增，P-1 小合同 selfhost-delimitation-v050 §三；readFile+byteAt 读侧对偶） |
| `bytesNew` | `(int) Bytes` | 长度 n 的全零 Bytes；n<0 trap（v0.18） |
| `bytesLen` | `(Bytes) int` | 长度（`len(b)` 同义；方法 `b.len()`）（v0.18） |
| `bytesGet` | `(Bytes, int) int?` | 读字节；越界 `None`（v0.18） |
| `bytesSet` | `(Bytes, int, int) void` | 写字节；越界或值不在 0..255 trap（v0.18） |
| `bytesPush` | `(Bytes, int) void` | 尾追加一字节；值不在 0..255 trap（v0.18） |
| `bytesAppend` | `(Bytes, Bytes) void` | 把第二个的内容追加到第一个（可为同一对象）（v0.18） |
| `bytesSlice` | `(Bytes, int, int) Bytes` | `bytesSlice(b, from, count)` 拷贝出新对象；越界 trap（v0.18） |
| `bytesEqual` | `(Bytes, Bytes) bool` | 内容逐字节相等（v0.18） |
| `bytesFromStr` | `(string) Bytes` | 字符串的 UTF-8 字节拷贝（v0.18） |
| `bytesToStr` | `(Bytes) string \| Error` | 严格 UTF-8 校验后构造字符串（v0.18） |
| `readFileBytes` | `(string) Bytes \| Error` | 读二进制文件（v0.18） |
| `embedBytes` | `(字符串字面量) Bytes` | 编译期嵌入文件，见 §6.15-4；不占 BuiltinId（v0.18） |
| `bytesAsList` | `(Bytes) List<u8>` | 零拷贝把 `Bytes` 当作 `List<u8>` 用：只改静态类型，返回同一个对象，经任一侧的修改（含 `listPop` 缩短长度）两侧互见；实参不是 `Bytes` 报 3002（v0.26，§6.15-7） |
| `listAsBytes` | `(List<u8>) Bytes` | 零拷贝把 `List<u8>` 当作 `Bytes` 用：返回同一个对象；实参不是 `List<u8>` 报 3002，运行期核对不是紧凑的 `List<u8>` 时 trap 4010（v0.26，§6.15-7） |
| `bytesCopy` | `(Bytes, int, Bytes, int, int) void` | `bytesCopy(dst, at, src, from, count)` 区间拷贝，同一对象、区间重叠均可（memmove）；位置或个数为负、越界 trap 4002，写冻结 trap 4022（v0.37，§6.15-8）。BuiltinId 77 |
| `bytesFill` | `(Bytes, int, int, int) void` | `bytesFill(b, at, count, value)` 区间填充；越界或值不在 0..255 trap 4002，写冻结 trap 4022（v0.37）。BuiltinId 78 |
| `bytesGetLE` | `bytesGetLE<T>(Bytes, int) T?` | 按小端读定宽数值（`T` 为 `i8`…`u64`、`int`、`f32`、`float`，须写出）；越界得 `None`（v0.37）。BuiltinId 79 |
| `bytesGetBE` | `bytesGetBE<T>(Bytes, int) T?` | 按大端读，同 `bytesGetLE`（v0.37）。BuiltinId 79 |
| `bytesSetLE` | `bytesSetLE<T>(Bytes, int, T) void` | 按小端写定宽数值，值须正是 `T`；越界 trap 4002，写冻结 trap 4022（v0.37）。BuiltinId 80 |
| `bytesSetBE` | `bytesSetBE<T>(Bytes, int, T) void` | 按大端写，同 `bytesSetLE`（v0.37）。BuiltinId 80 |
| `errAs` | `errAs<E>(Error) E?` | 取回错误 enum 种类：带 `E` 种类得该值，否则 `None`；类型实参必须显式写出（v0.19，§6.17-6） |
| `errWrap` | `(Error, string) Error` | 包一层上下文，结果的 `cause` 为原错误（v0.19，§6.17-6） |
| `makeDir` | `(string) void \| Error` | 创建单层目录（v0.17 增）：权限 `0777 & ~umask`（POSIX）；**已存在的目录视为成功**（幂等）；同名非目录条目得 `Error "invalid argument: <path>"`，父目录缺失得 `"no such file: <path>"`，无权限 `"access denied: <path>"`，空路径 `"invalid argument: "`；递归创建由标准库 `makeDirAll` 提供 |
| `writeExecutable` | `(string, List<int>) void \| Error` | **工具链专用**（v0.17 增）：写出可执行文件——元素规则同 `writeFileBytes`（非 int 得 `"writeExecutable: 元素 -1 不是 int"`，越界得 `"writeExecutable: 元素 {v} 越界（须 0..255）"`；校验先于写出）；POSIX 先删同名旧文件再以 `0777 & ~umask` 新建（与 `ld` 一致），Windows 同 `writeFileBytes`。**语义检查默认拒绝**（3001 `writeExecutable 仅限工具链构建（需 --toolchain）`），仅编译选项 `--toolchain` 放行；编译器写出原生产物时使用 |
| `hostTarget` | `() string` | **工具链专用**（v0.21 增，下同；未带 `--toolchain` 以 3001 `X 仅限工具链构建（需 --toolchain）` 拒绝）：程序的编译目标平台 `"windows"`／`"linux"`（由产物的目标决定，运行时不探测；macOS 产物在实施后为 `"macos"`） |
| `hostArch` | `() string` | **工具链专用**（v0.35 增）：程序的编译目标架构 `"x86_64"`、`"aarch64"`、`"loongarch64"` 或 `"riscv64"`（同 `hostTarget`，由产物的目标决定；`"loongarch64"` 自`"riscv64"` 自 A5 起） |
| `getEnv` | `(string) string?` | 读环境变量；未设置或名字不合法（空、含 `=` 或 NUL）得 `None`（Windows `GetEnvironmentVariableW`；Linux 读进程级环境表——初值为启动时的 envp，`std/os` 的 `setenv` 写入它；v0.24 起，原为只读启动时的 envp）；值中的非法 UTF-8 逐字节替换为 U+FFFD |
| `setEnv` | `(string, string) void \| Error` | **工具链专用**（v0.42 增）：设置环境变量，写 `getEnv` 读的同一张表（Windows `SetEnvironmentVariableW`；类 Unix 目标为运行时持锁的进程级表，同 `std/os` 的 `setenv`），此后起的子进程带上（v0.39）；名字为空、含 `=` 或 NUL，或值含 NUL 时得 `Error`（`invalid argument: <名字>`）；空值即设为空串（不是删除）；经外部函数调用的 C 代码在类 Unix 目标上用 `getenv` 看不到改动 |
| `runProcess` | `(string, List<string>, string) int \| Error` | 启动子进程并等待结束：第一实参为程序路径，第二实参为参数（原样传递——Windows 按 MSVC 规则加引号，Linux `execve`），继承 stdin/stdout（父进程关着的子进程里也关着），此外本进程的文件、套接字、管道等一律不进子进程（v0.40：不论是否设了 CLOEXEC 或可继承标志，含经外部函数打开的），子进程的环境为本进程当前的环境（v0.39：类 Unix 目标取进程级环境表，含 `std/os` 的 `setenv`；Windows 为进程环境块）；第三实参非空时 stderr 重定向到该文件（新建或覆盖），空串表示继承。返回退出码（Windows 为进程退出码，含异常码；Linux 被信号终止得负的信号号）；程序不存在等启动失败得 `Error` |
| `tempDir` | `() string` | 临时目录（Windows `GetTempPathW`，去掉末尾分隔符、保留盘符根；类 Unix `$TMPDIR`，去掉末尾的 `/`、只剩根时保留（v0.35 补记 M3a；此前 Linux 原样返回），缺省 `/tmp`） |
| `removeFile` | `(string) void \| Error` | 删除文件；不存在或无权限得 `Error` |
| `selfExePath` | `() string` | 当前可执行文件的路径（Windows `GetModuleFileNameW`；Linux `/proc/self/exe`） |
| `monoNs` | `() int` | 单调时钟纳秒（起点任意，只用于计时；断言面禁用） |
| `stdinRead` | `(Bytes, int) int` | **工具链专用**（v0.27 增，下同；未带 `--toolchain` 以 3001 `X 仅限工具链构建（需 --toolchain）` 拒绝；用到它们的程序只能原生构建，字节码构建报错）：把本进程标准输入读进缓冲（至多 `len(buf)` 字节），返回读到的字节数；等满第二实参的毫秒数仍无数据得 0，到流尾得 −1；毫秒为负一直等、为 0 不等；缓冲长度为 0 时得 0。标准输入是控制台时按行读（就绪后等到整行）。缓冲是冻结的 `Bytes`（§6.22-8）时 trap 4022 |
| `stdoutWrite` | `(Bytes) int` | 把数据原样写到本进程标准输出（不补换行），写完才返回；返回字节数，读端已关闭得 −1（Linux、macOS 都不因 SIGPIPE 结束进程）。标准输出是控制台时同 `print` 按 UTF-16 写出 |
| `procSpawn` | `(string, List<string>, string, int) int \| Error` | 起子进程，返回句柄（正整数）：第一实参为程序路径（不经 shell、不按 PATH 查找），第二实参为参数（原样传递，规则同 `runProcess`），第三实参为工作目录（空串继承），第四实参为选项位——位 0 stdin 走管道（否则接空设备），位 1 建调试控制通道（其余位 trap 4010）。stdout、stderr 恒走管道；子进程只得到这三端与调试控制通道的两端，本进程其余描述符／句柄一律不继承（v0.40，同 `runProcess`）。子进程环境为本进程当前的环境（v0.39，同 `runProcess`）去掉 `TAIXU_DEBUG_CHANNEL`，建控制通道时换成本次的。启动失败得 `Error`：`no such file: <程序路径>`、`access denied: …`；程序路径为空、程序路径或参数含 NUL、不是可执行程序得 `invalid argument: <程序路径>`；工作目录无效或含 NUL 时报该目录；同时存在的子进程已满（附录 A）得 `io error: <程序路径>` |
| `procTryWrite` | `(int, int, Bytes) int` | 单次非阻塞写子进程 stdin（流 0）或控制通道（流 3）；实际已写字节、暂不可写／中断为 0，关闭或错误为 −1；每次最多 4096 字节。得 −1 时该流已被关闭，此后对它恒为 −1（同 `procWrite`）；Linux 写入后恢复文件状态标志或信号掩码失败也得 −1，这时可能已有字节写出，只能把该流当作已断开。句柄规则与 procWrite 相同；Windows 管道写端第一次成功切成非阻塞后保持非阻塞（`procWrite` 写之前切回阻塞），管道仍有未读数据、尚未切成非阻塞时得 0（未尝试写入） |
| `procWrite` | `(int, int, Bytes) int` | 把数据全部写入子进程的 stdin（流 0）或控制通道（流 3），写完才返回；返回字节数，对端已关闭或该流不存在得 −1（此后恒为 −1） |
| `procRead` | `(int, int, Bytes) int` | 不阻塞地读子进程的 stdout（流 1）、stderr（流 2）或控制通道（流 3）进缓冲；返回字节数，暂无数据得 0，流尾或该流不存在得 −1（此后恒为 −1）。缓冲是冻结的 `Bytes` 时 trap 4022 |
| `ioWait` | `(int, int, int) int` | 等第二实参（位集合）选中的来源之一可读或到流尾：位 0 本进程 stdin，位 1–3 句柄的 stdout、stderr、控制通道（只选位 0 时句柄可为 0；位集合超出 0..15 trap 4010）。返回就绪位的集合，等满第三实参的毫秒数得 0（负数一直等）；已读到流尾的流不计，选中的都已读完时立即得 0 |
| `procKill` | `(int) void` | 强制结束子进程（Windows 退出码 1；Linux SIGKILL，`procWait` 得 −9）；已退出时无动作 |
| `procWait` | `(int, int) int?` | 等子进程退出，返回退出码（同 `runProcess`：Linux 被信号终止为负的信号号）；Linux 子进程已被别处回收、退出状态不可知时得 −256（不是信号号，`procSpawnLimited` 的组内后代未确认清理）；等满毫秒数仍未退出得 `None`，负数一直等；可重复调用 |
| `procClose` | `(int) void` | 子进程仍在运行时先强制结束并回收，再关闭全部管道、释放句柄 |
| `currentDir` | `() string \| Error` | **工具链专用**（v0.28 增；未带 `--toolchain` 以 3001 拒绝，只能原生构建）：本进程当前工作目录的绝对路径（Windows `GetCurrentDirectoryW`，Linux `getcwd`）；不合法的 UTF-8 字节换成 U+FFFD。当前目录已被删除等失败得 `Error`：`no such file: .`、`access denied: .`、`out of memory: .`、`io error: .` |
| `fileSize` | `(string) int \| Error` | **工具链专用**（v0.29 增；未带 `--toolchain` 以 3001 拒绝，只能原生构建）：普通文件的字节数，只取元数据、不读内容；跟随符号链接（Linux `newfstatat`；Windows `CreateFileW` 以访问掩码 0 打开，`GetFileType` 判磁盘文件后 `GetFileInformationByHandle`）。失败得 `Error`，文案同 `readFile`：目录、设备等非普通文件与含 NUL 的路径得 `invalid argument: 路径`，不存在（含悬空链接）得 `no such file: 路径`，另有 `access denied`、`out of memory`、`io error` |
| `procSpawnLimited` | `(string, List<string>, string, int, int, int) int \| Error` | **工具链专用**（v0.32）：前四参同 procSpawn；内存字节数与 CPU 秒数均为正数，CPU 秒数≤922337203685，非法数值 trap 4010。Windows 创建挂起进程，成功设置并加入 Job 后才恢复：Job 总提交及逐进程提交上限、逐进程及 Job 累计**用户态** CPU 上限（`PerProcessUserTimeLimit`／`PerJobUserTimeLimit`，内核态时间不计）、最多 32 个活动进程、不允许脱离 Job、关闭 Job 终止其中全部进程；Linux 在 exec 前 `setpgid(0, 0)` 自成进程组，并设置继承的 RLIMIT_AS/RLIMIT_CPU（hard=soft；CPU 计用户态与内核态）与 RLIMIT_CORE=0——AS/CPU 是逐进程限制，每个后代各有一份，不是进程组总额；不限进程数、不设 PDEATHSIG（本进程异常退出时子进程不随之结束）；子进程不在终端的前台进程组，收不到终端发出的 Ctrl-C。macOS（v0.35 补记 M3a）：fork 后同 Linux 自成进程组并设 RLIMIT_CPU（超限 SIGXCPU，`procWait` 得 −24）与 RLIMIT_CORE=0；没有可用的地址空间限额，内存由全进程一个看守线程每 50 ms 按进程组累加物理足迹，超过即整组 SIGKILL（`procWait` 得 −9）——事后发现、峰值可短暂超出，不是分配失败；清理同 Linux；子进程环境另带回收 cookie `TAIXU_REAP_COOKIE`（v0.35 补记 M3b，宿主服务用来找回脱离进程树的后代）。设置失败返回 Error，绝不执行。procKill/procClose：Windows 终止整个 Job；Linux 在主进程尚未被回收时向它的进程组发 SIGKILL，procWait 回收已退出的主进程之前也先按组清理（以 WNOWAIT 保留主进程，避免 PID 复用误杀）；已用 `setsid`／`setpgid` 离开该进程组的后代不在清理范围内。**资源限额不是不可信代码隔离** |
| `readLocalFileBytes` | `(string, string, int) Bytes \| Error` | **工具链专用**（v0.32）：本地根绝对路径、根内相对路径、最大内容字节数。按已验证句柄读取普通文件，拒绝符号链接、重解析／offline／recall 对象、绝对或 .. 相对路径、NUL、ADS 和超限／读取中增长；不支持的系统机制失败关闭，不回退普通读取。零长度返回空 Bytes，二进制无需 UTF-8；最大内容须在 0..INT_MAX−1，非法数值得 Error invalid argument。Windows 只接受盘符映射到本地硬盘卷（`\Device\HarddiskVolumeN`）的根，网络映射盘与 SUBST 拒绝，直接锚定该卷并逐组件持有句柄；Linux 根以 openat2 NO_SYMLINKS/NO_MAGICLINKS 打开，根内相对路径加 BENEATH/NO_XDEV（不跨挂载点）。原语本身不检查文件系统类型（Linux 的根可以位于网络或 FUSE 挂载上），显式根及祖先的挂载类型由调用服务启动策略核验；硬链接不检测。这些原语自身不代替服务的根授权 |
| `localEntryKind` | `(string, string) int` | **工具链专用**（v0.32）：同一本地根／相对路径和句柄检查，0 拒绝／不存在／无法判定、1 普通文件、2 目录；不读取内容。路径字节数分别≤32760（还须满足宿主自身上限） |
| `localFileSize` | `(string, string) int \| Error` | **工具链专用**（v0.32）：同一本地根／相对路径和句柄检查，仅查询普通文件大小，不读取内容；目录或拒绝时 Error |
| `localListDir` | `(string, string) List<string> \| Error` | **工具链专用**（v0.35）：同一本地根／相对路径和句柄检查，列出目录项名（按字节排序，不含 `.`、`..`；含链接本身的名字但不跟随），相对路径为空时列根本身；不是目录、链接或重解析点、根外或无法核验时 Error。服务模式据此核对导入写法与磁盘是否一致（§6.1-4） |
| `nowMs` | `() int` | 墙钟毫秒（宿主面观测）；**确定性纪律：e2e/教程断言面禁用**（输出随宿主墙钟变化） |
| `sleepMs` | `(int) Future<void>` | 定时器面（v0.9 增）：建 pending future，到期由调度泵置 done——**deadline 升序、同刻按登记序**（确定性红线时间维延伸）；钟源＝单调钟（墙钟禁用）；`await sleepMs(0)`＝下一调度轮到期（确定边界）；负值＝即 err future（`"sleep ms < 0"` 确定性错误通道）；**断言面只用 0ms/不到期两确定形态**；AOT 随异步面结构化拒绝（BuiltinId 25） |
| `assert` | `(bool, string) void` | 失败即测试失败 / 程序 panic |
| `panic` | `(string) void` | 不可恢复，打印消息并退出非零 |
| `threadSpawn` | `(() => T) Thread<T>` | 在新线程里运行无参函数值，捕获深拷贝进新线程（v0.25，§6.22-1）；创建失败不 trap，join 时得错误值。BuiltinId 61 |
| `threadJoin` | `(Thread<T>) T \| Error` | 等线程结束并取回结果（深拷贝回调用线程）；第二次 join 得错误值。BuiltinId 62 |
| `sharedNew` | `(T) Shared<T>` | 对象图深拷贝进共享区（§6.22-7）；实参须可共享（3035；经函数值、接口值带入的 trap 4021）。BuiltinId 63 |
| `sharedGet` | `(Shared<T>) T` | 取出冻结的对象图；值类型的根交一份拷贝（§6.22-8、§6.22-9）。BuiltinId 64 |
| `atomicNew` | `(int) Atomic<int>` | 新的原子整数（§6.22-10）。BuiltinId 65 |
| `atomicLoad` | `(Atomic<int>) int` | 读（seqcst）。BuiltinId 66 |
| `atomicStore` | `(Atomic<int>, int) void` | 写（seqcst）。BuiltinId 67 |
| `atomicAdd` | `(Atomic<int>, int) int` | 加并返回旧值（seqcst）；溢出 trap 4004、单元不变。BuiltinId 68 |
| `atomicCas` | `(Atomic<int>, int, int) bool` | 当前值等于第二实参时换成第三实参，返回是否换成（seqcst）。BuiltinId 69 |

**交给系统的串含 NUL（v0.35 安全修正）**：把串交给操作系统作路径、程序路径或进程实参的内建（`readFile`、`readFileBytes`、`writeFile`、`writeFileBytes`、`writeExecutable`、`makeDir`、`removeFile`、`listDir`、`runProcess` 及上表注明的工具链内建），串内含 NUL 字节时一律得 `Error "invalid argument: <路径>"`（`runProcess` 报程序路径），`entryKind` 得 `0`——不截断后照常操作（系统在 NUL 处截断，看到的是另一个路径）。同 Go 的 EINVAL、Rust 的 `NulError`、Python 的 ValueError。

**工具链进程句柄（v0.27）**：`procSpawn` 返回的句柄只在创建它的线程里有效。`procClose` 之后的、从未有效的、属于别的线程的句柄，以及不对的流号，在 `procWrite`、`procTryWrite`、`procRead`、`ioWait`、`procKill`、`procWait`、`procClose` 里 trap 4010（`bad_type`，文案写明原因）。没有 `procClose` 的句柄在进程退出时由操作系统回收。管道两端都要排干：子进程写满 stdout 或 stderr 的管道后会阻塞，调用方应以 `ioWait` 同时等两路。

**标准库内部内建（v0.24 补）**：以 `__rt` 开头的内建（如 `__rtHandleAdd`、`__rtEnvGet`、`__rtArgAt`）是原生运行时给标准库用的内部接口，只有标准库模块（模块文件在标准库根之下）可以调用；其余模块调用报 3001（文案同未定义函数并注明原因），用户声明的同名函数照常遮蔽。它们不属于语言面，签名与编号随实现变化，不列入上表。

`test "名称" { ... }` 块由 `taixu test` 枚举运行。v0.21：原生 `taixu test` 每个 test 块在独立进程中运行（模块初始化每个 test 重跑），trap 只判该 test 失败。

### 6.13 位运算（v0.14 新增）

1. **运算符与类型面**：双目 `&`（位与）、`|`（位或）、`^`（位异或）、`<<`（左移）、`>>`（**算术**右移——符号扩展）、`>>>`（**逻辑**右移——高位补零，hash/加密用例，TS 形态）；一元 `~`（位非）。操作数**恒 int**（64 位二补码）——float/bool 结构化拒绝（3002，无隐式转换、无 JS 掩码）；结果恒 int。**v0.26**：定宽整数的位运算与移位见 §6.21-15、§6.21-16（结果取公共类型或左侧类型，移位计数须在 0..宽度−1）。
2. **移位计数规则**：右操作数（移位计数）须落 **0..63**——界外（含负数）即结构化 trap `shift_count`（RuntimeCode 4016），**非 JS 掩码**（`x << (n & 63)` 的隐藏控制流永不发生——无隐藏控制流纪律 §2-4）。
3. **语义分立**：`*` 溢出 trap 不变；环绕乘经内建 `imul` 显式选入（§7——**64 位环绕语义**，`Math.imul` 仅借形态、其 32 位语义不继承）。`&`/`|`/`^`/`~`/移位无溢出面。
4. **VM/AOT 面**：新 opcode band=40/bor=41/bxor=42/bnot=43/shl=44/shr=45/sar=46/imul=47（追加编码值＝旧产物不含）；AOT 双面发射（x86-64 基线指令内联直译＋交叉后端 riscv64/loongarch64 shim 慢路 `__tx_arith_slow`/`__tx_bnot`——语义单一事实源）；解释器/直译/慢路三路 trap 码与消息逐项同源。
5. **优先级（TS 同构）**：移位紧于比较、`&` > `^` > `|` 居比较与 `&&` 之间（§5 EBNF 链）——`1 << 2 < 5` ≡ `(1 << 2) < 5`、`2 + 3 << 1` ≡ `(2 + 3) << 1`。

### 6.15 Bytes 与编译期嵌入（v0.18 新增）

1. **表示**：`Bytes` 是 RC 堆对象，句柄语义；`Bytes` 自身的内建只增不减长度（无 pop/truncate）。`bytesXxx` 与 `listXxx` 语义一一对应，不得分叉。**v0.26 修订**（原文“将来引入定宽整数时，`List<u8>` 采用同一紧凑存储，`Bytes` 成为其别名”，后定为不做别名）：`Bytes` 与 `List<u8>` 是**两个类型**——`Bytes` 的接口照旧以 `int` 读写（`bytesGet` 得 `int?`，`bytesSet`/`bytesPush` 检查 0..255），`List<u8>` 的元素是 `u8`；运行期两者是同一种对象形态，可零拷贝互转（第 7 条）。做成真别名会让 `bytesGet` 改返回 `u8?`，现有代码里取出的字节相加就从 `int` 运算变成 `u8` 运算、超过 255 即 trap，故不采用。
2. **越界与取值**：`bytesGet` 越界得 `None`（同 v0.50 的 `listGet`；v0.51 的越界即 trap 未扩及 `bytesGet`）；`bytesSet` 越界 trap（越界码 4002，同 `listSet`）；写入值不在 0..255 trap；`bytesNew` 长度为负 trap；`bytesSlice` 区间越界 trap。
3. **字符串互转**：`bytesFromStr` 拷贝字符串的 UTF-8 字节；`bytesToStr` 严格校验 UTF-8（拒绝过长编码、代理区与 > U+10FFFF），非法得 `Error "bytesToStr: 不是合法的 UTF-8"`。string 保持“合法 UTF-8”不变量，不提供原始字节构造字符串的通道。
4. **`embedBytes("路径")`**：参数必须是字符串字面量（否则 3002）；路径相对**调用所在源文件**的目录解析，也可为绝对路径；编译期读取，内容进入产物只读数据；文件不存在、不可读或超过 64MB 即编译错误 **3102**，不生成产物。每次求值返回一个**新的** `Bytes`（由只读数据拷贝），因为 Bytes 可变。
5. **文件**：`readFileBytes(path)` 读二进制文件得 `Bytes | Error`；`writeFileBytes`／`writeExecutable` 的内容参数同时接受 `List<int>` 与 `Bytes`（`Bytes` 直接写出，无逐元素检查）。
6. **零扰动**：不用本版新增面的程序零扰动；`.txc` 格式版本与金哈希不变。
7. **与 `List<u8>` 互转（v0.26 新增）**：`bytesAsList(b: Bytes): List<u8>` 与 `listAsBytes(l: List<u8>): Bytes` 只改静态类型，返回**同一个对象**（引用计数加一）——不是拷贝，也不是共用一块缓冲的两个对象。两边都是句柄语义：经任一侧的写入、追加、扩容与 `listPop`/`listRemoveAt`（`List<u8>` 一侧可以缩短长度）对另一侧立即可见；冻结（§6.22-8）两侧一致；放掉一侧不影响另一侧。经 `Bytes` 一侧读写照旧按 `int`（`bytesGet` 得 `int?`，`bytesSet`/`bytesPush` 检查 0..255），经 `List<u8>` 一侧按 `u8`。需要独立的副本时用 `bytesSlice(b, 0, len(b))`，或 `listNew` 后逐个推入。只收 `Bytes` 的位置（`writeFileBytes` 的内容参数、外部函数的 `Bytes` 形参、`bytesXxx` 内建）传 `List<u8>` 时先经 `listAsBytes`；要按 `u8` 逐个迭代 `Bytes` 用 `for (x in bytesAsList(b))`（`Bytes` 本身不可 for-in）。实参类型不符报 3002：`bytesAsList` 只收 `Bytes`，`listAsBytes` 只收 `List<u8>`（`List<int>` 等须逐个转换）。`listAsBytes` 在运行期再核对实参确是紧凑存放的 `List<u8>`（§6.21-22），否则 trap 4010 `bad_type`——静态类型为 `List<u8>` 的列表总是紧凑的，正常代码不会触发。
8. **批量操作（v0.37 新增）**：区间写法同 `bytesSlice(b, from, count)`，目的在前（同 C、Go、Rust）。
   - `bytesCopy(dst, at, src, from, count)`：把 `src` 的 `[from, from+count)` 拷到 `dst` 的 `[at, at+count)`；`dst` 与 `src` 可为同一对象，区间重叠时结果如同先拷出再写入。任一位置或个数为负、区间超出长度 trap 4002——不截断（Go 的 `copy` 静默取较短者，会把越界藏起来）。
   - `bytesFill(b, at, count, value)`：区间内每个字节写 `value`；越界或 `value` 不在 0..255 trap 4002。
   - `bytesGetLE<T>(b, at): T?` 与 `bytesGetBE<T>(b, at): T?`：从 `at` 起按小端／大端读 `T` 宽度的字节；`at` 为负或 `at + 宽度` 超出长度得 `None`（同 `bytesGet`，解析不可信输入时 `catch` 给缺省值即可）。有符号类型按宽度符号扩展；`f32`、`float` 按 IEEE 754 位型。
   - `bytesSetLE<T>(b, at, v)` 与 `bytesSetBE<T>(b, at, v)`：按小端／大端写 `v` 的 `T` 宽度字节；越界 trap 4002。
   - `T` 为 `i8 i16 i32 u8 u16 u32 u64`、`int`、`f32`、`float`，须显式写出（端序与宽度都不设缺省：DataView 缺省大端、字面量缺省 `int` 写 8 字节都是常见错）；`isize`／`usize` 不收——宽度随目标指针宽度而变，二进制格式须用显式宽度。写入的值须正是 `T`（窄类型先 `as`，字面量按 `T` 取型），否则 3002；不写类型实参、类型实参不合规也是 3002。
   - 写入冻结的 `Bytes`（§6.22-8）trap 4022；这些操作都不改变长度，外部调用借用中的 `Bytes`（§6.8-16）照常可写。

### 6.16 defer（v0.19 新增）

1. **语法**：`defer` 后随一个块，或一条表达式语句/赋值（视为只含这一条语句的块）。`defer` 是保留关键字。
2. **块作用域、后进先出**：离开声明 `defer` 的那个块时执行；同一块内多个 `defer` 按声明的逆序执行。离开多层块时由内向外逐层执行。
3. **循环**：循环体是块，**循环体里的 `defer` 在每一轮结束时执行**，不是整个循环结束后才执行——走到循环体末尾、`continue`、`break` 都先执行本轮已登记的 `defer`；C 风格 `for` 中在 `step` 之前执行，`for-in` 在取下一个元素之前执行。写在循环外的 `defer` 在外层块结束时执行一次。只执行已经执行到的 `defer` 语句所登记的。
4. **何时执行**：走到块尾、`return`、`break`/`continue` 离开该块、`?` 传播离开、`catch (e)` 块内 `return`。**何时不执行**：`panic`、`assert` 失败与运行期 trap（进程以非零退出码结束，资源由操作系统回收）。
5. **求值时机**：`defer` 体在离开时才求值，读取变量当时的值。`return expr` 先求出 `expr`，再执行 `defer`，最后返回；`defer` 改局部变量不影响已求出的返回值，经 `Ref` 修改的对象对调用方可见。
6. **体内限制**（编译错误 3026）：不得 `return`；不得用 `break`/`continue` 跳出 `defer` 体；不得使用 `?`（可能失败的调用用 `catch` 就地处理）；不得 `await`（v0.21：取消清理不能再挂起）。`test` 块与 `async` 函数（v0.21，§6.16-8）中可用。
7. **与所有权**：`defer` 在函数帧释放之前运行，它引用的局部都还活着；对象的死亡时点与 `Weak` 观察结果不变。实现为编译期展开，零新 opcode。
8. **`async` 函数（v0.21 新增）**：正常返回与错误返回同普通函数。任务**挂起中**（`await` 或 `taskAwaitAny` 未决）被 `taskCancel` 取消时，从挂起点按后进先出执行该点已登记的 `defer`，再把任务置为已取消，观察者得 `err("task cancelled")`；已被唤醒、尚在就绪队里的任务被取消同样执行。执行这些 `defer` 时当前任务是被取消者，`taskCancelled()` 为真；`defer` 体内可以再取消别的挂起任务（它的 `defer` 随即执行）。未启动就被取消的任务尚未登记任何 `defer`，不执行。实现：挂起点后的取消落点（编译期展开，零新 opcode），内核以取消模式进入挂起函数。

### 6.17 结构化错误（v0.19 新增）

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

### 6.18 接口与泛型约束（v0.20 新增）

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

### 6.19 编译器自带 prelude：Map 与 Set（v0.20 新增）

1. **prelude**：编译器自带一个用太虚写的模块（仓库 `prelude/collections.tx`，构建编译器时嵌入，同 NRT 内核）。程序的任一模块源码出现它的标志名（`Map<`、`Set<`、`mapNew`、`setNew`、`Hashable`、`intHash` 等）时编译器载入它，它的导出名对每个模块都是预声明标识符；用户声明与显式导入优先（只补缺）。不用 prelude 的程序零扰动。
2. **`Hashable`**：`interface Hashable { hash(): int; }`。prelude 为 `int`、`string`、`bool`、`Bytes` 提供 `intHash`、`stringHash`、`boolHash`、`bytesHash`；`float` 不提供（NaN 与 ±0 不适合作键）。用户类型自己写 `pointHash(p: Point): int`。**v0.26**：prelude 为定宽整数提供 `i8Hash` `i16Hash` `i32Hash` `u8Hash` `u16Hash` `u32Hash` `u64Hash` `isizeHash` `usizeHash`（值转成 `int` 后同 `intHash`；`u64`/`usize` 先 `wrapTo<int>`），定宽整数因此实现 `Hashable`、可作 `Map`/`Set` 的键（方法语法 `x.hash()` 即 `u8Hash(x)` 等）；`f32` 不提供（同 `float`），`Map<f32, V>`、`Set<f32>` 报 3030，文案指出缺少 `f32Hash`。
3. **`Map<K, V>`**：`K` 须实现 `Hashable`（`mapNew` 等处检查，3030）；键的相等用 `==`，因此函数值、`Future`、接口值不能作键。操作：`mapNew(): Map<K, V>`（类型实参经期望类型推断，同 `listNew`）、`mapSet(m, k, v)`、`mapGet(m, k): V?`（缺键是正常情形，得 `None`）、`mapAt(m, k): V`（v0.51：缺键 trap，确知键存在或值本身可空时用）、`mapHas(m, k): bool`、`mapRemove(m, k): bool`（是否删除了）、`mapLen(m): int`、`mapKeys(m): List<K>`、`mapValues(m): List<V>`、`mapClear(m)`；均可用方法语法（`m.set(k, v)`、`m.get(k)`）。**值可空（v0.51）**：`Map<K, V?>` 可用；`mapGet` 用于它时缺键与空值都得 `None`、无法区分，报 3002 定向拒绝——先 `mapHas(m, k)` 再 `mapAt(m, k)`；`for (k, v in m)` 经 `mapAt` 取值。
4. **`Set<T>`**：`T` 须实现 `Hashable`。`setNew()`、`setAdd(s, x): bool`（是否新加入）、`setHas`、`setRemove(s, x): bool`、`setLen`、`setToList(s): List<T>`、`setClear`。
5. **迭代序＝插入序**：`for (k in m)` 迭代键，`for (k, v in m)` 迭代键值对，`for (x in s)` 迭代元素；覆盖已有键不改变位置，删除后再插入排在末尾。
6. **句柄语义**（同 `List`）：赋值与传参共享同一张表。键与值按值语义存入（struct 键、值存拷贝，§6.9-5）。
7. **实现**：插入序条目表＋开放寻址索引表；删除只标记，不存活条目过半时整表重建（被删条目的键与值在重建前仍被表持有）。单态化后无间接调用。
8. **与 stdlib**：`std/map_set` 的非泛型 `Map` 于 v0.53 退役——导入它报 3008 并提示改用 prelude 的 `Map<K, V>`／`Set<T>`；`objToMap`／`mapToObj` 移入 `std/json`，改用 `Map<string, Ref<Json>>`（登记 `std.map-set-retire`）。

### 6.20 生成器（v0.22 新增）

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
7. **生命周期与所有权**：生成器对象持有实参与挂起时的全部局部（RC 照常计数）。正常结束、被关闭、或最后一个引用释放（含未开始与挂起中）时释放它持有的值；**丢弃与关闭都不执行生成器体**（TS 对被丢弃的生成器同样不执行 `finally`）。`Gen` 与 `Future` 一样不可发送到其他线程（可发送判定）。
8. **诊断**：3032 统管生成器规则——元素类型、`yield` 的位置与作标识符、`?`、带值 `return`、直接调用 `taskAwaitAny`、`Gen` 的 `==`；`await` 沿用 3024，`defer` 沿用 3026，双变量 for-in 沿用 3022；运行期 4017 `gen_running`。
9. **原生实现**：生成器函数编译为两个函数——原 fn 表序上的“创建函数”（实参移入生成器对象后返回）与追加的体函数（只经 `gen_next` 进入）。新操作码 48 `gen_new`、49 `yield`、50 `gen_next`、51 `gen_close`；`Gen` 的 `.txc` 类型种类 19；运行期值 tag 18。挂起是单帧快照（与 §6.12-5 同为 stackless）：`yield` 把产出值与整块值槽移入生成器对象（所有权转移，不增减 RC）；`gen_next` 在取值方帧之上开窗（越界 trap 4003）、移入快照并从恢复点继续。SEB1 在程序用到生成器且无诊断时追加 `GEN1` 节（生成器的 fn 表序）。不含生成器的程序，程序自身的代码不变。
10. **性能**：每次取值在调用栈窗口与生成器对象之间整块移入、移出值槽（按槽数线性，不增减 RC），适合一般用途；极热路径上的逐元素生成器（例如逐 token 的大型词法器）仍宜手写循环。

### 6.21 定宽数值（v0.23 新增第一步，v0.26 增第二步）

第 1–12 条为第一步（v0.23，第 3、5、8–12 条随 v0.26 修订），第 13–25 条为第二步（v0.26）。

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
12. **字节码**：SEB1 类型池增标签 21（另载种类码）；新操作码 `cvt`＝52（立即数为种类：1..24 收窄检查、`wrapTo`、`u64` 与浮点互转、`f32` 舍入；25/26 为 `floatToBits`/`floatFromBits`，§7）。**v0.26**：定宽运算（含环绕运算）、`toStr(f32)`、窄元素列表的创建与读写、`bytesAsList`/`listAsBytes` 使用新的字节码编码（2a、2c1、2c1b、2c2）——只有用到它们的程序才会产生；只用 `int`/`float` 的程序字节码逐字节不变。
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

### 6.22 线程、发送与共享（v0.25 新增）

设计与对照（并发模型见 `v052-concurrency-model-v0.1-20261014.md`）。

1. **线程**：`threadSpawn(f: () => T): Thread<T>` 在新的系统线程里运行 `f`。`f` 是无参、非 `async`、非生成器的函数值——顶层函数名、箭头函数，或存在变量、字段、容器里的函数值；`T` 可为 `void`。`threadJoin(t: Thread<T>): T | Error` 等线程结束并取回结果；`f` 的结果是失败联合时，错误原样作为 `threadJoin` 的错误返回。同一个线程第二次 join、或线程创建失败（系统资源不足）时，`threadJoin` 得到错误值，不 trap。放掉 `Thread` 句柄不影响线程，它照常运行；**`main` 返回即结束进程，未 join 的线程随之终止，不执行它们的 `defer`**（v0.53，同 C、Rust、Go；此前进程等全部线程结束，登记 `lang.threads-end-with-main`）——要等就 `threadJoin`。`f` 返回 `T | Error` 时句柄类型归一为 `Thread<T>`（标注写 `Thread<T | Error>` 同样归一，v0.53）。直接写在实参位置的箭头是线程入口，同 `main` 可直接 `await`（§6.12-4）；传给 `threadSpawn` 的具名函数不是入口，要等待就声明成 async 再写 `threadSpawn(() => await work())`。
2. **隔离与发送**：每个线程有自己的对象图，跨线程只有发送与 `Shared`。`f` 的捕获在 `threadSpawn` 时、结果在线程结束时深拷贝跨线程（结构化克隆：图内的共享与环保持原样；之后双方修改各自的那一份，互不可见）。模块级 `let` 每线程一份：线程启动时按模块依赖序把全部模块初始化一遍，初始化里的副作用每个线程各发生一次，新线程里的模块变量从初值开始。
3. **可发送**（编译期，只看类型）：数值、`bool`、`string`、`Bytes`、`Error`、`List`、定长数组与切片、`Ref<T>`、struct、enum、可选与失败联合，在成分都可发送时可发送；`Shared<T>` 可发送（发送的是句柄）。不可发送：`Weak<T>`（只观察本线程的对象）、`Future<T>`（任务属于本线程的执行器）、`Gen<T>`、`Thread<T>`、平台句柄（`declare type`），以及 `Atomic<int>`（只能经 `Shared` 跨线程）。发送点——`threadSpawn` 的实参是箭头函数时它的捕获、线程函数的结果类型、`sharedNew` 的实参——的静态类型里出现不可发送的成分（含经 struct 字段、enum 变体字段、`List`／数组／切片元素、`Ref`、可选、失败联合间接含有）报 3035 `not_sendable`，文案指出成分与路径（如“捕获的 `cache` 的字段 `owner` 是 `Weak<Node>`”）。
4. **擦除类型**：函数值与接口值静态算作可发送；它们装着的捕获、具体类型在闭包成形、接口装入处按同一规则算好，发送时核对，不可发送即在发送点 trap 4021 `unsendable`（`threadSpawn` 的调用处，或线程结束时的结果发送）。是否 trap 只取决于发送的值，与线程调度无关。
5. **线程里的 trap**：任一线程 trap 即打印诊断并终止整个进程（退出码 3）；多个线程同时 trap 时只输出第一个。需要隔离失败时让线程函数返回 `T | Error`。
6. **执行器**：每个线程一个确定性执行器，`async`/`await` 与定时器只在本线程内调度，任务不跨线程迁移；线程函数返回后取消本线程未完成的任务（同入口返回，§6.12-4；v0.53 起，此前先跑完本线程的就绪任务）。
7. **`Shared<T>`**：`sharedNew(v: T): Shared<T>` 把 `v` 可达的对象图深拷贝进进程级的共享区，原对象不受影响、照常可写。实参须可共享——同可发送，另允许 `Atomic<int>`——否则报 3035；经函数值、接口值带入的不可共享成分在 `sharedNew` 处 trap 4021。`sharedGet(s: Shared<T>): T` 取出共享的对象图，读不加锁。`Shared` 句柄可以随捕获、结果发送，也可以嵌进别的 `sharedNew`；发送句柄不拷贝内容。
8. **深度不可变**：`sharedGet` 取出的对象图（以及从中取出的任何对象）是冻结的。字段赋值、下标赋值、`listPush`/`listPop`/`listSet`/`listInsert`/`listRemoveAt`、`bytesSet`/`bytesPush`/`bytesAppend`（`Map`、`Set` 的写随之）、把冻结的外部结构体以 `Ref` 传给外部函数、把冻结的 `Bytes`（含经 `listAsBytes` 得到的冻结对象）作外部函数或系统调用的实参，都 trap 4022 `frozen_write`，文案指出操作与对象种类。（v0.33 勘误：v0.26 曾注记“冻结的 `Bytes` 作外部函数实参时不检查”，实现自 5839304 起已在交给 C 之前检查；即使 C 接口只读，也须先用 `bytesSlice` 拷出一份再传。）字符串拼接照常产生新串。要修改就构造一份新的对象；可变的共享状态放进 `Atomic<int>`（以后的 `Mutex<T>`）。
9. **同一性与值类型**：同一线程里，`Shared` 句柄活着时 `sharedGet` 每次返回同一个根（`Ref` 根之间 `==` 为真）；不同线程各自得到自己的那一份（跨线程的同一性本来不可观察）。所有句柄放掉之后再收到同一个共享区，得到的是新的一份。根是值类型（struct 值、enum 值、定长数组）时，`sharedGet` 按值语义交一份拷贝：拷贝归调用方、可写，其中的句柄类成分（`Ref`、`List`、`Bytes` 等）仍指向冻结的对象。大数据零拷贝用 `Shared<Ref<T>>` 或 `Shared<List<…>>`：字符串、`Bytes` 与数值 `List`（v0.26 起含紧凑列表）的数据各线程共用一份。
10. **`Atomic<int>`**：`atomicNew(v: int): Atomic<int>`；`atomicLoad(a): int`、`atomicStore(a, v): void`、`atomicAdd(a, d): int`（返回旧值；溢出同 `+` trap 4004，单元不变）、`atomicCas(a, expect, new): bool`（当前值等于 `expect` 时换成 `new`，返回是否换成），内存序都是 seqcst。`Atomic` 不冻结（自带同步）；放进 `sharedNew` 的对象图后，各线程取得的 `Atomic` 指向同一个值单元——`sharedNew` 拷贝的是单元的当前值，原对象里的 `Atomic` 与共享区里的是两个单元。`Atomic<T>` 目前只有 `T = int`（其余报 3020）；内存序参数以后作为纯增量开放。
11. **比较与文本**：`Thread`、`Shared`、`Atomic` 都是句柄，不可 `==`（3002）、不可 `toStr`。
12. **平台**：Windows `CreateThread`；Linux 静态产物 `clone`（自管 8 MiB 栈，低端保护页）；Linux 动态产物（调用了 `from "库"` 的外部函数）`pthread_create`。线程栈 8 MiB。`ffiLastError` 与回调窗口每线程一份（§6.8-14、§6.8-15）。
13. **字节码**：内建 BuiltinId 61..69（§7）；TXC 类型种类 20 `Thread`、21 `Shared`、22 `Atomic`（追加值），只出现在原生产物里。

### 6.14 字符字面量（v0.15 新增）

1. **形态与值**：'x' ＝ int 码点字面量——值为该字符的码点（'a'＝97；v0.52 起引号内可为任一单个字符，'太'＝22826、'😀'＝128512，与字符串 for-in 产出同一单位）；取定宽类型时按码点的真值查范围（`const b: u8 = '太'` 报 3034）；词法定界＋转义面校验＋值解码，**解析期即解糖为 int 字面量节点**（raw 保留原文形态）——sema/codegen/AOT/VM 零新面，bytecode 零新 opcode（金哈希恒等由零扰动亲证）。
2. **转义集**：\n（10）/ \t（9）/ \r（13）/ \\（92）/ \'（39）/ \0（0）；\u{...} 为**评估位**（结构化拒绝，登记不实施——需要时按尾段追加先例随单落位）。
3. **结构化拒绝**（诊断码 1005 bad_char_lit，1xxx 词法段尾段追加）：空字面量（''）/多字符（'ab'、'太虚'）/未知转义（'\q'）/\u 评估位拒绝；未闭合复用 1002（与模板串同款）。v0.51 及以前多字节字符（'太'）同样拒绝，v0.52 放开为码点。
4. **byteAt 锚**：'a' 的动机面＝`byteAt(s, i) == 97` 魔数可读性——惯用形 `byteAt(s, i) == 'a'`（v0.51 起 byteAt 越界 trap、返回 int；可能越界时 `(byteTryAt(s, i) ?? 0) == 'a'`；此前 byteAt 返回 T?、越界 None）；存量 `== 97` 形魔数按判据式（仅字节比较面）重写小批随单。

## 8. 明确排除清单

**S1 排除（规划于 S2+，第一阶段内补齐）**：~~闭包与箭头函数~~（**v0.7 起以单一箭头形式入 S1，§6.11**——引用捕获、闭包相等、`function` 表达式形式仍排除）、用户泛型/编译期参数、~~`interface`~~（**v0.20 起入 S1，§6.18**）、~~可选链 `?.`~~（**v0.50 起入 S1，§6.3-9**）、~~空值合并 `??`~~（**v0.12 起入 S1，修订 v0.12 第 1 项**）、解构赋值（**v0.12 起声明位的 List 形入 S1，修订 v0.12 第 2 项；表达式位与剩余形态仍排除**）、~~字符串插值~~（**v0.6 起以模板串形态入 S1，§6.10-3**）、字符串索引（**语法**形态 `s[i]` 仍排除属 S2；以 `byteAt` 内建函数形态提供逐字节访问——自举层能力，见 §7）、switch 字面量模式与块臂（复杂 case 逻辑拆辅助函数）、切片操作内建（追加/子切片，stdlib；**v0.5 注记：动态序列需求由 `List<T>` 内建承载**）、**Map/Set 等集合类型（v0.20 注记：泛型 `Map<K, V>`/`Set<T>` 由编译器自带 prelude 提供，§6.19；2026-09-03 明确推迟；**v0.5 注记：以 stdlib `.tx` 形态交付于 List 内建之上（`mapNew/mapGet/...`／`setNew/setAdd/...` 为普通导出函数，迭代序＝插入序），仍不进语言面——语言面容器内建冻结为 List 六件**；**v0.6 注记：`for (k, v in m)` 的 Map 迭代经 `mapKeys/mapGet` 解糖，Map 本体仍为 stdlib 类型**）**、`Ref<T>` 到 `T` 形参的自动解引用（避免隐式深拷贝，需要时显式 `unref` 或设计函数接收 `Ref`）、~~定宽数值类型（随 FFI）~~（**v0.23 起第一步入 S1，§6.21**——定宽算术、位运算与移位、`f32` 运算与格式化、紧凑容器、`u64` 大字面量随 **v0.26 起第二步入 S1，§6.21-13 起**）、`Ptr<T>` 与 FFI 全集（O1；**v0.4 注记：§6.8 `declare function` 是宿主函数调用面的最小前奏，非 FFI 全集**——任意外部符号链接、指针类型、结构体封送仍随 O1 期设计；**v0.23 注记：FFI 入 S1（§6.8-7 起：系统库与系统调用、句柄、外部结构体、回调）；原始指针 `Ptr<T>` 与 `unsafe` 仍排除，留给裸机轨道单独设计**）、`dynamic`（O5）、`is` 类型判断（switch 已覆盖）、~~方法语法~~（**v0.6 起书写形态入 S1，§6.10-1**；**v0.20 起接口值的动态分派入 S1，§6.18**；类、继承、方法表、`this`、重载仍永久排除）。**v0.6 排除增补**：嵌套模板串（插值段内模板）、string 码点迭代（v0.9 评估）、for-in 自定义迭代器协议（可迭代面冻结于 §6.10-2）、`?.`/`??` 与糖形组合（v0.9）。**v0.7 排除增补**：引用捕获（by-ref）、被捕获名赋值、闭包相等、`function` 表达式、函数重载/方法表（§6.11-7）。**v0.8 排除增补**：真线程/抢占式并发（设计定稿、实现 V1.0+ 候选——协作式单线程面，并发定档）；`Future<Future<T>>` 嵌套；async 函数指针/函数类型的 async 变体（fn 类型无着色位——**着色跟随被调表达式**：直接调用 async 函数/async 箭头调用表达式即 spawn；经普通 fn 类型转发调用＝着色擦除、同步执行，体内 await 即结构化 trap「await 执行于无任务上下文」；类型同一性不比较着色位——async 值可赋给同签名 fn 类型＝宽化擦除）；TaskGroup/Pool（库层交付，不进语言核心）；`async` 属性/装饰器形态；select/超时通道语法（stdlib 演进面）。**v0.22 排除增补**：异步生成器 `async function*`（将来另设类型，不复用 `Gen`）；`yield*` 委托；`yield` 作表达式与向生成器传值 `next(v)`；生成器的 `throw()`/`return(v)`；箭头与函数表达式形式的生成器；通用迭代器协议（用户类型自定义可迭代——将来可定义为“有返回 `Gen<T>` 的 `iter` 方法即可迭代”）；生成器内的 `defer` 与失败传播、可失败的生成器（失败结束迭代）；`Gen` 的相等比较（§6.20）。**v0.23 排除增补**：~~定宽值的算术、位运算与移位、`toStr(f32)`、定宽 `Map`/`Set` 键、超过 i64 最大值的 `u64` 字面量~~（**v0.26 起放开，§6.21-13 起**）；任意位宽整数（`u3`/`u17`，名字已保留，随位布局 struct 另行设计）、`i128`/`u128`；~~FFI 的按值传递或返回结构体~~（**v0.45 起放开，§6.8-11、12**；回调签名里的按值结构体、寄存器类结构体因寄存器用尽改走栈的调用仍排除）、联合与位域、~~可变参数函数~~（**v0.36 起以变参参数入 S1，§6.8-21**；调用处任意个数与类型的变参仍排除）、指向指针的指针、闭包回调、懒装载（缺库时返回错误）、`string?`/`wstr?` 返回（空指针现得 `""`）、Linux 上的宽字符串、JS 宿主种类（§6.8-12、§6.8-17）。**v0.26 排除增补**：`float`/`f32` 的 `%`（浮点取余另议）；饱和运算、SIMD 向量类型、`f16`/`bf16`、`i128`/`u128`、位域与紧凑 struct 布局（定宽数值）。

**永久排除（语言承诺）**：自增自减 `++`／`--`（v0.49：写了即定向诊断，用 `+= 1`／`-= 1`，同 Swift 3、Rust 不设）、`==` 隐式类型转换、`var` 提升与重复声明、`this` 与原型链、异常作主控制流、switch fallthrough、运算符重载、隐式数值转换、None 与 Error 的隐式互转、显式解引用操作符与指针算术。

## 9. 示例

> **全景示例**：`docs/design/D3-s1-syntax-tour-v0.1.tx` 覆盖性演示 S1 全部语法特性，是本节的评审观察材料；D3 定稿后转为首个正向守护用例（v0.2 修订后须补 Weak 段落并同步更新 skill 副本）。以下为最小示例。

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
    const shown = await r catch -1;    // main 体内可直接 await：其间调度泵运行任务（§6.12-4）
    return 0;                          // 返回即取消仍未完成的任务
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
// examples/hello.tx —— PoC 目标
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
    const head = listGet(xs, 0);              // 在界得 10（v0.51 起越界 trap 4002）
    const oob = listTryGet(xs, 99) ?? -1;     // 可能越界用 listTryGet：越界得 None → -1
    const top = listPop(xs);                  // 尾弹出 20（所有权移交；空表 trap）
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
// 目标释放后 deref(w) 得 None——由 `--mem-report` 与守护用例验证
```

---

## 附录 A 实现限制（v0.24 新增）

上限表为原生编译器的上限（内部 TXC v6，64 位指令字）。超限一律结构化拒绝或运行期 trap，不得静默生成错误代码（守护门：`scripts/test-capacity-limits.py`；评估与依据）。上限只会放宽（放宽总是兼容的），收紧须改本附录并升版。

| 项 | 上限 | 超限 |
|---|---|---|
| 单函数寄存器（局部、临时与调用实参窗口） | 65535（编号 0–65534） | 编译期 3101 |
| 函数、函数类型、箭头函数的参数 | 65534 | 编译期 3011 |
| 外部函数（`declare function … from`）的参数 | 254 | 编译期 3011 |
| 单函数常量 | 2^32 | 编译期 3101 |
| 单函数跳转距离 | ±2^31 条指令 | 编译期 3100 |
| struct 字段、单个 enum 变体的载荷字段 | 32767 | 编译期 3020 |
| 模块顶层绑定（`let`/`const`） | 32768 | 编译期 3020 |
| 闭包捕获 | 32765 | 编译期 3020 |
| 接口方法 | 32766 | 编译期 3020 |
| enum 变体 | 65536 | 编译期 3020 |
| 每程序外部函数（`declare function … from`） | 61440 | 编译期 3101 |
| 泛型实例化嵌套 | 256 层 | 编译期 3020 |
| 单个泛型实例的类型参数规模（展开树节点） | 2^20 | 编译期 3020 |
| 值语义克隆、比较的嵌套深度 | 1024 | 运行期 4007 |
| 对象链释放深度 | 不限（超过 8192 层转为迭代释放） | — |
| 每线程值栈 | 16,776,960 槽（按需提交） | 运行期 4003 |
| 原生调用深度 | 本线程实际栈（主线程与工作线程 8 MiB；Linux 主线程按 `RLIMIT_STACK`，至多 64 MiB）减 256 KiB 余量，小帧递归约 8 万层 | 运行期 4003 |
| 引用计数 | 到顶即永生：不再计数、不再释放（只泄漏该对象，不越界） | — |
| `Shared` 共享区的引用计数（v0.25） | 64 位原子计数，越过 2^62 即饱和、永不归还（只泄漏该共享区） | — |
| 线程数 | 受系统资源限制；每个线程预留 8 MiB 机器栈与 256 MiB 地址空间（值栈与线程状态，按需提交） | 创建失败（含新线程建不起自己的区域，v0.34）时 `threadJoin` 得错误值 |
| 运行期内存（v0.34 写明） | 受进程可用内存限制 | 致命：stderr 为 `taixu: 内存不足`、退出码 3，含启动时取参数表；有明示错误值的操作（`readFile`、`readFileBytes`、线程创建）除外 |
| `readFile`、`readFileBytes` 单次读取 | 不设上限（受可用内存限制；分配失败得错误值） | 错误值 |
| `writeFile`、`writeFileBytes`、`print`、`eprint` 单次写出 | 不设上限（逐块写出） | — |
| `embedBytes` 单个文件 | 64 MiB | 编译期诊断 |
| 同时存在的工具链子进程（`procSpawn`，v0.27） | 64 个 | `procSpawn` 得错误值 |
| 定长数组 `[N]T` 的长度 | 1–65535 | 编译期 3020 |
| 切片元素数 | 受源码规模限制：S1 的切片只来自字面量与 `main` 的实参表（S2 加入运行期按长度构造切片的接口时放宽到 64 位） | — |

