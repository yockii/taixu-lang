#!/usr/bin/env node
// run.mjs —— bench/algo 计时驱动器（V0.16-P1；D42 §〇-1 用户可复跑口径）。
// 用法：node bench/algo/run.mjs [--n=5] [--aot-only] [taixu 路径]
// 口径：interp（taixu run）与 AOT（taixu aot --release 产物）双路各 N 次
// wall-clock 中位；JSON 负载经 TAIXU_STDLIB（同级仓自动发现）。数据只写实测。
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, mkdtempSync, readdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { performance } from "node:perf_hooks";

const args = process.argv.slice(2);
let n = 5;
let aotOnly = false;
let taixu = "zig-out-rel/bin/taixu.exe";
for (const a of args) {
  if (a.startsWith("--n=")) n = parseInt(a.slice(4));
  else if (a === "--aot-only") aotOnly = true;
  else taixu = a;
}
if (!existsSync(taixu)) {
  console.error(`taixu 不存在: ${taixu}`);
  process.exit(1);
}
const algoDir = "bench/algo";
const loads = readdirSync(algoDir).filter((f) => f.endsWith(".tx")).sort();
const stdlibRoot = existsSync("../taixu-stdlib/src/json.tx")
  ? resolve("../taixu-stdlib/src")
  : null;

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

function timed(cmd, argv, env) {
  const t0 = performance.now();
  const r = spawnSync(cmd, argv, { encoding: "utf8", timeout: 120000, env: { ...process.env, ...env } });
  const ms = performance.now() - t0;
  if (r.status !== 0) return { err: `exit ${r.status}: ${(r.stderr || "").slice(0, 120)}` };
  return { ms, stdout: r.stdout };
}

const out_dir = mkdtempSync(join(tmpdir(), "taixu-algo-"));
const env = stdlibRoot ? { TAIXU_STDLIB: stdlibRoot } : {};
console.log(`taixu: ${taixu}  N=${n}（中位）`);
console.log("| 负载 | interp (ms) | AOT (ms) | interp/AOT | 校验和（双路一致） |");
console.log("|---|---|---|---|---|");
for (const load of loads) {
  const path = join(algoDir, load);
  const name = load.replace(".tx", "");
  const run = timed(taixu, ["run", path], env);
  if (run.err) { console.log(`| ${name} | interp 失败：${run.err} | — | — | — |`); continue; }
  const checksum = run.stdout.trim();
  const interpRuns = [];
  if (!aotOnly) {
    for (let i = 0; i < n; i++) {
      const r = timed(taixu, ["run", path], env);
      if (r.err) { console.log(`| ${name} | interp 失败 | — | — | — |`); process.exit(1); }
      if (r.stdout.trim() !== checksum) { console.log(`| ${name} | interp 校验和漂移 | — | — | — |`); process.exit(1); }
      interpRuns.push(r.ms);
    }
  }
  // AOT：生成一次，计时 N 次（生成时间不计入运行时）。
  const aot = timed(taixu, ["aot", path, "--release", "--out", out_dir], env);
  if (aot.err) { console.log(`| ${name} | ${median(interpRuns).toFixed(0)} | AOT 生成失败 | — | ${checksum} |`); continue; }
  const exeName = existsSync(join(out_dir, `${name}.exe`)) ? `${name}.exe` : name;
  const exe = join(out_dir, exeName);
  const aotRuns = [];
  let aotChecksum = checksum;
  for (let i = 0; i < n; i++) {
    const r = timed(exe, [], {});
    if (r.err) { console.log(`| ${name} | ${median(interpRuns).toFixed(0)} | 产物运行失败：${r.err} | — | — |`); process.exit(1); }
    if (i === 0) aotChecksum = r.stdout.trim();
    if (r.stdout.trim() !== checksum) { console.log(`| ${name} | 校验和不一致（interp=${checksum} aot=${r.stdout.trim()}） | — | — | — |`); process.exit(1); }
    aotRuns.push(r.ms);
  }
  const ratio = (median(interpRuns) / median(aotRuns)).toFixed(2);
  console.log(`| ${name} | ${median(interpRuns).toFixed(0)} | ${median(aotRuns).toFixed(0)} | ${ratio}× | ${checksum} |`);
}
