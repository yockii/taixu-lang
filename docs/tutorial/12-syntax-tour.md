# 12 关键字与语法总览

**本章你将学到**：全部关键字一张表；容易漏的字面量写法（进制、分隔符、
转义、字符、插值、注释）；全部语法糖；前面各章没展开的进阶语法；以及哪些
“习惯写法”太虚有意没有——遇到时别再找。（样例 [samples/12-syntax.tx](samples/12-syntax.tx)）

## 关键字全表（24 个）

| 关键字 | 用法 | 详见 |
|---|---|---|
| `function` | 声明函数（`function*` 为生成器） | 01、本章 |
| `let` / `const` | 可变 / 不可变绑定；类型标注可省略（可静态推断时） | 02 |
| `if` / `else` | 条件分支（条件严格 bool） | 03 |
| `while` / `for` | 循环 | 03 |
| `break` / `continue` | 跳出循环 / 跳过本轮 | 03 |
| `return` | 返回；`main` 的返回值即进程退出码 | 01 |
| `switch` / `case` / `else` | switch 表达式：模式＝enum 变体（字段可绑定）＋ `else` 兜底臂 | 02、03 |
| `struct` / `enum` | 值语义结构体 / 判别联合 | 02 |
| `interface` | 接口：只含方法签名，结构化实现 | 本章 |
| `new` | 堆分配，得 `Ref<T>` | 05 |
| `true` / `false` | 布尔字面量 | 02 |
| `as` | 数值显式转换（int↔float、定宽数值之间） | 02 |
| `catch` | 失败兜底（默认值／绑定块／降级三形态） | 04 |
| `defer` | 离开所在块时执行 | 本章 |
| `import` / `export` | 模块导入 / 符号对外导出 | 06 |
| `test` | test 块（`taixu test` 枚举运行） | 09 |

## 上下文关键字与预声明名字

这几个不是保留字，按位置起作用：

- `in`——只用于 for-in，其余场合是普通标识符；
- `async` / `await`——异步函数声明与等待（08 章）；
- `extends`——`enum E extends Error`（错误 enum）与泛型约束 `T extends I`；
- `yield`——只在生成器（`function*`）体内是关键字；
- `declare`——声明外部函数与外部类型：`declare function f(…): T from "库";`
  （或 `from syscall 编号`）。必须写 `from`，没有 `from` 的写法是编译错误
  3033；规则见语言规范 §6.8。

`None`（`T?` 的缺席值）、`Error`、`Ref`、`List`、`Map` 以及 `print` 等内建
函数是**预声明名字**：不是关键字，可以被你自己的声明遮蔽（不建议）。

## 字面量与注释

- 数字：十进制可用 `_` 分隔（`1_000_000`）；进制 `0xff`／`0o17`／`0b1011`；
  浮点 `3.14` 与指数 `6.02e23`；
- 字符：单引号 `'A'` 是该字符的码点（`int`，65）；转义 `\n` `\t` `\r` `\\`
  `\'` `\0`；只收单字节字符；
- 字符串：双引号，源码是 UTF-8，中文直接写；转义 `\n` `\t` `\r` `\\` `\"`
  `\0`、`\u{码点}`（如 `\u{2603}`＝☃）；
- 插值：反引号串 `` `hi=${name}` `` 等价于 `"hi=" + toStr(name)`；段的类型
  就是 `toStr` 能接受的类型（数值、bool、string、Error）；不支持嵌套，
  插值串里的反引号与 `$` 用 `` \` `` 与 `\$` 转义；
- 注释：`//` 行注释与 `/* */` 块注释（块注释不嵌套）；`///` 是文档注释
  （`taixu doc` 收集）。

运算符除算术、比较、逻辑外还有位运算 `& | ^ ~ << >> >>>`（操作数为整数；
`>>` 算术右移、`>>>` 逻辑右移，移位位数须在 0..63）。

## 语法糖（全部是等价改写，不用也行）

