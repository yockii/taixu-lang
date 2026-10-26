# 05 堆与引用

**本章你将学到**：`new` 与 `Ref<T>`；`Weak<T>` 观察句柄；`List<T>` 容器；
引用成环与 `--leak-check`。（样例 [samples/05-refs.tx](samples/05-refs.tx)）

## Ref：唯一堆分配入口

```tx
const c: Ref<Counter> = new Counter { hits: 0 };
```

- **共享**：赋值/传参复制句柄，指向同一对象（同 TS object 心智）；
- **同一性 ==**：`c == alias` 比较是否同一对象；
- `const` 只锁绑定不锁字段——`c.hits = ...` 合法（同 TS const object）；
- `c.hits` 自动解引用；要整体取一份值拷贝用 `unref(c)`；
- 引用计数自动回收：最后一个强引用消失（重绑定/出作用域）即释放。

## Weak：观察不拥有

```tx
const w = weak(v);                                 // 不延长目标生命周期
const snap = deref(w) catch Counter { hits: -1 };  // 目标已死得 None → catch 兜底
v = new Counter { hits: 55 };                      // 旧目标最后一个强引用消失
// 此后 deref(w) → None
```

`deref` 得到的是目标的**值拷贝**（`T?`），不产生新的 `Ref`。

## List：句柄语义堆容器

操作走内建函数：`listNew/listPush/listPop/listGet/listSet/listLen/
listInsert/listRemoveAt`；也可用方法写法（`xs.push(v)` 等价于
`listPush(xs, v)`）。`listGet`/`listPop` 越界或空表得 `None`，`listSet`/
`listInsert`/`listRemoveAt` 越界即运行期错误。元素可以是标量、`string`、
struct/enum（存取都按值拷贝）、`Ref`/`Weak`/`List`、函数值、`Future` 等；
`T?`、`T | Error` 不能直接作元素。迭代顺序恒等于插入顺序——可预期、可在
测试中断言。

实跑输出（05 样例）：

```text
hits=2 same:true
before:9
after:-1
name:太虚
name:taixu
len:2
```

## 成环与 --leak-check

引用计数**不回收强引用环**：两个对象经 `Ref` 互相指着，谁也不会被释放。
用 `--leak-check` 运行（`build`/`run`/`test` 都支持），程序正常退出时报告
仍存活的对象：

```tx
struct Node {
    name: string,
    peer: Ref<Node>?,
}

function main(args: []string): int {
    const a = new Node { name: "a", peer: None };
    const b = new Node { name: "b", peer: None };
    a.peer = b;
    b.peer = a;
    print("linked");
    return 0;
}
```

```text
$ taixu run --leak-check cycle.tx
linked
taixu: [runtime:leak] 退出时仍有 2 个对象存活（引用计数成环，或运行期少放了计数）：
  1 个 Ref，分配于 cycle.tx:7:15
  1 个 Ref，分配于 cycle.tx:8:15
```

退出码为 23。修法：反向的那一条（子指父、后指前、观察者指回主体）改成
`Weak<T>`，例如 `prev: Weak<Node>?` 并赋值 `b.prev = weak(a);`，再跑就没有
报告了。
