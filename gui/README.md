# 太虚 GUI

纯太虚桌面 GUI 库，随 [taixu-lang](https://github.com/yockii/taixu-lang) 发布。使用 `gui/` 前缀导入，依赖随附标准库。
包含组件、flex 布局、软件光栅、字体解析与文字绘制，以及 Windows、Linux X11、macOS 窗口后端。

## 使用

使用包含本库的同版本太虚工具链。发布包中的 `gui/src` 与编译器放在同一目录下，自动发现；
源码仓中也可自动发现 `gui/src`。自定义安装位置时，将 `TAIXU_GUI_ROOT` 设为本库 `src` 的绝对路径。

在 taixu-lang 或解压后的工具链根目录运行：

```sh
taixu init gui mytool
cd mytool
taixu run main.tx
```

也可在源码仓运行 `gui/examples/counter.tx`、`gui/examples/form.tx`、`gui/examples/textarea.tx` 等示例。
Windows 构建窗口程序可用 `taixu build main.tx --out dist --subsystem=windows`。
Linux 需要 X 服务器（包括 Xwayland 或 WSLg）；macOS 需要当前用户有可用的图形登录会话。
无窗口系统或启用重放时使用脚本窗口。完整上手说明见[五分钟 GUI]。

## 内容

| 路径 | 用途 |
|---|---|
| `src/components.tx` | 按钮、输入框、表格、多行文本等组件及绘制 |
| `src/app.tx` | 事件、焦点、拖拽、窗口与控件装配 |
| `src/layout.tx` | 确定性整数 flex 布局子集 |
| `src/text.tx` | 字体选择、度量与文字画笔 |
| `src/surface.tx` | 像素面、窗口、剪贴板与文件对话框 |
| `src/engine/` | 软件光栅、字体与各平台后端 |
| `examples/`（仅源码仓） | 示例及浏览器能力验证源码 |
| `tests/`、`verify.tx` | 自动验证与重放基准，仅源码仓包含 |

GUI 是可选库。程序不导入 GUI 时无需链接其实现。它依赖标准库，不能访问标准库专用的运行时内部接口。
浏览器示例用于展示制作能力，支持有限的 HTML/CSS，不提供完整网页兼容性或独立浏览器发行包。

## 自动验证

在 taixu-lang 源码根运行：

```sh
taixu run --toolchain scripts/gate.tx --compiler <taixu绝对路径> --only gui
```

验证使用无头重放与有界退出，每个重放默认运行八次，输出和截图与基准逐字节比较。
平台事件测试使用隐藏窗口，只在对应平台执行；Linux 的平台事件测试还需要 `DISPLAY`。
也可在本目录执行 `taixu run --toolchain verify.tx --compiler <taixu绝对路径> --stdlib <stdlib/src绝对路径>`。

## 限制与许可

文字栈支持当前测试覆盖的字体格式与排版功能，没有完整复杂文字塑形、逐字字体回退或完整浏览器排版能力。
Linux 文件对话框依赖 XDG 桌面门户，缺少门户时不可用。窗口效果随平台能力降级，确定性重放不包含合成器效果。

自有代码按仓库根的 [Apache-2.0](../LICENSE-APACHE) 或 [MIT](../LICENSE-MIT) 双许可提供。
随附字体使用独立的 SIL OFL 1.1，见[第三方说明](NOTICE.md)及[字体许可全文](src/engine/fonts/OFL.txt)。
