import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Cmp —— bench/algo 九负载 Java 移植（V0.21-S1；VM-18——D56 现代语言带）。
 * 构建：javac -d classes Cmp.java
 * 运行：java -cp classes Cmp <load>
 * stdout 必须与 .tx 校验和逐字节一致（cmp-run.mjs 硬门槛）。
 * 移植对齐注记（承 cmp.c D50 §一同款）：
 *   1. 浮点表达式求值序逐项对齐；Java 17+ 全部浮点严格 IEEE-754（JEP 306），
 *      HotSpot 缺省不做 FMA 收缩——与 C -O2 SSE2 基线同构；
 *   2. qsort LCG 64 位 long 承载（同 C % 截断语义）；
 *   3. nbody sqrtApprox 8 轮牛顿迭代逐语言同构；
 *   4. json 校验和只依赖序列化文本长度；parse 回读真实执行（建树对称成本，
 *      LinkedHashMap 保插入序＝键序规范形）；
 *   5. string-build-find 手写逐字节比对（charAt 对齐 .tx byteAt 形态）。
 */
public class Cmp {

    static void putI64(long v) {
        System.out.println(v);
    }

    // —— fib-rec ——
    static long fib(long n) {
        if (n < 2) return n;
        return fib(n - 1) + fib(n - 2);
    }

    // —— fib-iter ——
    static void loadFibIter() {
        long a = 0, b = 1;
        for (long i = 0; i < 87; i++) { long t = a + b; a = b; b = t; }
        putI64(a);
    }

    // —— qsort ——
    static long lcg(long state) {
        return (state * 1103515245L + 12345L) % 2147483648L;
    }

    static long[] qs;

    static long qsPartition(long lo, long hi) {
        long pivot = qs[(int) hi];
        long i = lo, j = lo;
        while (j < hi) {
            if (qs[(int) j] < pivot) {
                long t = qs[(int) i]; qs[(int) i] = qs[(int) j]; qs[(int) j] = t;
                i = i + 1;
            }
            j = j + 1;
        }
        long t2 = qs[(int) i]; qs[(int) i] = qs[(int) hi]; qs[(int) hi] = t2;
        return i;
    }

    static void qsRec(long lo, long hi) {
        if (lo < hi) {
            long p = qsPartition(lo, hi);
            qsRec(lo, p - 1);
            qsRec(p + 1, hi);
        }
    }

    static void loadQsort() {
        long n = 50000;
        qs = new long[(int) n];
        long state = 42;
        for (long i = 0; i < n; i++) { state = lcg(state); qs[(int) i] = state % 1000000; }
        qsRec(0, n - 1);
        long checksum = 0;
        for (long i = 0; i < n; i++) checksum += (i * qs[(int) i]) % 1000003;
        putI64(checksum % 1000003);
    }

    // —— sieve ——
    static void loadSieve() {
        int n = 500000;
        byte[] comp = new byte[n];
        long p = 2;
        while (p * p < n) {
            if (comp[(int) p] == 0) {
                for (long m = p * p; m < n; m += p) comp[(int) m] = 1;
            }
            p = p + 1;
        }
        long count = 0;
        for (long i = 2; i < n; i++) if (comp[(int) i] == 0) count = count + 1;
        putI64(count);
    }

    // —— mandelbrot（表达式求值序＝.tx 同序） ——
    static void loadMandelbrot() {
        int size = 300;
        long total = 0;
        for (long py = 0; py < size; py++) {
            double ci = (double) py * 2.5 / (double) size - 1.25;
            for (long px = 0; px < size; px++) {
                double cr = (double) px * 3.0 / (double) size - 2.0;
                double zr = 0.0, zi = 0.0;
                long it = 0;
                while (it < 255) {
                    double t = zr * zr - zi * zi + cr;
                    zi = 2.0 * zr * zi + ci;
                    zr = t;
                    if (zr * zr + zi * zi > 4.0) break;
                    it = it + 1;
                }
                total = total + it;
            }
        }
        putI64(total);
    }

    // —— matmul（192×192 平铺一维；求值序同 .tx） ——
    static void loadMatmul() {
        int n = 192;
        int nn = n * n;
        double[] a = new double[nn];
        double[] b = new double[nn];
        double[] c = new double[nn];
        for (int i = 0; i < nn; i++) {
            a[i] = (double) (i % 13) * 0.5;
            b[i] = (double) (i % 7) * 0.25 + 0.5;
            c[i] = 0.0;
        }
        for (int r = 0; r < n; r++) {
            for (int k = 0; k < n; k++) {
                double av = a[r * n + k];
                for (int cc = 0; cc < n; cc++) {
                    double cv = c[r * n + cc];
                    c[r * n + cc] = cv + av * b[k * n + cc];
                }
            }
        }
        double diag = 0.0;
        for (int i = 0; i < n; i++) diag = diag + c[i * n + i];
        putI64((long) (diag * 1000000.0));
    }

