# 五分钟 GUI

先安装包含 GUI 库的太虚工具链，将编译器所在目录加入 PATH。GUI 库根为编译器旁的 `gui/src`，无需额外下载。
自定义位置可通过 `TAIXU_GUI_ROOT` 指向库的 `src` 目录。

## 创建与运行

```sh
taixu init gui mytool
cd mytool
taixu run main.tx
```

模板生成 `main.tx`、`replay.txt` 和 `expected.out`。运行后点击按钮可增加计数。
Windows 直接使用系统窗口；Linux 需要 X 服务器（Xorg、Xwayland 或 WSLg）；macOS 需要可用的图形登录会话。

程序通过 `gui/app` 管理窗口、事件与控件装配，通过 `gui/components` 创建组件、绘制和命中测试。
`gui/text` 提供字体画笔，`gui/surface` 提供像素面与呈现，`gui/layout` 提供 flex 布局子集。
窗口循环调用 `pump`，有重绘请求时绘制并呈现，退出时释放应用、像素面与字体。

## 无头重放

使用环境变量启用重放，产物无需专门的测试构建。POSIX shell：

```sh
TAIXU_REPLAY=replay.txt taixu run main.tx
```

PowerShell：

```powershell
$env:TAIXU_REPLAY = "replay.txt"
taixu run main.tx
Remove-Item Env:TAIXU_REPLAY
```

标准输出应与 `expected.out` 相同。重放使用脚本窗口，有界结束，不需要点击或观看屏幕。
需要定格时设置 `TAIXU_FRAME` 为帧号、`TAIXU_OUT_DIR` 为输出目录，生成 `.bmp`、`.sha256`、`.txt` 三份结果。
运行交互程序前清除这些变量。重放采用确定性字体与光栅，平台合成器效果不进入截图基准。

## 构建

```sh
taixu build main.tx --out dist
```

Windows 窗口程序可增加 `--subsystem=windows`，启动时不附带控制台。
字体或其他随应用分发的资源应附带对应版权与许可；本库的嵌入字体见[第三方说明](../NOTICE.md)。

## 示例与边界

在 taixu-lang 源码仓根运行 `taixu run gui/examples/form.tx` 可查看表单组件，`textarea.tx` 展示多行文本，`flex.tx` 展示布局。
源码仓还包含完整的自动验证与重放基准，运行入口见 [GUI README](../README.md)。
文件对话框在 Linux 依赖 XDG 桌面门户；没有门户时不可用。文字排版与 flex 均为当前实现的子集，
没有完整复杂文字塑形、逐字字体回退、多行 flex 或完整网页排版功能。
