# 02 值与类型

**本章你将学到**：内建类型；struct/enum 的**值语义**；`const` 与对象字段
的可变性边界。（样例 [samples/02-values.tx](samples/02-values.tx)）

## 内建类型

`int`（64 位有符号，默认）/`float`（f64）/`bool`/`string`（不可变，UTF-8
字节串）/`void`（只作返回类型）。数值无隐式转换——`int` 变量与 `float`
变量混算须显式 `as`：

```tx
const ratio: float = 3 as float / 2.0;   // 1.5
```

数字字面量支持 `_` 分隔符（`1_000_000`）、`0x`/`0o`/`0b` 进制与浮点指数
（`6.02e23`）——全集见第 12 章。另有定宽数值 `i8 i16 i32 u8 u16 u32 u64
isize usize f32`（写 FFI、二进制格式时用；`i64`/`f64` 即 `int`/`float`），
日常计数、下标、累加一律用 `int`。

## struct：赋值即整体拷贝

```tx
let p: Point = { x: 1, y: 2 };   // 裸对象字面量（期望类型已知，TS 形式）
const q: Point = p;              // 值语义：整体拷贝
p.x = 100;                       // 只改 p，q 不受影响
```

没有期望类型时写类型名前缀：`Point { x: 1, y: 2 }`。

## enum：判别联合（TS union 的语义等价物）

变体可带字段；构造用限定名（`Shape.Rect(3, 4)`），模式用裸名；
`switch` 是**表达式**且必须穷尽（无 `else` 时漏变体即编译错误）：

```tx
const area = switch (s) {
    case Circle(r) => r * r * 3,
    case Rect(w, h) => w * h,
    case Dot => 0,
};
```

## const 的真实含义

- `const` 绑定的 struct 值**深层不可变**：`const p: Point = …; p.x = 2;`
  是编译错误 3004；要改就用 `let`，或先拷贝一份；
- `Ref<T>` 是唯一例外（05 章）：`const` 只约束绑定不可重绑，对象字段依然
  可变——同 TS `const object` 心智。

实跑输出（02 样例）：

```text
ratio=1.5 ok=true
q.x 不受影响: 1
area=12
```

> ⚠️ 类 TS 非 TS：`==` 无隐式转换（`1 == "1"` 编译错误 3002）；条件必须
> `bool`（`if (n)` 是编译错误 3014）。太虚刻意取 TS 的朴子集——TS 有而太虚
> 没有的写法几乎都是有意的取舍，这两类误写的诊断会直接说明原因。
