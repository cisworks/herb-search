# -*- coding: utf-8 -*-
"""
WMF/EMF 벡터 그림 -> SVG. 약전 hwpx 의 화학 구조식 그림(선, 다각형, 타원, 글자로만 이루어진 단순한 그림)을 브라우저에서 보이게 한다.
Pillow 는 윈도우가 아니면 이 형식을 못 열어서(한컴이 헤더 없는 WMF 로 저장하기도 한다) 직접 읽는다. 지원하지 않는 기록(비트맵 등)은 건너뛴다.
"""

import html
import struct


def _color(c):
    return "#%02x%02x%02x" % (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF)


class _Canvas:
    """그리기 상태(펜/붓/글꼴/글자 정렬)와 SVG 조각 모음."""

    def __init__(self):
        self.parts = []
        self.pen = ("#000000", 0, 0)  # (색, 굵기, 스타일: 5=없음)
        self.brush = ("#ffffff", 1)  # (색, 스타일: 1=속 빈)
        self.font = {"h": 12, "w": 400, "i": False, "name": "Arial"}
        self.text_color = "#000000"
        self.text_align = 0
        self.pos = (0, 0)
        self.min_x = self.min_y = 1e18
        self.max_x = self.max_y = -1e18

    def touch(self, x, y):
        self.min_x, self.max_x = min(self.min_x, x), max(self.max_x, x)
        self.min_y, self.max_y = min(self.min_y, y), max(self.max_y, y)

    def stroke_attrs(self, scale):
        color, w, style = self.pen
        if style == 5:
            return 'stroke="none"'
        w = max(w, scale)
        return f'stroke="{color}" stroke-width="{w:g}" stroke-linecap="round" stroke-linejoin="round"'

    def fill_attr(self):
        color, style = self.brush
        return 'fill="none"' if style == 1 else f'fill="{color}"'

    def line(self, x1, y1, x2, y2):
        self.touch(x1, y1)
        self.touch(x2, y2)
        self.parts.append(("line", x1, y1, x2, y2, self.pen))

    def poly(self, pts, closed):
        for x, y in pts:
            self.touch(x, y)
        self.parts.append(("poly", list(pts), closed, self.pen, self.brush))

    def ellipse(self, l, t, r, b):
        self.touch(l, t)
        self.touch(r, b)
        self.parts.append(("ellipse", l, t, r, b, self.pen, self.brush))

    def rect(self, l, t, r, b):
        self.poly([(l, t), (r, t), (r, b), (l, b)], True)

    def text(self, x, y, s):
        if not s.strip():
            return
        self.touch(x, y)
        self.parts.append(("text", x, y, s, dict(self.font), self.text_color, self.text_align))

    def svg(self, org, ext):
        if ext and ext[0] and ext[1]:
            x0, y0 = org
            w, h = ext
            if w < 0:
                x0, w = x0 + w, -w
            if h < 0:
                y0, h = y0 + h, -h
        elif self.max_x > self.min_x and self.max_y > self.min_y:
            pad = 4
            x0, y0 = self.min_x - pad, self.min_y - pad
            w, h = self.max_x - self.min_x + 2 * pad, self.max_y - self.min_y + 2 * pad
        else:
            return None
        scale = max(w, h) / 400.0  # 펜 굵기 0(장치 1픽셀)을 그림 크기에 맞는 가는 선으로
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:g} {y0:g} {w:g} {h:g}" width="{w:g}" height="{h:g}">']
        for p in self.parts:
            k = p[0]
            if k == "line":
                _, x1, y1, x2, y2, pen = p
                if pen[2] == 5:
                    continue
                out.append(f'<path d="M{x1:g} {y1:g}L{x2:g} {y2:g}" fill="none" stroke="{pen[0]}" stroke-width="{max(pen[1], scale):g}" stroke-linecap="round"/>')
            elif k == "poly":
                _, pts, closed, pen, brush = p
                d = "M" + "L".join(f"{x:g} {y:g}" for x, y in pts) + ("Z" if closed else "")
                fill = "none" if brush[1] == 1 or not closed else brush[0]
                stroke = 'stroke="none"' if pen[2] == 5 else f'stroke="{pen[0]}" stroke-width="{max(pen[1], scale):g}" stroke-linejoin="round" stroke-linecap="round"'
                out.append(f'<path d="{d}" fill="{fill}" {stroke}/>')
            elif k == "ellipse":
                _, l, t, r, b, pen, brush = p
                fill = "none" if brush[1] == 1 else brush[0]
                stroke = 'stroke="none"' if pen[2] == 5 else f'stroke="{pen[0]}" stroke-width="{max(pen[1], scale):g}"'
                out.append(f'<ellipse cx="{(l + r) / 2:g}" cy="{(t + b) / 2:g}" rx="{abs(r - l) / 2:g}" ry="{abs(b - t) / 2:g}" fill="{fill}" {stroke}/>')
            elif k == "text":
                _, x, y, s, f, color, align = p
                size = abs(f["h"]) or 12
                h_al, v_al = align & 6, align & 24
                anchor = "middle" if h_al == 6 else ("end" if h_al == 2 else "start")
                dy = {0: 0.8, 8: -0.2, 24: 0.0}.get(v_al, 0.8) * size
                weight = ' font-weight="700"' if f["w"] >= 600 else ""
                style = ' font-style="italic"' if f["i"] else ""
                name = html.escape(f["name"] or "Arial", quote=True)
                out.append(
                    f'<text x="{x:g}" y="{y + dy:g}" font-size="{size:g}" font-family="{name}, Arial, sans-serif" fill="{color}" text-anchor="{anchor}"{weight}{style}>{html.escape(s)}</text>'
                )
        out.append("</svg>")
        return "".join(out)


