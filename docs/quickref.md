# 太虚速查手册（quickref）

写码时随手查的单文件手册：语言面、内建函数、诊断码、命令行子命令与标准库
模块，对应 0.3.0-beta.1。与[入门教程](tutorial/README.md)配套——教程讲
“怎么用”，本手册管“查细节”；语言的权威定义见
语言规范。

诊断码一节与 `taixu explain` 的输出同源。若发现手册与工具实际行为不一致，
以工具为准，并欢迎反馈。

---

## 1. 语言面速查

### 类型

| 类 | 形态 | 备注 |
|---|---|---|
| 整数 | `int` | 64 位有符号；整数字面量的缺省类型 |
| 浮点 | `float` | f64 |
| 定宽数值 | `i8 i16 i32 u8 u16 u32 u64 isize usize f32` | `i64`/`f64` 即 `int`/`float`；无损方向自动扩宽，收窄用 `as`（越界 4018）；算术溢出 4004，要环绕写 `wrapAdd` 等 |
| 布尔 | `bool` | 条件严格 bool（`if`/`while`/`for` 不收其它类型） |
| 字符串 | `string` | 不可变 UTF-8；插值 `` `a=${x}` ``；for-in 逐字符（码点，v0.52）；`< <= > >=` 按字节字典序（v0.52） |
| 空 | `void` | 只作返回类型 |
| 结构体 | `struct` | **值语义**（赋值拷贝、按字段比较）；可带类型参数 `struct Pair<A, B>` |
| 枚举 | `enum` | 值语义；变体可带字段；构造 `E.V(...)`，模式 `case V(...)` |
| 错误 | `Error` | `{ message, cause? }`；`err("msg")` 构造 |
| 错误 enum | `enum E extends Error { V = "消息", … }` | 值同时是 `Error`；可作失败侧 `T \| E` |
| 可选 | `T?` | 缺席值 `None` |
| 失败联合 | `T \| Error`、`T \| E` | 可能故障的结果 |
| 堆引用 | `Ref<T>` | `new T { ... }` 构造；`.` 自动解引用；引用计数；按同一性比较 |
| 弱引用 | `Weak<T>` | `weak(r)` 构造；`deref(w)` 得 `T?`（值拷贝） |
| 动态序列 | `List<T>` | `listNew()` 构造；句柄共享；元素可为值类型（存取拷贝），不能是 `T?`／`T \| Error` |
| 映射与集合 | `Map<K, V>`、`Set<T>` | 编译器自带（不用 import）；键须可哈希；插入序迭代；句柄共享 |
| 数组与切片 | `[N]T`／`[]T` | 数组值拷贝、按内容比较；切片共享、按同一性比较；字面量 `[1, 2, 3]` |
| 字节 | `Bytes` | 可变字节序列，句柄共享；`==` 比句柄，内容比较用 `bytesEqual` |
| 函数 | `(T, …) => R` | 句柄共享；不可 `==` |
| 接口 | `interface I { m(): R; }` | 结构化实现（有约定名函数即实现）；接口值动态分派、不可 `==` |
| 异步 | `Future<T>` | async 函数调用的结果（声明 `T` 或 `T \| Error` 都得 `Future<T>`）；`await` 得 `T \| Error`；丢弃报 3005，发后即忘写 `detach` |
| 生成器 | `Gen<T>` | `function*` 调用的结果；`genNext(g)`／`g.next()` 得 `T?` |
| 线程 | `Thread<T>`、`Shared<T>`、`Atomic<int>` | `threadSpawn`／`sharedNew`／`atomicNew` 构造 |
| FFI | `declare type H;`、`declare struct S { … }` | 外部句柄与 C 布局结构体；结构体以 `Ref<S>` 拷入拷出，或按值传与返回（v0.45，按目标 C 调用约定）（语言规范 §6.8） |

### 控制流

