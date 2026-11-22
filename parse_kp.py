# -*- coding: utf-8 -*-
"""
대한민국약전(KP 13, 제2026-44호) hwpx 6개를 읽어 "대한민국약전 검색"(/kp)에서 쓰는 항목 목록으로 만든다.

    [별표 1] 통칙            -> 번호 제목(1. 일반사항 ...)마다 한 항목
    [별표 2] 제제총칙        -> Ⅰ./Ⅱ., 1., 1.1. 제목마다 한 항목
    [별표 3] 의약품각조 제1부 -> 품목(한글명 + 영문명)마다 한 항목
    [별표 4] 의약품각조 제2부 -> 품목마다 한 항목(그룹: 생물학적제제 등, 방사성의약품, 첨가제, 의약외품 ...)
    [별표 5] 일반시험법      -> "N. 시험법 이름"마다 한 항목
    [별표 6] 일반정보        -> "N. 이름"마다 한 항목

문단은 hwpx 의 순서대로 HTML 로 바꾼다(굵게/기울임/위·아래첨자, 수식, 표, 그림). 의약품각조는 "성상/확인시험/순도시험..." 항목명
(문자 스타일 "항목명")으로 구간을 나눠 생약 검색 화면과 같은 접고 펴는 구간으로 보여 준다.
수식 변환은 parse_hwpx 의 변환기를 그대로 쓴다. 이 모듈은 항목의 본문(html)을 zlib 로 압축해 담는다(메모리를 아끼려고).
"""

import collections
import html
import re
import zipfile
import zlib
from xml.etree import ElementTree as ET

from parse_hwpx import (
    _EQ_SUB_MARKER_RE,
    _ITALIC_MARKER_RE,
    _apply_equation_markup,
    _convert_equation,
    _plain_markup,
    fix_private_use_chars,
)

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
HC = "{http://www.hancom.co.kr/hwpml/2011/core}"

# 문서 번호 -> (파일명 머리, 화면 이름, 종류)
DOCS = {
    1: ("[별표 1]", "통칙", "ref"),
    2: ("[별표 2]", "제제총칙", "ref"),
    3: ("[별표 3]", "의약품각조 제1부", "drug"),
    4: ("[별표 4]", "의약품각조 제2부", "drug"),
    5: ("[별표 5]", "일반시험법", "ref"),
    6: ("[별표 6]", "일반정보", "ref"),
}

IMG_URL = "/kp/img/{doc}/{name}"