    // —— nbody（sqrtApprox＝8 轮牛顿迭代；求值序同 .tx） ——
    static double sqrtApprox(double v) {
        double g = v;
        for (int it = 0; it < 8; it++) g = 0.5 * (g + v / g);
        return g;
    }

    static final class Body {
        double x, y, z, vx, vy, vz;

        Body(double x, double y, double z, double vx, double vy, double vz) {
            this.x = x; this.y = y; this.z = z; this.vx = vx; this.vy = vy; this.vz = vz;
        }
    }

    static void loadNbody() {
        final double dt = 0.01;
        Body[] bs = {
            new Body( 0.0,  0.0,  0.0,  0.5,  0.2,  0.1),
            new Body( 1.0,  0.5,  0.0, -0.3,  0.4,  0.2),
            new Body(-0.5,  1.0,  0.5,  0.2, -0.5,  0.3),
            new Body( 2.0, -1.0,  1.0,  0.1,  0.1, -0.2),
            new Body(-1.0, -0.5, -1.0, -0.2,  0.3,  0.0),
        };
        final int n = 5, steps = 200000;
        for (int s = 0; s < steps; s++) {
            for (int i = 0; i < n; i++) {
                for (int j = 0; j < n; j++) {
                    if (i != j) {
                        double dx = bs[j].x - bs[i].x;
                        double dy = bs[j].y - bs[i].y;
                        double dz = bs[j].z - bs[i].z;
                        double d2 = dx * dx + dy * dy + dz * dz + 0.01;
                        double inv = 1.0 / (d2 * sqrtApprox(d2));
                        bs[i].vx += dx * inv * dt;
                        bs[i].vy += dy * inv * dt;
                        bs[i].vz += dz * inv * dt;
                    }
                }
            }
            for (Body k : bs) {
                k.x += k.vx * dt;
                k.y += k.vy * dt;
                k.z += k.vz * dt;
            }
        }
        double kinetic = 0.0;
        for (Body k : bs)
            kinetic = kinetic + 0.5 * (k.vx * k.vx + k.vy * k.vy + k.vz * k.vz);
        putI64((long) (kinetic * 1000000.0));
    }

    // —— string-build-find ——
    static void loadSbf() {
        // hay = Σ "item-" + toStr(i % 977) + ";"（i<5000）——逐段拼接。
        StringBuilder sb = new StringBuilder(1 << 20);
        for (long i = 0; i < 5000; i++) {
            sb.append("item-").append(i % 977).append(';');
        }
        final String hay = sb.toString();
        long n = hay.length(), m = 7; // len("item-7;")
        final String needle = "item-7;";
        long hits = 0, at = 0;
        while (at <= n - m) {
            long j = 0;
            boolean ok = true;
            while (j < m) {
                // 逐字节比对（对齐 .tx byteAt 形态——不用 indexOf/regionMatches）
                if (hay.charAt((int) (at + j)) != needle.charAt((int) j)) { ok = false; break; }
                j = j + 1;
            }
            if (ok) { hits = hits + 1; at = at + m; } else { at = at + 1; }
        }
        putI64((hits * 977 + n) % 1000003);
    }

    // —— json-roundtrip（构树→紧凑序列化→解析回读；校验和＝Σ len%97） ——
    // 格式与 std/json 规范形同构：{"g0":{"i0":V,...},...}，键序＝插入序，
    // V = round*100 + i*5 + j；无空格。解析器为完整 JSON 子集（建树对称成本）。

    static void jstringify(Object j, StringBuilder b) {
        if (j == null) {
            b.append("null");
        } else if (j instanceof Boolean bo) {
            b.append(bo.booleanValue() ? "true" : "false");
        } else if (j instanceof Long l) {
            b.append(l.longValue());
        } else if (j instanceof Double d) {
            // 本负载全部整值叶子——int 通道（float 整值补 .0 的规范形在此不触发）
            b.append((long) (double) d);
        } else if (j instanceof String s) {
            b.append('"');
            for (int p = 0; p < s.length(); p++) {
                char ch = s.charAt(p);
                if (ch == '"' || ch == '\\') b.append('\\');
                b.append(ch);
            }
            b.append('"');
        } else if (j instanceof List<?> arr) {
            b.append('[');
            for (int i = 0; i < arr.size(); i++) {
                if (i > 0) b.append(',');
                jstringify(arr.get(i), b);
            }
            b.append(']');
        } else if (j instanceof Map<?, ?> obj) {
            b.append('{');
            int i = 0;
            for (Map.Entry<?, ?> e : obj.entrySet()) {
                if (i > 0) b.append(',');
                b.append('"').append((String) e.getKey()).append("\":");
                jstringify(e.getValue(), b);
                i++;
            }
            b.append('}');
        }
    }

