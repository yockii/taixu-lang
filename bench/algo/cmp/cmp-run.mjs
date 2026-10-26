#!/usr/bin/env node
// cmp-run.mjs —— 九实现主流对照计时驱动器（V0.19-S1；D50 §一；V0.20-S1/VM-16 方法学升级；
// V0.21-S1/VM-18 扩 Zig/Java 画现代语言带——D56 §〇 性能门对照基准；
// V0.30-S1/TOOL-32 增性能门 v2 分负载目标带机械判定——D71 §二-3）。
// 用法：node bench/algo/cmp/cmp-run.mjs [--n=5] [--taixu=zig-out-rel/bin/taixu.exe] [--save=<path>]
//   --save 未给时默认写 bench/algo/cmp/samples-latest.json（全样本留存）。
// 测量方法学（V0.20-S1，VM-16——V0.19 验收外部三审采纳固化为机械面）：
//   ①轮次交错采样：按"轮"轮换实现（round r 内依 impl 表轮转各采一次），
//     非整段连跑——时钟漂移/热节流等慢变量对九实现同步作用，不偏袒任一方；
//   ②全样本留存：每实现每负载全部样本（按轮序）落 JSON，非仅中位；
//   ③版本与二进制哈希随表：taixu --version 输出＋各实现二进制 sha256 入
//     JSON 与 stderr 表头——跨窗对照可溯源（V0.19 跨窗异常教训：无哈希
//     即不可对账）；
//   ④启动与稳态分离：round 0 采样（进程冷启＋页缓存冷）单列 first，
//     稳态中位＝rounds ≥1；表内主列＝稳态中位，比率按稳态口径。
// 口径＝bench/algo/run.mjs 同款：spawnSync 整进程 wall-clock、校验和硬校验
//（九实现×九负载逐字节一致为门槛——不一致即 fail 不落表）。
// 构建命令（首次）：
//   gcc -O2  -o bench/algo/cmp/cmp.exe    bench/algo/cmp/cmp.c
//   gcc -O2 -fno-tree-vectorize -o bench/algo/cmp/cmp_scalar.exe bench/algo/cmp/cmp.c
//   go build -o bench/algo/cmp/cmp-go.exe bench/algo/cmp/cmp.go
//   rustc -O -o bench/algo/cmp/cmp-rs.exe bench/algo/cmp/cmp.rs
//   zig build-exe -O ReleaseFast -femit-bin=bench/algo/cmp/cmp-zig.exe bench/algo/cmp/cmp.zig
//   javac -d bench/algo/cmp/classes bench/algo/cmp/Cmp.java
//   （Python 免构建）
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";

const args = process.argv.slice(2);
let n = 5;
let taixu = "zig-out-rel/bin/taixu.exe";
let save = "";
let pgo = false;
for (const a of args) {
  if (a.startsWith("--n=")) n = parseInt(a.slice(4));
  else if (a.startsWith("--taixu=")) taixu = a.slice(8);
  else if (a.startsWith("--save=")) save = a.slice(7);
  else if (a === "--pgo") pgo = true; // V0.25-P1：AOT 生成走 --pgo 两段式（先采画像后特化编译）
}
if (!existsSync(taixu)) {
  console.error(`taixu 不存在: ${taixu}`);
  process.exit(1);
}
if (!save) save = pgo ? "bench/algo/cmp/samples-latest-pgo.json" : "bench/algo/cmp/samples-latest.json";

const loads = [
  "fib-rec", "fib-iter", "qsort", "sieve", "mandelbrot",
  "matmul", "nbody", "string-build-find", "json-roundtrip",
];

// —— 性能门 v2：分负载目标带表（V0.30-S1/TOOL-32；D71 §一——带宽＝归因链在案，
// 不按现值贴线；升带须归因新证据＋用户确认——带复核条款每两关口一次）——
const BANDS = {
  "fib-rec":           { cls: "递归调用密集",  beta: 5.0,  sprint: 3.0 },
  "fib-iter":          { cls: "整数核",       beta: 4.0,  sprint: 2.0 },
  "qsort":             { cls: "容器访问",     beta: 7.0,  sprint: 3.0 },
  "sieve":             { cls: "容器访问",     beta: 7.0,  sprint: 3.0 },
  "mandelbrot":        { cls: "数值核-标量",   beta: 4.0,  sprint: 2.0 },
  "matmul":            { cls: "数值核-向量化", beta: 4.0,  sprint: 1.5 },
  "nbody":             { cls: "超越函数",     beta: 15.0, sprint: 6.0 },
  "string-build-find": { cls: "分配密集",     beta: 4.0,  sprint: 1.2 },
  "json-roundtrip":    { cls: "容器访问",     beta: 7.0,  sprint: 3.0 },
};
const stdlibRoot = existsSync("../taixu-stdlib/src/json.tx")
  ? resolve("../taixu-stdlib/src")
  : null;
