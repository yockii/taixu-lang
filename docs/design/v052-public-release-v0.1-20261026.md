# 首次公测：公开仓 taixu-lang 与发布包（v0.1，2026-10-26）

来源：用户 2026-10-26 裁决——不求十全十美，达到可用即发首次公测（beta 不承诺可靠性，靠真实使用反馈）；新建公开仓
`yockii/taixu-lang`，以一个独立的单次提交为起点（taixu-project 的提交与试验过程不公开）；版本 **0.3.0-beta.1**；现阶段仍以
taixu-project 为主开发仓、**按版本导出**到 taixu-lang，待稳定后再切换主仓、归档 project；标准库**并入** taixu-lang 的
`stdlib/`；文档**精选**。

## 一、对照

| 问题 | 做法 | 例子 |
|---|---|---|
| 私有仓按版本导出到公开仓 | 声明式清单（收哪些路径、排除哪些、改名）＋确定性变换；每版压成一个提交，或逐提交同步 | Google Copybara（`origin_files` 白名单、`transformations`、SQUASH／ITERATIVE 两种模式，TensorFlow、Bazel 等由内部仓导出）；git-filter-repo 改写历史（本仓不取：历史本身不公开） |
| 从源码构建的自举种子 | 用上一个发布版作种子，不要求公开仓带完整自举历史 | Rust 的 stage0（下载上一版 beta）、Go 1.21+ 用上一版 Go 自举、Zig 的 zig-bootstrap |
| 标准库的位置 | 按编译器二进制的位置找，不按当前目录 | Go 的 GOROOT（`<根>/bin/go` → `<根>/src`）、Rust 的 sysroot、Zig 的 lib 目录 |
| 发布包 | 每个目标一个归档（Windows 用 zip，类 Unix 用 tar.gz）＋校验和清单；一台机器交叉构建全部目标，再在各平台冒烟 | Go 的 `go<版本>.<os>-<arch>`、Rust 的 `rust-<版本>-<三元组>`、Zig 的 `zig-<os>-<arch>-<版本>`；Go 发布由 Linux 交叉构建全部二进制 |
| 未签名的二进制 | beta 先不做商业签名，说明里写明系统提示与放行方法 | 多数早期语言工具链的预发布版；macOS 公证需 Apple 开发者账号 |

## 二、导出（`scripts/export.tx`，太虚写）

1. **只取已提交内容**：核心经 `git archive <提交>` 取，标准库取 taixu-stdlib 的给定提交，同样经 `git archive`；工作树里没提交的东西
   一律不进公开仓。
2. **清单** `release/export.txt`：逐行 `include <路径>`、`exclude <路径>`、`map <源> <目的>`（标准库 → `stdlib/`、公开 README →
   根目录）。路径为目录或文件前缀，不用通配。
3. **范围**：编译器与运行时源码（`bootstrap/`、`prelude/`）、门与测试（`scripts/`、`tests/`、`spike/loads`）、`examples/`、
   `bench/algo`、`skill/taixu`、许可证；文档精选——语言规范现行版、`docs/ai`（AI 指南与工具链变更）、`docs/tools`、教程
   `docs/tutorial`、语法速览，以及公开 README 链到的几份现行设计（安全边界、真实根、堆检查、产物符号）。设计演进稿、工单、
   需求清单、AGENTS 卡片、`docs/build` 证据与 `.zcode`、`CLAUDE.md` 不导出。
4. **变换**：Markdown 链接指向未导出的仓内文件时改为纯文本（保留链接文字，不留死链）；`AGENTS.md` 的链接同样处理。
5. **生成物**：`tests/frozen/pinned-bootstrap.tar`——前端冻结测试的历史输入（ZIG_SEED 提交 794d3ba 的 `bootstrap/`，单提交仓里取不到
   那段历史），测试先试 `git archive`，取不到才用它；输入逐文件的 SHA-256 本就记在冻结清单里，内容是否原样由清单核对。
6. **自检**（不过即不出）：导出树里所有 Markdown 仓内链接可达；不含清单 `forbid` 行列出的本机痕迹（开发机与测试机的路径、
   用户名、主机名；写在不导出的清单里，导出器源码自己不含它们）；单文件不超过 10 MB；清单里的每条 include 都命中。
7. **每版一个提交**（SQUASH）：导出树写进 taixu-lang 的工作副本、整体替换、提交信息为版本说明。

## 三、单次提交仓的自洽

1. **标准库根**：编译器在“二进制旁”的规则里另认 `stdlib/src`（原认 `taixu-stdlib/src`）；LSP／MCP 把二进制旁的库根当作同一信任域
   的规则随之覆盖。按当前目录向上找的规则不加这个通用名（避免别的项目里恰好有 `stdlib/src` 被当作标准库）。
2. **门**：`gate.tx` 的缺省标准库为 `<仓库根>/stdlib`（存在时），否则仍为同级 `taixu-stdlib`。
3. **自举**：`scripts/build.tx` 只在种子缺少新特性时才按 `bootstrap/STAGE*` 从历史提交补阶段；公开仓以上一个发布版（首版即
   0.3.0-beta.1 本身）作种子，不需要历史。种子过旧时报错说明须用发布版。
4. 完整门在导出树（初始化为单次提交的 git 仓）里于三台机器各跑一遍，作为导出的验收。

## 四、发布包（`scripts/release.tx`）

1. 一台机器交叉构建四个目标的编译器（x86_64 Windows／Linux／macOS、arm64 macOS；三端交叉一致已由门逐次核对）。
2. 每个目标一个目录 `taixu-0.3.0-beta.1-<三元组>/`：`taixu`（或 `taixu.exe`）、`stdlib/src`、`examples/`、`docs/`（教程、语言规范、
   AI 指南）、`README.md`（快速上手与已知限制）、两份许可证；Windows 打 zip，其余打 tar.gz（系统自带的 tar），另出
   `SHA256SUMS`。
3. 冒烟（各平台在干净目录解压后跑）：`--version`、`capabilities`、hello 的 run 与 build、`std/` 导入、`test`、`fmt --check`、
   LSP 的 initialize。
4. 签名：macOS 产物为 ad-hoc 签名（链接器既有），不做公证；Windows 不签名。系统提示与放行方法写进 README。

## 五、给测试者的说明（README 与 Release 说明）

现状与已知限制：beta 不承诺可靠性；支持的四个目标；GUI 外部库暂缺；不能编出给 C 调用的库（`--lib` 只检查库模块）；Windows
产物无 PDB；性能尚未调优（以自身为基线的度量刚建立）；反馈走 GitHub Issues。
