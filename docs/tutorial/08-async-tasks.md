# 08 异步与任务

**本章你将学到**：async/await 着色；调用即排队与确定性 FIFO 调度；main 里直接
await 与 `detach`；TaskGroup 并发上限；定时器与超时、组级取消。
（样例 [samples/08-tasks.tx](samples/08-tasks.tx)、[samples/08-timers.tx](samples/08-timers.tx)）

## 着色规则

```tx
async function fetchScore(id: int): int {   // 声明写成功侧 T（写 T | Error 也得 Future<T>）
    return id * 10;
}
function main(args: []string): int {
    const a = fetchScore(1);                // 调用即排队 → a: Future<int>
    const va = await a catch -1;            // await 产出 T | Error；main 里可以直接 await
    return 0;
}
```

- `await` 写在 **async 函数/async 箭头体内**，或写在**同步入口**——`main`、`test`
  块、直接写在 `threadSpawn(…)` 实参里的箭头——里；普通同步函数里是编译错误 3024
  （要等待就把它声明成 async），嵌套的普通箭头也不继承外层的资格；操作数须是
  `Future<T>`（3025）；
- 任务内 `err(...)` 原样穿透 await（`T | Error`）——`?`/`catch` 照常作用；
- `await` 的结果未就绪时任务挂起，就绪后从挂起处继续；同一个 `Future`
  可以多次 `await`，每次得同一结果。

## 确定性全序

调用 async 函数＝把新任务放进 FIFO 就绪队列**即返回**（调用＝排队，不急切执行）。
任务要等到有人 `await` 时才按先来后到执行：main 里 `await` 一个还没完成的任务时，
就在原地运行调度泵，按 FIFO 跑就绪任务，直到它完成——启动序、完成序、唤醒序都
可以静态推理，同一程序每次输出相同：

```text
main-done        ← main 的 print 先出现（此时任务只是排着队）
joined:30        ← main 里 await 时，泵按创建顺序执行任务
done:30
done:40
done:50
```

**main 返回即结束程序**：还没完成的任务按取消处理（挂起中的先执行已登记的
`defer`），没有 join 的线程也一并结束——所以要等的任务一定要在 main 里 `await`。
如果剩下的任务互相等待、谁也推进不了（await 等待环），程序以运行期错误 4014
（`sched_deadlock`）结束，退出码 3。

## 发后即忘

把 async 调用的结果当语句丢掉（`job();`）是编译错误 3005——多半是忘了 `await`。
真的不需要结果时写 `detach job();`：任务照常排队，但 main 返回时若还没完成同样被
取消。以 `Error` 结束却没人 `await` 的任务静默，不影响退出码；要知道它的错误，就
`await` 它。

## TaskGroup（std/task 库层）

```tx
const tg = taskGroupNew<int>();    // 无参，须写结果类型
taskGroupAdd(tg, fetchScore(3));   // 创建任务后把 Future 入组
const rs = taskGroupDrain(tg, 2);  // limit=2：同时至多 2 个在跑，空出一个补一个
```

`taskGroupDrain` 本身是 async 函数，`await` 它得到各任务结果的
`List<int>`。TaskGroup 与 `timeout` 对结果类型泛型（`TaskGroup<T>`、`timeout<T>`）。要分道优先级用
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
const v = await timeout(slow, 0) catch (e) {   // 超时包装（泛型 timeout<T>）
    print("timeout err: " + e.message);        // 0ms 立即判超时 → err("timeout")
};

const tg = taskGroupNew<int>();
const slot = taskGroupAdd(tg, slow);           // 返回组内登记序槽位号
taskGroupCancel(tg) catch (e) { ... };         // 组级取消：按登记序逐个 taskCancel
const out = await slow catch (e) { ... };      // await 得 err("task cancelled")
```

- `sleepMs(ms): Future<void>`——内建；到期由调度泵按 **deadline 升序、同刻
  按登记序**唤醒；负数得 `err("sleep ms < 0")`；
- 就绪任务都跑完、只剩定时器在等时，泵会真的等到最早的到期时刻；main
  返回时还没到期的定时器直接丢弃，不会拖住退出；
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