| 糖 | 等价于 |
|---|---|
| `` `total=${x}!` `` | `"total=" + toStr(x) + "!"` |
| `xs.push(v)`／`xs.get(i)`／`xs.len()` 等方法写法 | `listPush(xs, v)`／`listGet(xs, i)`／`listLen(xs)` |
| `a ?? b` | `a catch b`（`T?` 与 `T \| Error` 都适用） |
| `const (a, b) = xs;` | 依次取 `listGet(xs, 0)`、`listGet(xs, 1)`，每个都是 `T?` |
| `for (x in xs)` | 遍历 List/字符串/数组/切片/Map/Set/生成器 |
| 裸对象字面量 `{ x: 1 }` | 期望类型已知时省掉类型名 |
| switch 表达式直接出值 | 带值的分支（每臂一个表达式） |

**自定义“方法”**：按约定命名的函数可以通过方法写法调用——首参为接收者、
名字取“类型名（小写开头）＋方法名（首字母大写）”，如 `pointOffset(p, dx)`
即可写成 `p.offset(dx)`；编译器自带的 Map 即此约定（`mapGet(m, k)` 可写
`m.get(k)`）。只是调用的另一种写法，没有类、没有 `this`。

## 进阶语法（本书不单列成章）

样例后半段把下面几项各演示一遍，细节见语言规范：

```tx
function firstOr<T>(xs: List<T>, fallback: T): T {   // 泛型函数（struct 也可带类型参数）
    return xs.get(0) ?? fallback;
}

interface HasArea {                  // 接口：不写 implements——
    area(): int;                     // 有约定名函数 squareArea(Square): int
}                                    // 即实现；接口值动态分派

enum ParseError extends Error {      // 错误 enum：每个变体带消息，作失败侧
    Empty = "输入为空",
    BadDigit(at: int) = `第 ${at} 个字节不是数字`,
}

function* countdown(n: int): int {   // 生成器：yield 按需产出，for-in 可遍历
    let i = n;
    while (i > 0) {
        yield i;
        i = i - 1;
    }
}

function withDefer(): void {
    defer print("defer: 最后执行");  // 离开所在块时执行，多个时后进先出
    print("defer: 先执行函数体");
}
```

另有几块能力本教程不展开：线程与共享（`threadSpawn`/`sharedNew`/
`Atomic<int>`）、可变字节序列 `Bytes` 与编译期嵌入 `embedBytes`、定宽数值的
运算规则、外部函数（FFI）。内建函数一览见[速查手册](../quickref.md)，规则见
[语言规范](../design/D3-s1-language-spec-v0.42.md)。

## 有意没有的写法

这些是刻意排除，遇到别找，编译器会报错而不是猜你的意思：

- 没有 `+=` `-=` `++` 等复合赋值与自增——写 `n = n + 1`；
- 没有 `? :` 三目——单行用 `catch`／`??` 兜底，多行用 if/else 或 switch 表达式；
- 没有 truthiness——`if (n)` 非法，写 `if (n != 0)`；
- `==` 无隐式转换——`1 == "1"` 是编译错误；
- 没有可选链 `?.`、没有字符串下标 `s[i]`（逐字节读用 `byteAt(s, i)`，按码点
  取子串用 `strSub`）；
- 没有 `class`/`this`/继承/重载、没有 `throw`/`try`——数据用 struct/enum，
  行为用函数（加方法写法），错误走失败通道；
- switch 模式没有字面量与通配绑定——对枚举用变体模式，其余判断用
  `if`/`else`（`else =>` 兜底臂始终可用）。

实跑输出（12 样例）：

```text
million=1000000 mask=255
tiny>0=true
hi=太虚 mask=255
quote:" snow:☃
n=42 flag=true
sum=12
v=2 mod=2
first=10 len=2
q=101,2
kind=other area=12/0
on/off
char=65 bits=8,16
key=lang value=tx
firstOr=7,a
square=9
ok 42 / ParseError.BadDigit: 第 1 个字节不是数字
countdown=321
defer: 先执行函数体
defer: 最后执行
```
