#!/usr/bin/env python3
# ============================================================================
# gen_vectors.py —— std/compress 与 std/image 的官方向量锚生成器（V0.40-S1）。
#
# 事实官方实现：Python 3.13 zlib/gzip（RFC 1950/1951/1952）＋ zlib 构造的
# 确定性 PNG fixture（PNG 规范 §5/§7 分块＋filter 0–4）。生成产物为固定
# 十六进制文本（tests/vectors/compress-vectors.txt／png-vectors.txt），
# **逐字节冻结**——向量不随 zlib 版本漂移（测试比对的是冻结字节与已知
# 明文/像素面 golden，不是现场重压缩）。
#
# tests/compress-fx.tx／tests/image-fx.tx 内嵌同一批十六进制常量（仓内既有
# 固定向量形态，见 v038-std-fx.tx／net-fx.tx 先例）；本目录文件为出处与
# 对账底稿。任何向量变更必须重跑本脚本并同步两处。
#
# 生成期证明（写入产物头注）：
#   - zlib.decompress/gzip.decompress 可解出 stdlib stored 写入器计划产出
#     的字节（roundtrip 官方侧证明——写入器输出与 Python level-0 stored
#     布局逐字节一致的载荷单独标注）；
#   - 三种 deflate 块形态（stored/fixed/dynamic）在向量集内全覆盖。
# ============================================================================

import gzip
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).parent

FNV_OFF = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MASK64 = (1 << 64) - 1


def fnv1a64(data: bytes) -> int:
    h = FNV_OFF
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & MASK64
    return h


# —— 确定性载荷（零随机源；LCG 仅用于扩面，序列冻结） ——

def lcg_bytes(n: int, seed: int) -> bytes:
    x = seed
    out = bytearray()
    for _ in range(n):
        x = (1103515245 * x + 12345) % (1 << 31)
        out.append(x & 0xFF)
    return bytes(out)


def build_dyn_payload() -> bytes:
    """混合分布：随机字面段＋重复段交替——zlib level 9 稳定选 dynamic 块。"""
    parts = []
    for i in range(8):
        parts.append(lcg_bytes(100, 1000 + i))
        parts.append(b"taixu-stdlib-compress-vector-" * 2)
    return b"".join(parts)[:1600]


P_EMPTY = b""
P_SHORT = b"hello taixu"
P_REP = b"taixu taixu taixu taixu taixu compress compress compress"
P_FIPS = b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"
P_DYN = build_dyn_payload()

PAYLOADS = [
    ("p_empty", P_EMPTY),
    ("p_short", P_SHORT),
    ("p_rep", P_REP),
    ("p_fips", P_FIPS),
    ("p_dyn", P_DYN),
]

# —— compress 向量生成 ——

