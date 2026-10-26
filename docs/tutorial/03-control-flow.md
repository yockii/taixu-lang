# 03 控制流

**本章你将学到**：if/while/for/for-in；switch 表达式的穷尽与模式解构。
（样例 [samples/03-control.tx](samples/03-control.tx)）

## 条件严格 bool

`if (n)` 非法（编译错误 3014）——没有 truthiness；显式比较：`if (n != 0)`。

## while / for / for-in

```tx
while (i <= 10) { ... }                 // 经典循环
for (let j = 0; j < 3; j = j + 1) { }   // C 风格；init 的类型可推断
for (v in xs) { ... }                   // for-in
```

for-in 能遍历：`List`（下标序）、`string`（逐**字节**，得 `int`）、数组
`[N]T` 与切片 `[]T`、`Map`（单变量得键，`for (k, v in m)` 得键值对，插入序）、
`Set`（元素，插入序）、生成器 `Gen<T>`（12 章）。双变量只用于 `Map`。
`in` 是上下文关键字，其余场合是普通标识符。break/continue 与 TS 同义：

```tx
let sum = 0;
for (let i = 0; i < 10; i = i + 1) {
    if (i == 3) { continue; }   // 跳过 3
    if (i == 6) { break; }      // 到 6 收工
    sum = sum + i;
}                               // sum = 0+1+2+4+5 = 12
```

## switch 表达式

臂是**表达式**（复杂逻辑拆辅助函数）；case 模式＝enum 变体（字段可绑定），
对 enum 必须穷尽全部变体（或写 `else =>` 兜底臂）。无穿透——没有 break
遗忘问题。

实跑输出（03 样例）：

```text
even-sum:30
for:0
for:1
for:2
got:7
got:8
```

> ⚠️ 类 TS 非 TS：比较不可连写（`a < b < c` 编译错误——写
> `a < b && b < c`）；无逗号表达式；`&&`/`||` 短路与 TS 一致。
