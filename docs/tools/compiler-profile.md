# 编译画像：供人阅读，也供 AI 调用

`taixu profile compile` 用太虚实现，走与 `build` 相同的原生生产管线，编译一次并分析工作量。它不运行目标程序、不写出可执行文件。当前支持 x86_64 Windows／Linux。

```sh
taixu profile compile app.tx
taixu profile compile app.tx --sort=instructions --top 10
taixu profile compile app.tx --format=json --top 0
taixu profile compile bootstrap/taixu.tx --toolchain --format=json --top 0
taixu profile compile app.tx --target=linux
taixu profile compile --help
```

## 人的入口

默认输出目标、观测墙钟、阶段、记录子项、桥接构造／复制汇总、原生指令／操作数使用汇总、前 20 个函数及产物 SHA-256。排行缺省按记录片段数降序，相同值按本次函数 ID 升序；可选 `--sort=records|instructions|record-time`。`--top N` 控制显示数，0 表示全部，截断会明确提示。

先看 records 中的桥接、准备、拼接与函数工作量，或按 instructions 排序找发射规模较大的函数。高工作量并不自动等于 CPU 热点；结合具体代码与后续剖析，再选择优化假设。阶段与记录子项有包含关系，不把它们相加当总时间。

排行中的函数是**被编译的目标函数**。例如画像编译器自身时，`renderLop2` 的记录片段数表示编译器为这个函数生成了多少记录；`record_wall_ns` 表示为它生成记录花费的观测墙钟，不是执行 `renderLop2` 的 CPU 时间。判断哪个编译器函数在运行时最耗 CPU，仍需后续采样或明确的调用计数。

## AI 的入口

先通过 `taixu capabilities --format=json` 查询 `profile=compile`、`profile_schema=compile-profile/1` 及 schemas 列表，再调用上面的 JSON 命令。stdout 恰为一个 JSON 文档；进度和内部拒绝不会混入其中。捕获原始 stdout，按 UTF-8 JSON 读取。

| 字段 | 含义与读取方式 |
|---|---|
| `schema` / `toolchain_version` | 本工具的结构版本与当前开发版本；不支持的主版本应明确拒绝 |
| `ok` / `error` / `diags` | 成败、错误种类／退出码／原因，以及共享诊断；失败时 `profile=null` |
| `profile.numeric_encoding` | `decimal-string/int64`：所有64位计数、字节和纳秒是十进制字符串，按整数解析，例如 JS `BigInt`，避免先转换成浮点 |
| `profile.summary` | 全函数汇总，独立于显示数量；含 `functions_total` 和 `functions_returned` |
| `profile.functions` | 所选函数及同源事实；`id` 是本次编译内的整数索引，结合 module/name 定位，不假定跨构建永久一致 |
| `profile.truncated` | 是否因 `--top` 只返回部分函数；完整导出用 `--top 0` |
| `profile.phases` / `record_details` | 单调墙钟纳秒；包括观测开销，子项属于 records 阶段，不是额外耗时 |
| `profile.codegen_details` | codegen 阶段的子项：`seb1-parse-diagnostics`（驱动为诊断与入口检查先解析一次 SEB1）、`seb1-parse`、`fab1-parse`、`generate`（生成程序上下文时再解析与生成）；属于 codegen 阶段 |
| `profile.text_hops` | 两段文本中转的 UTF-8 字节：语义分析交给代码生成的 `seb1_utf8_bytes`、`fab1_utf8_bytes`，以及记录流 `record_utf8_bytes`（同 summary） |
| `profile.coverage` | 哪些节点／原生来源已统计、哪些未覆盖；没有运行频次 |
| `profile.product` | 与普通 build 可核对的最终字节数和 SHA-256，`written=false`、`executed=false` |

比较两份报告时先核对目标、工具链、输入与度量范围，再按函数工作量提出假设。构建内 ID 用来核对本次 disasm，不单独作为跨构建匹配键。JSON 保留完整精度；文本使用毫秒显示便于阅读。

## 指标边界

- `input_ir_words` 是进入记录桥接的 IR 字数；`bridge_ir_words_copied` 是该桥接实际复制量。后续内联等变换后的规模单列 `record_ir_words`。
- `bridge_fn_nodes`、`bridge_const_nodes`、`bridge_line_nodes` 对应桥接的 Fn／Const／LineCol 构造循环；这是有范围的构造次数，不是整个进程的堆统计或峰值。
- `record_parts` 与 `record_utf8_bytes` 统计片段及含拼接换行的 UTF-8 字节。全量汇总还含协议头，函数汇总不含。
- 原生指令及立即数、零立即数、内存操作数是程序函数发射时的使用次数，含序言／尾声；不含内核、运行时和 FFI／调试／工具桩，也不是不同对象数或运行频次。
- `analysis_cells` 是函数的分析规模（记录时的指令数×寄存器数，即逐 pc 稠密表的项数）；超过 `summary.analysis_budget_cells`
  的函数 `analysis_skipped=true`，整段不做槽类型、逃逸与 IR 分析，按无类型事实保守发射（语义不变，只少优化）。
  `summary.analysis_skipped_functions` 与 `analysis_max_cells` 是全函数汇总。
- CPU、OS 峰值、通用堆分配和正式性能结论未采集，JSON 明确为 null。报告与最终哈希计算排除在观测计时外；这份画像不能替代正式回归验收。

退出码：0 完成，1 编译失败，3 输出失败，64 用法错误。JSON 模式的用法／编译失败仍返回同 schema 的失败对象；文本模式在 stderr 给出原因，编译失败同时保留源码诊断。`--toolchain` 仅在分析工具链源码时使用。

实现与验证范围见设计。链接地址、太虚生成函数范围与帧事件见[链接映射](link-map.md)；原生采样、系统展开和通用堆分析另行推进。
