# 04 失败通道

**本章你将学到**：`T | Error` 与 `T?` 两条失败通道；`?`/`catch`/`??` 的全部
形态；通道间桥接。（样例 [samples/04-failure.tx](samples/04-failure.tx)）

## 两条通道

- **`T | Error`**：操作可能故障（IO、解析、除零）。`err("msg")` 构造；
  `e.message` 取消息（另有只读的 `e.cause`：`errWrap(e, "上下文")` 包一层
  时记下原错误）。
- **`T?`**：预期内的缺失（查无此键、文件尾）。`None` 是唯一缺席值
  （没有 null/undefined 二元）。

失败值不能悄悄丢掉：`readFile(p);` 这样把 `T | Error` 当语句扔掉是编译错误
3005——必须 `?` 传播、`catch` 处理、绑定或传参。

## `?` 传播

`expr?` — 失败则立即从**本函数**返回该失败值（替代 TS 的 try/throw）；
要求本函数返回类型就是对应失败通道：

```tx
function timesTen(a: int, b: int): int | Error {
    const q = div(a, b)?;   // div 失败 → timesTen 失败
    return q * 10;
}
```

## catch 三形态与 `??`

```tx
const a = div(9, 3) catch -1;            // ① 默认值：失败取默认
div(1, 0) catch (e) {                    // ② 绑定块：e.message 诊断；
    print("blocked:" + e.message);       //    成功侧非 void 时块须发散
    return -1;                           //    （return/panic）
};
const m: int? = div(4, 2) catch None;    // ③ 降级：T | Error → T?
const c = firstChar("") ?? -1;           // a ?? b 即 a catch b（None 与 Error 都合并）
```

## 桥接（跨通道必须显式）

`x catch err(...)` 把 `T?` **升级**为 `T | Error`；`x catch None` 把
`T | Error` **降级**为 `T?`。混写（如 `?` 从 `T?` 通道传播进返回
`T | Error` 的函数）是编译错误 3006——编译器逼你显式选择。

除了通用的 `Error`，失败侧也可以是自定义的**错误 enum**
（`enum ParseError extends Error { … }`，每个变体带消息），写法见 12 章。

实跑输出（04 样例）：

```text
a=3
blocked:division by zero
demo:-1
strict=116
timesTen=30
empty=-1
```
