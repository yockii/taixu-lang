# 循环标量寄存器分配

编译器默认允许把循环携带的标量载荷保留在寄存器中，减少选中槽的重复读取。分配同时用于默认后端和可移植后端。
自动策略保留类型、活跃性和控制流证明；目前对循环内有显式调用的函数保留值槽。调试、恢复点及分析预算不足时也继续使用值槽。
开启表示允许自动选择，不保证每个函数使用持久寄存器，也不保证所有程序提速。

用相同源码与输入比较开启、关闭两份产物：

```sh
taixu build app.tx --regalloc=on -o out/on
taixu build app.tx --regalloc=off -o out/off
taixu run --regalloc=off app.tx
taixu test --regalloc=off tests.tx
taixu disasm app.tx --regalloc=on --format=json
taixu profile compile app.tx --regalloc=off --format=json --top 0
```

`run` 的选项放在源文件之前，文件之后是程序参数；`test` 也接受文件之后的编译选项。
`--regalloc` 只接受 `=on` 或 `=off`。在 `build/run/test` 中可与目标选择、`--codegen=portable`、堆检查和泄漏检查组合。它不改变运行时例程自己的寄存器分配。

`capabilities --format=json` 的 `program_regalloc` 标明策略，`program_regalloc_default` 是布尔默认值；`program_regalloc_flag`、`program_regalloc_controls`、`program_regalloc_commands` 给出控制方式与支持命令。
文本 capabilities 提供相同字段。比较性能时核对源码、输入、目标、内核和其余选项，保留输出校验、逐次样本和机器码是否真的变化。
产物逐字节相同时，计时差不能解释为分配策略带来的收益或退化。[编译画像](compiler-profile.md)可核对指令工作量与产物哈希，但不等于运行性能测量。