# ---- WMF -------------------------------------------------------------------------------------------
def _wmf(data):
    off = 0
    bbox = None
    if data[:4] == b"\xd7\xcd\xc6\x9a":  # 위치 지정(placeable) 머리
        l, t, r, b = struct.unpack_from("<hhhh", data, 6)
        bbox = (l, t, r - l, b - t)
        off = 22
    hdr_words = struct.unpack_from("<H", data, off + 2)[0]
    pos = off + hdr_words * 2
    cv = _Canvas()
    objs = []
    win_org, win_ext = (0, 0), None
    n = len(data)

    def i16(p, k):
        return struct.unpack_from("<h", data, p + 6 + 2 * k)[0]

    def u16(p, k):
        return struct.unpack_from("<H", data, p + 6 + 2 * k)[0]

    def u32(p, k):
        return struct.unpack_from("<I", data, p + 6 + 2 * k)[0]

    def add_obj(o):
        for i, v in enumerate(objs):
            if v is None:
                objs[i] = o
                return
        objs.append(o)

    while pos + 6 <= n:
        size, fn = struct.unpack_from("<IH", data, pos)
        if size < 3 or fn == 0:
            break
        try:
            if fn == 0x020B:  # SetWindowOrg(y, x)
                win_org = (i16(pos, 1), i16(pos, 0))
            elif fn == 0x020C:  # SetWindowExt(cy, cx)
                win_ext = (i16(pos, 1), i16(pos, 0))
            elif fn == 0x02FA:  # CreatePenIndirect
                add_obj(("pen", _color(u32(pos, 3)), abs(i16(pos, 1)), u16(pos, 0) & 0xF))
            elif fn == 0x02FC:  # CreateBrushIndirect
                add_obj(("brush", _color(u32(pos, 1)), u16(pos, 0)))
            elif fn == 0x02FB:  # CreateFontIndirect
                p0 = pos + 6
                h, w_, _esc, _ori, weight = struct.unpack_from("<hhhhh", data, p0)
                italic = data[p0 + 10] != 0
                name = data[p0 + 18 : p0 + 50].split(b"\0")[0].decode("cp949", "replace")
                add_obj(("font", {"h": h, "w": weight, "i": italic, "name": name}))
            elif fn == 0x012D:  # SelectObject
                o = objs[u16(pos, 0)] if u16(pos, 0) < len(objs) else None
                if o:
                    if o[0] == "pen":
                        cv.pen = (o[1], o[2], o[3])
                    elif o[0] == "brush":
                        cv.brush = (o[1], o[2])
                    elif o[0] == "font":
                        cv.font = o[1]
            elif fn == 0x01F0:  # DeleteObject
                idx = u16(pos, 0)
                if idx < len(objs):
                    objs[idx] = None
            elif fn == 0x0209:  # SetTextColor
                cv.text_color = _color(u32(pos, 0))
            elif fn == 0x012E:  # SetTextAlign
                cv.text_align = u16(pos, 0)
            elif fn == 0x0214:  # MoveTo(y, x)
                cv.pos = (i16(pos, 1), i16(pos, 0))
            elif fn == 0x0213:  # LineTo(y, x)
                x, y = i16(pos, 1), i16(pos, 0)
                cv.line(cv.pos[0], cv.pos[1], x, y)
                cv.pos = (x, y)
            elif fn in (0x0324, 0x0325):  # Polygon / Polyline
                cnt = u16(pos, 0)
                pts = [(i16(pos, 1 + 2 * k), i16(pos, 2 + 2 * k)) for k in range(cnt)]
                cv.poly(pts, fn == 0x0324)
            elif fn == 0x0538:  # PolyPolygon
                npoly = u16(pos, 0)
                counts = [u16(pos, 1 + k) for k in range(npoly)]
                base = 1 + npoly
                for c in counts:
                    pts = [(i16(pos, base + 2 * k), i16(pos, base + 1 + 2 * k)) for k in range(c)]
                    cv.poly(pts, True)
                    base += 2 * c
            elif fn == 0x041B:  # Rectangle(bottom, right, top, left)
                cv.rect(i16(pos, 3), i16(pos, 2), i16(pos, 1), i16(pos, 0))
            elif fn == 0x0418:  # Ellipse
                cv.ellipse(i16(pos, 3), i16(pos, 2), i16(pos, 1), i16(pos, 0))
            elif fn == 0x0521:  # TextOut(len, string, y, x)
                ln = u16(pos, 0)
                s = data[pos + 8 : pos + 8 + ln].decode("cp949", "replace")
                p2 = pos + 8 + ln + (ln & 1)
                y, x = struct.unpack_from("<hh", data, p2)
                cv.text(x, y, s)
            elif fn == 0x0A32:  # ExtTextOut(y, x, count, options, [rect], string, [dx])
                y, x, ln, opts = i16(pos, 0), i16(pos, 1), u16(pos, 2), u16(pos, 3)
                p2 = pos + 6 + 8 + (8 if opts & 0x6 else 0)
                s = data[p2 : p2 + ln].decode("cp949", "replace")
                cv.text(x, y, s)
        except (struct.error, IndexError):
            pass
        pos += size * 2
    ext = win_ext if win_ext else (bbox[2:] if bbox else None)
    org = win_org if win_ext else (bbox[:2] if bbox else (0, 0))
    return cv.svg(org, ext)


