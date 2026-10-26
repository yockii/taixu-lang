// cmp.rs —— bench/algo 九负载 Rust 移植（V0.19-S1；D50 §一）。
// 构建：rustc -O -o cmp-rs cmp.rs   （无 cargo/serde 依赖——惯用形态）
// 用法：cmp-rs <load>；stdout 必须与 .tx 校验和逐字节一致。
// 注：json 手写 stringify（紧凑 "k":v，插入序）＋手写解析回读（对称成本，
// D50 §一-4——无 serde 依赖）；浮点求值序逐表达式对齐（Rust 无收缩）。
use std::env;
use std::process::exit;

fn fib_rec(n: i64) -> i64 {
    if n < 2 {
        return n;
    }
    fib_rec(n - 1) + fib_rec(n - 2)
}

fn load_fib_rec() {
    println!("{}", fib_rec(27));
}

fn load_fib_iter() {
    let (mut a, mut b) = (0i64, 1i64);
    for _ in 0..87 {
        let t = a + b;
        a = b;
        b = t;
    }
    println!("{}", a);
}

fn load_qsort() {
    fn lcg(state: i64) -> i64 {
        (state.wrapping_mul(1103515245) + 12345) % 2147483648
    }
    let n: i64 = 50000;
    let mut xs: Vec<i64> = Vec::with_capacity(n as usize);
    let mut state: i64 = 42;
    for _ in 0..n {
        state = lcg(state);
        xs.push(state % 1000000);
    }

    fn partition(xs: &mut [i64], lo: i64, hi: i64) -> i64 {
        let pivot = xs[hi as usize];
        let (mut i, mut j) = (lo, lo);
        while j < hi {
            if xs[j as usize] < pivot {
                let t = xs[i as usize];
                xs[i as usize] = xs[j as usize];
                xs[j as usize] = t;
                i += 1;
            }
            j += 1;
        }
        let t2 = xs[i as usize];
        xs[i as usize] = xs[hi as usize];
        xs[hi as usize] = t2;
        i
    }
    fn qsort(xs: &mut [i64], lo: i64, hi: i64) {
        if lo < hi {
            let p = partition(xs, lo, hi);
            qsort(xs, lo, p - 1);
            qsort(xs, p + 1, hi);
        }
    }
    qsort(&mut xs, 0, n - 1);
    let mut checksum: i64 = 0;
    for i in 0..n {
        checksum = checksum + (i * xs[i as usize]) % 1000003;
    }
    println!("{}", checksum % 1000003);
}

fn load_sieve() {
    let n: i64 = 500000;
    let mut comp: Vec<bool> = vec![false; n as usize];
    let mut p: i64 = 2;
    while p * p < n {
        if !comp[p as usize] {
            let mut m = p * p;
            while m < n {
                comp[m as usize] = true;
                m += p;
            }
        }
        p += 1;
    }
    let mut count: i64 = 0;
    for i in 2..n {
        if !comp[i as usize] {
            count += 1;
        }
    }
    println!("{}", count);
}

fn load_mandelbrot() {
    let size: i64 = 300;
    let mut total: i64 = 0;
    for py in 0..size {
        let ci = py as f64 * 2.5 / size as f64 - 1.25;
        for px in 0..size {
            let cr = px as f64 * 3.0 / size as f64 - 2.0;
            let (mut zr, mut zi) = (0.0f64, 0.0f64);
            let mut it: i64 = 0;
            while it < 255 {
                let t = zr * zr - zi * zi + cr;
                zi = 2.0 * zr * zi + ci;
                zr = t;
                if zr * zr + zi * zi > 4.0 {
                    break;
                }
                it += 1;
            }
            total += it;
        }
    }
    println!("{}", total);
}

fn load_matmul() {
    let n: i64 = 192;
    let mut a: Vec<f64> = vec![0.0; (n * n) as usize];
    let mut b: Vec<f64> = vec![0.0; (n * n) as usize];
    let mut c: Vec<f64> = vec![0.0; (n * n) as usize];
    for i in 0..(n * n) {
        a[i as usize] = (i % 13) as f64 * 0.5;
        b[i as usize] = (i % 7) as f64 * 0.25 + 0.5;
        c[i as usize] = 0.0;
    }
    for r in 0..n {
        for k in 0..n {
            let av = a[(r * n + k) as usize];
            for cc in 0..n {
                let cv = c[(r * n + cc) as usize];
                c[(r * n + cc) as usize] = cv + av * b[(k * n + cc) as usize];
            }
        }
    }
    let mut diag = 0.0f64;
    for i in 0..n {
        diag += c[(i * n + i) as usize];
    }
    println!("{}", (diag * 1000000.0) as i64);
}