```tx
if (cond) { ... } else { ... }        // cond 严格 bool
while (cond) { ... }
for (let i = 0; i < n; i += 1) { ... }   // 复合赋值 += -= *= /= %= &= |= ^=（v0.49）；没有 ++／--
for (x in xs) { ... }                 // List/string(字节)/[N]T/[]T/Map(键)/Set/Gen
for (k, v in m) { ... }               // 双变量只用于 Map
const s = n > 0 ? "正" : "非正";      // 三元（v0.50）：最低一层、右结合；条件以后缀 ? 结尾须加括号
const r = switch (v) {                // switch 是表达式
    case Variant(a, b) => expr,       // 模式＝enum 变体（字段可绑定）
    else => expr,                     // 对 enum 不写 else 须穷尽全部变体
};
defer cleanup();                      // 离开所在块时执行（后进先出）
```

### 失败通道（两通道独立，`?`/`catch` 统一作用）

| 通道 | 字面 | 传播 | 兜底 |
|---|---|---|---|
| 错误 | `T \| Error`（`err("msg")` 构造） | `expr?`（通道不符＝3006） | `catch (e) { ... }`／`catch 兜底值`／`??` |
| 可选 | `T?`（`None`） | `expr?` | 同上 |

跨通道须 `catch` 显式桥接：`opt catch err("缺失")`（`T?` → `T | Error`）、
`risky catch None`（`T | Error` → `T?`）。`a ?? b` ≡ `a catch b`。
`errWrap(e, "上下文")` 包一层（`cause` 为原错误），`errAs<E>(e)` 取回错误
enum 的种类。失败值不得作为语句丢弃（3005）。返回 `void | Error` 的函数 `return;` 或走到末尾即成功；
`catch (e)` 块在循环里可以 `continue`／`break` 离开（v0.49）。
可选链 `a?.b`（v0.50）：`a` 为 `None` 时整条链得 `None`（其后的调用不求值），结果 `U?`；只用于 `T?`，“传播后取字段”写 `(x?).f`。
struct 更新（v0.51）：`{ ...t, done: true }`，展开写在最前、须同一 struct 的值。对 struct 拷贝的字段写入之后再没读取（`let t = listGet(xs, i); t.f = v;` 不写回）报 3036。导入改名（v0.51）：`import { a as b } from "./m";`。
None 收窄（v0.50）：`const` 局部与参数在 `if (x != None)` 的分支、`&&` 右侧、`if (x == None) { return; }` 之后等处即为 `T`；`let` 与字段不收窄。

### 函数、闭包、泛型与异步

```tx
function f(x: int): int { return x; }        // 入口 main(args: []string): int（也可 void、int | Error）
const g = (x: int): int => x * 2;            // 箭头函数；参数与返回必须标注；捕获为值快照（只读）
function id<T>(x: T): T { return x; }        // 泛型（函数与 struct；单态化）
function keyOf<T extends Hashable>(x: T): int { return x.hash(); }   // 约束须为接口
async function work(): int { ... }           // 着色异步：调用即排队，得 Future<int>
const r = (await work()) catch 0;            // await 写在 async 体、main、test 块或 threadSpawn 实参箭头里；产 T | Error
detach work();                               // 发后即忘（裸写 work(); 报 3005）；main 返回即取消未完成的任务
function* gen(): int { yield 1; }            // 生成器
declare function f2(x: i32): i32 from "库名";  // 外部函数（或 from syscall "openat"：按目标架构查号表）
```

方法写法：`recv.name(args)` 依次找“类型名小写开头＋方法名首字母大写”的
函数（`xs.push(v)` → `listPush`，`p.offset(1)` → `pointOffset`），再找同名
的首参为接收者的函数；接收者为接口值时动态分派。

### 字面量与插值

```tx
1_000_000  0xff  0o17  0b1011  6.02e23       // 数字
'A'                                          // 字符字面量＝码点 int（65）；'太' 即 22826（v0.52）
"tab\t snow\u{2603}"                         // 转义 \n \t \r \\ \" \0 \u{..}
`total=${total}!`                            // ≡ "total=" + toStr(total) + "!"
const (a, b) = xs;                           // 解构声明：每个都是 T?
```