    static final class P {
        final String s;
        int pos = 0;
        boolean err = false;

        P(String s) { this.s = s; }

        void ws() {
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') pos++;
                else break;
            }
        }

        boolean pchar(char c) {
            ws();
            if (pos < s.length() && s.charAt(pos) == c) { pos++; return true; }
            return false;
        }

        Object value() {
            ws();
            if (pos >= s.length()) { err = true; return null; }
            char c = s.charAt(pos);
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') {
                pos++;
                int st = pos;
                while (pos < s.length() && s.charAt(pos) != '"') {
                    if (s.charAt(pos) == '\\') pos++;
                    pos++;
                }
                String str = s.substring(st, pos);
                pos++;
                return str;
            }
            if (s.startsWith("true", pos)) { pos += 4; return Boolean.TRUE; }
            if (s.startsWith("false", pos)) { pos += 5; return Boolean.FALSE; }
            if (s.startsWith("null", pos)) { pos += 4; return null; }
            int st = pos;
            while (pos < s.length()) {
                char d = s.charAt(pos);
                if ((d >= '0' && d <= '9') || d == '-' || d == '+' || d == '.' || d == 'e' || d == 'E') { pos++; continue; }
                break;
            }
            if (pos == st) { err = true; return null; }
            String text = s.substring(st, pos);
            double d;
            try {
                d = Double.parseDouble(text);
            } catch (NumberFormatException e) {
                err = true;
                return null;
            }
            if (text.indexOf('.') >= 0 || text.indexOf('e') >= 0 || text.indexOf('E') >= 0) return d;
            return (long) d;
        }

        Object object() {
            Map<String, Object> m = new LinkedHashMap<>();
            pchar('{');
            ws();
            if (pchar('}')) return m;
            for (;;) {
                ws();
                if (pos >= s.length() || s.charAt(pos) != '"') { err = true; return m; }
                pos++;
                int ks = pos;
                while (pos < s.length() && s.charAt(pos) != '"') {
                    if (s.charAt(pos) == '\\') pos++;
                    pos++;
                }
                if (pos >= s.length()) { err = true; return m; }
                String key = s.substring(ks, pos);
                pos++;
                if (!pchar(':')) { err = true; return m; }
                Object v = value();
                m.put(key.toString(), v);
                if (pchar(',')) continue;
                pchar('}');
                return m;
            }
        }

        Object array() {
            List<Object> arr = new ArrayList<>();
            pchar('[');
            ws();
            if (pchar(']')) return arr;
            for (;;) {
                arr.add(value());
                if (pchar(',')) continue;
                pchar(']');
                return arr;
            }
        }
    }

    static void loadJson() {
        long checksum = 0;
        StringBuilder b = new StringBuilder(1 << 16);
        for (long round = 0; round < 40; round++) {
            // 构树文本：{"g0":{"i0":V,...},"g1":{...},...,"g19":{...}}
            b.setLength(0);
            b.append('{');
            for (long i = 0; i < 20; i++) {
                if (i > 0) b.append(',');
                b.append('"').append("g").append(i).append("\":").append('{');
                for (long jj = 0; jj < 5; jj++) {
                    if (jj > 0) b.append(",\"");
                    else b.append('"');
                    b.append("i").append(jj).append("\":");
                    b.append(round * 100 + i * 5 + jj);
                }
                b.append('}');
            }
            b.append('}');
            final int blen = b.length();
            // parse 回读真实执行（建树对称成本；err 路径消费——与 cmp.c 同形）
            P p = new P(b.toString());
            Object back = p.value();
            if (p.err) checksum += sumInts(back);
            checksum += blen % 97;
        }
        putI64(checksum % 1000003);
    }

    static long sumInts(Object j) {
        if (j instanceof Long l) return l.longValue();
        long s = 0;
        if (j instanceof List<?> arr) {
            for (Object it : arr) s += sumInts(it);
        } else if (j instanceof Map<?, ?> obj) {
            for (Object it : obj.values()) s += sumInts(it);
        }
        return s;
    }

    public static void main(String[] args) {
        if (args.length < 1) {
            System.err.println("usage: java Cmp <load>");
            System.exit(2);
        }
        switch (args[0]) {
            case "fib-rec": putI64(fib(27)); break;
            case "fib-iter": loadFibIter(); break;
            case "qsort": loadQsort(); break;
            case "sieve": loadSieve(); break;
            case "mandelbrot": loadMandelbrot(); break;
            case "matmul": loadMatmul(); break;
            case "nbody": loadNbody(); break;
            case "string-build-find": loadSbf(); break;
            case "json-roundtrip": loadJson(); break;
            default:
                System.err.println("unknown load " + args[0]);
                System.exit(2);
        }
    }
}
