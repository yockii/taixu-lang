# tests/ —— 端到端用例（D2 §6-2、TEST-01/02）

当前完整入口为 `taixu run --toolchain scripts/gate.tx --compiler <原生 taixu>`；单独运行 CLI 或独立产物门可用 `scripts/test-native-cli.py`、`scripts/test-nrt-products.py`。`--quick` 不代替完整验收。正式性能与 OS 隔离另跑，见 6e 设计与结果。

`runtime-functions` 复用 `runtime-security/` 中独立 C／汇编探针，验证实际发射的 RC、对象表 OOM 恢复、管道状态与 FFI 回调。测试机需要 GCC／Clang 兼容的 C 编译器，可用 `TAIXU_TEST_CC` 指定可执行文件，或从 PATH 查找 cc/gcc/clang；缺失会使该门失败。这只是独立验证依赖，核心自举与严格隔离不依赖 C 编译器。guard／quarantine 的阳性控制会故意使独立测试进程异常退出，由驱动核对退出状态；不把它们称为整个运行时的内存／竞态检测。

当前声明式用例位于 `cases/`、`nrt-cases/` 及核心仓 `examples/`。`.out` 在 CRLF→LF 后逐字节比较，保留尾部空白和换行；`.err`、`.json`、`.test`、`.testjson` 继续以首行 `exit=N` 和其余非空行的子串判据检查退出与诊断。新增或迁移用例还使用：

| 伴文件 | 当前原生含义 |
|---|---|
| `.argv` | 每行一个程序实参，放在源文件名之后；编译开关不能放在这里 |
| `.buildflags` | 每行一个编译开关，例如 `--toolchain`、`--leak-check`、`--static` |
| `.windows` / `.linux` | 仅适用于指定目标；另一目标明确记录跳过 |
| `.leakok` | 逐例说明有意保留对象的原因，不能作为关闭全局泄漏断言的开关 |

格式化、doc/init 使用 `fmt-native*.json`、`doc-native.json`、`init-native.json` 的冻结行为；分析、调试和协议夹具仍在 `ana/`、`debug/`、`lsp/`。`debug/vm/` 和 `lsp/zig/` 是历史参照，重录须显式使用归档实现。`golden/hello.native.txc.sha256` 仅锁定内部 IR 编码，由 `scripts/mcp-debug-e2e.py` 的 golden 场景经 MCP compile 回报的 `txc_sha256` 核对；`frozen/frontend-outputs.json` 冻结 lexer、parser、doc 与 spike/loads 诊断的输出及输入摘要（bootstrap 取 `bootstrap/ZIG_SEED` 提交的内容，夹具取工作树），由 `scripts/test-frontend-frozen.py` 核对；公开 `.txc` 产物和 GUI 回放锚已退场。`perf/behavior.sein` 是 D102 固定行为输入，样本和构建产物进入忽略的 `work/`。

## 历史门说明（截至 794d3bad，仅供追溯）

以下 Zig runner、公开 VM 与 GUI 回放说明不再是当前入口，相应实现和专属脚本已清退。

### 历史用例约定

每个用例 = 一个 `.tx` 源文件 + 一个伴文件（两者同基名，置于 `cases/` 或 `examples/`）：

| 伴文件 | 命令 | 通过条件 |
|---|---|---|
| `<name>.out` | `taixu run <name>.tx` | 退出码 0 且 stdout 与 `.out` 内容逐字节一致 |
| `<name>.err` | `taixu run <name>.tx` | 退出码为伴文件首行 `exit=<N>`；其余内容为 stderr（或 stdout）须包含的子串 |
| `<name>.json` | `taixu run --format=json <name>.tx` | 同 `.err`（诊断 JSON 走 stderr） |
| `<name>.test` | `taixu test <name>.tx` | 同 `.err`（0 全过 / 2 存在失败） |
| `<name>.testjson` | `taixu test --format=json <name>.tx` | 同 `.err`（检查 summary JSON 子串） |
| `<name>.argv` | （任意） | 每行一个参数，追加在源文件名之后传给程序 `main(args)` |
| `<name>.runflags` | （任意） | 每行一个 taixu 旗标，置于源文件名**之前**（V0.10-G2 帧级定格用例形态：`--replay=<file>`／`--frame=N`／`--out-dir=<dir>`；runner 于用例后清理 `--out-dir` 产物目录——用例自包含）。golden 锚＝`tests/golden/gui/gui-replay-*.sha256`（重放格式 v1 见 `src/gui/host.zig` 头注；帧号＝present 计数，用例须保证每帧可达 present） |

- 子进程工作目录 = 用例所在目录，用例可用相对路径引用随附数据文件；
- 无任何伴文件的 `.tx` 不构成用例（可作共享桩模块被其他用例 import）；
- 白名单守护（TEST-02）：`cases/guard-ok-*.tx`（必过）与 `cases/guard-bad-*.tx`
  （必拒、诊断可读）成对维护，与 S1 白名单（D3）同步演进；
- 金哈希回归（TEST-04）：`golden/hello.txc.sha256` 锁定 `examples/hello.tx` 的
  `.txc` sha256；格式有意变更时更新金值并全量重跑。

### 历史目录

```text
tests/
├── README.md          本说明
├── ana/               分析节黄金用例（第 6 轮 6d-3a1）：每个入口 <名>.tx 与期望结果 <名>.ana（`taixu dev-check --analysis`
                       的出现行，逐行人工核对过；标准库与 prelude 里的出现不比；类型标注的补面行带 guess），被导入的模块在 data/——
                       由 scripts/test-ana.py 比对；非 e2e 用例目录
├── cases/             端到端用例（含 S1 白名单守护正负用例）
├── debug/             调试协议基准（第 6 轮 6c-0）：cases.json 列用例，程序在本目录，Zig VM 的转录在 vm/——
                       由 scripts/debug-baseline.py 录制与复核（record／check），供原生调试构建对照；非 e2e 用例目录
├── golden/            字节码产物金哈希（TEST-04）＋gui/ 帧级定格三伴锚（BMP/sha256/投影）
└── lsp/               LSP 与 MCP 核心工具基准（第 6 轮 6d-0）：语料（lsp-app-main.tx、lsp-geom.tx 等）与 cases.json，
                       Zig 版的转录在 zig/——由 scripts/lsp-baseline.py 录制与复核；非 e2e_runner 用例目录，无伴本不进 e2e 集。
                       原生 `taixu lsp` 的会话门 scripts/lsp-e2e.py（6d-2 起）在临时目录自建夹具，不读这里
```
