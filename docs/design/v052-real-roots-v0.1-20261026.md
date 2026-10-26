# 服务信任根解析为真实路径；调试断点按真实路径比对（v0.1，2026-10-26）

来源：用户 2026-10-26 裁决第 3 项（跟进第 5 条），附带跟进第 4 条。

## 一、问题

1. LSP／MCP 的信任根（`--workspace`、`--library-root`、`TAIXU_STDLIB`／`TAIXU_GUI_ROOT`、可执行文件旁的分发库）此前按字面路径
   核验，路径任何一级是符号链接即拒绝（`local*` 原语逐组件拒绝链接）。macOS 的 `/tmp`、`/var` 本身是链接，操作者给
   `/tmp/proj` 只能改写成 `/private/tmp/proj`；链接进来的工作区、Windows 上目录联接或 SUBST 盘符下的工作区同样起不来。
2. 挂载核验也按字面路径做：`/tmp/x` 若是指向 9p 挂载的链接，核验看的是 `/tmp` 所在的挂载——字面路径不是实际位置。
3. DAP 的断点按字面绝对路径比对：模块路径按（物理的）当前目录解析，客户端给的是它看到的逻辑路径，经链接时对不上，断点
   落空（跟进第 4 条）。

## 二、对照

| 做法 | 例子 |
|---|---|
| 身份按真实路径 | POSIX `realpath(3)`；Node 的模块身份缺省取真实路径（`--preserve-symlinks` 才保留链接）；`getcwd` 返回物理路径 |
| 按路径匹配的沙箱规则须写真实路径 | macOS 的 Seatbelt 规则匹配解析后的路径，规则里写 `/private/tmp` |
| 只信启动时给的根、之下不跟链接 | 本仓既有做法（`local*` 原语、worker 暂存区 `scratchRealDir`） |

## 三、做法

1. **根在启动时解析一次**：`addRoot` 先字面规范化，再经 `realPath`（三端模块 `real_path_{linux,macos,windows}.tx`：Linux
   逐组件 `readlinkat`，macOS libSystem `realpath`，Windows `GetFinalPathNameByHandleW`）取真实路径；挂载核验、目录核验与此后的
   全部访问都对真实根做。解析不了（不存在、链接过深、网络路径）时原样返回，由随后的核验拒绝——与此前同一条拒绝信息。
2. **写法作别名**：写法与真实根不同时记一对（写法 → 真实根）。请求里的路径按最长前缀与真实根、写法一起比，落在写法之下
   即改写为真实根加相对部分；访问仍经真实根与 `local*` 原语，**根之下的链接照样拒绝**（协议层面不能经链接出根）。
3. **报告**：LSP `initialize` 的 `taixuFileAccess` 与 MCP `server/discover` 的 `security` 里，`roots` 报真实根（实际授权的），
   新增 `root_aliases`（`[{"given": 写法, "real": 真实根}]`）。
4. **不放宽的部分**：`serviceLocalRoot` 仍按原样核验，worker 暂存区等非操作者给的路径不解析；网络路径（含 Windows 网络映射盘，
   解析为 `\\?\UNC\…`）仍拒绝；`initialize/workspaceFolders` 与 RPC 参数仍不能授权新根。
5. **时机**：只在启动时解析。启动后链接被改指向别处，服务仍用当初的真实根——安全的方向（不跟随改动）。
6. **共用实现**：MCP 暂存区的上级（`TMPDIR` 等，同属操作者配置）与构建驱动的 `osRealPath` 改用同一模块（此前 Linux、Windows
   的暂存区不解析，`TMPDIR` 经链接时 worker 起不来；构建脚本各有一份副本）。
7. **DAP**：`pathKey` 把规范化的绝对路径再解析为真实路径（按路径缓存，断点请求逐个模块求键），Windows 不分大小写照旧。
   MCP 的调试工具按模块名设断点，不涉及。

## 四、验证

`service_security_checks`（工具链合同步）加一组：工作区经链接（类 Unix 符号链接、Windows 目录联接）给出时服务能起，`roots`
为真实根、不含写法，`root_aliases` 有这一对；按写法、按真实路径、按相对路径都能编译；经写法访问根之下的链接仍拒绝。三宿主
完整门（M1 的工作目录在 `/private/tmp` 下）。
