#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 static/logo.png 生成网站图标（favicon / PWA / iOS 桌面图标）。

纯 Python 标准库实现，不依赖 Pillow / ImageMagick。
换 logo 后重新执行本脚本即可全量刷新图标：

    python deploy/gen_icons.py

产物：
    static/favicon.ico                  16/32/48 三尺寸（PNG 内嵌，Vista+ 浏览器均可读）
    static/icons/favicon-16.png         透明底
    static/icons/favicon-32.png         透明底
    static/icons/favicon-48.png         透明底
    static/icons/apple-touch-icon.png   180 不透明品牌蓝底（iOS 添加到主屏幕）
    static/icons/icon-192.png           不透明品牌蓝底（Android / PWA）
    static/icons/icon-512.png           不透明品牌蓝底（PWA 启动画面）
    static/manifest.webmanifest         PWA 清单（名称、主题色、图标）
"""
import math
import struct
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
LOGO = BASE / "static" / "logo.png"
STATIC = BASE / "static"
ICONS = STATIC / "icons"

# 官网同款品牌色
BRAND_BLUE = (0x0E, 0x42, 0xD2, 255)   # --navy #0e42d2
WHITE = (255, 255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)

PNG_SIG = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- PNG 解码
def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    data = path.read_bytes()
    if not data.startswith(PNG_SIG):
        raise ValueError("不是 PNG 文件: %s" % path)
    pos, idat, w, h = 8, b"", 0, 0
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, color, _comp, _filt, inter = struct.unpack(">IIBBBBB", chunk)
            if (depth, color, inter) != (8, 6, 0):
                raise ValueError("仅支持 8 位 RGBA 非隔行 PNG，当前为 %s" % ((depth, color, inter),))
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat)
    bpp, stride = 4, w * 4
    rows, prev, off = [], bytearray(stride), 0
    for _ in range(h):
        f = raw[off]
        off += 1
        line = bytearray(raw[off:off + stride])
        off += stride
        if f:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                if f == 1:
                    line[i] = (line[i] + a) & 255
                elif f == 2:
                    line[i] = (line[i] + b) & 255
                elif f == 3:
                    line[i] = (line[i] + ((a + b) >> 1)) & 255
                elif f == 4:
                    line[i] = (line[i] + _paeth(a, b, c)) & 255
                else:
                    raise ValueError("未知 PNG 行滤波器 %d" % f)
        rows.append(line)
        prev = line
    return w, h, rows


def alpha_bbox(w, h, rows, threshold=8):
    """裁掉 logo 四周的透明边，返回 (x0,y0,x1,y1)。"""
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = rows[y]
        for x in range(w):
            if row[x * 4 + 3] > threshold:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < 0:
        raise ValueError("logo 全透明，无法生成图标")
    return x0, y0, x1 + 1, y1 + 1


def crop(w, h, rows, box):
    x0, y0, x1, y1 = box
    out = []
    for y in range(y0, y1):
        out.append(rows[y][x0 * 4:x1 * 4])
    return x1 - x0, y1 - y0, out


# ---------------------------------------------------------------- 高质量重采样
def _weights(src_len, dst_len):
    """目标轴每个像素对应的 (源索引, 权重)。缩小时用面积平均，放大时用双线性。"""
    scale = src_len / float(dst_len)
    result = []
    for d in range(dst_len):
        if scale >= 1.0:
            lo, hi = d * scale, (d + 1) * scale
            s, ws = int(math.floor(lo)), []
            while s < hi and s < src_len:
                wt = min(hi, s + 1) - max(lo, s)
                if wt > 0:
                    ws.append((min(s, src_len - 1), wt))
                s += 1
            result.append(ws)
        else:
            c = (d + 0.5) * scale - 0.5
            s0 = int(math.floor(c))
            f = c - s0
            a = min(max(s0, 0), src_len - 1)
            b = min(max(s0 + 1, 0), src_len - 1)
            result.append([(a, 1 - f), (b, f)])
    return result


def resample(sw, sh, rows, dw, dh):
    """预乘 alpha 的重采样，返回 dw*dh*4 的 RGBA bytearray（非预乘）。"""
    wx, wy = _weights(sw, dw), _weights(sh, dh)
    out = bytearray(dw * dh * 4)
    for y, wys in enumerate(wy):
        line_start = y * dw * 4
        for x, wxs in enumerate(wx):
            pr = pg = pb = pa = wsum = 0.0
            for sy, wyi in wys:
                row = rows[sy]
                for sx, wxi in wxs:
                    wt = wxi * wyi
                    if wt <= 0:
                        continue
                    i = sx * 4
                    a = row[i + 3] / 255.0
                    wsum += wt
                    pa += wt * a
                    pr += wt * row[i] * a
                    pg += wt * row[i + 1] * a
                    pb += wt * row[i + 2] * a
            o = line_start + x * 4
            if pa <= 0 or wsum <= 0:
                continue
            # 归一化面积（缩小时 wsum = scale_x*scale_y），并反预乘
            inv = 1.0 / pa
            out[o] = max(0, min(255, int(pr * inv + 0.5)))
            out[o + 1] = max(0, min(255, int(pg * inv + 0.5)))
            out[o + 2] = max(0, min(255, int(pb * inv + 0.5)))
            out[o + 3] = max(0, min(255, int(255 * pa / wsum + 0.5)))
    return out


# ---------------------------------------------------------------- 图标合成
def _over(dst, src, a):
    """src (rgb) 以覆盖率 a 叠到 dst (rgba, 0..1) 上，返回 (rgb, alpha)。"""
    dr, dg, db, da = dst
    oa = a + da * (1 - a)
    if oa <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    inv = 1.0 / oa
    return ((src[0] * a + dr * da * (1 - a)) * inv,
            (src[1] * a + dg * da * (1 - a)) * inv,
            (src[2] * a + db * da * (1 - a)) * inv, oa)


def render(size, sw, sh, rows, opaque=False, disc_ratio=0.465, logo_ratio=0.78):
    """白色圆盘衬底 + 居中 logo；opaque=True 时铺满品牌蓝底（iOS / Android 必须不透明）。"""
    inner = max(1, int(round(size * logo_ratio)))
    logo = resample(sw, sh, rows, inner, inner)
    off = (size - inner) // 2
    bg = BRAND_BLUE if opaque else TRANSPARENT
    bg4 = tuple(c / 255.0 for c in bg)
    white3 = (1.0, 1.0, 1.0)
    radius = size * disc_ratio
    feather = max(0.75, size / 160.0)
    half = size / 2.0
    out = bytearray(size * size * 4)
    for y in range(size):
        dy = y + 0.5 - half
        for x in range(size):
            dx = x + 0.5 - half
            d = math.hypot(dx, dy)
            cov = max(0.0, min(1.0, (radius + feather / 2 - d) / feather))
            col = _over(bg4, white3, cov) if cov > 0 else bg4
            lx, ly = x - off, y - off
            if 0 <= lx < inner and 0 <= ly < inner:
                i = (ly * inner + lx) * 4
                la = logo[i + 3] / 255.0
                if la > 0:
                    # 与 _over 的 0..1 量纲保持一致
                    col = _over(col, (logo[i] / 255.0, logo[i + 1] / 255.0,
                                      logo[i + 2] / 255.0), la)
            o = (y * size + x) * 4
            out[o] = max(0, min(255, int(col[0] * 255 + 0.5)))
            out[o + 1] = max(0, min(255, int(col[1] * 255 + 0.5)))
            out[o + 2] = max(0, min(255, int(col[2] * 255 + 0.5)))
            out[o + 3] = max(0, min(255, int(col[3] * 255 + 0.5)))
    return out


# ---------------------------------------------------------------- PNG / ICO 编码
def _chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def png_bytes(w, h, rgba):
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    return (PNG_SIG
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def ico_bytes(entries):
    """entries: [(size, png_bytes)]，PNG 内嵌式 ICO（Windows Vista+ / 所有现代浏览器）。"""
    head = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    table, blob = b"", b""
    for size, png in entries:
        b = size if size < 256 else 0
        table += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        blob += png
    return head + table + blob


MANIFEST = """{
  "name": "锦联国际 · 楼层报修",
  "short_name": "楼层报修",
  "description": "浙江锦联国际物流有限公司楼层报修与工单管理",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#ffffff",
  "theme_color": "#0e42d2",
  "lang": "zh-CN",
  "icons": [
    { "src": "/static/icons/icon-192.png?v=1", "sizes": "192x192", "type": "image/png", "purpose": "any" },
    { "src": "/static/icons/icon-512.png?v=1", "sizes": "512x512", "type": "image/png", "purpose": "any" }
  ]
}
"""


def main():
    if not LOGO.is_file():
        raise SystemExit("缺少 %s" % LOGO)
    ICONS.mkdir(parents=True, exist_ok=True)
    w, h, rows = read_png(LOGO)
    sw, sh, cropped = crop(w, h, rows, alpha_bbox(w, h, rows))
    print("源 logo: %dx%d，有效图形区域 %dx%d" % (w, h, sw, sh))

    targets = [
        ("favicon-16.png", 16, False),
        ("favicon-32.png", 32, False),
        ("favicon-48.png", 48, False),
        ("apple-touch-icon.png", 180, True),
        ("icon-192.png", 192, True),
        ("icon-512.png", 512, True),
    ]
    built = {}
    for name, size, opaque in targets:
        rgba = render(size, sw, sh, cropped, opaque=opaque)
        data = png_bytes(size, size, rgba)
        (ICONS / name).write_bytes(data)
        built[size] = data
        print("  %-24s %3dx%-3d %6d bytes %s" % (name, size, size, len(data),
                                                  "不透明蓝底" if opaque else "透明底"))

    ico = ico_bytes([(s, built[s]) for s in (16, 32, 48)])
    (STATIC / "favicon.ico").write_bytes(ico)
    print("  %-24s 16/32/48  %6d bytes" % ("favicon.ico", len(ico)))

    (STATIC / "manifest.webmanifest").write_text(MANIFEST, encoding="utf-8", newline="\n")
    print("  manifest.webmanifest 已生成")


if __name__ == "__main__":
    main()
