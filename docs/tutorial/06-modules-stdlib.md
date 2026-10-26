# 06 模块与标准库

**本章你将学到**：import 与模块寻径；编译器自带的 Map/Set；标准库的面貌。
（样例 [samples/06-modules.tx](samples/06-modules.tx)）

## import 两种来源

```tx
import { helper } from "./util";       // 相对路径（同项目内，省略 .tx）
import { pathJoin } from "std/path";   // 标准库，随包零配置
```

文件即模块。导出用 `export` 前缀（`export function helper() { ... }`）；
未 `export` 的符号模块私有。导入不存在的模块或未导出的符号是编译错误
3008，循环导入是编译错误 3009。导入写法须与磁盘上的文件名逐字节一致
（大小写也算）——在 Windows、macOS 上能打开、到 Linux 上就打不开的写法，
三个平台一律报 3008。

## Map 与 Set：编译器自带

`Map<K, V>` 与 `Set<T>` 由编译器自带的预置库提供，**不用 import**：

```tx
const m: Map<string, int> = mapNew();
m.set("tai", 30);                  // ≡ mapSet(m, "tai", 30)
const v = m.get("none") catch -1;  // mapGet 得 V?，查无此键为 None
for (k, v in m) { ... }            // 插入序
```

操作：`mapNew/mapSet/mapGet/mapHas/mapRemove/mapLen/mapKeys/mapValues/
mapClear` 与 `setNew/setAdd/setHas/setRemove/setLen/setToList/setClear`，
都能用方法写法。键须可哈希：`int`、`string`、`bool`、`Bytes`、定宽整数
可直接用（`float` 不行）；自定义类型写一个 `pointHash(p: Point): int` 即可
作键。迭代顺序恒为插入顺序，覆盖已有键不改变位置。Map/Set 是句柄语义，
赋值与传参共享同一张表。

## 标准库（随包）

`std/` 前缀导入。全部模块与关键导出见[速查手册](../quickref.md)第 5 节，
这里列常用的几组：

| 模块 | 内容 | 确定性 |
|---|---|---|
| `std/path` | pathJoin/pathBase/pathDir/pathExt/pathNormalize | 纯函数（同入参恒同出参） |
| `std/string`、`std/strconv`、`std/fmt` | 字符串工具、数字与字符串互转、格式化 | 纯函数 |
| `std/json` | `jsonParse`（`Ref<Json> \| Error`）／`jsonStringify` | 纯函数；对象键序＝插入序 |
| `std/fs`、`std/file` | 整文件读写、按行读、目录操作；流式文件读写 | 依赖宿主文件系统 |
| `std/random` | 种子化伪随机（同种子同序列） | 种子面可断言 |
| `std/time`、`std/datetime` | 当前时间；日期时间格式化 | 格式化可断言，墙钟不可 |
| `std/task`、`std/pool` | TaskGroup 并发上限、超时、分道执行池（08 章） | 调度序确定 |
| `std/events` | 事件订阅与派发（07 章） | 注册序＝派发序 |

失败一律经 `T | Error` 返回，不用 0／−1 之类的返回码。导出的完整签名以
`taixu doc` 的输出或标准库源码为准（例如 `taixu doc <标准库目录>/path.tx`）。

实跑输出（06 样例）：

```text
docs/tutorial
c.tx ext=.tx
has-tai:true len:2
tai=30
xu=20
miss=-1
{"name":"太虚","tags":[1,2]}
```
