# 11 AI 原生工作流

**本章你将学到**：把太虚工具链当作 Agent 的协作面来用——CLI/LSP/MCP
三通道同引擎，诊断码即事实源，确定性输出即验证闭环。太虚从第一天起就
为“AI 写码、人验收”而设计；本章是这套用法的最小操作手册。

## 三通道，一个引擎

| 通道 | 入口 | 适用 |
|---|---|---|
| CLI | `taixu run/build/test/fmt/doc/explain/capabilities` | 脚本化与 Agent shell 调用 |
| LSP | `taixu lsp --stdio` | 编辑器实时诊断/补全（VS Code 扩展同引擎，无私有前端） |
| MCP | `taixu mcp` | Agent 会话内 compile/run/test/disasm 与 `debug_*` 调试工具（stdio 上一行一条 JSON-RPC） |

三通道走同一编译器与同一诊断源——**编辑器里报的错、命令行报的错、
MCP 返回的错是同一个错误**（同码同消息同位置），不存在旁路。

MCP 服务缺省只开放 compile 与 disasm；要让它运行程序、测试或调试，启动它
的人须明确信任工作区（`taixu mcp --workspace <目录> --trust-workspace`），
且入口文件在工作区内。资源限额（内存、CPU、请求超时）可调，但它不是运行
不可信代码的沙箱。

## 诊断码即事实源

```text
$ taixu explain 3006
3006 [sema:channel_mismatch] ? 传播通道与函数返回类型不一致
修复建议：Error 传播要求返回 U | Error；None 传播要求 U?；跨通道先 catch 显式桥接
$ taixu explain            # 无参数列出全表
```

编译器报错时每条诊断都带码与位置，并提示 `处置详见: taixu explain <码>`。
`run`、`test`、`fmt`、`doc` 等加 `--format=json` 给机读形态（诊断为
`{"schema":"diag/1","diags":[…]}`，含 code/msg/module/line/col）。Agent 的
修复循环 = 读码 → `explain <码>` → 改码 → 复跑，不需要人类转译。

## 能力探测与文档

```text
$ taixu capabilities             # 工具链能力清单（--format=json 给 JSON）
$ taixu changes                  # 工具链兼容变更登记（--format=json 给 JSON）
$ taixu doc samples/09-toolchain.tx   # /// 文档注释 → Markdown/JSON API 参考
```

Agent 面对陌生环境的第一步：`capabilities` 探测、`doc` 取 API 面、
`explain` 备诊断——三件都是确定性输出。

## 确定性优先：验证闭环

- `taixu test`：test 块逐个独立运行，退出码即判定（0 全过 / 2 有失败 /
  1 编译失败）；`--format=json` 给逐项结果；
- `--leak-check`：程序退出时仍存活的对象（引用成环等）按分配位置报告，
  退出码 23（05 章）；
- 异步调度是确定的 FIFO 全序（08 章），同一程序每次输出相同——输出可以
  直接与期望文本逐字节比对；
- `taixu fmt --check`：格式合规，退出码即结论。

## 给 Agent 的完整说明书

- [速查手册](../quickref.md)：单文件速查（语言面/内建/诊断码/CLI/标准库），
  Agent 查阅的首选；
- 本教程各章与 `samples/` 样例本身就是给 Agent 的上手教材——把本目录
  给 Agent，它即可学会太虚；
- 面向大模型的完整语言生成指南 [llm-guide-s1.md](../ai/llm-guide-s1.md)、
  可分发的 [Skill 包](../../skill/taixu/SKILL.md)、工具链
  [兼容变更](../ai/toolchain-changes.md)与[语言规范](../design/D3-s1-language-spec-v0.42.md)
  ——需要更深的规范细节时取用。

## 最小 Agent 会话（示例剧本）

```sh
taixu capabilities                         # ① 探测能力
taixu init hello                           # ② 脚手架（生成 hello/main.tx）
cd hello
taixu run main.tx                          # ③ 跑通基线
taixu test main.tx                         # ④ test 块判定
taixu fmt --check .                        # ⑤ 格式合规（退出码即结论）
taixu explain 3010                         # ⑥ 遇诊断按码取修复建议
```

每一行的输出都是机器可判定的——这就是“AI 原生”在工具链层的含义。
