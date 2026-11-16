# 太虚标准库

纯太虚（`.tx`）写成的标准库，以 `std/` 前缀导入：

```tx
import { join } from "std/string";
import { jsonParse, jsonStringify } from "std/json";
```

编译器按以下次序找标准库根：环境变量 `TAIXU_STDLIB`；编译器可执行文件同目录的 `stdlib/src`（发布包的布局）或
`taixu-stdlib/src`；从当前目录向上找 `taixu-stdlib/src`。在本仓里开发时可设 `TAIXU_STDLIB=<仓库>/stdlib/src`。

## 模块

| 模块 | 内容 |
|---|---|
| `collections`、`collect` | List 的高阶工具（map／filter／fold／range 等）与泛型集合算法（sortBy、binarySearch、堆） |
| `string`、`utf8`、`text`、`strconv` | 字符串工具（split／join／replace、Unicode 大小写与空白）、UTF-8 码点、文本工具、数值与字符串互转 |
| `fmt` | `{}` 占位格式化（对齐、宽度、精度、进制） |
| `bytes`、`base64`、`hash` | 字节缓冲与 hex、RFC 4648 base64、SHA-256 |
| `json`、`csv` | JSON（解析与规范输出，对象键保持插入顺序）、CSV（RFC 4180 子集） |
| `regexp` | 回溯正则（字符类、量词、锚、分组、捕获，带步数上限） |
| `compress`、`image` | DEFLATE／zlib／gzip 解压与存储式写出、PNG 解码（输出 RGBA8） |
| `math`、`random`、`sort` | 数学函数、可设种子的伪随机数、稳定排序 |
| `datetime`、`time` | 确定性的日期格式化、时间 |
| `os`、`path`、`fs`、`dir`、`file` | 环境变量与命令行参数、路径处理、文件系统、递归建目录、流式文件读写（数据为 Bytes） |
| `net` | TCP 与最小的明文 HTTP 客户端（字符串收发） |
| `url`、`ip` | URI 解析与相对引用（RFC 3986）、IPv4／IPv6 字面量 |
| `net_socket`、`deadline` | 非阻塞 Bytes 套接字（连接、读写、poll）、单调时钟与截止时间 |
| `http_client` | HTTP/1.1 客户端协议层：构造请求、增量解析响应（chunked 与 gzip 解码、字段与正文上限）、重定向判定；网络与 TLS 由调用方驱动 |
| `tls13_client`、`x509`、`x509_verify`、`pem` | 与套接字无关的 TLS 1.3 客户端、X.509 证书读取、按平台信任库验证服务器证书链、PEM 文本容器 |
| `gzip` | 有界的 gzip 解码（RFC 1952，多成员，校验 CRC32 与长度） |
| `crypto/aes`、`crypto/gcm`、`crypto/hmac`、`crypto/hkdf`、`crypto/sha384` | AES-128、AES-128-GCM、HMAC-SHA-256、HKDF-SHA-256、SHA-384 |
| `crypto/ecdh`、`crypto/p256`、`crypto/ecdsa_p256`、`crypto/ecdsa_p384`、`crypto/rsa` | X25519 与 P-256 密钥交换；ECDSA P-256／P-384 与 RSA 公钥验签 |
| `crypto/rand`、`crypto/subtle`、`crypto/der` | 操作系统密码学随机源、常量时间比较（等长时不按字节值提前返回）、有界 DER 读取 |
| `task`、`pool`、`events` | 带并发上限的任务组（TaskGroup）、分道执行池、事件订阅分发 |

Map 与 Set 由编译器自带，无需导入（原 `map_set` 模块已在语言规范 v0.53 退役）。把套接字、TLS 与 HTTP 协议层组装成完整 HTTPS 取页的写法，
见 `gui/examples/browser`（`connect.tx`、`transport.tx`、`fetch.tx`）。

失败一律经 `T | Error` 返回（例如 `fsRemove` 返回 `void | Error`），用 `?` 传播或 `catch` 处理。

平台相关的部分按目标拆成 `*_sys_windows.tx`（kernel32、ws2_32）、`*_sys_linux.tx`（系统调用，产物不依赖 libc）与
`*_sys_macos.tx`（libSystem）；同名模块在各目标上的导出面相同。

## 测试

```sh
taixu run --toolchain stdlib/verify.tx --compiler <taixu>
```

检查格式、逐个运行 `tests/` 下的用例与独立产物的期望输出，并做泄漏检查。完整门（`scripts/gate.tx`）也会运行它。