const env = stdlibRoot ? { TAIXU_STDLIB: stdlibRoot } : {};

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}
function sha256Of(path) {
  const buf = readFileSync(path);
  return createHash("sha256").update(buf).digest("hex").slice(0, 16);
}
function versionOf(cmd, argv) {
  const r = spawnSync(cmd, argv, { encoding: "utf8", timeout: 30000 });
  return r.status === 0 ? r.stdout.trim().split(/\r?\n/)[0] : `exit ${r.status}`;
}
function timed(cmd, argv, extraEnv) {
  const t0 = performance.now();
  const r = spawnSync(cmd, argv, { encoding: "utf8", timeout: 300000, env: { ...process.env, ...extraEnv } });
  const ms = performance.now() - t0;
  if (r.status !== 0) return { err: `exit ${r.status}: ${(r.stderr || "").slice(0, 200)}` };
  return { ms, stdout: r.stdout.trim() };
}

// —— 溯源面：版本＋二进制哈希（VM-16 ③）——
const provenance = {
  generated_at: new Date().toISOString(),
  taixu_version: versionOf(taixu, ["--version"]),
  taixu_path: taixu,
  python_version: versionOf("python", ["--version"]),
  javac_version: versionOf("javac", ["-version"]),
  zig_version: versionOf("zig", ["version"]),
  binaries: {},
};
for (const [k, p] of Object.entries({
  taixu, c: "bench/algo/cmp/cmp.exe", c_scalar: "bench/algo/cmp/cmp_scalar.exe",
  go: "bench/algo/cmp/cmp-go.exe", rust: "bench/algo/cmp/cmp-rs.exe",
  zig: "bench/algo/cmp/cmp-zig.exe",
})) {
  provenance.binaries[k] = existsSync(p)
    ? `${p} sha256:${sha256Of(p)}`
    : `${p} 缺失`;
}
{
  const cls = "bench/algo/cmp/classes/Cmp.class";
  provenance.binaries.java = existsSync(cls)
    ? `${cls} sha256:${sha256Of(cls)}`
    : `${cls} 缺失`;
}
console.error(`[溯源] taixu: ${provenance.taixu_version}`);
console.error(`[溯源] python: ${provenance.python_version}`);
for (const [k, v] of Object.entries(provenance.binaries)) console.error(`[溯源] ${k}: ${v}`);

// 校验和基准＝taixu 双路（interp/AOT 一致为前提）。
const out_dir = mkdtempSync(join(tmpdir(), "taixu-cmp-"));
const rows = [];
const samples = { provenance, loads: {} };
console.log("| 负载 | interp | AOT | C -O2 | C 标量 | Go | Rust | Zig | Java | CPython | AOT/C | AOT/C标量 | AOT/Zig | AOT/Java | AOT/Py |");
console.log("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|");

