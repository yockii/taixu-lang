# 01 安装与 Hello

**本章你将学到**：拿到 `taixu` 可执行文件并自检；用 `init` 生成项目；跑起
第一个程序；理解 `main` 的签名约定。

## 安装

太虚是**绿色免安装**的：下载对应平台的发布包，解压即用——不需要安装器，
也不需要 C 编译器、汇编器或链接器（编译器自己直接生成可执行文件）。

| 平台 | 发布包 |
|---|---|
| Windows x86_64 | `taixu-0.3.0-beta.1-x86_64-windows.zip` |
| Linux x86_64 | `taixu-0.3.0-beta.1-x86_64-linux.tar.gz` |
| macOS x86_64 | `taixu-0.3.0-beta.1-x86_64-macos.tar.gz` |
| macOS arm64（Apple 芯片） | `taixu-0.3.0-beta.1-aarch64-macos.tar.gz` |

只支持上面四个目标。包内结构：

```text
taixu（Windows 为 taixu.exe）   编译器与全部工具（run/build/test/fmt/doc/lsp/dap/mcp……）
stdlib/                         标准库（与可执行文件同目录，自动发现，零配置）
examples/                       样例
docs/                           本教程、语言规范与 AI 指南
skill/                          给 AI 代理的 skill 包
README.md                       快速上手与已知限制
LICENSE-APACHE、LICENSE-MIT     许可证
```

把解压目录加入 `PATH`，然后自检：

```bash
taixu --version
```

输出形如：

```text
taixu 0.3.0-beta.1 native bootstrap compiler (x86_64-windows)
```

括号里是本机目标（如 `x86_64-linux`、`aarch64-macos`）。

> **首次运行被系统拦下？**
>
> - **macOS**：可执行文件只有 ad-hoc 签名、没有经过公证。经浏览器下载的
>   包首次运行会被拦下，在解压目录执行 `xattr -dr com.apple.quarantine .`
>   后再运行即可。
> - **Windows**：可执行文件未签名，SmartScreen 可能提示“未知发布者”，
>   选择“仍要运行”即可。

## 第一个程序

先试包内样例：

```bash
taixu run examples/hello.tx
```

```text
Hello, 太虚!
```

再用脚手架建一个自己的项目：

```bash
taixu init hello        # 生成 hello/main.tx（console 模板）
cd hello
taixu run main.tx
```

```text
init: 1 个文件写入 hello（模板 console）
hello from hello
```

`taixu init` 只生成一个文件 `hello/main.tx`，项目怎么分目录由你决定；
也可以写成 `taixu init console hello`。另有库模板
`taixu init mylib --template=library`，生成 `mylib/src/mylib.tx` 与冒烟测试
`mylib/tests/smoke.tx`。目录里已有同名文件时 `init` 不覆盖。

`taixu run` 先把程序编译成本机可执行文件（放在临时目录）再运行，退出码
原样转发；写在文件名之后的参数传给程序的 `main`。

最小程序就是**一个 `main`**（样例 [samples/01-hello.tx](samples/01-hello.tx)）：

```tx
function main(args: []string): int {
    print("Hello, 太虚!");
    const answer: int = 42;
    print("answer=" + toStr(answer));
    return 0;
}
```

实跑输出：

```text
Hello, 太虚!
answer=42
```

要点：

- `main` 签名约定为 `(args: []string): int`；`args` 不含程序名；返回值即
  进程退出码。返回类型也可写 `void`（视为 0），或 `int | Error`——返回
  错误时 stderr 打印该错误、退出码 1（失败通道见 04 章）；
- `print` 只收 `string`——数字先用 `toStr`（无隐式数值拼接，见 02 章）；
- 语句以 `;` 结尾；`function` + `: 返回类型`（TS 风格，无箭头返回）。

下一步：`taixu test main.tx` 运行文件里的 `test` 块（09 章），
`taixu build main.tx --out dist` 生成可以单独分发的可执行文件（09 章）。