fn sqrt_approx(v: f64) -> f64 {
    let mut g = v;
    for _ in 0..8 {
        g = 0.5 * (g + v / g);
    }
    g
}

#[derive(Clone, Copy)]
struct Body {
    x: f64,
    y: f64,
    z: f64,
    vx: f64,
    vy: f64,
    vz: f64,
}

fn load_nbody() {
    let dt = 0.01f64;
    let mut bs = [
        Body { x: 0.0, y: 0.0, z: 0.0, vx: 0.5, vy: 0.2, vz: 0.1 },
        Body { x: 1.0, y: 0.5, z: 0.0, vx: -0.3, vy: 0.4, vz: 0.2 },
        Body { x: -0.5, y: 1.0, z: 0.5, vx: 0.2, vy: -0.5, vz: 0.3 },
        Body { x: 2.0, y: -1.0, z: 1.0, vx: 0.1, vy: 0.1, vz: -0.2 },
        Body { x: -1.0, y: -0.5, z: -1.0, vx: -0.2, vy: 0.3, vz: 0.0 },
    ];
    let n = 5usize;
    for _ in 0..200000 {
        for i in 0..n {
            for j in 0..n {
                if i != j {
                    let dx = bs[j].x - bs[i].x;
                    let dy = bs[j].y - bs[i].y;
                    let dz = bs[j].z - bs[i].z;
                    let d2 = dx * dx + dy * dy + dz * dz + 0.01;
                    let inv = 1.0 / (d2 * sqrt_approx(d2));
                    bs[i].vx += dx * inv * dt;
                    bs[i].vy += dy * inv * dt;
                    bs[i].vz += dz * inv * dt;
                }
            }
        }
        for k in 0..n {
            bs[k].x += bs[k].vx * dt;
            bs[k].y += bs[k].vy * dt;
            bs[k].z += bs[k].vz * dt;
        }
    }
    let mut kinetic = 0.0f64;
    for k in 0..n {
        kinetic += 0.5 * (bs[k].vx * bs[k].vx + bs[k].vy * bs[k].vy + bs[k].vz * bs[k].vz);
    }
    println!("{}", (kinetic * 1000000.0) as i64);
}

fn load_sbf() {
    let mut hay = String::new();
    for i in 0..5000i64 {
        hay.push_str("item-");
        hay.push_str(&(i % 977).to_string());
        hay.push(';');
    }
    let hb = hay.as_bytes();
    let n = hb.len() as i64;
    let m: i64 = 7; // "item-7;".len()
    let needle = b"item-7;";
    let (mut hits, mut at) = (0i64, 0i64);
    while at <= n - m {
        let mut j: i64 = 0;
        let mut ok = true;
        while j < m {
            if hb[at as usize + j as usize] != needle[j as usize] {
                ok = false;
                break;
            }
            j += 1;
        }
        if ok {
            hits += 1;
            at += m;
        } else {
            at += 1;
        }
    }
    println!("{}", (hits * 977 + n) % 1000003);
}

// —— json：手写紧凑 stringify（插入序）＋手写解析回读（对称成本） ——
#[derive(Debug)]
enum Jv {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Arr(Vec<Jv>),
    Obj(Vec<(String, Jv)>),
}

fn jv_stringify(j: &Jv, out: &mut String) {
    match j {
        Jv::Null => out.push_str("null"),
        Jv::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Jv::Int(v) => out.push_str(&v.to_string()),
        Jv::Float(x) => out.push_str(&x.to_string()),
        Jv::Str(s) => {
            out.push('"');
            for ch in s.chars() {
                if ch == '"' || ch == '\\' {
                    out.push('\\');
                }
                out.push(ch);
            }
            out.push('"');
        }
        Jv::Arr(items) => {
            out.push('[');
            for (i, v) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                jv_stringify(v, out);
            }
            out.push(']');
        }
        Jv::Obj(entries) => {
            out.push('{');
            for (i, (k, v)) in entries.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push('"');
                out.push_str(k);
                out.push_str("\":");
                jv_stringify(v, out);
            }
            out.push('}');
        }
    }
}

struct Parser<'a> {
    s: &'a [u8],
    pos: usize,
}

