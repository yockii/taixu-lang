#!/usr/bin/env node
// pgo-ab.mjs —— V0.25-P1 PGO 特化同窗配对 A/B 驱动（VM-23/D63 柱③）。
// 口径＝V0.24 aot-perf-p1 同窗背靠背配对方法学延续：
//   ①两段式：每负载先 `taixu run --profile-out` 采画像（TXPGO1），
//     再 `taixu aot --pgo=<画像>` 特化编译（pgo 产物）与 `taixu aot`
//     基线编译（base 产物）；
//   ②行为恒等门槛：base/pgo 两产物 stdout＋exit 与 interp 逐字节一致
//     ——不一致即 fail 不落表（特化非语义面红线）；
//   ③N=10 逐轮交替（base/pgo 轮转各采一次）——时钟漂移免疫；
//   ④特化点命中计数随表（TAIXU_AOT_PGO_STATS stderr 摘要）。
// 用法：node bench/algo/cmp/pgo-ab.mjs [--n=10] [--taixu=zig-out/bin/taixu.exe] [--save=<path>]
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";

const args = process.argv.slice(2);
let n = 10;
let taixu = "zig-out/bin/taixu.exe";
let save = "";
for (const a of args) {
  if (a.startsWith("--n=")) n = parseInt(a.slice(4));
  else if (a.startsWith("--taixu=")) taixu = a.slice(8);
  else if (a.startsWith("--save=")) save = a.slice(7);
}
if (!save) save = "bench/algo/cmp/samples-v025-pgo-ab.json";

const loads = [
  "fib-rec", "fib-iter", "qsort", "sieve", "mandelbrot",
  "matmul", "nbody", "string-build-find", "json-roundtrip",
];
const stdlibRoot = existsSync("../taixu-stdlib/src/json.tx")
  ? resolve("../taixu-stdlib/src")
  : null;
const env = stdlibRoot ? { TAIXU_STDLIB: stdlibRoot } : {};

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}
function timed(cmd, argv, extraEnv) {
  const t0 = performance.now();
  const r = spawnSync(cmd, argv, { encoding: "utf8", timeout: 300000, env: { ...process.env, ...extraEnv } });
  const ms = performance.now() - t0;
  if (r.status !== 0) return { err: `exit ${r.status}: ${(r.stderr || "").slice(0, 200)}` };
  return { ms, stdout: r.stdout.trim(), stderr: r.stderr || "" };
}
function sha256Of(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex").slice(0, 16);
}

const out_dir = mkdtempSync(join(tmpdir(), "taixu-pgo-ab-"));
const rows = [];
const samples = {
  provenance: { taixu: `${taixu} sha256:${sha256Of(taixu)}`, rounds: n },
  loads: {},
};
console.log("| 负载 | base(ms) | pgo(ms) | pgo/base | 命中(sites) | 判读 |");
console.log("|---|---|---|---|---|---|");

for (const load of loads) {
  const path = join("bench/algo", `${load}.tx`);
  const d = join(out_dir, load);
  // 画像路径须相对 cwd（--profile-out 经 Dir.cwd().writeFile 落盘——
  // Windows 绝对 sub_path 形态不受支持）；产物目录绝对路径无碍。
  const prof = `tmp-pgo-ab-${load}.txp`;

  // interp 参考输出（行为恒等裁判）。
  const ref = timed(taixu, ["run", path], env);
  if (ref.err) { console.error(`${load}: interp 失败 ${ref.err}`); process.exit(1); }

  // 段①画像采集（解释执行全量——重放确定性）。
  const pr = timed(taixu, ["run", path, `--profile-out=${prof}`], env);
  if (pr.err || pr.stdout !== ref.stdout) { console.error(`${load}: 画像采集失败或输出漂移 ${pr.err}\n${pr.stderr?.slice(0, 300)}`); process.exit(1); }
  if (!existsSync(prof)) { console.error(`${load}: 画像未落盘\n${pr.stderr?.slice(0, 300)}`); process.exit(1); }

  // 段②双形态编译。
  const genBase = timed(taixu, ["aot", path, "--out", join(d, "base")], env);
  const genPgo = timed(taixu, ["aot", path, `--pgo=${prof}`, "--out", join(d, "pgo")], env);
  if (genBase.err || genPgo.err) { console.error(`${load}: aot 生成失败 ${genBase.err || genPgo.err}`); process.exit(1); }
  // 特化点命中计数（stats 摘要 stderr）。
  const st = timed(taixu, ["aot", path, `--pgo=${prof}`, "--out", join(d, "pgo2")], { TAIXU_AOT_PGO_STATS: "1", ...env });
  const m = /pgo: scanned=(\d+) inlined=(\d+) callees=(\d+)/.exec(st.stderr || "");
  const sites = m ? parseInt(m[2]) : 0;

  const baseExe = join(d, "base", `${load}.exe`);
  const pgoExe = join(d, "pgo", `${load}.exe`);
  const rb = timed(baseExe, [], {});
  const rp = timed(pgoExe, [], {});
  if (rb.err || rp.err) { console.error(`${load}: 产物运行失败 ${rb.err || rp.err}`); process.exit(1); }
  if (rb.stdout !== ref.stdout || rp.stdout !== ref.stdout) {
    console.error(`${load}: 三路不一致（base=${rb.stdout.slice(0, 40)} pgo=${rp.stdout.slice(0, 40)} ref=${ref.stdout.slice(0, 40)}）——特化非语义面红线`);
    process.exit(1);
  }

  // ③N=10 逐轮交替。
  const sb = [], sp = [];
  for (let r = 0; r < n; r++) {
    const a = timed(baseExe, [], {});
    if (a.err) { console.error(`${load}: base 轮失败`); process.exit(1); }
    sb.push(a.ms);
    const b = timed(pgoExe, [], {});
    if (b.err) { console.error(`${load}: pgo 轮失败`); process.exit(1); }
    sp.push(b.ms);
  }
  const mb = median(sb), mp = median(sp);
  const ratio = mp / mb;
  const verdict = sites === 0 ? "无命中（画像无热标量调用点）" : (ratio <= 0.97 ? "改善" : ratio >= 1.03 ? "回退" : "中性");
  rows.push({ load, base: mb, pgo: mp, ratio, sites });
  samples.loads[load] = { base: sb, pgo: sp, median_base: mb, median_pgo: mp, pgo_sites: sites };
  console.log(`| ${load} | ${mb.toFixed(1)} | ${mp.toFixed(1)} | ${ratio.toFixed(3)} | ${sites} | ${verdict} |`);
}
const withSites = rows.filter((r) => r.sites > 0);
if (withSites.length > 0) {
  const geo = Math.exp(withSites.reduce((acc, r) => acc + Math.log(r.ratio), 0) / withSites.length);
  console.log(`\n特化命中负载（${withSites.length}）：pgo/base 几何均值 ${geo.toFixed(3)}`);
}
writeFileSync(save, JSON.stringify(samples, null, 1));
console.log(`全样本留存 → ${save}`);
rmSync(out_dir, { recursive: true, force: true });
for (const load of loads) {
  try { rmSync(`tmp-pgo-ab-${load}.txp`); } catch {}
}