def gen_compress() -> str:
    lines = []
    lines.append("# std/compress 向量锚（gen_vectors.py 冻结产物；勿手改）")
    lines.append("# 载荷与流均为十六进制；adler/crc 为十进制 int。")
    seen_btypes = set()

    def note_btype(stream: bytes, wrapped: str) -> None:
        off = 2 if wrapped == "zlib" else (10 if wrapped == "gzip" else 0)
        b0 = stream[off]
        seen_btypes.add((b0 >> 1) & 0x3)

    for name, p in PAYLOADS:
        lines.append(f"# --- {name} ({len(p)} bytes) ---")
        lines.append(f"payload:{name} = {p.hex()}")
        for lvl in (1, 6, 9):
            z = zlib.compress(p, lvl)
            note_btype(z, "zlib")
            lines.append(f"zlib_l{lvl}:{name} = {z.hex()}")
            raw = zlib.compressobj(lvl, zlib.DEFLATED, -15)
            r = raw.compress(p) + raw.flush()
            note_btype(r, "raw")
            lines.append(f"raw_l{lvl}:{name} = {r.hex()}")
        g = gzip.compress(p, mtime=0)
        note_btype(g, "gzip")
        lines.append(f"gzip:{name} = {g.hex()}")
        lines.append(f"adler32:{name} = {zlib.adler32(p) & 0xFFFFFFFF}")
        lines.append(f"crc32:{name} = {zlib.crc32(p) & 0xFFFFFFFF}")
        lines.append(f"fnv1a64:{name} = {fnv1a64(p)}")

    # stored 写入器对账底稿：Python level-0 zlib（stored 块）与本仓写入器
    # 的计划产出逐字节一致（单块载荷：78 01 + BFINAL stored + LEN/NLEN +
    # 数据 + adler32）；生成期官方侧解压证明。
    for name, p in PAYLOADS:
        if len(p) > 0:
            z0 = zlib.compress(p, 0)
            assert zlib.decompress(z0) == p
            note_btype(z0, "zlib")
            lines.append(f"zlib_store0:{name} = {z0.hex()}")
            gz_hdr = b"\x1f\x8b\x08\x00" + b"\x00" * 4 + b"\x00" + b"\xff"
            co = zlib.compressobj(0, zlib.DEFLATED, -15)
            store_raw = co.compress(p) + co.flush()
            note_btype(store_raw, "raw")
            gz_ours = gz_hdr + store_raw + struct.pack("<II", zlib.crc32(p) & 0xFFFFFFFF, len(p) & 0xFFFFFFFF)
            assert gzip.decompress(gz_ours) == p
            lines.append(f"gzip_store:{name} = {gz_ours.hex()}")

    lines.append(f"# 块形态覆盖：{{stored=0, fixed=1, dynamic=2}} → {sorted(seen_btypes)}")
    assert seen_btypes == {0, 1, 2}, seen_btypes
    # 单块 stored 载荷（Python level-0 输出与写入器 canonical 形逐字节一致，
    # 可作写入器 golden；p_dyn 被 zlib 按 1264+336 分块——只做 roundtrip）。
    lines.append("# store_single_block = p_short,p_rep,p_fips")

    # 负例（结构化 Error 断言面；craft＋翻转全部确定性）。
    z1s = zlib.compress(P_SHORT, 1)
    g1s = gzip.compress(P_SHORT, mtime=0)
    st0s = zlib.compress(P_SHORT, 0)
    lines.append("neg:zlib_bad_hdr = " + (b"\x79" + z1s[1:]).hex())          # CM≠8
    lines.append("neg:zlib_bad_adler = " + (z1s[:-1] + bytes([z1s[-1] ^ 0x01])).hex())
    lines.append("neg:zlib_fdict = " + (b"\x78\x20\x00\x00\x00\x00\x03\x00\x00\x00\x00\x01").hex())
    lines.append("neg:zlib_trunc = " + z1s[:6].hex())
    lines.append("neg:zlib_stored_lenbad = " + (st0s[:4] + bytes([st0s[4] ^ 0x01]) + st0s[5:]).hex())
    lines.append("neg:gzip_bad_magic = " + (b"\x1f\x8c" + g1s[2:]).hex())
    lines.append("neg:gzip_bad_crc = " + (g1s[:-8] + bytes([g1s[-8] ^ 0xFF]) + g1s[-7:]).hex())
    lines.append("neg:gzip_bad_isize = " + (g1s[:-1] + bytes([g1s[-1] ^ 0x01])).hex())
    lines.append("neg:gzip_trunc = " + g1s[:12].hex())
    lines.append("neg:raw_btype3 = " + bytes([0x07, 0x00]).hex())            # final＋非法形态 3

    # dynamic 空码长码：bfinal=1＋btype=10＋HLIT=0/HDIST=0/HCLEN=0＋4×000
    # ＝码长码全零 → 解码首符号即流终（结构化 Error）。
    bits_out = []

    def put(val, n):
        for i in range(n):
            bits_out.append((val >> i) & 1)

    put(1, 1)   # bfinal
    put(2, 2)   # btype=dynamic
    put(0, 5)   # HLIT-257
    put(0, 5)   # HDIST-1
    put(0, 4)   # HCLEN-4
    for _ in range(4):
        put(0, 3)
    while len(bits_out) % 8:
        bits_out.append(0)
    dyn_empty = bytes(
        sum(bits_out[i + j] << j for j in range(8)) for i in range(0, len(bits_out), 8)
    )
    lines.append("neg:raw_dyn_empty_clen = " + dyn_empty.hex())
    return "\n".join(lines) + "\n"


# —— PNG fixture 生成 ——