# ---- EMF -------------------------------------------------------------------------------------------
_STOCK_PEN = {6: ("#ffffff", 0, 0), 7: ("#000000", 0, 0), 8: ("#000000", 0, 5)}
_STOCK_BRUSH = {0: ("#ffffff", 0), 1: ("#c0c0c0", 0), 2: ("#808080", 0), 3: ("#404040", 0), 4: ("#000000", 0), 5: ("#ffffff", 1)}


def _emf(data):
    cv = _Canvas()
    objs = {}
    pos = 0
    n = len(data)
    win_org, win_ext = (0, 0), None
    bounds = None
    while pos + 8 <= n:
        typ, size = struct.unpack_from("<II", data, pos)
        if size < 8:
            break
        b = pos + 8
        try:
            if typ == 1:  # HEADER
                bounds = struct.unpack_from("<iiii", data, b)
            elif typ == 10:  # SETWINDOWORGEX
                win_org = struct.unpack_from("<ii", data, b)
            elif typ == 9:  # SETWINDOWEXTEX
                win_ext = struct.unpack_from("<ii", data, b)
            elif typ == 38:  # CREATEPEN
                ih, style, wx, _wy, color = struct.unpack_from("<IIiiI", data, b)
                objs[ih] = ("pen", (_color(color), abs(wx), style & 0xF))
            elif typ == 95:  # EXTCREATEPEN
                ih = struct.unpack_from("<I", data, b)[0]
                style, width, _bs, color = struct.unpack_from("<IIII", data, b + 20)
                objs[ih] = ("pen", (_color(color), width, style & 0xF))
            elif typ == 39:  # CREATEBRUSHINDIRECT
                ih, style, color = struct.unpack_from("<III", data, b)
                objs[ih] = ("brush", (_color(color), style))
            elif typ == 82:  # EXTCREATEFONTINDIRECTW
                ih = struct.unpack_from("<I", data, b)[0]
                h, _w, _e, _o, weight = struct.unpack_from("<iiiii", data, b + 4)
                italic = data[b + 4 + 20] != 0
                name = data[b + 4 + 28 : b + 4 + 28 + 64].decode("utf-16le", "ignore").split("\0")[0]
                objs[ih] = ("font", {"h": h, "w": weight, "i": italic, "name": name})
            elif typ == 37:  # SELECTOBJECT
                ih = struct.unpack_from("<I", data, b)[0]
                if ih & 0x80000000:
                    s = ih & 0x7FFFFFFF
                    if s in _STOCK_PEN:
                        cv.pen = _STOCK_PEN[s]
                    elif s in _STOCK_BRUSH:
                        cv.brush = _STOCK_BRUSH[s]
                else:
                    o = objs.get(ih)
                    if o:
                        if o[0] == "pen":
                            cv.pen = o[1]
                        elif o[0] == "brush":
                            cv.brush = o[1]
                        else:
                            cv.font = o[1]
            elif typ == 40:  # DELETEOBJECT
                objs.pop(struct.unpack_from("<I", data, b)[0], None)
            elif typ == 24:  # SETTEXTCOLOR
                cv.text_color = _color(struct.unpack_from("<I", data, b)[0])
            elif typ == 22:  # SETTEXTALIGN
                cv.text_align = struct.unpack_from("<I", data, b)[0]
            elif typ == 27:  # MOVETOEX
                cv.pos = struct.unpack_from("<ii", data, b)
            elif typ == 54:  # LINETO
                x, y = struct.unpack_from("<ii", data, b)
                cv.line(cv.pos[0], cv.pos[1], x, y)
                cv.pos = (x, y)
            elif typ in (86, 87):  # POLYGON16 / POLYLINE16
                cnt = struct.unpack_from("<I", data, b + 16)[0]
                pts = [struct.unpack_from("<hh", data, b + 20 + 4 * k) for k in range(cnt)]
                cv.poly(pts, typ == 86)
            elif typ in (3, 4):  # POLYGON / POLYLINE (32비트 좌표)
                cnt = struct.unpack_from("<I", data, b + 16)[0]
                pts = [struct.unpack_from("<ii", data, b + 20 + 8 * k) for k in range(cnt)]
                cv.poly(pts, typ == 3)
            elif typ == 42:  # ELLIPSE
                cv.ellipse(*struct.unpack_from("<iiii", data, b))
            elif typ == 43:  # RECTANGLE
                cv.rect(*struct.unpack_from("<iiii", data, b))
            elif typ == 84:  # EXTTEXTOUTW
                x, y, nchars, off_str = struct.unpack_from("<iiII", data, b + 28)
                s = data[pos + off_str : pos + off_str + nchars * 2].decode("utf-16le", "ignore")
                cv.text(x, y, s)
        except (struct.error, IndexError):
            pass
        pos += size
    if win_ext is None and bounds:
        org, ext = (bounds[0], bounds[1]), (bounds[2] - bounds[0], bounds[3] - bounds[1])
        return cv.svg(org, ext)
    return cv.svg(win_org, win_ext)


def to_svg(data, ext):
    """WMF/EMF 바이트 -> SVG 문자열(못 바꾸면 None)."""
    return _wmf(data) if ext == "wmf" else _emf(data)
