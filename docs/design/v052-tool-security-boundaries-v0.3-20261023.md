# 工具输入、执行与后代回收边界 v0.3（2026-10-23）

保留v0.2。信任根、资源限额、非阻塞父→worker管道、FFI及RC规则不变。本版补充实际隔离复核发现的Linux后代回收缺口，不引入新的公开语言内建。

## 实际问题与最小修正

0.2.24-dev中间候选的53项服务功能检查通过后，独立检查`/proc`仍发现两个已终止的调试产物进程，状态Z、PPid=1。它们对应两次“活调试会话＋编译超时杀worker”控制；隔离容器的PID1是普通等待程序，不代替init收割。进程组终止解决存活问题，回收worker本身不能回收其被重新收养的后代。这是确认的资源回收缺口，不是UAF或执行越权证据；原53项结果不证明无进程残留。

MCP的Linux父进程在接收请求前设置并查询`PR_SET/GET_CHILD_SUBREAPER`，并将本进程SIGCHLD设为默认，防止继承SIG_IGN／SA_NOCLDWAIT而丢失显式wait所需状态。只影响本进程的后代，子进程不继承subreaper设置；普通CLI不启用。失败明确退出64，不降级成未经验证的回收模式。Windows继续使用Job关闭与已有worker等待。

通用`procWait`仍拥有受管理worker退出码。回收器先以`waitid(P_ALL, WEXITED|WNOHANG|WNOWAIT)`窥视，不抢先消费任何受管理worker；只对已退出、非受管理的收养后代调用指定PID的非阻塞wait4。每轮最多64个。READY增加可信自进程PID，父进程记录当前及待回收worker PID；尚未获得PID的存活worker会阻止不加区分的wait，避免退出码被偷取。该内部协议没有公开兼容别名。

父循环在空闲时继续轮询worker与后代回收。终止时的worker等待及后代收割共享250ms清理预算；正常关闭也有界排空已退出后代。没有受管理worker但仍有收养后代时，不无限新建worker；以cleanup_pending和机器可读进程清理状态报告。已有两个待清理worker／产物上限保留。系统不可中断I/O、受信程序刻意脱离进程组及服务器自身被外部强杀，仍不能保证即时或完整收尾；本修正不构成OS沙箱。

## 增量验收门

1. 现有toolchain-contract增加独立reaper探针：子进程确实进入可wait的退出状态后，回收器跳过其受管理PID，随后procWait仍取得精确退出码41；之后无待回收子进程。Linux的观察不提前wait消耗退出状态。
2. Linux头文件独立检查syscall号、prctl／waitid常量和siginfo_t大小／PID偏移；加入现有runtime-functions，避免只对照手写常量。Windows不使用该Linux布局。
3. 服务原有“活调试＋超时＋恢复”控制捕获实际worker和调试后代身份，确认两者均已消失（僵尸也算未清理），再检查新代可用。不能只核对reaped或文件删除。
4. 在同样无init、禁网、只读根、无宿主挂载、低权限及资源受限容器复跑；旧候选负控制保留僵尸证据，新候选不得残留活进程、僵尸或产物。重置测试容器只为清除先前已归PID1的旧僵尸，不用增加init掩盖产品缺口。
5. 正常／leak／debug运行时语义、完整35组双宿主门、自举与双向交叉、严格隔离及全源码／双内核同源继续执行。旧188源码快照验收不代表本版新源码验收。

依据：[Linux subreaper](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)、[waitid／WNOWAIT](https://man7.org/linux/man-pages/man2/waitpid.2.html)。固定结构布局以实际x86_64 Linux C头文件校验，不由文档描述猜测。

## 状态

实现及增量回归完成；Windows完整35组门通过；Linux原门34组通过，唯一失败MCP组修正旧夹具后完整220项复验通过，原FAIL报告保留。严格隔离、自举和实际双向交叉通过。当前源码190份、双内核同源；独立ACL232处无任务SID残留。准确候选hash、运行时／合同数量、隔离31项控制、故障注入校准、失败历史及未执行项见安全复审实际验证状态。旧53项或188源码结果保留为历史，不能代替当前验收。
