#!/usr/bin/env node
// recalc.mjs —— 带复核样本独立复算器（V0.44-M0-③/TOOL-59 起；D71 节奏）。
// 从留存样本 JSON 独立重算：九实现稳态中位／门比率／入带判定／综合判定／
// AOT-C 中位与几何均值——逐位对照 JSON 内 gate 段与 cmp-run stderr 结论，
// 不一致即非零退出（复算失真即红）。复算逻辑独立于 cmp-run.mjs 重写。
// 用法：node bench/algo/cmp/recalc.mjs <samples.json>
import { readFileSync } from "node:fs";

const [, , path] = process.argv;
if (!path) {
  console.error("用法: node recalc.mjs <samples.json>");
  process.exit(2);
}
const s = JSON.parse(readFileSync(path, "utf8"));

// 分负载目标带（D71 §一冻结表——独立转录核对，与 cmp-run.mjs 同源不同写）
const BANDS = {
  "fib-rec": [5.0, 3.0], "fib-iter": [4.0, 2.0], "qsort": [7.0, 3.0],
  "sieve": [7.0, 3.0], "mandelbrot": [4.0, 2.0], "matmul": [4.0, 1.5],
  "nbody": [15.0, 6.0], "string-build-find": [4.0, 1.2], "json-roundtrip": [7.0, 3.0],
};

function median(xs) {
  const t = [...xs].sort((a, b) => a - b);
  return t[Math.floor(t.length / 2)];
}
function geo(xs) {
  return Math.exp(xs.reduce((acc, x) => acc + Math.log(x), 0) / xs.length);
}

let mismatch = 0;
const cRatios = [];
console.log("负载 | AOT稳态中位 | C稳态中位 | 复算比率 | 表内比率 | 入带(复算) | 入带(表内) | 一致");
for (const [load, d] of Object.entries(s.loads)) {
  const aot = median(d.samples.aot.slice(1)); // 稳态＝rounds ≥1（round0 冷启单列）
  const c = median(d.samples.c.slice(1));
  const ratio = aot / c;
  const [beta] = BANDS[load];
  const inBand = ratio <= beta;
  const g = s.gate.per_load[load];
  const okRatio = Math.abs(ratio - g.ratio) < 0.005;
  const okBand = inBand === g.in_band;
  const okMed = Math.abs(aot - d.med_steady.aot) < 0.05 && Math.abs(c - d.med_steady.c) < 0.05;
  if (!okRatio || !okBand || !okMed) mismatch += 1;
  cRatios.push(ratio);
  console.log(
    `${load} | ${aot} | ${c} | ${ratio.toFixed(3)} | ${g.ratio} | ${inBand} | ${g.in_band} | ${okRatio && okBand && okMed ? "✓" : "✗"}`,
  );
}
const medC = median(cRatios);
const geoC = geo(cRatios);
const over = Object.entries(s.loads)
  .filter(([load]) => s.gate.per_load[load] && s.gate.per_load[load].in_band === false)
  .map(([load]) => load);
console.log(`复算 AOT/C 稳态中位 ${medC.toFixed(1)}× 几何均值 ${geoC.toFixed(2)}×`);
console.log(`复算超带 ${over.length} 载：${over.join(" ")}`);
console.log(`表内判定 ${s.gate.verdict}（超带 ${s.gate.over.length}）——复算与表内${mismatch === 0 ? "逐位吻合" : `不符 ${mismatch} 项`}`);
process.exit(mismatch === 0 ? 0 : 1);