插值段的类型＝`toStr` 能接受的类型；不支持嵌套模板串；串内 `` \` ``/`\$`
转义。位运算 `& | ^ ~ << >> >>>`（`>>` 算术、`>>>` 逻辑右移；移位位数
0..63，越界 4016）。

---

## 2. 内建函数

普通程序可用的内建如下（另有一批只供构建工具链用的内建，需 `--toolchain`
才放行，普通程序调用报 3001，此处不列）。多数内建也能用方法写法调用。

### 输入输出与文件

| 内建 | 签名 | 说明 |
|---|---|---|
| `print` | `(string) void` | 标准输出一行 |
| `eprint` | `(string) void` | 标准错误输出一行（原样，不加前缀） |
| `readFile` | `(string) string \| Error` | 读文本文件（UTF-8） |
| `writeFile` | `(string, string) void \| Error` | 写文本文件（覆盖写） |
| `readFileBytes` | `(string) Bytes \| Error` | 读二进制文件 |
| `writeFileBytes` | `(string, List<int> \| Bytes) void \| Error` | 写二进制文件（元素须 0..255，校验先于写出） |
| `listDir` | `(string) List<string> \| Error` | 目录条目名（不含 `.` `..`，按字节序） |
| `entryKind` | `(string) int` | 0 不可判／1 文件／2 目录／3 符号链接（不跟随链接） |
| `makeDir` | `(string) void \| Error` | 建单层目录（已存在视为成功）；多层用 `std/dir` 的 `makeDirAll` |

### 值、字符串与数值

| 内建 | 签名 | 说明 |
|---|---|---|
| `err` | `(string) Error` | 构造错误 |
| `errWrap` | `(Error, string) Error` | 包一层上下文，`cause` 为原错误 |
| `errAs` | `errAs<E>(Error) E?` | 取回错误 enum 种类（类型实参必须写出） |
| `len` | `(string \| []T \| [N]T \| Bytes) int` | 长度（字符串、Bytes 为字节数） |
| `toStr` | `(int \| float \| bool \| string \| Error) string` | 转字符串；也收定宽数值与错误 enum；NaN 打印 `nan` |
| `unref` | `(Ref<T>) T` | 从堆引用整体取值拷贝 |
| `weak` | `(Ref<T>) Weak<T>` | 弱引用（不拥有目标） |
| `deref` | `(Weak<T>) T?` | 目标存活得值拷贝、已死得 `None` |
| `byteAt` | `(string, int) int` | 逐字节读（v0.51 起越界 4002） |
| `byteTryAt` | `(string, int) int?` | 逐字节读，越界得 `None`（v0.51） |
| `utf8Encode` | `(int) string \| Error` | 码点 → UTF-8 串 |
| `strSlice` | `(string, int, int) string` | `strSlice(s, from, to)`，字节区间 `[from, to)`；越界、`to < from` 或切在字符中间 4002（v0.52） |
| `strFind` | `(string, string) int` | 首现位置（字节下标）；缺席 `-1`；可直接交给 `strSlice`（v0.52） |
| `strCompare` | `(string, string) int` | 按字节字典序（即码点序）：-1／0／1（v0.52） |

字符串的整数位置与长度一律按字节：`len`、`byteAt`、`strSlice`、`strFind` 一套（v0.52）；要字符就迭代（`for (c in s)` 得码点），
按“字”数的少数场合用 `std/utf8` 的 `slice`／`find`／`count`。`strSub`／`strIndexOf`／`strByteSub`／`strByteIndexOf` 已移除（3001，诊断给改写）。
| `strSplit` | `(string, string) List<string>` | 分隔符切分（保留空片；空分隔符为运行期错误） |
| `imul` | `(int, int) int` | 64 位环绕乘 |
| `wrapTo` | `wrapTo<T>(整数) T` | 截断转换（取低位，不检查） |
| `wrapAdd`／`wrapSub`／`wrapMul` | `<T>(T, T) T` | 按整数类型 `T` 的宽度环绕加减乘 |
| `floatToBits`／`floatFromBits` | `(float) int`／`(int) float` | 双精度与 64 位模式互转 |

### List

| 内建 | 签名 | 说明 |
|---|---|---|
| `listNew` | `listNew<T>(): List<T>` | 空表（`T` 由期望类型推断，无期望即编译错误） |
| `listPush` | `(List<T>, T) void` | 尾追加 |
| `listPop` | `(List<T>) T` | 尾弹出（v0.51 起空表 4002） |
| `listTryPop` | `(List<T>) T?` | 尾弹出，空表得 `None`（v0.51） |
| `listGet` | `(List<T>, int) T` | 索引读（v0.51 起越界 4002） |
| `listTryGet` | `(List<T>, int) T?` | 索引读，越界得 `None`（v0.51） |
| `listSet` | `(List<T>, int, T) void` | 索引写（越界 4002） |
| `listLen` | `(List<T>) int` | 长度 |
| `listInsert` | `(List<T>, int, T) void` | 插入（`i == len` 即尾加；越界 4002） |
| `listRemoveAt` | `(List<T>, int) void` | 按索引删除（越界 4002） |

方法写法：`xs.push(v)`／`xs.get(i)`／`xs.tryGet(i)`／`xs.len()`／`xs.insert(i, v)`／`xs.removeAt(i)` 等。期望类型为 `List<T>` 时数组字面量 `[1, 2]` 构造 List（v0.51）；元素可为 `T?`（v0.51，`listTryGet`／`listTryPop` 用于它报 3002）。
遍历：`for (x in xs)`、`for (i, x in xs)`（下标, 元素；v0.51）、`for (i, c in s)`（字节偏移, 码点；v0.52）、`for (i in range(n))`／`range(a, b)`（计数循环、不分配，只用于 for-in；v0.51）。

### Map 与 Set（编译器自带，不用 import）

| 函数 | 签名 | 说明 |
|---|---|---|
| `mapNew` | `mapNew<K, V>(): Map<K, V>` | 由期望类型推断 |
| `mapSet`／`mapGet`／`mapHas`／`mapRemove` | `(m, k, v) void`／`(m, k) V?`／`(m, k) bool`／`(m, k) bool` | 覆盖已有键不改变位置 |
| `mapAt` | `(m, k) V` | 缺键 trap（v0.51）；值可空的 `Map<K, V?>` 用 `mapHas`＋`mapAt`（`mapGet` 报 3002） |
| `mapLen`／`mapKeys`／`mapValues`／`mapClear` | `int`／`List<K>`／`List<V>`／`void` | 键序＝插入序 |
| `setNew`／`setAdd`／`setHas`／`setRemove` | `Set<T>`／`bool`（是否新加入）／`bool`／`bool` | |
| `setLen`／`setToList`／`setClear` | `int`／`List<T>`／`void` | 插入序 |

键须实现 `Hashable`（`hash(): int`）：`int`、`string`、`bool`、`Bytes` 与定宽
整数自带；自定义类型写 `xxxHash(v: Xxx): int`；`float` 不能作键。

### Bytes

| 内建 | 签名 | 说明 |
|---|---|---|
| `bytesNew` | `(int) Bytes` | 长度 n 的全零 Bytes |
| `bytesLen`／`bytesGet`／`bytesSet`／`bytesPush` | `int`／`(b, i) int?`／`(b, i, v) void`／`(b, v) void` | 值须 0..255 |
| `bytesAppend`／`bytesSlice`／`bytesEqual` | `(a, b) void`／`(b, from, count) Bytes`／`(a, b) bool` | 切片为拷贝 |
| `bytesFromStr`／`bytesToStr` | `(string) Bytes`／`(Bytes) string \| Error` | 后者严格校验 UTF-8 |
| `bytesCopy`／`bytesFill` | `(dst, at, src, from, count)`／`(b, at, count, value)` | 区间拷贝（可重叠）／填充 |
| `bytesGetLE`／`bytesGetBE` | `<T>(Bytes, int) T?` | 按端序读定宽数值（`T` 须写出） |
| `bytesSetLE`／`bytesSetBE` | `<T>(Bytes, int, T) void` | 按端序写定宽数值 |
| `bytesAsList`／`listAsBytes` | `(Bytes) List<u8>`／`(List<u8>) Bytes` | 零拷贝互转（同一对象） |
| `embedBytes` | `(字符串字面量) Bytes` | 编译期嵌入文件（路径相对源文件；失败 3102） |

### 异步、生成器与时间

| 内建 | 签名 | 说明 |
|---|---|---|
| `sleepMs` | `(int) Future<void>` | 定时器；deadline 升序、同刻按登记序唤醒；负数得错误 |
| `taskCancel` | `(Future<T>) void` | 取消；await 它得 `err("task cancelled")` |
| `taskCancelled` | `() bool` | 当前任务是否被取消（协作检查点） |
| `taskAwaitAny` | `(List<Future<T>>) int \| Error` | 最先完成者的下标（插入序裁决） |
| `genNext` | `(Gen<T>) T?` | 生成器下一个值；取完得 `None` |
| `nowMs` | `() int` | 墙钟毫秒——**仅观测，测试断言勿用** |

### 线程

| 内建 | 签名 | 说明 |
|---|---|---|
| `threadSpawn`／`threadJoin` | `(() => T) Thread<T>`／`(Thread<T>) T \| Error` | 新线程运行无参函数值（捕获深拷贝）；每个线程只能 join 一次，第二次得错误 |
| `sharedNew`／`sharedGet` | `(T) Shared<T>`／`(Shared<T>) T` | 跨线程只读共享（取出的对象图冻结，写入 4022） |
| `atomicNew`／`atomicLoad`／`atomicStore`／`atomicAdd`／`atomicCas` | 原子整数 | seqcst；`atomicAdd` 返回旧值，`atomicCas` 返回是否换成 |

### 测试与 FFI

| 内建 | 签名 | 说明 |
|---|---|---|
| `assert` | `(bool, string) void` | 失败即该 test 失败（运行期 4009） |
| `panic` | `(string) void` | 不可恢复退出（运行期 4008） |
| `handleBits`／`handleFromBits` | `(H) int`／`handleFromBits<H>(整数) H` | 外部句柄与数值互转 |
| `ffiLastError` | `() int` | 最近一次库函数调用后的系统错误码 |
| `ffiMem` | `(int, int, bool) FfiMem \| Error` | 给外部地址定大小得外部内存区（创建时核验；只许声明了 from 外部函数的模块） |
| `ffiMemOf<S>` | `(int, bool) FfiMem \| Error` | 同上，大小取外部结构体 S 的布局大小 |
| `ffiMemRead` / `ffiMemReadInto` / `ffiMemWrite` | 见 D3 §7 | 按区的大小查界读写字节（越界 trap 4025） |
| `ffiMemGet<S>` / `ffiMemSet<S>` | `(FfiMem, int) S` / `(FfiMem, int, S) void` | 按外部结构体布局读写 |
| `ffiMemUtf16` / `ffiMemUtf8` | `(FfiMem, int) string` | 读串到 NUL 或区尾 |
| `ffiMemEnd` | `(FfiMem) void` | 提前结束区（区本在创建它的函数返回时结束） |

---

## 3. 诊断码（`taixu explain [码]` 同源，共 70 个）

编译期诊断形如 `文件:行:列: error 3006: 消息`，并提示
`处置详见: taixu explain 3006`；运行期错误形如
`文件:行:列: [runtime:div_by_zero] 消息`，退出码 3（4023 为 23）。

### 词法 1xxx

| 码 | 名 | 一句话 |
|---|---|---|
| 1001 | illegal_char | 源码含未定义的字符 |
| 1002 | unterminated_string | 字符串、模板串、字符字面量或块注释未闭合 |
| 1003 | bad_escape | 转义序列非法 |
| 1004 | bad_number | 数字字面量格式或范围非法 |
| 1005 | bad_char_lit | 字符字面量无效 |

### 语法 2xxx

| 码 | 名 | 一句话 |
|---|---|---|
| 2001 | unexpected_token | 当前位置出现语法不期待的记号 |
| 2002 | expected_token | 缺少语法要求的记号 |
| 2003 | bare_literal_stmt | 裸字面量作为语句首记号（与块语法歧义） |

### 检查与链接 30xx

| 码 | 名 | 一句话 |
|---|---|---|
| 3001 | undefined_symbol | 引用了未声明的符号（含不带 `--toolchain` 时调用工具链专用内建） |
| 3002 | type_mismatch | 类型不匹配 |
| 3003 | duplicate_symbol | 同名重复声明 |
| 3004 | const_assignment | 对 const 的深层字段赋值 |
| 3005 | discarded_failure | 失败值（`T?` / `T \| Error`）或 Future 作为语句被丢弃（发后即忘写 `detach`） |
| 3006 | channel_mismatch | ? 传播通道与函数返回类型不一致 |
| 3007 | not_exhaustive | 对 enum 的 switch 不穷尽 |
| 3008 | bad_import | 导入路径不存在、写法与磁盘不一致或导出符号缺失 |
| 3009 | cyclic_import | 模块循环导入 |
| 3010 | bad_main | 入口模块缺少 main、签名不对，或 main 被调用、取值、声明为 async |
| 3011 | arity_mismatch | 实参个数不符 |
| 3012 | missing_return | 非 void 函数存在未 return 的路径 |
| 3013 | bad_cast | as 转换不合法 |
| 3014 | condition_not_bool | 条件不是 bool |
| 3015 | unknown_field | 字段不存在或访问目标不是 struct、enum 或 Error |
| 3016 | bad_lvalue | 赋值目标不是名字起头的后缀链 |
| 3017 | struct_literal_arity | struct 字面量字段数不符 |
| 3018 | arm_type_mismatch | switch 各臂、三元两支类型合不成一个类型（v0.49 起先按期望类型、再按 T／Error、T／None 合成；三元 v0.50） |
| 3019 | variant_not_found | enum 变体不存在或字段不符 |
| 3020 | bad_type_expr | 类型表达式非法 |
| 3021 | immutable_binding | 对不可变绑定重新赋值 |
| 3022 | not_iterable | for-in 目标类型不可迭代 |
| 3023 | closure_capture_readonly | 闭包捕获的外部名字在闭包体内被赋值（捕获为值快照、只读） |
| 3024 | await_outside_async | await 写在 async 函数与箭头、main、test 块、threadSpawn 实参箭头之外（或 defer、生成器体内；嵌套的普通箭头不继承外层） |
| 3025 | bad_await | await 操作数须为 `Future<T>` |
| 3026 | bad_defer | defer 用法不合法 |
| 3027 | bad_error_enum | 错误 enum 的声明或构造不合法 |
| 3028 | bad_failure_union | 失败联合的组成或失败侧传播不合法 |
| 3029 | bad_err_as | errAs 的类型实参不合法 |
| 3030 | constraint_unmet | 类型不满足接口或泛型约束，或接口值用法不合法 |
| 3031 | constrained_template | 带约束的泛型模板体使用了约束之外的操作 |
| 3032 | bad_generator | 生成器规则不满足 |
| 3033 | bad_ffi | 外部函数（FFI）的声明、封送或目标不合法（含没有 `from` 的 declare function） |
| 3034 | fixed_width | 定宽数值用法不合法（字面量越界、无公共类型、无符号取负等） |
| 3035 | not_sendable | 值不能跨线程发送（含 Weak、Future、Gen、Thread、平台句柄等） |

### 代码生成 31xx

| 码 | 名 | 一句话 |
|---|---|---|
| 3100 | jump_offset_overflow | 单函数指令过多，跳转偏移超出编码范围 |
| 3101 | operand_overflow | 单函数寄存器、字段号、常量或外部函数过多，超出编码范围 |
| 3102 | embed_failed | embedBytes 读不到文件或文件超过 64MB |

### 运行期 4xxx

| 码 | 名 | 一句话 |
|---|---|---|
| 4001 | div_by_zero | 除数为零 |
| 4002 | out_of_bounds | 数组、切片或列表索引越界 |
| 4003 | stack_overflow | 调用栈或值槽栈深度超限 |
| 4004 | int_overflow | 整数算术溢出（int 与定宽整数） |
| 4005 | f2i_overflow | 浮点转整数越界或为 NaN |
| 4006 | bad_opcode | 未定义的操作码或损坏的产物 |
| 4007 | resource_depth | 值克隆或比较的嵌套深度超限 |
| 4008 | panicked | panic 主动触发 |
| 4009 | assert_failed | assert 断言失败 |
| 4010 | bad_type | 运行期类型不符，或内建的实参在运行期不合法 |
| 4011 | not_exhaustive | switch 跳转表未覆盖该变体 |
| 4012 | corrupt_code | 产物或运行期状态损坏（如引用计数下溢） |
| 4013 | extern | 宿主外部函数未注册（原生产物不提供宿主外部函数） |
| 4014 | sched_deadlock | 入口等待时调度泵没有进展：所等的 Future 永不完成 |
| 4015 | exit_pending | （已退役，v0.53 起不再发出；入口返回即取消未完成的任务） |
| 4016 | shift_count | 移位计数不在 0..宽度−1（int 为 0..63） |
| 4017 | gen_running | 生成器运行中再对它取值或关闭 |
| 4018 | narrow_range | as 收窄越界 |
| 4019 | ffi_arg | 外部函数的 string/wstr 实参含 NUL 字节 |
| 4020 | ffi_callback | 回调不在外部函数调用期间、或不在太虚运行时所在的线程上被调用 |
| 4021 | unsendable | 发送或共享的数据经函数值、接口值带入了不可发送的成分 |
| 4022 | frozen_write | 写入了冻结的对象（从 Shared 取出的对象图不可写） |
| 4023 | leak | `--leak-check`：退出时仍有对象存活（引用成环等），退出码 23 |
| 4024 | ffi_buffer_borrow | 外部函数借用 Bytes 期间，回调改变了它的长度 |
| 4025 | ffi_mem | 外部内存区越界、写只读区、区已结束、负大小或同时存活的区超过 64 个 |

---

## 4. CLI 子命令

| 命令 | 用途 |
|---|---|
| `taixu run [选项] <file.tx> [程序参数...]` | 编译到临时目录并运行，转发退出码；文件名之后的参数都交给程序，选项写在文件名之前 |
| `taixu build <file.tx> --out <目录>` | 生成可执行文件（产物名取入口文件名）；`--target=windows\|linux\|macos\|<三元组>` 交叉编译 |
| `taixu test <file.tx>` | 运行 test 块，每块一个进程；退出码 0 全过／2 有失败／1 编译失败 |
| `taixu fmt [--check\|--write] <文件\|目录>...` | 保守格式化；不带选项时单文件输出到 stdout |
| `taixu doc [--format=json] [--out-dir=<目录>] <文件\|目录>` | `///` 文档注释 → Markdown/JSON |
| `taixu explain [码]` | 诊断码解释与修复建议（无参列全表） |
| `taixu init <名> [--template=console\|library\|gui]` | 脚手架（也可 `taixu init <模板> <名>`）；console 只生成 `<名>/main.tx`，gui 另生成重放脚本与期望输出（要用外部 GUI 库） |
| `taixu bench [--rounds N] <file.tx>` | 编译一次、多轮运行计时（中位／最小／最大） |
| `taixu disasm [--target=…] <file.tx>` | 按函数和源码行显示生成的汇编（目前只有 x86_64） |
| `taixu profile compile <file.tx>` | 编译画像（`taixu profile compile --help` 看用法） |
| `taixu lsp [--stdio]` | 语言服务器 |
| `taixu dap` | 调试适配器（Debug Adapter Protocol，stdio） |
| `taixu mcp [--workspace <目录>] [--trust-workspace]` | MCP 服务器（stdio，一行一条 JSON-RPC）；运行类工具须 `--trust-workspace` |
| `taixu capabilities [--format=json]` | 工具链能力清单 |
| `taixu changes [--format=json]` | 工具链兼容变更登记 |
| `taixu --version`（或 `taixu version`） | 版本与本机目标 |

