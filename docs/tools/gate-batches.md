# 固定快照批次验证

`scripts/gate_batch.tx` 将多个宿主的验证合成一份报告。它适合已准备好的、内容一致的 Git 快照。
先按源码构建说明生成内核和编译器；工作树与索引须一致，没有未跟踪源码，16 份内核会另外计算摘要。
运行前清除继承的 `TAIXU_*` 测试开关。默认并发为 1，同一 `resource` 的宿主始终按清单顺序执行。

```sh
taixu run --toolchain scripts/gate_batch.tx --manifest batch.json --work work/batches
```

清单中的程序与参数属于操作者代码。编排器直接传参数数组，不替调用者解释 shell 语法。
`exe` 使用绝对路径；底层进程接口不搜索 PATH。
WSL、SSH 或容器可作为 runner；传输层若需要处理含空格的远端路径，应使用能正确引用参数的适配程序。
每个 runner 最终调用本宿主的 `scripts/gate_batch_worker.tx`，编排器会追加阶段、步骤、工作目录、运行身份与预期宿主参数。

## 开发批次

以下为三宿主示例，路径按实际安装修改。Windows 与本机 WSL 共享 `local` 资源组，macOS 使用另一组。

```json
{
  "schema": "gate-batch/1",
  "mode": "development",
  "deduplicate_cross_products": true,
  "steps": ["cli", "program-regalloc", "cross-products"],
  "hosts": [
    {
      "id": "windows", "target": "x86_64-windows", "resource": "local",
      "exe": "C:/tx/taixu.exe",
      "args": ["run", "--toolchain", "C:/tx/scripts/gate_batch_worker.tx", "--root", "C:/tx", "--compiler", "C:/tx/work/bin/taixu.exe"],
      "cwd": "C:/tx", "work": "C:/tx/work/batch-host"
    },
    {
      "id": "linux", "target": "x86_64-linux", "resource": "local",
      "exe": "C:/Windows/System32/wsl.exe",
      "args": ["-e", "/opt/tx/taixu", "run", "--toolchain", "/opt/tx/scripts/gate_batch_worker.tx", "--root", "/opt/tx", "--compiler", "/opt/tx/work/bin/taixu"],
      "cwd": "C:/tx", "work": "/opt/tx/work/batch-host"
    },
    {
      "id": "macos", "target": "aarch64-macos", "resource": "macos",
      "exe": "C:/Windows/System32/OpenSSH/ssh.exe",
      "args": ["mac", "/opt/tx/taixu", "run", "--toolchain", "/opt/tx/scripts/gate_batch_worker.tx", "--root", "/opt/tx", "--compiler", "/opt/tx/work/bin/taixu"],
      "cwd": "C:/tx", "work": "/opt/tx/work/batch-host"
    }
  ]
}
```

每个平台先实际执行所选的本机步骤。只有 `cross-products` 可以共享：源码、标准库、GUI 和内核身份必须相同，
且每个平台实际生成的共同 Windows 编译器必须逐字节一致。负责共享的平台构建全部八目标，包含自己的目标；
其他平台的七个交叉目标在报告中逐项引用该来源。本机执行、平台接口、GUI、资源限制等检查不共享。
输入或见证不同的组分别执行。共享构建失败时会换同组平台补跑，所有失败仍保留，整批仍为 FAIL。

## 里程碑

将 `mode` 设为 `milestone`、`deduplicate_cross_products` 设为 false、`steps` 设为空数组。
此模式至少需要 Windows x64、Linux x64、macOS arm64，每个平台独立跑完整 `gate.tx`，不使用开发批次去重。

## 报告

每次运行创建新目录，写入 `report.json` 与逐阶段日志；批次层不自动删除历史运行。
`attempts` 保留每次真实进程的退出码、耗时、收据和原 gate／cross 报告；`cross_coverage` 区分实际执行、共享构建及未覆盖目标。
开发批次不能称为完整发布验证，共同编译器见证也不能代替三端自举或全部产物逐项一致检查。

单宿主 `gate.tx --report <文件>` 可同时写一份报告，供外部编排读取。
`test_cross_products.tx --include-host --report <文件>` 会构建全八目标并输出逐目标语料计数与报告摘要；默认仍只构建宿主外目标。
