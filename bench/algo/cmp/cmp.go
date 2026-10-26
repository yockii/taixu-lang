// cmp.go —— bench/algo 九负载 Go 移植（V0.19-S1；D50 §一）。
// 构建：go build -o cmp-go cmp.go   （gc 默认档＝常规发行形态）
// 用法：cmp-go <load>；stdout 必须与 .tx 校验和逐字节一致。
// 注：json 用标准库 encoding/json（惯用面——紧凑输出；map 键序排序不影响
// 总长：各组内文只依赖组号，D50 §一-4）。
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func putI64(v int64) { fmt.Println(v) }

func fibRec(n int64) int64 {
	if n < 2 {
		return n
	}
	return fibRec(n-1) + fibRec(n-2)
}

func loadFibRec() { putI64(fibRec(27)) }

func loadFibIter() {
	var a, b int64 = 0, 1
	for i := int64(0); i < 87; i++ {
		t := a + b
		a = b
		b = t
	}
	putI64(a)
}

func lcg(state int64) int64 { return (state*1103515245 + 12345) % 2147483648 }

func qsPartition(xs []int64, lo, hi int64) int64 {
	pivot := xs[hi]
	i, j := lo, lo
	for j < hi {
		if xs[j] < pivot {
			t := xs[i]
			xs[i] = xs[j]
			xs[j] = t
			i++
		}
		j++
	}
	t2 := xs[i]
	xs[i] = xs[hi]
	xs[hi] = t2
	return i
}
func qsRec(xs []int64, lo, hi int64) {
	if lo < hi {
		p := qsPartition(xs, lo, hi)
		qsRec(xs, lo, p-1)
		qsRec(xs, p+1, hi)
	}
}

func loadQsort() {
	n := int64(50000)
	xs := make([]int64, n)
	state := int64(42)
	for i := int64(0); i < n; i++ {
		state = lcg(state)
		xs[i] = state % 1000000
	}
	qsRec(xs, 0, n-1)
	var checksum int64 = 0
	for i := int64(0); i < n; i++ {
		checksum = checksum + (i*xs[i])%1000003
	}
	putI64(checksum % 1000003)
}

func loadSieve() {
	const n = int64(500000)
	comp := make([]bool, n)
	for p := int64(2); p*p < n; p++ {
		if !comp[p] {
			for m := p * p; m < n; m += p {
				comp[m] = true
			}
		}
	}
	count := int64(0)
	for i := int64(2); i < n; i++ {
		if !comp[i] {
			count++
		}
	}
	putI64(count)
}

func loadMandelbrot() {
	const size = int64(300)
	var total int64 = 0
	for py := int64(0); py < size; py++ {
		ci := float64(py)*2.5/float64(size) - 1.25
		for px := int64(0); px < size; px++ {
			cr := float64(px)*3.0/float64(size) - 2.0
			zr, zi := 0.0, 0.0
			it := int64(0)
			for it < 255 {
				t := zr*zr - zi*zi + cr
				zi = 2.0*zr*zi + ci
				zr = t
				if zr*zr+zi*zi > 4.0 {
					break
				}
				it++
			}
			total += it
		}
	}
	putI64(total)
}

func loadMatmul() {
	const N = int64(192)
	a := make([]float64, N*N)
	b := make([]float64, N*N)
	c := make([]float64, N*N)
	for i := int64(0); i < N*N; i++ {
		a[i] = float64(i%13) * 0.5
		b[i] = float64(i%7)*0.25 + 0.5
		c[i] = 0.0
	}
	for r := int64(0); r < N; r++ {
		for k := int64(0); k < N; k++ {
			av := a[r*N+k]
			for cc := int64(0); cc < N; cc++ {
				cv := c[r*N+cc]
				c[r*N+cc] = cv + av*b[k*N+cc]
			}
		}
	}
	diag := 0.0
	for i := int64(0); i < N; i++ {
		diag += c[i*N+i]
	}
	putI64(int64(diag * 1000000.0))
}