常用选项：

| 选项 | 适用 | 作用 |
|---|---|---|
| `--target=…` | build、disasm | `windows`、`linux`、`macos`（按本机架构）或 `x86_64-windows`、`x86_64-linux`、`x86_64-macos`、`aarch64-macos` |
| `--out <目录>` | build | 输出目录（必填；只自动创建最后一层） |
| `--leak-check` | build、run、test | 退出时报告仍存活的对象（引用成环等），退出码 23 |
| `--heap-check` | build、run、test | 链接检测内核，查越界、释放后使用、重复释放（很慢，只供调试） |
| `--debug` | build、run | build：插桩调试构建；run：stdin/stdout 上的 NDJSON 调试会话 |
| `--strip` | build | Linux、macOS 产物不带函数符号 |
| `--static` | build | Linux 产物必须静态（调用了 `from "库"` 的外部函数则报 3033） |
| `--subsystem=windows` | build | Windows GUI 子系统产物（启动不带控制台；只用于 Windows 目标，缺省 `console`） |
| `--lib` | build | 入口不要求 main，只检查库模块能否编成原生代码（产物不是库） |
| `--format=json` | run、test、fmt、doc、bench、disasm、capabilities、changes | 机读输出 |
| `--toolchain` | run、build、test 等 | 放行工具链专用内建（构建编译器自身与工具脚本用） |

