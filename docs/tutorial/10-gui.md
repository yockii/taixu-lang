# 10 GUI 入门

**本章说明**：用外部 GUI 库 taixu-gui 写一个桌面小工具——取库、建项目、运行、发布，以及怎样用重放脚本做不看屏幕的确定性
测试。

GUI 在太虚里是**外部库**，不进语言核心；它的引擎（像素面、文字排版与光栅、窗口与事件）全部用太虚写成，经外部函数接口
调用操作系统。目前各平台的状况：

| 平台 | 真窗 | 无头运行与重放 |
|---|---|---|
| Windows | 有 | 有 |
| Linux、macOS | 暂无：开窗退回脚本窗，没有事件就结束 | 有 |

## 取得 GUI 库

发布包里没有 GUI 库。克隆 [taixu-gui](https://github.com/yockii/taixu-gui)，然后二选一：

- 设环境变量 `TAIXU_GUI_ROOT` 为它的 `src` 目录；
- 或把整个仓放在编译器所在目录下，目录名 `taixu-gui`。

程序里以 `gui/` 前缀导入它的模块，例如 `import { appNew } from "gui/app";`。

## 建项目、运行

```sh
taixu init gui mytool
cd mytool
taixu run main.tx
```

`taixu init gui` 写出三个文件：`main.tx`（窗口、文字、按钮与点击计数）、`replay.txt`（重放脚本）、`expected.out`（重放的
期望输出）。Windows 上 `taixu run main.tx` 打开一个窗口：点按钮计数加一，关窗后程序打印点击次数。

## 程序的骨架

`main.tx` 的主循环是所有 GUI 程序的样子：

```tx
const app = appNew(defaultSpec("mytool", 240, 140));
onClick(app, BTN, (rec: EventRecord): void => {
        m.count = m.count + 1;
    });
while (app.running) {
    const live: bool = pump(app, 16);
    if (app.dirty) {
        const cv = textCanvasK(m.surface, app.w, app.h, font.handle, 14, app.k);
        render(cv, app.w, app.h, m);
        hitZone(app, BTN, m.btn.x, m.btn.y, m.btn.w, m.btn.h);
        windowPresent(app.hwnd, m.surface);
        app.dirty = false;
    }
    if (!live) {
        break;
    }
}
```

- `appNew` 开窗；没有窗口系统或处于重放时自动改用脚本窗，程序不用分支。
- `pump` 取一条事件并分发给订阅者（`onClick`、`onResize` 等，订阅机制同第 7 章的 `std/events`），至多等 16 毫秒；关窗后
  返回 false。`onClick` 在组件被按下时调用。
- 有变化（`app.dirty`）才重画：画到像素面上，用 `hitZone` 登记按钮当前的位置（真窗的鼠标坐标靠它换成组件），再
  `windowPresent` 交给窗口。
- 坐标与尺寸用与设备无关的像素（DIP）；`app.k` 是窗口的缩放（100 即 100%），绘制面按它开成物理像素。

## 发布

```sh
taixu build main.tx --out dist --subsystem=windows
```

`--subsystem=windows` 让 Windows 产物以 GUI 子系统启动，不带出控制台窗口；这样的程序没有继承到标准输出时 `print` 不输出
（父进程交下了输出句柄时照常写出）。它只用于 Windows 目标。

## 确定性测试：重放与定格

GUI 程序可以不看屏幕地测试：用重放脚本代替鼠标与键盘，比对程序输出与画面。

```sh
TAIXU_REPLAY=replay.txt taixu run main.tx                                # 输出应与 expected.out 相同
TAIXU_REPLAY=replay.txt TAIXU_FRAME=2 TAIXU_OUT_DIR=out taixu run main.tx  # 另把第 2 帧存为 out/replay-f2.bmp 等
```

`replay.txt` 每行“帧号 kind target x y”：`1 3 1 0 0` 表示第 1 帧时按下组件 1，`3 6 0 0 0` 表示第 3 帧关窗。帧号是程序
`windowPresent` 的次数。`TAIXU_FRAME=N` 在第 N 帧把画面存成三个文件：`.bmp`（像素）、`.sha256`（像素哈希）、`.txt`
（绘制记录），可以逐字节与预先存好的结果比对。重放时一律用脚本窗与库里嵌入的字体，结果在三个平台上逐字节相同。

更多组件（输入框、下拉框、表格、多行文本等）、事件编码与重放格式见 taixu-gui 仓的文档。
