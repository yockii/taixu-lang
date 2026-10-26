# 太虚（Taixu）

面向人类与 AI 协作开发的静态类型语言（源文件 `.tx`）。编译器、引用计数运行时、汇编器、链接器、格式化器、LSP／DAP／MCP
服务全部用太虚自身写成并完全自举，直接生成 PE／ELF／Mach-O 可执行文件——开发与构建都不需要 C 编译器、外部汇编器或链接器。

*Taixu is a statically typed language designed for humans and AI agents working together. The compiler, runtime, assembler,
linker and language services are written in Taixu itself (fully self-hosted) and emit native executables directly. Docs are
currently in Chinese.*

> **0.3.0-beta.1 是首次公测版。** beta 不承诺可靠性与兼容性：语言与工具链仍会变化，可能遇到错误、崩溃或错误的结果。
> 欢迎在使用中把问题反馈到 [Issues](https://github.com/yockii/taixu-lang/issues)。

## 支持的平台

| 平台 | 发布包 |
|---|---|
| Windows x86_64 | `taixu-0.3.0-beta.1-x86_64-windows.zip` |
| Linux x86_64 | `taixu-0.3.0-beta.1-x86_64-linux.tar.gz` |
| macOS x86_64（13 及以上） | `taixu-0.3.0-beta.1-x86_64-macos.tar.gz` |
| macOS arm64（Apple 芯片，13 及以上） | `taixu-0.3.0-beta.1-aarch64-macos.tar.gz` |

编译器可以从任一平台为其他三个平台交叉编译产物（`--target=windows|linux|macos|aarch64-macos`）。

## 安装

从 [Releases](https://github.com/yockii/taixu-lang/releases) 下载对应平台的发布包，解压即用，不需要安装器：

```text
taixu-0.3.0-beta.1-<平台>/
  taixu（Windows 为 taixu.exe）
  stdlib/      标准库（与可执行文件同目录，自动发现）
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
- [语言规范](docs/design/D3-s1-language-spec-v0.42.md)（D3 v0.42）与[语法速览](docs/design/D3-s1-syntax-tour-v0.1.tx)。
- 给 AI 的[语言指南](docs/ai/llm-guide-s1.md)与可分发的 [skill 包](skill/taixu/SKILL.md)；工具链的[兼容变更](docs/ai/toolchain-changes.md)。
- 工具：[编译画像](docs/tools/compiler-profile.md)、[链接映射](docs/tools/link-map.md)。
- 安全：LSP／MCP 的[信任与资源边界](docs/design/v052-tool-security-boundaries-v0.3-20261023.md)、
  [信任根解析为真实路径](docs/design/v052-real-roots-v0.1-20261026.md)；调试工具[堆检查](docs/design/v052-heap-check-tool-v0.1-20261026.md)。

## 已知限制

- GUI 外部库尚在迁移，暂不提供 GUI 模板与引擎。
- 还不能编出给 C 调用的库（`build --lib` 只检查库模块能否编成原生代码，产物不是库）。
- Windows 产物尚无 PDB 调试符号（Linux、macOS 产物带函数符号）；`disasm` 目前只支持 x86_64。
- 只支持上表四个目标；生成代码的性能尚未系统调优。

## 从源码构建与验证

需要一个已发布的 `taixu`（本版本或更新）作为种子——与 Rust、Go 用上一版编译器自举相同：

```sh
taixu run --toolchain scripts/build.tx --compiler /path/to/taixu --out work/bin
```

它会重新生成四个目标的运行时内核，再构建 B、C、D 三代编译器并要求 C 与 D 逐字节相同。完整的验收门：

```sh
taixu run --toolchain scripts/gate.tx --compiler /path/to/taixu
```

门的部分步骤需要 Python 3.12+ 与一个 C 编译器（独立的运行时安全校验用；可用环境变量 `TAIXU_TEST_CC` 指定），在 Windows、
Linux 与 Apple 芯片的 macOS 上运行。标准库位于 `stdlib/`，门会自动使用它。

## 许可证

[Apache-2.0](LICENSE-APACHE) 或 [MIT](LICENSE-MIT) 双许可，任选其一。
