# 太虚语言入门教材

写给所有想用太虚写程序的人——也包括替你写码的 AI Agent。每章＝一页正文
＋`samples/` 可运行样例；**章内展示的输出都是实际运行得到的**（样例用
`taixu run`）。

写码时随手查的**速查手册**（语言面／内建函数全表／诊断码／CLI 子命令／
标准库模块）：[../quickref.md](../quickref.md)。语言的权威定义见
[语言规范](../design/D3-s1-language-spec-v0.42.md)。

## 章节

| 章 | 文件 | 内容 |
|---|---|---|
| 01 | [01-install-hello.md](01-install-hello.md) | 安装与自检、`taixu init/run/test`、第一个程序 |
| 02 | [02-values-types.md](02-values-types.md) | 值与类型：内建类型、struct/enum 值语义 |
| 03 | [03-control-flow.md](03-control-flow.md) | 控制流：条件严格 bool、while/for/for-in/switch |
| 04 | [04-failure-channels.md](04-failure-channels.md) | 失败通道：`T \| Error`、`?`/`catch`/`??`、`T?` 桥接 |
| 05 | [05-heap-references.md](05-heap-references.md) | 堆与引用：`new`/`Ref`/`Weak`/`List`，成环与 `--leak-check` |
| 06 | [06-modules-stdlib.md](06-modules-stdlib.md) | 模块与标准库：import、`std/` 模块、自带的 Map/Set |
| 07 | [07-closures-events.md](07-closures-events.md) | 闭包与函数类型：捕获、fn 类型字段、事件订阅 |
| 08 | [08-async-tasks.md](08-async-tasks.md) | 异步与任务：async/await、TaskGroup、定时器与取消 |
| 09 | [09-toolchain.md](09-toolchain.md) | 工具链：fmt/test/doc/lsp/dap/build 与发布 |
| 10 | [10-gui.md](10-gui.md) | GUI 现状：迁移空窗期说明 |
| 11 | [11-ai-workflow.md](11-ai-workflow.md) | AI 原生工作流：CLI/LSP/MCP 三通道、诊断码即事实源 |
| 12 | [12-syntax-tour.md](12-syntax-tour.md) | 关键字全表、字面量与转义、语法糖、进阶语法、有意没有的写法 |

## 运行样例

标准库随包、零配置——解压后在包根目录直接：

```bash
taixu run docs/tutorial/samples/01-hello.tx          # 只用内建
taixu run docs/tutorial/samples/06-modules.tx        # 用到 std/ 模块
```

`taixu` 会自动找到与可执行文件同目录的 `stdlib/`。只有当你另有一份自己的
标准库时，才需要用环境变量 `TAIXU_STDLIB` 指向它的源码根目录（放
`path.tx`、`json.tx` 等模块文件的那一层）。

## 本书用词

全书统一用这几组词，遇到即按此理解：

- **失败通道**：`T | Error`（可能出错的操作）与 `T?`（可能缺值）两条
  独立通道，`?` 传播、`catch` 兜底；
- **着色**：`async`/`await`——异步函数要用 await 等结果；
- **句柄语义**：`Ref`/`List`/`Map`/`Set`/`Future` 赋值传参共享同一对象；
- **值语义**：struct/enum 赋值即整体拷贝，互不影响。

太虚刻意取 TypeScript 的一个朴子集：凡是 TS 有而太虚没有的写法，几乎都
是有意排除——编译器会直接报错，不会静默猜；常见误写（跨类型 `==`、
`if (n)`、连写比较等）的诊断还会说明原因。
