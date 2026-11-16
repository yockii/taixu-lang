# 太虚（Taixu）

面向人类与 AI 协作开发的静态类型语言（源文件 `.tx`）。编译器、引用计数运行时、汇编器、链接器、格式化器、LSP／DAP／MCP
服务全部用太虚自身写成并完全自举，直接生成 PE／ELF／Mach-O 可执行文件——开发与构建都不需要 C 编译器、外部汇编器或链接器。

*Taixu is a statically typed language designed for humans and AI agents working together. The compiler, runtime, assembler,
linker and language services are written in Taixu itself (fully self-hosted) and emit native executables directly. Docs are
currently in Chinese.*

> **0.3.0-beta.2 是第二个公测版**，新增可选 GUI 库、HTTPS 客户端与循环标量寄存器分配，详见 [Releases](https://github.com/yockii/taixu-lang/releases) 页的发布说明。
> beta 不承诺可靠性与兼容性：语言与工具链仍会变化，可能遇到错误、崩溃或错误的结果。
> 欢迎在使用中把问题反馈到 [Issues](https://github.com/yockii/taixu-lang/issues)。

## 支持的平台

| 平台 | 发布包 |
|---|---|
| Windows x86_64 | `taixu-0.3.0-beta.2-x86_64-windows.zip` |
| Linux x86_64 | `taixu-0.3.0-beta.2-x86_64-linux.tar.gz` |
| macOS x86_64（13 及以上） | `taixu-0.3.0-beta.2-x86_64-macos.tar.gz` |
| macOS arm64（Apple 芯片，13 及以上） | `taixu-0.3.0-beta.2-aarch64-macos.tar.gz` |

源码候选编译器支持八个目标：Windows x86_64／arm64、Linux x86_64／arm64／LoongArch64／RISC-V 64、macOS x86_64／arm64。
用 `--target=<三元组>` 选择，例如 `--target=aarch64-linux`。上表为 beta.2 的发布包。
Windows x86_64、Linux x86_64、macOS arm64 执行完整宿主门；其余目标的交叉构建与结构检查、实际运行分别验证和记账。

## 安装

从 [Releases](https://github.com/yockii/taixu-lang/releases) 下载对应平台的发布包，解压即用，不需要安装器：

```text
taixu-<版本>-<平台>/
  taixu（Windows 为 taixu.exe）
  stdlib/      标准库（与可执行文件同目录，自动发现）
  gui/         可选 GUI 库（源码、字体与文档，自动发现）
  examples/    样例
  docs/        教程、语言规范与 AI 指南
  skill/       给 AI 代理的 skill 包（语言指南的可分发形态）
```

把该目录加入 `PATH` 后运行 `taixu --version` 自检。`SHA256SUMS` 可核对下载是否完整。

- **macOS**：可执行文件只有 ad-hoc 签名、没有经过公证。经浏览器下载的包首次运行会被拦下，可在解压目录执行
  `xattr -dr com.apple.quarantine .` 后再运行（用 `curl` 下载的不受影响）。
- **Windows**：可执行文件未签名，SmartScreen 可能提示“未知发布者”，选择“仍要运行”即可。

## 快速上手

```sh
taixu init hello-app             # 生成 hello-app/main.tx（库骨架用 taixu init library <名字>）
cd hello-app
taixu run main.tx                # 编译到临时目录并运行
taixu build main.tx --out build                        # 生成独立可执行文件
taixu build main.tx --target=linux --out build-linux   # 交叉编译
taixu test main.tx --leak-check  # 运行文件里的 test 块，并检查引用计数成环与泄漏
taixu fmt --check main.tx        # 格式检查（--write 就地格式化）
```

一个最小程序（把它写进 `main.tx` 即可运行与测试）：

```tx
import { join } from "std/string";

function greet(names: List<string>): string {
    return "你好，" + join(names, "、");
}

export function main(args: []string): int {
    const names: List<string> = listNew();
    listPush(names, "太虚");
    listPush(names, "世界");
    print(greet(names));
    return 0;
}

test "greet" {
    const one: List<string> = listNew();
    listPush(one, "太虚");
    assert(greet(one) == "你好，太虚", "greet");
}
```

其他入口：`taixu doc` 生成 API 文档；`taixu explain <诊断码>` 解释诊断；`taixu lsp`、`taixu dap` 供编辑器使用；`taixu mcp`
为 AI 代理提供编译、运行、测试与调试工具；`taixu capabilities --format=json` 与 `taixu changes --format=json` 供自动化读取。
编辑器支持见 [taixu-vscode](https://github.com/yockii/taixu-vscode)。

## 文档

- [教程](docs/tutorial/README.md)：从安装到异步、模块与工具链，每章附可运行的样例。
- [语言规范](docs/design/D3-s1-language-spec-v0.54.md)（D3 v0.54）与[语法速览](docs/design/D3-s1-syntax-tour-v0.1.tx)。
- 给 AI 的[语言指南](docs/ai/llm-guide-s1.md)与可分发的 [skill 包](skill/taixu/SKILL.md)；工具链的[兼容变更](docs/ai/toolchain-changes.md)。
- 工具：[编译画像](docs/tools/compiler-profile.md)、[链接映射](docs/tools/link-map.md)、[固定快照批次验证](docs/tools/gate-batches.md)。
- 安全：LSP／MCP 的[信任与资源边界](docs/design/v052-tool-security-boundaries-v0.3-20261023.md)、
  [信任根解析为真实路径](docs/design/v052-real-roots-v0.1-20261026.md)；调试工具[堆检查](docs/design/v052-heap-check-tool-v0.1-20261026.md)。

## 已知限制

- [GUI 库](gui/README.md)随附：`taixu init gui mytool` 创建窗口程序。Linux 需要 X 服务器，macOS 需要图形登录会话；无头重放可用于自动验证。
- 还不能编出给 C 调用的库（`build --lib` 只检查库模块能否编成原生代码，产物不是库）。
- Windows 产物尚无 PDB 调试符号（Linux、macOS 产物带函数符号）；`disasm` 可用 `--target` 查看各目标生成的汇编清单。
- 生成代码的性能仍在调优；局部寄存器分配可用 `--regalloc=on|off` 控制，具体程序的收益应成对测量。

## 从源码构建与验证

需要已发布的 `taixu` 0.3.0-beta.1 或更新版本作为种子——与 Rust、Go 用上一版编译器自举相同：

```sh
taixu run --toolchain scripts/build.tx --compiler /path/to/taixu --out work/bin
```

它会重新生成支持目标的普通与堆检查内核，再构建 B、C、D 三代编译器并要求 C 与 D 逐字节相同。完整的验收门：

```sh
taixu run --toolchain scripts/gate.tx --compiler /path/to/taixu
```

构建与门都只需要种子 `taixu`（门与各门步都由太虚写成），另需一个 C 编译器（独立的运行时安全校验用；
可用环境变量 `TAIXU_TEST_CC` 指定）；门在 Windows、Linux 与 Apple 芯片的 macOS 上运行。标准库位于 `stdlib/`，门会自动使用它。

## 许可证

[Apache-2.0](LICENSE-APACHE) 或 [MIT](LICENSE-MIT) 双许可，任选其一。