for (const load of loads) {
  const path = join("bench/algo", `${load}.tx`);
  const ref = timed(taixu, ["run", "--frontend=zig", path], env);
  if (ref.err) { console.error(`${load}: interp 失败 ${ref.err}`); process.exit(1); }
  const aotDir = join(out_dir, load);
  let pgoArg = [];
  if (pgo) {
    // 段①画像（相对路径——--profile-out 经 Dir.cwd().writeFile 落盘）＋段②特化编译。
    const prof = `tmp-cmp-pgo-${load}.txp`;
    const pr = timed(taixu, ["run", "--frontend=zig", path, `--profile-out=${prof}`], env);
    if (pr.err || pr.stdout !== ref.stdout) { console.error(`${load}: 画像采集失败 ${pr.err}`); process.exit(1); }
    pgoArg = [`--pgo=${prof}`];
    try { rmSync(prof); } catch {} // 画像消费毕即清（临时改动退场纪律）
  }
  // 柱三缺省翻转（V0.49）后镜像路对 SIMD 面结构化拒绝（E006 simd_loops/E008 avx2）
  // ——该面生产产物仍由 zig 臂承担（两阀＝在册永久逃生舱）。带窗对被拒负载用
  // TAIXU_EMIT_MIRROR=0 阀重生成并逐负载记录 aot_route（非静默重基；带表不动）。
  let gen = timed(taixu, ["aot", path, ...pgoArg, "--out", aotDir], env);
  let aotRoute = "emit_mirror_default";
  if (gen.err && /EMITMIRROR-REJECT/.test(gen.err)) {
    console.error(`[阀回退] ${load}: 镜像路结构化拒绝（${gen.err.slice(0, 120)}）——TAIXU_EMIT_MIRROR=0 重生成（aot_route 在案）`);
    gen = timed(taixu, ["aot", path, ...pgoArg, "--out", aotDir], { ...env, TAIXU_EMIT_MIRROR: "0" });
    aotRoute = "emit_mirror=0_valve";
  }
  if (gen.err) { console.error(`${load}: aot 生成失败 ${gen.err}`); process.exit(1); }
  const exe = join(aotDir, `${load}.exe`);
  const checksum = timed(exe, [], {});
  if (checksum.err || checksum.stdout !== ref.stdout) {
    console.error(`${load}: 校验和双路不一致（${checksum.err || checksum.stdout} vs ${ref.stdout}）`);
    process.exit(1);
  }

  const impls = [
    ["interp", () => timed(taixu, ["run", "--frontend=zig", path], env)],
    ["aot", () => timed(exe, [], {})],
    ["c", () => timed("bench/algo/cmp/cmp.exe", [load], {})],
    ["c_scalar", () => timed("bench/algo/cmp/cmp_scalar.exe", [load], {})],
    ["go", () => timed("bench/algo/cmp/cmp-go.exe", [load], {})],
    ["rust", () => timed("bench/algo/cmp/cmp-rs.exe", [load], {})],
    ["zig", () => timed("bench/algo/cmp/cmp-zig.exe", [load], {})],
    ["java", () => timed("java", ["-cp", "bench/algo/cmp/classes", "Cmp", load], {})],
    ["python", () => timed("python", ["bench/algo/cmp/cmp.py", load], {})],
  ];
  // ①交错采样：samples[impl]＝按轮序全样本——round r 依 impl 表轮转各采一次。
  const samplesByImpl = {};
  for (const [name] of impls) samplesByImpl[name] = [];
  for (let r = 0; r < n; r++) {
    for (const [name, fn] of impls) {
      const res = fn();
      if (res.err) { console.error(`${load}/${name}: ${res.err}`); process.exit(1); }
      if (res.stdout !== ref.stdout) {
        console.error(`${load}/${name}: 校验和不一致（${res.stdout} vs ${ref.stdout}）`);
        process.exit(1);
      }
      samplesByImpl[name].push(+res.ms.toFixed(1));
    }
  }
  // ④启动与稳态分离：主列＝稳态中位（rounds ≥1；n=1 时退化为单样本）；
  // first＝round 0 冷启样本；全样本中位只留 JSON。
  const ms = {};
  for (const [name] of impls) {
    const all = samplesByImpl[name];
    const steady = all.slice(1);
    ms[name] = steady.length ? median(steady) : all[0];
    ms[`${name}_first`] = all[0];
  }
  samples.loads[load] = {
    checksum: ref.stdout,
    aot_route: aotRoute,
    samples: samplesByImpl,
    med_steady: Object.fromEntries(impls.map(([nm]) => [nm, ms[nm]])),
    med_all: Object.fromEntries(impls.map(([nm]) => [nm, median(samplesByImpl[nm])])),
  };

  ms.aot_c = +(ms.aot / ms.c).toFixed(1);
  ms.aot_c_scalar = +(ms.aot / ms.c_scalar).toFixed(1);
  ms.aot_zig = +(ms.aot / ms.zig).toFixed(1);
  ms.aot_java = +(ms.aot / ms.java).toFixed(1);
  ms.aot_py = +(ms.aot / ms.python).toFixed(2);
  rows.push({ load, ...ms });
  console.log(`| ${load} | ${ms.interp.toFixed(0)} | ${ms.aot.toFixed(0)} | ${ms.c.toFixed(0)} | ${ms.c_scalar.toFixed(0)} | ${ms.go.toFixed(0)} | ${ms.rust.toFixed(0)} | ${ms.zig.toFixed(0)} | ${ms.java.toFixed(0)} | ${ms.python.toFixed(0)} | ${ms.aot_c}× | ${ms.aot_c_scalar}× | ${ms.aot_zig}× | ${ms.aot_java}× | ${ms.aot_py}× |`);
}
writeFileSync(save, JSON.stringify(samples, null, 1));
console.error(`[留存] 全样本 → ${save}（${loads.length} 负载×9 实现×N=${n} 交错轮；round0＝冷启单列）`);

