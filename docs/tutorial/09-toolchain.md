# 09 工具链

**本章你将学到**：fmt/test/doc/lsp/dap/build 的最小可用面，以及怎样把程序
发给别人。（样例 [samples/09-toolchain.tx](samples/09-toolchain.tx)）

全部工具都在同一个 `taixu` 可执行文件里，`taixu --help` 列出全部子命令与
选项。

## fmt——格式化是一等公民

```bash
taixu fmt main.tx              # 把格式化结果输出到 stdout（不改文件）
taixu fmt --check .            # 检查：有文件需格式化时退出码 1
taixu fmt --write main.tx      # 写回
```

参数可以是多个文件或目录。格式化器是保守的：只把空格与缩进整理到位，
不动你的代码结构。团队协作或 Agent 写码后跑一遍 `taixu fmt --check`，
退出码即结论。

## test——test 块枚举运行

```tx
test "add 基本行为" {
    assert(add(2, 3) == 5, "2+3 应为 5");
    assert(add(-1, 1) == 0, "负数参与");
}
```

```text
$ taixu test 09-toolchain.tx
✓ test "add 基本行为" (96.469ms)
1 passed, 0 failed
```

（括号里的耗时每次不同。）`taixu test` 一次接一个 `.tx` 文件，每个 test 块
在独立进程中运行；这个文件导入的模块里的 test 块也一并运行（例如库模板的
`tests/smoke.tx` 会连 `src/` 里库文件自己的 test 一起跑）。退出码 0 全过、
2 有失败、1 编译失败。失败的块会写出
断言消息，例如 `✗ test "broken" — assert_failed: 2+2 应为 5`。加
`--leak-check` 顺带检查引用成环（05 章），选项写在文件前后都可以：
`taixu test main.tx --leak-check`。`--format=json` 输出机读结果。

## doc——文档注释 → Markdown/JSON

`///` 前缀的文档注释被收集；`taixu doc file.tx` 输出 Markdown，
`taixu doc --format=json file.tx` 输出机读形态，`--out-dir=<目录>` 写成
文件；参数也可以是目录。

## lsp——编辑器同引擎

`taixu lsp --stdio` 起语言服务器（诊断、格式化、文档符号、悬停、签名
提示、补全、跳转定义、查找引用、重命名、语义高亮）。诊断与 CLI **同引擎**
——没有第二套语义实现；VS Code 扩展 taixu-vscode 即该子进程的客户端。

## 调试你的程序（dap）

断点、单步、看变量——不用 print 大法。调试走**插桩调试构建**：
`taixu run --debug` 在 stdin/stdout 上提供 NDJSON 调试会话；`taixu dap`
用同一套调试引擎起一个标准 DAP（Debug Adapter Protocol）stdio 服务器，
编辑器即插即用：

```bash
taixu dap                # DAP stdio 服务器（一般不手动跑——编辑器会替你起）
```

**VS Code 使用路径**（推荐）：装 taixu-vscode 扩展 → 打开含 `.tx` 的工程 →
运行和调试面板 → 创建 launch.json → 选 **Taixu Debug**：

```json
{
  "type": "taixu",
  "request": "launch",
  "name": "调试 Taixu 程序",
  "program": "${workspaceFolder}/main.tx",
  "cwd": "${workspaceFolder}"
}
```

需要给 `main` 传参数时加 `"args": ["..."]`。F5 后：代码行号左侧打断点
（也可按函数名下断点）、F10 步过、F11 步入、Shift+F11 步出、调用栈面板
看帧、变量面板按名字看局部变量（另有“槽位”视图）——全部由 `taixu` 同
引擎驱动。目前不支持条件断点与在调试中修改变量值。

## build——从源码到可执行程序

```bash
taixu build app.tx --out dist                    # 生成 dist/app.exe（Windows）或 dist/app
taixu build app.tx --target=linux --out dist     # 交叉编译到另一平台
```

`build` 遵循大众直觉（Go/Rust 同构）：**源码进、可执行文件出**。产物名取
入口文件名；`--out` 必填，目录不存在时自动创建（只建最后一层）。
`--target` 可取 `windows`、`linux`、`macos`（按本机架构）或三元组
`x86_64-windows`、`x86_64-linux`、`x86_64-macos`、`aarch64-macos`——任一
平台都能为其余三个平台编译。其他常用选项：`--strip`（Linux、macOS 产物
不带函数符号）、`--leak-check`、`--debug`（调试构建）。

## 发布你的程序

产物是**单个可执行文件**，标准库等用到的代码都已编进去——运行时**不需要**
`taixu`、源码或 `stdlib/` 文件夹：

- **Windows**：只经导入表使用系统 DLL（如 kernel32）；
- **Linux**：静态链接，不依赖 libc（程序用 `from "库"` 调了外部库时除外）；
- **macOS**：动态链接系统的 libSystem，带 ad-hoc 签名（未公证，分发给别人
  时同样会遇到首次运行的拦截，见 01 章）。

```bash
taixu build app.tx --out dist
# 把 dist 里的那一个文件发出去即可
```

## 其他工具

`taixu explain <码>` 解释诊断码（11 章）；`taixu bench` 多轮运行计时；
`taixu disasm` 按函数和源码行显示生成的汇编（目前只有 x86_64）；
`taixu profile compile` 编译画像；`taixu capabilities`／`taixu changes`
报告工具链能力与兼容变更（供自动化读取）。

实跑输出（09 样例 `taixu run`）：

```text
toolchain:42
```
