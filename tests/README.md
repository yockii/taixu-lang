# tests/ —— 端到端用例

当前完整入口为 `taixu run --toolchain scripts/gate.tx --compiler <原生 taixu>`；单独运行 CLI 或独立产物门可用 `scripts/test-native-cli.py`、`scripts/test-nrt-products.py`。`--quick` 不代替完整验收。正式性能与 OS 隔离另跑，见 6e 设计与结果。

`runtime-functions` 复用 `runtime-security/` 中独立 C／汇编探针，验证实际发射的 RC、对象表 OOM 恢复、管道状态与 FFI 回调。测试机需要 GCC／Clang 兼容的 C 编译器，可用 `TAIXU_TEST_CC` 指定可执行文件，或从 PATH 查找 cc/gcc/clang；缺失会使该门失败。这只是独立验证依赖，核心自举与严格隔离不依赖 C 编译器。guard／quarantine 的阳性控制会故意使独立测试进程异常退出，由驱动核对退出状态；不把它们称为整个运行时的内存／竞态检测。

当前声明式用例位于 `cases/`、`nrt-cases/` 及核心仓 `examples/`。`.out` 在 CRLF→LF 后逐字节比较，保留尾部空白和换行；`.err`、`.json`、`.test`、`.testjson` 继续以首行 `exit=N` 和其余非空行的子串判据检查退出与诊断。新增或迁移用例还使用：

| 伴文件 | 当前原生含义 |
|---|---|
| `.argv` | 每行一个程序实参，放在源文件名之后；编译开关不能放在这里 |
| `.buildflags` | 每行一个编译开关，例如 `--toolchain`、`--leak-check`、`--static` |
| `.windows` / `.linux` | 仅适用于指定目标；另一目标明确记录跳过 |
| `.leakok` | 逐例说明有意保留对象的原因，不能作为关闭全局泄漏断言的开关 |

格式化、doc/init 使用 `fmt-native*.json`、`doc-native.json`、`init-native.json` 的冻结行为；分析、调试和协议夹具仍在 `ana/`、`debug/`、`lsp/`。`debug/vm/` 是历史参照，重录须显式使用归档实现。`golden/hello.native.txc.sha256` 仅锁定内部 IR 编码，由 `scripts/mcp-debug-e2e.py` 的 golden 场景经 MCP compile 回报的 `txc_sha256` 核对；`frozen/frontend-outputs.json` 冻结 lexer、parser、doc 与 spike/loads 诊断的输出及输入摘要（夹具取工作树；开发仓另固定一个提交的 bootstrap/ 作输入），由 `scripts/test-frontend-frozen.py` 核对；公开 `.txc` 产物和 GUI 回放锚已退场。`perf/behavior.sein` 是固定行为输入，样本和构建产物进入忽略的 `work/`。