// —— 性能门 v2 机械判定（D71 §二-3——判定机械化不靠手算；旧中位转副指标）——
// 口径：AOT/C 稳态比率（未取整）对照内测带；缺口＝(比率−带)/带。
// 综合判定：绿＝全部入带（内测放行）；黄＝恰一超带 ≤15%（公开注记＋下轮必治）；红＝其余（内测不出）。
const gate = { per_load: {}, verdict: "", over: [] };
for (const r of rows) {
  const b = BANDS[r.load];
  if (!b) { console.error(`[新门] ${r.load} 不在带表——拒绝判定`); process.exit(1); }
  const ratio = r.aot / r.c;
  const inBand = ratio <= b.beta;
  const gap = inBand ? 0 : +(((ratio - b.beta) / b.beta) * 100).toFixed(1);
  gate.per_load[r.load] = {
    cls: b.cls, ratio: +ratio.toFixed(2), beta_band: b.beta, sprint_band: b.sprint,
    in_band: inBand, gap_pct: gap,
  };
  console.error(`[新门] ${r.load.padEnd(18)} ${ratio.toFixed(2)}× ${inBand ? "≤" : ">"} ${b.beta.toFixed(1)}×（${b.cls}）${inBand ? "入带" : `超带 ${gap}%`}`);
  if (!inBand) gate.over.push({ load: r.load, gap_pct: gap });
}
if (gate.over.length === 0) gate.verdict = "green";
else if (gate.over.length === 1 && gate.over[0].gap_pct <= 15) gate.verdict = "yellow";
else gate.verdict = "red";
samples.gate = gate;
writeFileSync(save, JSON.stringify(samples, null, 1)); // 回写含判定
const cRatios = rows.map((r) => r.aot / r.c).sort((a, b) => a - b);
const verdictCn = { green: "绿——九负载全入带，内测放行", yellow: "黄——恰一超带 ≤15%，可开内测但公开注记＋下一轮必治", red: "红——内测不出" }[gate.verdict];
console.error(`[新门判定] ${gate.verdict.toUpperCase()}（${verdictCn}）＝入带 ${rows.length - gate.over.length}/超带 ${gate.over.length}——依据章程 v0.13 性能门 v2（D71）；AOT/C 中位 ${median(cRatios).toFixed(1)}× 转仪表盘副指标`);
const csRatios = rows.map((r) => r.aot / r.c_scalar).sort((a, b) => a - b);
const zigRatios = rows.map((r) => r.aot / r.zig).sort((a, b) => a - b);
const javaRatios = rows.map((r) => r.aot / r.java).sort((a, b) => a - b);
const pyRatios = rows.map((r) => r.aot / r.python).sort((a, b) => a - b);
const geo = (xs) => Math.exp(xs.reduce((s, x) => s + Math.log(x), 0) / xs.length);
console.error(`AOT/C 稳态中位 ${median(cRatios).toFixed(1)}×（几何均值 ${geo(cRatios).toFixed(2)}×）；AOT/C标量 中位 ${median(csRatios).toFixed(1)}×`);
console.error(`现代语言带（VM-18）：AOT/Zig 中位 ${median(zigRatios).toFixed(1)}×（几何均值 ${geo(zigRatios).toFixed(2)}×）；AOT/Java 中位 ${median(javaRatios).toFixed(1)}×（几何均值 ${geo(javaRatios).toFixed(2)}×）`);
console.error(`AOT/CPython 中位 ${median(pyRatios).toFixed(2)}×（几何均值 ${geo(pyRatios).toFixed(2)}×）`);