def chunk(typ: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + typ + data + struct.pack(
        ">I", zlib.crc32(typ + data) & 0xFFFFFFFF
    )


def build_png(w, h, depth, ctype, raw_rows, plte=None, trns=None,
              idat_split=1, interlace=0, corrupt=None, flip_depth=False,
              flip_interlace=False, bad_magic=False):
    ihdr = struct.pack(">IIBBBBB", w, h, 16 if flip_depth else depth, ctype,
                       0, 0, 1 if flip_interlace else interlace)
    raw = b"".join(bytes([f]) + r for f, r in raw_rows)
    comp = zlib.compress(raw, 9)
    out = b"\x89PNG\r\n\x1a\n" if not bad_magic else b"\x88PNG\r\n\x1a\n"
    out += chunk(b"IHDR", ihdr)
    if plte is not None:
        out += chunk(b"PLTE", plte)
    if trns is not None:
        out += chunk(b"tRNS", trns)
    step = max(1, len(comp) // idat_split)
    parts = [comp[i:i + step] for i in range(0, len(comp), step)][:idat_split]
    if idat_split > 1:
        parts = [comp[:len(comp) // 2], comp[len(comp) // 2:]]
    for part in parts:
        out += chunk(b"IDAT", part)
    out += chunk(b"IEND", b"")
    if corrupt == "crc":
        # 翻转 IDAT 块 CRC 首字节（定位于 IEND 前 4 字节）。
        i = out.rindex(b"IEND")
        out = out[: i - 4] + bytes([out[i - 4] ^ 0xFF]) + out[i - 3:]
    return out


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def apply_filter(f, row, prev, ch):
    """PNG §6 filter 编码（stored = orig − predictor；prev＝上一原始行）。"""
    out = bytearray()
    for x in range(len(row)):
        a = row[x - ch] if x >= ch else 0
        b = prev[x] if prev is not None else 0
        c = prev[x - ch] if (prev is not None and x >= ch) else 0
        if f == 0:
            v = row[x]
        elif f == 1:
            v = row[x] - a
        elif f == 2:
            v = row[x] - b
        elif f == 3:
            v = row[x] - ((a + b) >> 1)
        else:
            v = row[x] - paeth(a, b, c)
        out.append(v & 0xFF)
    return bytes(out)


def rows_from_pixels(filters, plane, w, ch):
    """plane: rows of component tuples → 已编码（filter 已应用）的扫描行。"""
    flat = [bytes(c for px in row for c in px) for row in plane]
    out = []
    for y, f in enumerate(filters):
        prev = flat[y - 1] if y > 0 else None
        out.append((f, apply_filter(f, flat[y], prev, ch)))
    return out


def gen_png() -> str:
    lines = ["# std/image PNG 向量锚（gen_vectors.py 冻结产物；勿手改）"]
    lines.append("# pixels_fnv = RGBA8 像素面（w*h*4 字节行主序）FNV-1a 64。")

    def emit(name, blob, w, h, plane_rgba):
        plane = bytes(c for row in plane_rgba for px in row for c in px)
        lines.append(f"png:{name} = {blob.hex()}")
        lines.append(f"size:{name} = {w}x{h}")
        lines.append(f"pixels_fnv:{name} = {fnv1a64(plane)}")
        lines.append(f"pixels0:{name} = {plane[:16].hex()}")

    # 1) 2x2 RGBA filter 0（全字面 16 值断言用；plane 值冻结于此）。
    w, h = 2, 2
    plane = [[(0, 0, 0, 255), (255, 0, 0, 255)], [(0, 255, 0, 128), (0, 0, 255, 0)]]
    emit("tiny2x2", build_png(w, h, 8, 6, rows_from_pixels([0, 0], plane, w, 4)), w, h, plane)

    # 2) 8x5 RGBA，行 filter＝0..4 全覆盖；确定性像素函数。
    w, h = 8, 5
    plane = [[((x * 17 + y * 11 + 29) & 0xFF, (x * 5 + y * 37) & 0xFF,
               (x * 91 + y * 13) & 0xFF, (200 - x * 7 + y * 3) & 0xFF)
              for x in range(w)] for y in range(h)]
    emit("filters8x5", build_png(w, h, 8, 6, rows_from_pixels(list(range(5)), plane, w, 4)), w, h, plane)

    # 3) 8x4 灰度（type 0），filter 0/4/1/2。
    w, h = 8, 4
    plane = [[(v, v, v, 255) for v in ((x * 23 + y * 41) & 0xFF for x in range(w))] for y in range(h)]
    gray_rows1 = [[(px[0],) for px in row] for row in plane]
    emit("gray8x4", build_png(w, h, 8, 0, rows_from_pixels([0, 4, 1, 2], gray_rows1, w, 1)), w, h, plane)

    # 4) 4x2 灰度＋tRNS（灰值 51 → alpha 0）。
    w, h = 4, 2
    plane = [[(51, 51, 51, 0) if (x == 1 and y == 0) else ((x * 60 + y * 100) & 0xFF,) * 3 + (255,)
              for x in range(w)] for y in range(h)]
    gray_rows = [[(px[0],) for px in row] for row in plane]
    emit("gray_trns4x2", build_png(w, h, 8, 0, rows_from_pixels([0, 2], gray_rows, w, 1), trns=struct.pack(">H", 51)), w, h, plane)

    # 5) 12x8 RGB（type 2），filter 循环 0..4，IDAT 拆 2 块——gui 消费 fixture。
    w, h = 12, 8
    plane = [[((x * 13 + y * 29 + 7) & 0xFF, (x * 3 + y * 71 + 100) & 0xFF,
               (x * 57 + y * 5) & 0xFF, 255)
              for x in range(w)] for y in range(h)]
    rgb_rows3 = [[px[:3] for px in row] for row in plane]
    emit("rgb12x8", build_png(w, h, 8, 2, rows_from_pixels([y % 5 for y in range(h)], rgb_rows3, w, 3), idat_split=2), w, h, plane)

    # 6) 6x4 调色板（type 3）＋PLTE＋tRNS（alpha 255/128/0/255）。
    w, h = 6, 4
    plte = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 240, 240, 240])
    trns = bytes([255, 128, 0, 255])
    idx_rows = [[(x + y) % 4 for x in range(w)] for y in range(h)]
    alpha_map = [255, 128, 0, 255]
    pal_rgb = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (240, 240, 240)]
    plane = [[pal_rgb[i] + (alpha_map[i],) for i in row] for row in idx_rows]
    emit("pal6x4", build_png(w, h, 8, 3,
                             rows_from_pixels([y % 3 for y in range(h)],
                                              [[(i,) for i in row] for row in idx_rows],
                                              w, 1),
                             plte=plte, trns=trns), w, h, plane)

    # 7) 6x4 灰＋alpha（type 4）。
    w, h = 6, 4
    plane = [[(v, v, v, a) for v, a in
              zip(((x * 37 + y * 19) & 0xFF for x in range(w)),
                  ((x * 11 + y * 53 + 60) & 0xFF for x in range(w)))]
             for y in range(h)]
    ga_rows = [[(px[0], px[3]) for px in row] for row in plane]
    emit("ga6x4", build_png(w, h, 8, 4, rows_from_pixels([1, 3, 2, 0], ga_rows, w, 2)), w, h, plane)

    # 8) 32x16 RGBA 大面（inflate 工作量锚；filter 循环全覆盖）。
    w, h = 32, 16
    plane = [[((x * 7 + y * 13) & 0xFF, (x * 29 + y) & 0xFF, (x + y * 31) & 0xFF,
               255 if (x + y) % 3 else 64)
              for x in range(w)] for y in range(h)]
    emit("big32x16", build_png(w, h, 8, 6, rows_from_pixels([y % 5 for y in range(h)], plane, w, 4)), w, h, plane)

    # 9) 负例（结构化 Error 断言面）。
    ok1x1 = build_png(1, 1, 8, 6, [(0, b"\x00\x00\x00\xff")])
    lines.append(f"png_ok1x1 = {ok1x1.hex()}")
    lines.append(f"png_bad_magic = {build_png(1, 1, 8, 6, [(0, b'\x00\x00\x00\x00\xff')], bad_magic=True).hex()}")
    lines.append(f"png_depth16 = {build_png(1, 1, 8, 6, [(0, b'\x00\x00\x00\x00\xff')], flip_depth=True).hex()}")
    lines.append(f"png_interlaced = {build_png(1, 1, 8, 6, [(0, b'\x00\x00\x00\x00\xff')], flip_interlace=True).hex()}")
    lines.append(f"png_bad_crc = {build_png(1, 1, 8, 6, [(0, b'\x00\x00\x00\x00\xff')], corrupt='crc').hex()}")
    return "\n".join(lines) + "\n"


def main():
    (HERE / "compress-vectors.txt").write_text(gen_compress(), encoding="utf-8")
    (HERE / "png-vectors.txt").write_text(gen_png(), encoding="utf-8")
    print("vectors written:", HERE)


if __name__ == "__main__":
    main()