完整用法以 `taixu --help` 为准。

---

## 5. 标准库模块（`std/` 前缀导入；随包零配置）

| 模块 | 关键导出 |
|---|---|
| `std/path` | pathJoin／pathBase／pathDir／pathExt／pathNormalize |
| `std/string` | contains／indexOf／split／join／replace／trim／trimStart／trimEnd／toLower／toUpper |
| `std/strconv` | parseInt／parseFloat（`T \| Error`）／formatInt／formatFloat／formatFloatF |
| `std/fmt` | format（`{}`、`{:d} {:f} {:x}`、对齐与精度——printf 子集；宽度按显示列宽）＋ d／s／f／x／spaces |
| `std/text` | repeat／startsWith／endsWith／padStart／padEnd／truncate（按显示列宽，汉字 2 列）／displayWidth／codepointWidth／padStartBytes／padEndBytes／truncateBytes（按字节）／indent／lineCount／wordCount |
| `std/utf8` | count／seqLen／decode／encode／codepoints／fromCodepoints／byteLenOf |
| `std/bytes` | newBuilder／writeString／writeInt／writeByte／writeLn／builderLen／build／reset＋hexEncode／hexDecode |
| `std/base64` | b64Encode／b64Decode／b64EncodeUrl／b64DecodeUrl |
| `std/csv` | parseRows／serializeRows |
| `std/json` | Json（enum）／jsonParse（`Ref<Json> \| Error`）／jsonStringify／objToMap、mapToObj（与 `Map<string, Ref<Json>>` 互转） |
| `std/hash` | sha256Hex／sha256Bytes |
| `std/regexp` | matches／findGroups |
| `std/compress` | inflate／zlibDecompress／gzipDecompress／adler32／crc32＋deflateStore 等（只写不压缩的 stored 块） |
| `std/image` | decodePng／decodePngHex（PNG 解码为 RGBA8） |
| `std/math` | sin／cos／tan／asin／acos／atan／atan2／sqrt／cbrt／pow／exp／log／log2／log10／floor／ceil／round／trunc／abs／fmin／fmax／hypot＋PI()／E() |
| `std/sort` | sortInt／sortFloat／sortString（稳定） |
| `std/collections` | range／reverseInt／takeInt／dropInt／mapInt／filterInt／foldInt／sumInt／maxInt |
| `std/collect` | 泛型 map／filter／fold／take／drop／reverse／containsWith／sortBy／binarySearch／heapPush／heapPop |
| `std/fs` | fsReadAll／fsWriteAll／fsReadLines／fsExists／fsMkdir／fsMkdirs／fsRemove／fsRename／statSize／statIsDir／readDir／cwd |
| `std/file` | fileOpen／fileRead／fileWrite／fileSeek／fileClose（流式读写，数据为 Bytes） |
| `std/dir` | makeDirAll（递归建目录） |
| `std/os` | getenv／getenvOr／setenv／args／argsLen／argAt |
| `std/net` | tcpConnect／tcpListen／tcpAccept／tcpSend／tcpRecv／tcpClose＋parseUrl／httpGet／httpPost 等（最小 HTTP） |
| `std/time` | timeNowMs／formatMs |
| `std/datetime` | formatEpochMs／dateOf／timeOf／dayOfWeekEpoch／isLeap／daysInMonth |
| `std/random` | randomNew（种子）／randomSeed／randomNext／randomRange／randomFloat（同种子同序列） |
| `std/task` | taskGroupNew／taskGroupAdd／taskGroupDrain／taskGroupFailed／taskGroupCancel／timeout（对结果类型泛型：`taskGroupNew<int>()`、`timeout<T>`） |
| `std/pool` | poolNew／poolDefault／poolSubmit／poolDrain／poolFailed／poolLanes（分道执行池；对结果类型泛型：`poolNew<int>(2)`） |
| `std/events` | EventRecord／listenersNew／on／off／dispatch／listenersCount |

失败一律经 `T | Error` 返回。完整签名以 `taixu doc <模块文件>` 的输出或
标准库源码为准。

GUI 外部库 taixu-gui 不随包：取库、`taixu init gui`、真窗与重放测试见[教程第 10 章](tutorial/10-gui.md)。
