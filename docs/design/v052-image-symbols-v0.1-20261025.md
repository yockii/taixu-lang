# 产物带函数符号（v0.1，2026-10-25）

来源：跟进第 24 条（工具链、macOS 适配）。系统的符号化工具（Linux 的 perf、gdb、addr2line，macOS 的崩溃报告、lldb、atos、
Instruments）都靠可执行文件里的符号表把地址归到函数。实测现状（2026-10-25）：

- **Linux**：产物没有节头表（`e_shnum` 为 0），也就没有 `.symtab`——工具一个函数名都看不到；
- **macOS**：符号表只有对象的全局符号（约 342 个），内核里不导出的 rtfn 例程（约 150 个）没有，崩溃报告把它们归到前一个全局
  符号；程序自己的函数叫 `f_0`、`f_1`（递归函数 `deep` 里崩溃，报告里是 `f_0`）；`LC_FUNCTION_STARTS` 同样只有全局符号；
- **Windows**：系统工具的符号来自 PDB，本批不涉及（另立跟进）。

而链接时这些信息都在：内核对象总带函数目录（TXFN：每个函数的名字与范围，`--map` 用它），程序函数的源码名在代码生成的上下文里。

## 一、他家怎么做

| 语言 | 缺省 | 去掉符号 |
|---|---|---|
| Go | 带 `.symtab`（ELF）／符号表（Mach-O），另有运行期用的 pclntab；函数名为 `包路径.函数` | `-ldflags=-s` |
| Rust | 带符号表（release 缺省只去调试信息，`strip = "debuginfo"`）；名字为改编名 | `strip = "symbols"` |
| Zig | 带符号表；名字为 `模块.函数` | `-fstrip` |
| C（gcc／clang＋ld） | 带 `.symtab`，`static` 函数为本地符号 | `strip`、`-s` |

都缺省带函数符号，另给去掉的开关；名字取“模块限定”的可读形（Go、Zig）或改编名（Rust、C++）。

## 二、做法

1. **名字**：程序函数为 `<模块>.<函数>`；模块取入口的末段名或导入说明符，去掉开头的 `./` 与结尾的 `.tx`（如 `s.deep`、
   `sub/m.f`、`std/os.getenv`），与宿主无关（路径规范形之后，见 v052-path-canonical）。内核例程用其链接名（如
   `__tx_retain`）。名字只用于符号表，链接仍按对象里的 `f_N`。
2. **来源**：程序函数取对象里的全局符号 `f_N` 的地址，配以代码生成上下文里的源码名；内核内部例程取内核对象的 TXFN 目录
   （名字与起点），按对象的 `.text` 放置结果换成映像地址。不依赖 `--map` 的整套函数帧收集（实测那一套连同渲染使编译器的
   构建慢约 24%；只取名字与起点的开销可忽略）。
3. **ELF（Linux 静态与动态产物）**：在文件末尾加节头表——描述已有内容的 `.text`、`.rodata`、`.data`、`.bss`（以及 TLS、
   动态产物的 `.interp`、`.dynamic`、`.dynsym`、`.dynstr`、`.got` 等），加 `.symtab`、`.strtab`、`.shstrtab`。每个函数一个
   `STT_FUNC`、`STB_LOCAL` 符号，大小到下一个函数的起点或 `.text` 末端。节头与符号表不在任何 `PT_LOAD` 里，装载与执行不受影响。
4. **Mach-O**：本地符号补齐 TXFN 里的内核内部例程，程序函数用可读名代替 `f_N`；`LC_FUNCTION_STARTS` 覆盖全部函数。符号表
   的分区（本地、外部定义、未定义）不变，`macho_check.py` 照常核对。
5. **`--strip`**：不写函数符号——ELF 不写节头与符号表，Mach-O 只留必需的 `__mh_execute_header` 与未定义符号、function starts
   照旧（它不含名字）。缺省带符号（同上表各家）。
6. **Windows**：不变；PDB（CodeView）另立跟进。