_HANGUL_RE = re.compile(r"[가-힣]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WS_RE = re.compile(r"\s+")

# 글자 서식 비트
B_BOLD, B_ITALIC, B_SUB, B_SUP, B_UNDER = 1, 2, 4, 8, 16

# 사용자 영역(한컴 기호 글꼴) 글자 -> 화면에 보이는 글자
_PUA_MAP = {
    "": "·",
    "": "•",
    "": "|",
    "\U000f070b": "蓖",
}
_PUA_LEFT_RE = re.compile("[-]")  # 위에서 바꾸지 못한 사용자 영역 글자(화면에 네모로 보인다)는 뺀다

_NAME_STYLES = {
    "* 의약품명",
    "* 의약품명 사본2",
    "* 의약외품명",
    "* 의약외품명 사본2",
    "* 생약제제명",
    "* 생물학적제제명",
}
# 굵은 제목처럼 보이게 할 문단 스타일(일반시험법/일반정보 본문의 소제목들)
_HEADING_STYLES = {"* 항목명", "* 소항목명", "* 표제목", "표제목", "* 부록제목", "* 그림제목", "그림제목", "소제목", "제목", "* 큰제목"}


def _local(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _fix_text(s):
    if not s:
        return s
    for bad, good in _PUA_MAP.items():
        if bad in s:
            s = s.replace(bad, good)
    return _PUA_LEFT_RE.sub("", fix_private_use_chars(s))


def equation_html(script):
    """hwpx 수식 스크립트 -> (HTML, 평문)."""
    try:
        marked = _convert_equation(script)
    except Exception:  # noqa: BLE001  변환 못 하는 수식은 원문 스크립트를 그대로 보여 준다
        return html.escape(script or ""), script or ""
    esc = html.escape(marked, quote=False)
    esc = _ITALIC_MARKER_RE.sub(r"<i>\1</i>", esc)
    esc = _EQ_SUB_MARKER_RE.sub(r"<sub>\1</sub>", esc)
    return _apply_equation_markup(esc), _plain_markup(marked)


class _Header:
    """header.xml 의 문자 모양/문단 모양/스타일/테두리 카탈로그."""

    def __init__(self, data):
        root = ET.fromstring(data)
        self.styles = {}  # id -> (type, name)
        for s in root.iter(HH + "style"):
            self.styles[s.get("id")] = (s.get("type"), (s.get("name") or "").strip())
        self.charpr = {}  # id -> 서식 비트
        for c in root.iter(HH + "charPr"):
            f = 0
            if c.find(HH + "bold") is not None:
                f |= B_BOLD
            if c.find(HH + "italic") is not None:
                f |= B_ITALIC
            if c.find(HH + "subscript") is not None:
                f |= B_SUB
            if c.find(HH + "supscript") is not None:
                f |= B_SUP
            u = c.find(HH + "underline")
            if u is not None and u.get("type") not in (None, "NONE"):
                f |= B_UNDER
            self.charpr[c.get("id")] = f
        self.parapr = {}  # id -> (정렬, 왼쪽 여백, 첫줄 들여쓰기)
        for p in root.iter(HH + "paraPr"):
            al = p.find(HH + "align")
            align = (al.get("horizontal") if al is not None else "JUSTIFY") or "JUSTIFY"
            marg = None
            for d in p.iter(HP + "default"):
                marg = d.find(HH + "margin")
            if marg is None:
                marg = p.find(".//" + HH + "margin")
            left = intent = 0
            if marg is not None:
                try:
                    left = int(marg.find(HC + "left").get("value") or 0)
                    intent = int(marg.find(HC + "intent").get("value") or 0)
                except (AttributeError, ValueError):
                    pass
            self.parapr[p.get("id")] = (align, left, intent)
        self.borders = {}  # id -> ((왼,오,위,아래 테두리 굵기 px 또는 0), 배경색 또는 "")
        for b in root.iter(HH + "borderFill"):
            sides = []
            for tag in ("leftBorder", "rightBorder", "topBorder", "bottomBorder"):
                e = b.find(HH + tag)
                w = 0
                if e is not None and e.get("type") not in (None, "NONE"):
                    m = re.match(r"([\d.]+)", e.get("width") or "0.1")
                    mm = float(m.group(1)) if m else 0.1
                    w = 2 if mm >= 0.3 else 1
                sides.append(w)
            fill = ""
            wb = b.find(".//" + HC + "winBrush")
            if wb is not None:
                face = wb.get("faceColor") or ""
                if face and face.lower() not in ("none", "#ffffff"):
                    fill = face
            self.borders[b.get("id")] = (tuple(sides), fill)

    def style_name(self, sid):
        return self.styles.get(sid, ("", ""))[1]


class _Ctx:
    def __init__(self, docno, header, manifest):
        self.docno = docno
        self.h = header
        self.manifest = manifest  # 그림 id -> 확장자
        self.col_w = 23668  # 한 단 폭(hwpunit): 표 폭 비율 계산용. parse_doc 이 문서에서 읽은 값으로 바꾼다
        self.drawn_skipped = 0  # 그리지 못한 도형 수(점검용)
        self.drug = DOCS[docno][2] == "drug"
        self.label_rx = None  # 항목명 서식이 빠진 문단을 알아보는 정규식(parse_doc 이 문서의 항목명 어휘로 만든다)
        self.label_words = set()  # 문서에서 배운 항목명 어휘(공백 뺀 글자)

    # ---- 글자 ---------------------------------------------------------
    def t_pieces(self, t):
        """<hp:t> 안의 글자와 탭/줄바꿈을 [문자열...] 로(탭 \\t, 줄바꿈 \\n)."""
        out = []
        if t.text:
            out.append(t.text)
        for ch in t:
            tag = _local(ch.tag)
            if tag == "tab":
                out.append("\t")
            elif tag == "lineBreak":
                out.append("\n")
            elif tag in ("nbSpace", "fwSpace"):
                out.append(" ")
            elif tag == "hyphen":
                out.append("-")
            if ch.tail:
                out.append(ch.tail)
        return _fix_text("".join(out))

    def flags_for(self, run_cp, t):
        f = self.h.charpr.get(run_cp, 0)
        sty = self.h.style_name(t.get("charStyleIDRef")).lstrip("* ").strip() if t.get("charStyleIDRef") else ""
        if sty.startswith("아래첨자"):
            f |= B_SUB
        elif sty.startswith("위첨자"):
            f |= B_SUP
        elif sty.startswith("학명"):
            f |= B_ITALIC
        return f, sty

    # ---- 문단 ---------------------------------------------------------
    def para(self, p):
        """최상위 문단 하나 -> 블록 목록. 블록: {"k":"p"|"tbl"|"img", "html", "plain", ...}"""
        blocks = []
        cur = {"html": [], "plain": [], "lab": None, "started": False}

        def flush():
            h = "".join(cur["html"])
            plain = _WS_RE.sub(" ", "".join(cur["plain"])).strip()
            if plain or "<img" in h or "kp-eq" in h:
                blocks.append(self._p_block(p, h, plain, cur["lab"]))
            cur["html"], cur["plain"], cur["lab"], cur["started"] = [], [], None, False

        for run in p.findall(HP + "run"):
            cp = run.get("charPrIDRef")
            for ch in run:
                tag = _local(ch.tag)
                if tag == "t":
                    self._text(ch, cp, cur)
                elif tag == "tbl":
                    flush()
                    thtml, tplain = self.table(ch)
                    if thtml:
                        blocks.append({"k": "tbl", "html": thtml, "plain": tplain})
                elif tag == "pic":
                    self._pic(ch, cur)
                elif tag == "equation":
                    sc = ch.find(HP + "script")
                    if sc is not None and sc.text:
                        eh, ep = equation_html(sc.text)
                        cur["html"].append(f'<span class="kp-eq">{eh}</span>')
                        cur["plain"].append(ep)
                        cur["started"] = True
                elif tag in ("container", "rect", "ellipse", "line", "polygon", "curve", "arc", "connectLine", "textart", "ole", "compose", "dutmal"):
                    self._shape(ch, cur)
        flush()
        return blocks

    def _p_block(self, p, h, plain, lab):
        al, left, intent = self.h.parapr.get(p.get("paraPrIDRef"), ("JUSTIFY", 0, 0))
        b = {"k": "p", "html": h, "plain": plain, "lab": lab, "al": al[0].lower(), "left": left, "ind": intent}
        return b

    def _text(self, t, cp, cur):
        text = self.t_pieces(t)
        if text == "":
            return
        f, sty = self.flags_for(cp, t)
        is_label = sty in ("항목명", "통칙_항목명") and not cur["started"] and text.strip()
        if is_label and self.drug:
            lab = _WS_RE.sub("", text)
            tail = ""
            m = re.match(r"^(.*?[가-힣A-Za-z])(\d{1,2}\)?)$", lab)  # "확인시험1)" 처럼 번호가 항목명에 붙은 경우
            if m:
                lab, tail = m.group(1), m.group(2)
            words = self.label_words
            if re.match(r"^\d{1,2}[).]", lab) or re.fullmatch(r"\d{1,2}\)?", lab):
                lab = ""  # "3)비소" 처럼 번호로 시작하는 것은 항목명이 아니라 구간 안의 소항목이다(아래에서 굵은 글자로 그린다)
            elif words and lab not in words:
                # 항목명 서식이 뒤따르는 글까지 번졌으면("순도시험1)용해상태", "정량법이약 ...") 앞의 항목명 단어만 떼어 낸다
                w = next((x for x in sorted(words, key=len, reverse=True) if lab.startswith(x) and len(lab) > len(x)), None)
                if w and (len(lab) > 12 or re.match(r"^\d{1,2}\)", lab[len(w):])):
                    count, k = 0, 0
                    while k < len(text) and count < len(w):
                        if not text[k].isspace():
                            count += 1
                        k += 1
                    lab, tail = w, text[k:].lstrip()
            if lab:
                cur["lab"] = lab
                cur["plain"].append(lab + " ")
                cur["started"] = True
                if tail:
                    cur["plain"].append(tail)
                    cur["html"].append(html.escape(tail, quote=False))
                return
        if not cur["started"]:
            text = text.lstrip()  # 문단 맨 앞의 공백/탭(전각 공백 포함)은 내어쓰기로 대신하므로 버린다
            if text == "":
                return
            # 항목명 문자 스타일이 빠진 문단("성 상", "확인시험  이 약은 ..."): 문서에서 배운 항목명 어휘로 알아본다
            if self.drug and self.label_rx is not None and not sty.startswith(("학명", "아래첨자", "위첨자")):
                m = self.label_rx.match(text)
                if m:
                    cur["lab"] = _WS_RE.sub("", m.group(1))
                    cur["plain"].append(cur["lab"] + " ")
                    cur["started"] = True
                    text = text[m.end():].lstrip(" \t")
                    if text == "":
                        return
        if text.strip():
            cur["started"] = True
        cur["plain"].append(text.replace("\t", " "))
        esc = html.escape(text, quote=False).replace("\t", '<span class="kp-tab"></span>').replace("\n", "<br>")
        if not esc:
            return
        if sty in ("항목명", "통칙_항목명", "소항목명", "소항목명 사본1"):
            f |= B_BOLD
        if f & B_SUB:
            esc = f"<sub>{esc}</sub>"
        if f & B_SUP:
            esc = f"<sup>{esc}</sup>"
        if f & B_ITALIC:
            esc = f"<i>{esc}</i>"
        if f & B_BOLD:
            esc = f"<b>{esc}</b>"
        if f & B_UNDER:
            esc = f"<u>{esc}</u>"
        cur["html"].append(esc)

    # ---- 그림 / 도형 ---------------------------------------------------
    def _pic(self, pic, cur):
        img = pic.find(HC + "img")
        bid = img.get("binaryItemIDRef") if img is not None else None
        if not bid:
            return
        cs = pic.find(HP + "curSz")
        w = int(cs.get("width")) if cs is not None and cs.get("width") else 0
        crop = ""
        clip, dim = pic.find(HP + "imgClip"), pic.find(HP + "imgDim")
        if clip is not None and dim is not None:
            try:
                dw, dh = int(dim.get("dimwidth")), int(dim.get("dimheight"))
                l, r = int(clip.get("left")) / dw, int(clip.get("right")) / dw
                tp, bt = int(clip.get("top")) / dh, int(clip.get("bottom")) / dh
                if dw > 0 and dh > 0 and (r - l < 0.985 or bt - tp < 0.985):
                    crop = "?c=%.4f,%.4f,%.4f,%.4f" % (max(0, l), max(0, tp), min(1, r), min(1, bt))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        ext = self.manifest.get(bid, "")
        src = IMG_URL.format(doc=self.docno, name=bid) + crop
        style = f' style="width:{w / 900:.2f}em"' if w > 0 else ""
        cur["html"].append(f'<img class="kp-img" src="{src}"{style} alt="" loading="lazy" data-ext="{ext}">')
        cur["started"] = True
        # 그림 위에 얹힌 글상자 글자는 그림 아래 줄로 붙인다
        cap = " ".join(_WS_RE.sub(" ", "".join(x.itertext())).strip() for x in pic.iter(HP + "drawText")).strip()
        if cap:
            cur["html"].append(f'<br><span class="kp-cap">{html.escape(_fix_text(cap), quote=False)}</span>')
            cur["plain"].append(cap)

    def _shape(self, shp, cur):
        """컨테이너/도형 안의 그림·수식·글상자 글자만 꺼내 그린다(선·도형 자체는 그리지 못한다)."""
        tag = _local(shp.tag)
        if tag == "ole":
            return  # 수식 개체(OLE): 같은 자리에 수식(<hp:equation>)이 따로 있다
        found = False
        for pic in shp.iter(HP + "pic"):
            self._pic(pic, cur)
            found = True
        for eq in shp.iter(HP + "equation"):
            sc = eq.find(HP + "script")
            if sc is not None and sc.text:
                eh, ep = equation_html(sc.text)
                cur["html"].append(f'<span class="kp-eq">{eh}</span>')
                cur["plain"].append(ep)
                found = True
        texts = []
        for dt in shp.iter(HP + "drawText"):
            for p in dt.iter(HP + "p"):
                s = _WS_RE.sub(" ", _fix_text("".join("".join(t.itertext()) for t in p.iter(HP + "t")))).strip()
                if s:
                    texts.append(s)
        if texts:
            cur["html"].append('<span class="kp-cap">' + "<br>".join(html.escape(s, quote=False) for s in texts) + "</span>")
            cur["plain"].append(" ".join(texts))
            cur["started"] = True
            found = True
        if not found and tag in ("container", "polygon", "curve", "connectLine"):
            self.drawn_skipped += 1
            cur["html"].append('<span class="kp-cap kp-missing">[도형 그림은 표시하지 못했습니다]</span>')
            cur["started"] = True

    # ---- 표 -----------------------------------------------------------
    def table(self, tbl):
        rows = []
        plain_parts = []
        ncols = int(tbl.get("colCnt") or 0)
        widths = {}  # 열 -> 폭(hwpunit)
        for tr in tbl.findall(HP + "tr"):
            cells = []
            for tc in tr.findall(HP + "tc"):
                addr = tc.find(HP + "cellAddr")
                span = tc.find(HP + "cellSpan")
                sz = tc.find(HP + "cellSz")
                col = int(addr.get("colAddr")) if addr is not None else len(cells)
                cs = int(span.get("colSpan") or 1) if span is not None else 1
                rs = int(span.get("rowSpan") or 1) if span is not None else 1
                if sz is not None and cs == 1:
                    widths[col] = max(widths.get(col, 0), int(sz.get("width") or 0))
                sub = tc.find(HP + "subList")
                va = (sub.get("vertAlign") if sub is not None else "CENTER") or "CENTER"
                inner_html, inner_plain = self._cell_content(sub)
                sides, fill = self.h.borders.get(tc.get("borderFillIDRef"), ((1, 1, 1, 1), ""))
                cells.append((col, cs, rs, inner_html, va, sides, fill))
                if inner_plain:
                    plain_parts.append(inner_plain)
            rows.append(cells)
        if not rows:
            return "", ""
        total = sum(widths.values())
        tw = 0
        szt = tbl.find(HP + "sz")
        if szt is not None and szt.get("width"):
            tw = int(szt.get("width"))
        pct = min(100.0, tw * 100.0 / self.col_w) if tw and self.col_w else 100.0
        out = [f'<div class="kp-tblwrap"><table class="kp-tbl" style="width:{pct:.1f}%">']
        if widths and total:
            out.append("<colgroup>" + "".join(f'<col style="width:{widths.get(c, 0) * 100.0 / total:.1f}%">' for c in range(max(widths) + 1)) + "</colgroup>")
        for cells in rows:
            out.append("<tr>")
            for col, cs, rs, inner, va, sides, fill in cells:
                attrs = ""
                if cs > 1:
                    attrs += f' colspan="{cs}"'
                if rs > 1:
                    attrs += f' rowspan="{rs}"'
                st = []
                for nm, w in zip(("left", "right", "top", "bottom"), sides):
                    st.append(f"border-{nm}:{w}px solid #555" if w else f"border-{nm}:none")
                if fill:
                    st.append(f"background:{fill}")
                if va in ("TOP", "BOTTOM"):
                    st.append(f"vertical-align:{va.lower()}")
                out.append(f'<td{attrs} style="{";".join(st)}">{inner}</td>')
            out.append("</tr>")
        out.append("</table></div>")
        return "".join(out), _WS_RE.sub(" ", " ".join(plain_parts)).strip()

    def _cell_content(self, sub):
        if sub is None:
            return "", ""
        parts, plains = [], []
        for p in sub.findall(HP + "p"):
            for b in self.para(p):
                if b["k"] == "p":
                    al = {"c": "center", "r": "right"}.get(b["al"], "")
                    parts.append(f'<div class="kp-cp"{f" style=text-align:{al}" if al else ""}>{b["html"]}</div>')
                else:
                    parts.append(b["html"])
                if b["plain"]:
                    plains.append(b["plain"])
        return "".join(parts), " ".join(plains)


# ---- 블록 -> HTML --------------------------------------------------------------------------
# 문단 맨 앞의 번호 기호: "1)", "1.", "(1)", "가)", "①", "(제 1 법)", "○" ...
_MARK_RE = re.compile(r"^(\(?\d{1,2}[.)]|\([가-힣\d]\)|[가-힣]\)|[①-⑳㉮-㉻]|\(제\s*\d+\s*법\)|[○◦•※]|[-·ㆍ]\s)")


def block_html(b, heading=False):
    if b["k"] != "p":
        return b["html"]
    style = []
    left, ind = b["left"], b["ind"]
    plain = b["plain"]
    if b.get("lab") and plain.startswith(b["lab"]):
        plain = plain[len(b["lab"]):]
    hi = b.get("hi")
    if hi is not None:
        # 번호/가나다 계층에 맞춘 들여쓰기(_apply_hierarchy): 깊이마다 한 단계 안쪽, 기호 줄은 기호 폭만큼 내어쓰기
        depth, hang, marked = hi
        ml, ti = depth * _HI_STEP + hang, (-hang if marked else 0.0)
    elif ind < 0:
        # 내어쓰기(첫 줄만 왼쪽, 나머지 줄은 안쪽)는 번호 기호로 시작하는 문단에만 적용한다. 항목명 뒤나 일반 문장에 쓰면 줄마다 들쑥날쑥해 보인다.
        if _MARK_RE.match(plain.strip()) and b["al"] != "c":
            hang = min(-ind / 900.0, 3.0)
            ml, ti = left / 900.0 + hang, -hang
        else:
            ml, ti = left / 900.0, 0.0
    else:
        ml, ti = left / 900.0, ind / 900.0
    if b["al"] == "c":  # 가운데 정렬 문단은 들여쓰기를 쓰지 않는다
        ml, ti = 0.0, 0.0
    ml = min(max(ml, 0), 9)
    if b.get("fm_top"):
        style.append("margin-top:1.75em")  # 계산식 위 빈 줄 한 줄(본문 줄 높이)
    if b.get("fm_bot"):
        style.append("margin-bottom:1.75em")  # 계산식 아래 빈 줄 한 줄
    if ml:
        style.append(f"margin-left:{ml:.2f}em")
    if ti:
        style.append(f"text-indent:{max(min(ti, 6), -6):.2f}em")
    cls = "kp-p"
    if b["al"] == "c":
        cls += " kp-c"
    elif b["al"] == "r":
        cls += " kp-r"
    if heading:
        cls += " kp-h"
    if b.get("h2"):
        cls += " kp-h2"
    st =f' style="{";".join(style)}"' if style else ""
    return f'<p class="{cls}"{st}>{b["html"]}</p>'


# ---- 번호/가나다 계층 들여쓰기 -----------------------------------------------------------------
# 문단 맨 앞 기호의 종류(1., 1.1., 1), (1), 가), (가), 가., ①, ㉮, (제 1 법), ○)별 (종류, 기호 폭 em).
_HI_KINDS = [
    ("beop", re.compile(r"^\(제\s*\d+\s*법\)"), 4.6),
    ("dec", re.compile(r"^\d{1,2}(\.\d{1,2})+\.(?=\s|[가-힣])"), 2.9),  # "1.1. 본문"과 "1.2.본문"(띄어 쓰지 않은 경우)
    ("num.", re.compile(r"^\d{1,2}\.\s"), 1.9),
    ("num)", re.compile(r"^\d{1,2}\)"), 1.7),
    ("(num)", re.compile(r"^\(\d{1,2}\)"), 2.1),
    ("kor)", re.compile(r"^[가-하]\)"), 1.7),
    ("(kor)", re.compile(r"^\([가-하]\)"), 2.1),
    ("kor.", re.compile(r"^[가-하]\.\s"), 1.7),
    ("circ", re.compile(r"^[①-⑳]"), 1.5),
    ("circkor", re.compile(r"^[㉮-㉻]"), 1.5),
    ("bul", re.compile(r"^[○◦•]\s?"), 1.3),
]
_HI_STEP = 1.9  # 한 층 들어가는 폭(em)


def _apply_hierarchy(blocks):
    """한 구간(항목명 아래, 또는 일반시험법 한 항목)의 문단들에 번호 계층에 맞는 들여쓰기(hi = (깊이, 내어쓰기 폭, 기호 줄 여부))를 붙인다.
    기호 종류가 나온 순서대로 층을 정한다(처음 나온 종류가 맨 바깥, 그 안에서 처음 나온 종류가 한 층 안쪽 ...). 이미 나온 종류가 다시 나오면
    그 층으로 돌아간다. 기호 없는 문단(설명, 조건 줄)은 바로 앞 기호 줄의 본문과 같은 위치에 맞춘다. 소제목(hd)이 나오면 처음부터 다시 센다."""
    def eligible(b):
        return b["k"] == "p" and b.get("al") != "c" and b.get("left", 0) <= 0

    def marker_of(b):
        """(종류, 기호 글자) 또는 None"""
        plain = b["plain"].strip()
        if b.get("lab") and plain.startswith(b["lab"]):
            plain = plain[len(b["lab"]):].strip()
        for k, rx, _w in _HI_KINDS:
            m = rx.match(plain)
            if m:
                return k, m.group(0)
        return None

    # 1차: 종류마다 가장 넓은 기호 폭을 구한다(1) ... 10) 처럼 자릿수가 늘어도 같은 종류는 본문 시작 위치가 같아야 한다)
    width = {}
    for b in blocks:
        if eligible(b) and not b.get("hd"):
            mk = marker_of(b)
            if mk:
                width[mk[0]] = max(width.get(mk[0], 0), _marker_width(mk[1]))
    # 2차: 층과 들여쓰기를 붙이고, 기호 뒤의 공백은 지워 기호를 고정 폭 칸에 넣는다
    stack = []  # 바깥 -> 안쪽 기호 종류
    out = []
    for b in blocks:
        if not eligible(b):
            out.append(b)
            continue
        if b.get("hd"):
            stack = []
            out.append(b)
            continue
        mk = marker_of(b)
        if mk:
            k = mk[0]
            if k in stack:
                del stack[stack.index(k) + 1 :]
            else:
                stack.append(k)
            w = width[k]
            nb = dict(b, hi=(len(stack) - 1, w, True))
            nb["html"] = _wrap_marker(b["html"], mk[1], w)
            out.append(nb)
        elif stack and not b.get("lab"):
            out.append(dict(b, hi=(len(stack) - 1, width[stack[-1]], False)))
        else:
            if b.get("lab"):
                stack = []  # 새 항목명 구간은 처음부터
            out.append(b)
    return out


def _marker_width(marker):
    """기호 글자를 그릴 때의 대략적인 폭(em) + 본문과의 간격. 숫자 .55, 한글/동그라미 숫자 1, 문장부호 .3, 공백 .28, 영문 .6."""
    w = 0.0
    for ch in marker:
        if ch.isdigit():
            w += 0.55
        elif ch.isspace():
            w += 0.28
        elif ch in ".)(·":
            w += 0.3
        elif ch.isascii():
            w += 0.6
        else:
            w += 1.0
    return max(1.5, round(w + 0.6, 2))


def _wrap_marker(h, marker, width_em):
    """문단 HTML 맨 앞의 기호(marker 글자)를 고정 폭 <span class="kp-mk"> 칸에 넣고, 기호 뒤의 공백/탭은 지운다. 그러면 원문에서 기호 뒤를
    한 칸 띄웠든 두 칸이든 붙였든 본문이 같은 위치에서 시작한다. 기호가 굵은 글자 등 여러 태그에 걸쳐 있어도 태그 짝이 맞게 닫고 다시 연다."""
    toks = re.split(r"(<[^>]+>)", h)
    vis = "".join(t for t in toks if not t.startswith("<")).lstrip()
    if not vis.startswith(marker):
        return h
    need = len(marker)
    out = ['<span class="kp-mk" style="min-width:%.2fem">' % width_em]
    stack = []  # 기호 칸 안에서 열려 있는 태그
    phase = 1  # 1: 기호 안, 2: 기호 뒤 공백 지우는 중, 3: 본문
    used = 0
    skipped_lead = False
    skip_close = False
    for t in toks:
        if t.startswith("<"):
            if phase == 1:
                out.append(t)
                nm = re.match(r"</?(\w+)", t)
                if nm and nm.group(1) not in ("br", "img"):
                    if t.startswith("</"):
                        if stack:
                            stack.pop()
                    elif not t.endswith("/>"):
                        stack.append(t)
            elif phase == 2 and t == '<span class="kp-tab">':
                skip_close = True  # 기호 뒤 탭(<span class="kp-tab"></span>)은 지운다
            elif phase == 2 and skip_close and t == "</span>":
                skip_close = False
            else:
                out.append(t)
            continue
        # 글자 조각
        if phase == 1:
            s = t
            if not skipped_lead:
                lead = len(s) - len(s.lstrip())
                s = s[lead:]
                if s:
                    skipped_lead = True
            take = min(need - used, len(s))
            out.append(s[:take])
            used += take
            if used >= need:
                out.append("".join("</%s>" % re.match(r"<(\w+)", x).group(1) for x in reversed(stack)))
                out.append("</span>")
                out.append("".join(stack))
                rest = s[take:].lstrip()
                phase = 3 if rest else 2
                if rest:
                    out.append(rest)
        elif phase == 2:
            s = t.lstrip()
            if s:
                out.append(s)
                phase = 3
        else:
            out.append(t)
    return "".join(out)


_DEF_START_RE = re.compile(r"^(이\s*약|이것|본\s*품|이\s*제제|이\s*[가-힣]+(은|는)\s)")


def _is_definition_block(b):
    """"이 약은 ..." 처럼 정의문으로 시작하는 문단. 스타일 이름("정의")은 구조식 그림 줄과 화학명 줄에도 붙어 있어 쓸 수 없다."""
    return bool(_DEF_START_RE.match(b["plain"].strip()))


_FORMULA_MERGE_MAX = 70  # 합친 줄의 글자 수 상한(생약 검색의 정량법 계산식과 같다). 넘으면 두 줄 그대로 둔다


def _merge_formula_lines(blocks):
    """정량법 계산식을 한 줄로 합친다(생약 검색의 정량법과 같다):
       "가벡세이트메실산염 (...)의 양 (mg)"            "X의 양 (mg) = 표준품의 양 (mg) × Q_T/Q_S"
       "= 표준품의 양 (mg) × Q_T/Q_S"           ->   (가운데 정렬 한 줄)
    앞 줄이 "=" 로 끝나고 다음 줄이 수식뿐인 경우("아니시딘가 =" / 수식)도 합친다. 합친 글이 너무 길면 두 줄 그대로 둔다."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None
            and b["k"] == "p"
            and prev["k"] == "p"
            and not prev.get("lab")
            and not b.get("lab")
        ):
            a_pl, b_pl = prev["plain"].strip(), b["plain"].strip()
            # 이미 한 번 합친 줄에는 "= ..." 줄을 또 붙이지 않는다(연산자로 끝난 줄에 이어지는 식만 붙인다)
            eq_first = b_pl.startswith("=") and not a_pl.startswith("=") and not prev.get("merged")
            eq_tail = a_pl.endswith("=") and "kp-eq" in b["html"] and not b_pl[:1].isalpha() and not prev.get("merged")
            centered = prev.get("al") == "c" or (prev.get("sty") or "").lstrip("* ").startswith("대응량")
            # 앞 줄이 "... (mg) = ... 의 양 (mg) ×" 처럼 연산자로 끝나고 다음 줄이 수식(분수)뿐인 경우도 이어 붙인다
            eq_only = bool(re.fullmatch(r'\s*<span class="kp-eq">.*</span>\s*', b["html"], re.S))
            # 수식 개체가 아니라 "(A_T / A_S)" 처럼 아래첨자 글자로 쓴 짧은 식 줄(한글 없음)도 같다
            eq_text = bool(b_pl) and len(b_pl) <= 40 and not _HANGUL_RE.search(b_pl) and b.get("al") == "c"
            eq_cont = (eq_only or eq_text) and centered and a_pl[-1:] in "×=+-−÷/·"
            if a_pl and (eq_first and centered or eq_tail or eq_cont) and len(a_pl) + len(b_pl) <= _FORMULA_MERGE_MAX:
                out[-1] = dict(prev, html=prev["html"] + " " + b["html"], plain=a_pl + " " + b_pl, al="c", merged=True)
                continue
        out.append(b)
    return out


def _space_formula_lines(blocks):
    """계산식 줄(가운데 정렬 문단 중 수식이나 "="가 있는 줄, 그 앞의 "…의 양" 줄 포함)이 이어진 덩어리의 위아래에 빈 줄 한 줄씩 띄운다."""
    def centered(b):
        return b["k"] == "p" and b.get("al") == "c" and not b.get("lab") and b["plain"].strip() != ""

    def formulaish(b):
        pl = b["plain"].strip()
        return b.get("merged") or "kp-eq" in b["html"] or pl.startswith("=") or pl.endswith("=")

    out = list(blocks)
    i, n = 0, len(out)
    while i < n:
        if not centered(out[i]):
            i += 1
            continue
        j = i
        while j < n and centered(out[j]):
            j += 1
        if any(formulaish(out[k]) for k in range(i, j)):
            out[i] = dict(out[i], fm_top=True)
            out[j - 1] = dict(out[j - 1], fm_bot=True)
        i = j
    return out


def _sections_html(blocks):
    """의약품각조 본문: 항목명 앞 글(별명/분자식/정의)은 머리 상자에, 이후는 항목명별 접고 펴는 구간으로."""
    head, secs = [], []
    for b in blocks:
        if b["k"] == "p" and b.get("lab"):
            secs.append([b["lab"], [b]])
        elif secs:
            secs[-1][1].append(b)
        else:
            head.append(b)
    out = []
    if head:
        # 구조식 그림과 정의문("이 약은 ...") 사이의 줄들(별명, 분자식, 화학명, CAS 번호)은 가운데 정렬한다
        first_def = next((k for k, b in enumerate(head) if b["k"] == "p" and _is_definition_block(b)), None)
        # 정의문("이 약은 ...")부터는 첫 줄 들여쓰기(원문의 앞 공백)를 없애 모든 줄이 왼쪽 끝에서 시작한다
        if first_def is not None:
            head = [
                dict(b, al="c") if (k < first_def and b["k"] == "p")
                else (dict(b, ind=0, left=0) if (k >= first_def and b["k"] == "p") else b)
                for k, b in enumerate(head)
            ]
        out.append('<div class="definition kp-definition">' + "".join(block_html(b) for b in head) + "</div>")
    for lab, bs in secs:
        body = "".join(block_html(b) for b in _space_formula_lines(_apply_hierarchy(_merge_formula_lines(bs))))
        out.append(
            f'<div class="section"><div class="section-header"><div class="label">{html.escape(lab)}</div>'
            f'<span class="toggle-icon">−</span></div><div class="section-content">{body}</div></div>'
        )
    return "".join(out)


def _flat_html(blocks):
    return "".join(block_html(b, heading=bool(b.get("hd"))) for b in _apply_hierarchy(blocks))


# ---- 항목 나누기 ------------------------------------------------------------------------------
def _split_ko_en(text):
    """"정제 Tablets" -> ("정제", "Tablets"). 영문이 따로 없으면 영문은 빈 문자열."""
    text = _WS_RE.sub(" ", text).strip()
    m = re.match(r"^(.*?[가-힣\)\]])\s+([A-Za-z][^가-힣]*)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return text, ""


def _new_entry(docno, ko, en="", no="", group="", part=""):
    return {"doc": docno, "no": no, "ko": ko, "en": en, "group": group, "part": part, "blocks": []}


def _split_drug_names(names):
    """제목 줄들 -> (한글명, 영문명). 한글이 있는 첫 줄이 한글명, 나머지 한글 없는 줄을 이어 영문명."""
    ko = next((n for n in names if _HANGUL_RE.search(n)), "")
    rest = [n for n in names if n is not ko and not _HANGUL_RE.search(n) and _LATIN_RE.search(n)]
    return _WS_RE.sub(" ", ko).strip(), _WS_RE.sub(" ", " ".join(rest)).strip()


def _segment_drugs(docno, blocks, part):
    entries, cur = [], None
    group = "의약품"
    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        sty = b.get("sty", "")
        if sty in _NAME_STYLES:
            start, j, names = i, i, []
            while j < n and blocks[j].get("sty", "") in _NAME_STYLES:
                if blocks[j]["plain"].strip():
                    names.append(blocks[j]["plain"].strip())
                j += 1
            i = j
            if not names:
                continue
            # 한글이 없는 제목 스타일 줄이 아직 항목명(성상 ...)이 나오기 전의 품목 머리에 끼어 있으면(예: 화학명 줄에 제목 스타일이 잘못
            # 붙은 경우) 새 품목이 아니라 앞 품목의 머리 글이다.
            if cur is not None and not any(_HANGUL_RE.search(x) for x in names) and not any(b.get("lab") for b in cur["blocks"]):
                if not cur["en"]:  # 한글 제목만 있던 품목의 영문 제목 줄이 떨어져 있는 경우
                    cur["en"] = _WS_RE.sub(" ", " ".join(names)).strip()
                else:
                    cur["blocks"].extend(blocks[start:j])
                continue
            ko, en = _split_drug_names(names)
            if not ko and cur is not None:
                # 한글 제목이 다른 스타일로 앞 항목 끝에 붙어 있는 경우: 짧은 한글 줄을 가져온다
                for k in range(len(cur["blocks"]) - 1, max(-1, len(cur["blocks"]) - 4), -1):
                    cb = cur["blocks"][k]
                    pl = cb["plain"].strip()
                    if cb["k"] == "p" and not cb.get("lab") and 0 < len(pl) < 40 and _HANGUL_RE.search(pl) and "다." not in pl and not _LATIN_RE.search(pl[:1]):
                        ko = pl
                        del cur["blocks"][k:]
                        break
            if not ko:
                ko = en
            if not en and ko != en and j < n:
                # 영문 제목이 제목 스타일이 아닌 큰 글자 줄("바탕글", "본문")로 바로 다음에 오는 경우
                nb = blocks[j]
                pl = nb["plain"].strip()
                if nb["k"] == "p" and nb.get("vs", 0) >= 1100 and pl and len(pl) < 120 and _LATIN_RE.search(pl) and not _HANGUL_RE.search(pl):
                    en = _WS_RE.sub(" ", pl)
                    i = j + 1
            cur = _new_entry(docno, ko, en, group=group, part=part)
            entries.append(cur)
            continue
        if sty == "* 큰제목":
            title = re.sub(r"^\s*\d+\)\s*", "", b["plain"]).strip()
            if title and docno == 4:
                group = title
                cur = None
            i += 1
            continue
        if cur is not None:
            cur["blocks"].append(b)
        i += 1
    return entries


def _segment_numbered(docno, blocks, start_after_style=None, min_vs=1050):
    """"N. 이름"(N=1,2,3...) 순서대로 나오는 큰 글자 제목으로 항목을 나눈다."""
    entries, cur = [], None
    started = start_after_style is None
    exp = 1
    for b in blocks:
        if not started:
            if b.get("sty") == start_after_style:
                started = True
            continue
        pl = b["plain"].strip()
        if b["k"] == "p" and b.get("vs", 0) >= min_vs and len(pl) < 160:
            m = re.match(rf"^{exp}\.\s*(\S.*)$", pl)
            if m:
                ko, en = _split_ko_en(m.group(1))
                cur = _new_entry(docno, ko, en, no=str(exp))
                entries.append(cur)
                exp += 1
                continue
        if cur is not None:
            cur["blocks"].append(b)
    return entries


def _split_reagent_entry(entries):
    """일반시험법 마지막 항목 "92. 표준품, 시약·시액, ..."(전체의 절반이 넘는 긴 항목)을 1)~8) 하위 항목으로 나눈다.
    앞머리 목록("1) 표준품 ... 8) 멸균법 및 무균조작법")의 이름대로 뒤쪽 본문의 같은 제목 문단을 찾아 자른다. 못 찾으면 그대로 둔다."""
    if not entries or entries[-1]["no"] != "92":
        return
    big = entries[-1]
    bs = big["blocks"]
    names = []
    for b in bs:
        m = re.match(r"^(\d)\)\s*(\S.*)$", b["plain"].strip())
        if b["k"] == "p" and b.get("sty") == "항목" and m and int(m.group(1)) == len(names) + 1 and len(b["plain"]) < 60:
            names.append(_WS_RE.sub(" ", m.group(2)).strip())
    if len(names) < 3:
        return
    squash = lambda s: _WS_RE.sub("", s)
    cuts, pos = [], 0
    for k, name in enumerate(names, 1):
        want = f"{k}){squash(name)}"
        hit = next((i for i in range(pos, len(bs)) if bs[i]["k"] == "p" and bs[i].get("sty") != "항목" and squash(bs[i]["plain"]) == want), None)
        if hit is None:
            return
        cuts.append(hit)
        pos = hit + 1
    group = "92. 표준품, 시약·시액 등"
    subs = []
    for k, (name, start) in enumerate(zip(names, cuts)):
        end = cuts[k + 1] if k + 1 < len(cuts) else len(bs)
        e = _new_entry(big["doc"], name, no=f"92-{k + 1}", group=group)
        e["blocks"] = bs[start + 1 : end]
        subs.append(e)
    big["blocks"] = bs[: cuts[0]]
    big["ko"] = "표준품, 시약·시액 등 (개요)"
    big["group"] = group
    entries.extend(subs)


def _segment_general(docno, blocks):
    """통칙: "1. 일반사항" 같은 번호 제목(짧은 줄)으로 나눈다."""
    entries, cur = [], None
    started = False
    exp = 1
    for b in blocks:
        if not started:
            if b.get("sty") == "* 큰제목":
                started = True
            continue
        pl = b["plain"].strip()
        if b["k"] == "p" and len(pl) < 40:
            m = re.match(rf"^{exp}\.\s*(\S.*)$", pl)
            if m:
                cur = _new_entry(docno, _WS_RE.sub(" ", m.group(1)).strip(), no=str(exp))
                entries.append(cur)
                exp += 1
                continue
        if cur is not None:
            cur["blocks"].append(b)
    return entries


def _segment_preparations(docno, blocks):
    """제제총칙: Ⅰ.(일반사항)과 "1."~"11."(투여 경로)마다 한 항목. 투여 경로 아래의 제형("1.1. 정제" 등)은 따로 항목으로 나누지 않고 그 투여 경로
    항목의 본문 안에 소제목(h2)으로 넣는다. 내용이 없는 "Ⅱ. 제제 각조"(제목뿐)는 항목으로 두지 않는다."""
    entries, cur = [], None
    group = ""
    l1_no = ""
    for b in blocks:
        sty = b.get("sty", "")
        pl = b["plain"].strip()
        if b["k"] == "p" and pl:
            # 제형 제목("2.1. 구강용 정제")은 문단 스타일이 제각각이라(* 제제_1.1, * 제제_1.1.1, * 제제_가., 바탕글) 스타일이 아니라 글 꼴로 알아본다:
            # 지금 투여 경로 묶음 번호(l1_no)로 시작하고, 번호 뒤에 한글 제목이 오는 짧은 줄. (3단계 번호 "1.6.1."은 번호 뒤에 숫자가 와서 걸리지 않는다.)
            m2 = re.match(r"^(\d{1,2})\.(\d{1,2})\.\s*([가-힣].{0,70})$", pl)
            kind = None
            if sty == "* 제제_Ⅰ.":
                kind = "roman"
            elif sty == "* 제제_1.":
                kind = "l1"
            elif m2 and len(pl) < 90 and m2.group(1) == l1_no:
                kind = "l2"
            if kind == "l2" and cur is not None:
                cur["blocks"].append(dict(b, hd=True, h2=True))  # 제형 제목은 투여 경로 항목 안의 소제목
                continue
            if kind in ("roman", "l1"):
                if kind == "roman":
                    m = re.match(r"^([ⅠⅡⅢⅣⅤ]+)\.\s*(.*)$", pl)
                    no, name = (m.group(1), m.group(2)) if m else ("", pl)
                    l1_no = ""
                else:
                    m = re.match(r"^(\d{1,2})\.\s*(.*)$", pl)
                    no, name = (m.group(1), m.group(2)) if m else ("", pl)
                    l1_no = no
                ko, en = _split_ko_en(name)
                cur = _new_entry(docno, ko, en, no=no, group=group)
                entries.append(cur)
                continue
        if cur is not None:
            cur["blocks"].append(b)
    return entries  # 내용이 없는 "Ⅱ. 제제 각조"도 목록에는 남긴다(parse_doc 이 nolink 로 표시해 화면에서 누를 수 없는 줄로 보인다)


# ---- 문서 하나 읽기 -------------------------------------------------------------------------------
def _label_regex(z, secs, header):
    """문서에서 "항목명" 문자 스타일이 붙은 글자(성상, 확인시험, 저장법 ...)를 세어, 서식이 빠진 문단에서도 그 단어로
    시작하면 항목으로 알아보는 정규식을 만든다. 글자 사이 공백("성    상")은 건너뛰고, 뒤에는 공백 두 칸 이상/탭/끝/번호("1)")가 와야 한다."""
    counts = collections.Counter()
    for sec in secs:
        with z.open(sec) as fh:
            for ev, el in ET.iterparse(fh, events=("end",)):
                if el.tag == HP + "t" and el.get("charStyleIDRef"):
                    nm = header.style_name(el.get("charStyleIDRef")).lstrip("* ").strip()
                    if nm == "항목명":
                        txt = _WS_RE.sub("", "".join(el.itertext()))
                        txt = re.sub(r"\d{1,2}\)?$", "", txt)
                        if 2 <= len(txt) <= 14:
                            counts[txt] += 1
                el.clear() if el.tag in (HP + "t", HP + "run") else None
    words = sorted((w for w, c in counts.items() if c >= 2 and not re.match(r"\d", w)), key=len, reverse=True)
    if not words:
        return None, set()
    alt = "|".join(r"\s*".join(re.escape(ch) for ch in w) for w in words)
    return re.compile(rf"^(?:({alt}))(?=\s{{2,}}|\t|\s*$|\d{{1,2}}\))"), set(words)



def _manifest(z):
    out = {}
    try:
        root = ET.fromstring(z.read("Contents/content.hpf"))
    except (KeyError, ET.ParseError):
        return out
    for it in root.iter():
        if _local(it.tag) == "item" and it.get("id") and it.get("href", "").startswith("BinData/"):
            out[it.get("id")] = it.get("href").rsplit(".", 1)[-1].lower()
    return out


def parse_doc(path, docno):
    """hwpx 한 파일 -> 항목 목록(딕셔너리). 항목: doc, no, ko, en, group, part, html_z(압축된 본문 HTML), text(검색용 평문),
    head(머리 글: 별명/분자식 등, 검색용), skipped(그리지 못한 도형 수)."""
    _, title, kind = DOCS[docno]
    with zipfile.ZipFile(path) as z:
        header = _Header(z.read("Contents/header.xml"))
        ctx = _Ctx(docno, header, _manifest(z))
        secs = sorted((n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)), key=lambda s: int(re.search(r"(\d+)\.xml", s).group(1)))
        if kind == "drug":
            ctx.label_rx, ctx.label_words = _label_regex(z, secs, header)
        blocks = []
        widths = collections.Counter()
        for sec in secs:
            depth = 0
            with z.open(sec) as fh:
                for ev, el in ET.iterparse(fh, events=("start", "end")):
                    if ev == "start":
                        depth += 1
                        continue
                    depth -= 1
                    if el.tag == HP + "p" and depth == 1:
                        sty = header.style_name(el.get("styleIDRef"))
                        ls = el.find(f"{HP}linesegarray/{HP}lineseg")
                        vs = int(ls.get("vertsize")) if ls is not None and ls.get("vertsize", "").isdigit() else 0
                        if ls is not None and ls.get("horzsize", "").isdigit():
                            widths[int(ls.get("horzsize"))] += 1
                        for k, b in enumerate(ctx.para(el)):
                            b["sty"], b["vs"] = sty, vs
                            if sty in _HEADING_STYLES and b["k"] == "p":
                                b["hd"] = True
                            blocks.append(b)
                        el.clear()
            if widths:
                ctx.col_w = widths.most_common(1)[0][0]

    if docno in (3, 4):
        part = "각조 제1부" if docno == 3 else "각조 제2부"
        entries = _segment_drugs(docno, blocks, part)
    elif docno == 1:
        entries = _segment_general(docno, blocks)
    elif docno == 2:
        entries = _segment_preparations(docno, blocks)
    elif docno == 5:
        entries = _segment_numbered(docno, blocks, start_after_style="* 큰제목")
        _split_reagent_entry(entries)
    else:
        entries = _segment_numbered(docno, blocks)

    out = []
    for e in entries:
        bs = e.pop("blocks")
        e["html_z"] = zlib.compress((_sections_html(bs) if kind == "drug" else _flat_html(bs)).encode("utf-8"), 6)
        text = "\n".join(b["plain"] for b in bs if b["plain"])
        e["text"] = text
        # 머리 글: 첫 항목명 앞의 문단들(별명, 분자식, IUPAC 이름, 정의)
        head = []
        for b in bs:
            if b["k"] == "p" and b.get("lab"):
                break
            if b["plain"]:
                head.append(b["plain"])
        e["head"] = " ".join(head)[:600]
        e["skipped"] = 0
        e["nolink"] = not bs  # 본문이 하나도 없는 제목뿐인 항목(예: "Ⅱ. 제제 각조"): 목록에는 보이지만 링크(열기)는 없다
        out.append(e)
    if out:
        out[0]["skipped"] = ctx.drawn_skipped
    return out