impl<'a> Parser<'a> {
    fn ws(&mut self) {
        while self.pos < self.s.len() && (self.s[self.pos] as char).is_ascii_whitespace() {
            self.pos += 1;
        }
    }
    fn peek(&mut self) -> u8 {
        self.ws();
        if self.pos < self.s.len() {
            self.s[self.pos]
        } else {
            0
        }
    }
    fn value(&mut self) -> Option<Jv> {
        match self.peek() {
            b'{' => {
                self.pos += 1;
                let mut entries = Vec::new();
                if self.peek() == b'}' {
                    self.pos += 1;
                    return Some(Jv::Obj(entries));
                }
                loop {
                    if self.peek() != b'"' {
                        return None;
                    }
                    self.pos += 1;
                    let ks = self.pos;
                    while self.pos < self.s.len() && self.s[self.pos] != b'"' {
                        if self.s[self.pos] == b'\\' {
                            self.pos += 1;
                        }
                        self.pos += 1;
                    }
                    let key = String::from_utf8_lossy(&self.s[ks..self.pos]).into_owned();
                    self.pos += 1;
                    if self.peek() != b':' {
                        return None;
                    }
                    self.pos += 1;
                    let v = self.value()?;
                    entries.push((key, v));
                    if self.peek() == b',' {
                        self.pos += 1;
                        continue;
                    }
                    if self.peek() == b'}' {
                        self.pos += 1;
                        return Some(Jv::Obj(entries));
                    }
                    return None;
                }
            }
            b'[' => {
                self.pos += 1;
                let mut items = Vec::new();
                if self.peek() == b']' {
                    self.pos += 1;
                    return Some(Jv::Arr(items));
                }
                loop {
                    let v = self.value()?;
                    items.push(v);
                    if self.peek() == b',' {
                        self.pos += 1;
                        continue;
                    }
                    if self.peek() == b']' {
                        self.pos += 1;
                        return Some(Jv::Arr(items));
                    }
                    return None;
                }
            }
            b'"' => {
                self.pos += 1;
                let st = self.pos;
                while self.pos < self.s.len() && self.s[self.pos] != b'"' {
                    if self.s[self.pos] == b'\\' {
                        self.pos += 1;
                    }
                    self.pos += 1;
                }
                let v = String::from_utf8_lossy(&self.s[st..self.pos]).into_owned();
                self.pos += 1;
                Some(Jv::Str(v))
            }
            b't' if self.s[self.pos..].starts_with(b"true") => {
                self.pos += 4;
                Some(Jv::Bool(true))
            }
            b'f' if self.s[self.pos..].starts_with(b"false") => {
                self.pos += 5;
                Some(Jv::Bool(false))
            }
            b'n' if self.s[self.pos..].starts_with(b"null") => {
                self.pos += 4;
                Some(Jv::Null)
            }
            c if c == b'-' || c.is_ascii_digit() => {
                let st = self.pos;
                self.pos += 1;
                while self.pos < self.s.len() {
                    match self.s[self.pos] {
                        b'0'..=b'9' | b'.' | b'e' | b'E' | b'+' | b'-' => self.pos += 1,
                        _ => break,
                    }
                }
                let txt = std::str::from_utf8(&self.s[st..self.pos]).ok()?;
                let is_float = txt.contains('.') || txt.contains('e') || txt.contains('E');
                if is_float {
                    Some(Jv::Float(txt.parse().ok()?))
                } else {
                    Some(Jv::Int(txt.parse().ok()?))
                }
            }
            _ => None,
        }
    }
}

fn load_json() {
    let mut checksum: i64 = 0;
    for round in 0..40i64 {
        let mut entries: Vec<(String, Jv)> = Vec::new();
        for i in 0..20i64 {
            let mut inner: Vec<(String, Jv)> = Vec::new();
            for j in 0..5i64 {
                inner.push((
                    "i".to_string() + &j.to_string(),
                    Jv::Int(round * 100 + i * 5 + j),
                ));
            }
            entries.push(("g".to_string() + &i.to_string(), Jv::Obj(inner)));
        }
        let tree = Jv::Obj(entries);
        let mut text = String::new();
        jv_stringify(&tree, &mut text);
        // parse 回读真实执行（对称成本）
        let mut p = Parser { s: text.as_bytes(), pos: 0 };
        let back = p.value().expect("json parse");
        let mut sum = 0i64;
        fn sum_ints(j: &Jv) -> i64 {
            match j {
                Jv::Int(v) => *v,
                Jv::Arr(items) => items.iter().map(sum_ints).sum(),
                Jv::Obj(entries) => entries.iter().map(|(_, v)| sum_ints(v)).sum(),
                _ => 0,
            }
        }
        sum += sum_ints(&back);
        let _ = sum;
        checksum += (text.len() as i64) % 97;
    }
    println!("{}", checksum % 1000003);
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: cmp-rs <load>");
        exit(2);
    }
    match args[1].as_str() {
        "fib-rec" => load_fib_rec(),
        "fib-iter" => load_fib_iter(),
        "qsort" => load_qsort(),
        "sieve" => load_sieve(),
        "mandelbrot" => load_mandelbrot(),
        "matmul" => load_matmul(),
        "nbody" => load_nbody(),
        "string-build-find" => load_sbf(),
        "json-roundtrip" => load_json(),
        _ => {
            eprintln!("unknown load {}", args[1]);
            exit(2);
        }
    }
}