## 三、实施

- **记录**（`native_function_data.tx`）：每次 ELF／Mach-O 构建都开的轻量记录 `NativeSymbol`（可读名、链接名、起止），与 `--map`
  的帧收集无关。汇编器的函数起止条目（含缓冲里的 rtfn 例程、可移植与 A64 渲染器）改为总是记——它们只是元数据，不占机器字节；
  序言、尾声与帧事件仍只在 `--map` 时记。驱动在汇编程序前给出程序函数 id → `<模块>.<函数>`（`imageSymbolName`），链接前取走记录。
- **汇总**（`link_symbols.tx`）：按对象取函数并按地址排序。程序对象取记录；带函数目录（`.txfn`，内核对象总带）的取目录里的
  名字与起止——结构按完整解码（`native_function_object.tx`、`native_function_codec.tx`）的同一套规则与次序核对（节重复、
  代码节号、版本、代码长、计数、整数溢出、截断、UTF-8、函数范围、尾随字节），拒绝原因同名，只是不重算代码摘要、不校验帧事实
  （那归 `--map`）；其余对象取符号表里定义在已放置代码节上的 `STT_FUNC`。记录或目录不合规时链接失败：此前不带 `--map` 的构建
  不读目录，坏目录被忽略；现在任何构建都拒绝——畸形的目标文件按畸形处理（同主流链接器），不悄悄写出错名。目录缺失（外来对象）
  不算错，退回符号表。
- **ELF**（`link_elf_objects.tx`）：节头按地址列出 `.text`、`.rodata`（动态产物另有 `.interp`、`.hash`、`.dynsym`、`.dynstr`、
  `.rela.dyn`）、`.data`（及 `.dynamic`、`.got`）、`.bss`，再是 `.symtab`、`.strtab`、`.shstrtab`。`.symtab` 全为本地符号：先是
  函数（`STT_FUNC`，带大小），再是函数表以外的链接全局符号——`.text` 里的记 `STT_FUNC`、大小 0（手写的运行时例程，如
  `__tx_linux_memcpy`，没有目录项；同 Go 的链接器把代码里的符号都记为函数），其余 `STT_NOTYPE`，绝对值记 `SHN_ABS`。可装载范围
  里唯一的变化是 ELF 头的 `e_shoff`、`e_shentsize`、`e_shnum`、`e_shstrndx`。TLS 模板在 `.rodata` 里由 `PT_TLS` 描述，不另立
  `.tdata`。
- **Mach-O**（`link_macho.tx`）：本地符号先是全部函数，再是函数表未列出的链接全局符号（同名同址的不重复，程序函数的 `f_N` 因此
  只以可读名出现）；名字不加前导下划线（同既有本地符号；`nm`、`atos` 照原样显示）。function starts 取全局符号与全部函数的起点。
- **`--strip`**（`taixu build`）：ELF 不写节头与符号表（`e_shnum` 为 0），Mach-O 不写本地符号；function starts 与代码、数据不变。

未覆盖：没有全局标号的手写内部例程（目录与符号表里都没有）归到前一个符号；Windows 的 PDB 另立跟进。

## 四、验证

- 映像的可装载部分逐字节不变：ELF 的全部 `PT_LOAD` 段、Mach-O 的 `__TEXT`／`__DATA_CONST`／`__DATA` 段与基线相同（只多了文件
  末尾的节头与符号表、`__LINKEDIT` 里的符号与签名）。
- 独立核对：ELF 由 Python 解析节头与 `.symtab`（每个函数恰有一个符号、值落在 `.text` 内、大小不越界、名字与 `--map` 一致）；
  Mach-O 由 `macho_check.py`（本地符号覆盖全部 function starts）。
- 端到端：测试机上 `atos` 把递归函数里的地址解析成 `s.deep`；Linux 上按 `.symtab` 把同一地址解析成同名。
- `--strip`：ELF 没有节头，Mach-O 没有函数符号，可装载部分与不带 `--strip` 的相同。