func sqrtApprox(v float64) float64 {
	g := v
	for it := int64(0); it < 8; it++ {
		g = 0.5 * (g + v/g)
	}
	return g
}

type Body struct{ x, y, z, vx, vy, vz float64 }

func loadNbody() {
	const dt = 0.01
	bs := []Body{
		{0.0, 0.0, 0.0, 0.5, 0.2, 0.1},
		{1.0, 0.5, 0.0, -0.3, 0.4, 0.2},
		{-0.5, 1.0, 0.5, 0.2, -0.5, 0.3},
		{2.0, -1.0, 1.0, 0.1, 0.1, -0.2},
		{-1.0, -0.5, -1.0, -0.2, 0.3, 0.0},
	}
	const n, steps = 5, 200000
	for s := 0; s < steps; s++ {
		for i := 0; i < n; i++ {
			for j := 0; j < n; j++ {
				if i != j {
					dx := bs[j].x - bs[i].x
					dy := bs[j].y - bs[i].y
					dz := bs[j].z - bs[i].z
					d2 := dx*dx + dy*dy + dz*dz + 0.01
					inv := 1.0 / (d2 * sqrtApprox(d2))
					bs[i].vx += dx * inv * dt
					bs[i].vy += dy * inv * dt
					bs[i].vz += dz * inv * dt
				}
			}
		}
		for k := 0; k < n; k++ {
			bs[k].x += bs[k].vx * dt
			bs[k].y += bs[k].vy * dt
			bs[k].z += bs[k].vz * dt
		}
	}
	kinetic := 0.0
	for k := 0; k < n; k++ {
		kinetic += 0.5 * (bs[k].vx*bs[k].vx + bs[k].vy*bs[k].vy + bs[k].vz*bs[k].vz)
	}
	putI64(int64(kinetic * 1000000.0))
}

func loadSbf() {
	var sb strings.Builder
	for i := int64(0); i < 5000; i++ {
		sb.WriteString("item-" + strconv.FormatInt(i%977, 10) + ";")
	}
	hay := sb.String()
	n := int64(len(hay))
	m := int64(len("item-7;"))
	var hits, at int64 = 0, 0
	for at <= n-m {
		j := int64(0)
		ok := true
		for j < m {
			if hay[at+j] != "item-7;"[j] {
				ok = false
				break
			}
			j++
		}
		if ok {
			hits++
			at += m
		} else {
			at++
		}
	}
	putI64((hits*977 + n) % 1000003)
}

func loadJson() {
	var checksum int64 = 0
	for round := int64(0); round < 40; round++ {
		/* 构树（map+slice 与 .tx 结构同构；序列化走 encoding/json——
		 * 紧凑无空格；键序排序不影响总长：各组内文只依赖组号）。 */
		obj := map[string]any{}
		for i := int64(0); i < 20; i++ {
			inner := map[string]any{}
			for j := int64(0); j < 5; j++ {
				inner["i"+strconv.FormatInt(j, 10)] = round*100 + i*5 + j
			}
			obj["g"+strconv.FormatInt(i, 10)] = inner
		}
		text, err := json.Marshal(obj)
		if err != nil {
			os.Exit(1)
		}
		/* parse 回读真实执行（对称成本） */
		var back any
		if json.Unmarshal(text, &back) != nil {
			os.Exit(1)
		}
		checksum += int64(len(text)) % 97
	}
	putI64(checksum % 1000003)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: cmp-go <load>")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "fib-rec":
		loadFibRec()
	case "fib-iter":
		loadFibIter()
	case "qsort":
		loadQsort()
	case "sieve":
		loadSieve()
	case "mandelbrot":
		loadMandelbrot()
	case "matmul":
		loadMatmul()
	case "nbody":
		loadNbody()
	case "string-build-find":
		loadSbf()
	case "json-roundtrip":
		loadJson()
	default:
		fmt.Fprintf(os.Stderr, "unknown load %s\n", os.Args[1])
		os.Exit(2)
	}
}
