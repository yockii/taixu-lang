# 07 闭包与事件

**本章你将学到**：箭头函数与函数类型；捕获语义；fn 类型字段直调与事件订阅。
（样例 [samples/07-closures.tx](samples/07-closures.tx)）

## 唯一箭头形式

参数与返回**必须显式**（`(x) => x` 是编译错误）：

```tx
function makeAdder(n: int): (int) => int {
    return (x: int): int => x + n;
}
const add10 = makeAdder(10);   // add10(5) == 15
```

函数类型写作 `(参数类型, …) => 返回类型`（参数位只写类型不写名）。

## 捕获＝创建时值快照

值类型（int/float/bool/struct/enum/定长数组）在创建闭包时拷贝进去；句柄
（`Ref`、`string`、切片、`List`、函数）复制句柄、共享对象。**闭包体内被
捕获名只读**——赋值是编译错误 3023；要可变状态就捕获 `Ref`：

```tx
const c: Ref<Counter> = new Counter { hits: 0 };
const btn = new Button {
    id: 7,
    onClick: (t: int): string => {      // 函数类型字段：btn.onClick(7) 直接调用
        c.hits = c.hits + 1;            // 捕获的是 Ref（共享）→ 字段可变
        return "clicked:" + toStr(t) + " hits=" + toStr(c.hits);
    },
};
```

函数是值：具名函数可赋给 fn 类型变量（`const g: (int) => int = twice;`），
也能存进 struct 字段与 `List`；函数值不可 `==`（编译错误 3002）。

## 事件订阅（std/events）

标准库 `std/events` 是一张按 target 分组的回调表：`listenersNew()` 建表，
`on(table, target, handler)` 注册并返回注销用的 token，`off(table, token)`
注销，`dispatch(table, record)` 把 `EventRecord`（kind/target/x/y/mods 五个
`int` 字段）派发给同一 target 的存活 handler——**派发序＝注册序**，顺序
可预期、可断言：

```tx
const token = on(table, 1, first);
on(table, 1, second);
const n = dispatch(table, { kind: 0, target: 1, x: 5, y: 0, mods: 0 });  // first、second
off(table, token);                                                     // 注销 first
dispatch(table, { kind: 0, target: 1, x: 6, y: 0, mods: 0 });          // 只剩 second
```

handler 的类型是 `(EventRecord) => void`，就是普通闭包。

实跑输出（07 样例）：

```text
add10:15
clicked:7 hits=1
clicked:8 hits=2
dispatched:2
first x=5
second x=5
second x=6
```
