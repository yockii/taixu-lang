# 08 异步与任务

**本章你将学到**：async/await 着色；确定性 FIFO 调度；TaskGroup 并发上限；
定时器与超时、组级取消。
（样例 [samples/08-tasks.tx](samples/08-tasks.tx)、[samples/08-timers.tx](samples/08-timers.tx)）

## 着色规则

```tx
async function fetchScore(id: int): int {   // 声明写成功侧 T
    return id * 10;
}
const a = fetchScore(1);                    // 调用即创建任务 → a: Future<int>
const va = (await a)?;                      // await 产出 T | Error
```

- `await` **只在 async 函数/async 箭头体内**合法（普通函数、main 内是编译
  错误 3024）；操作数须是 `Future<T>`（3025）；
- 任务内 `err(...)` 原样穿透 await（`T | Error`）——`?`/`catch` 照常作用；
- `await` 的结果未就绪时任务挂起，就绪后从挂起处继续；同一个 `Future`
  可以多次 `await`，每次得同一结果。

## 确定性全序

调用 async 函数＝把新任务放进 FIFO 就绪队列**即返回**（不急切执行）。main
同步体跑完后，调度泵按 FIFO 跑任务——启动序、完成序、唤醒序都可以静态
推理，同一程序每次输出相同：

```text
main-done        ← main 的 print 先出现
joined:30        ← 泵中任务按创建顺序执行
done:30
done:40
done:50
```

main 返回后，已创建的任务照样由泵跑完（发后即忘的任务也会执行）。如果
剩下的任务互相等待、谁也推进不了（await 等待环），程序以运行期错误 4014
（`sched_deadlock`）结束，退出码 3。

## TaskGroup（std/task 库层）

```tx
const tg = taskGroupNew();
taskGroupAdd(tg, fetchScore(3));   // 创建任务后把 Future 入组
const rs = taskGroupDrain(tg, 2);  // limit=2：同时至多 2 个在跑，空出一个补一个
```

`taskGroupDrain` 本身是 async 函数，`await` 它得到各任务结果的
`List<int>`。目前 TaskGroup 与 `timeout` 只收 `Future<int>`。要分道优先级用
`std/pool`（道＝优先级，任务之间不抢占）。取消：`taskCancel(fut)` 立即
清理未完成的任务，`await` 它得 `err("task cancelled")`；任务体内用
`taskCancelled()` 做协作检查。

实跑输出（08-tasks 样例）：

```text
main-done
joined:30
done:30
done:40
done:50
```

## 定时器、超时与组级取消

```tx
async function sleeper(tag: string): int {
    await sleepMs(0) catch (e) { print(tag + " sleep err"); return -1; };
    print("woke-" + tag);           // 两任务同刻登记：唤醒序＝登记序
    return 0;
}

const slow = stalled("loser");                 // 内部 await sleepMs(60000)
const v = await timeout(slow, 0) catch (e) {   // 超时包装（Future<int>）
    print("timeout err: " + e.message);        // 0ms 立即判超时 → err("timeout")
};

const tg = taskGroupNew();
const slot = taskGroupAdd(tg, slow);           // 返回组内登记序槽位号
taskGroupCancel(tg) catch (e) { ... };         // 组级取消：按登记序逐个 taskCancel
const out = await slow catch (e) { ... };      // await 得 err("task cancelled")
```

- `sleepMs(ms): Future<void>`——内建；到期由调度泵按 **deadline 升序、同刻
  按登记序**唤醒；负数得 `err("sleep ms < 0")`；
- 就绪任务都跑完、只剩定时器在等时，泵会真的等到最早的到期时刻；程序
  结束时已经没有任务在等的定时器直接丢弃，不会拖住退出；
- `timeout(f, ms)`——std/task 提供：超时得 `err("timeout")`，被超时的任务
  随即 `taskCancel`；
- `taskGroupCancel(tg): void | Error`——组级取消；取消后组内不再有未决任务。

想让测试输出稳定，只依赖两种确定的定时器：`sleepMs(0)`（同刻登记序）与
远未到期、只作为被超时或取消一方的长定时器——样例 08-timers 就是这样写的。

实跑输出（08-timers 样例）：

```text
added slot:0
cancelled: task cancelled
woke-a
woke-b
timeout err: timeout
```
