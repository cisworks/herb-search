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
_TBL_HEAD_BG = "#ebebeb"  # 표 머리 행의 밝은 회색 배경
_TBL_TITLE_RE = re.compile(r"^(?:표|Table)\s*\d+")  # 표 안의 표 번호·설명 줄("표 1. ...")


def _is_neutral_gray(color):
    """"#EFEFEF" 같은 무채색(회색) 배경인가. 원문의 색 있는 칸(노랑 등)은 그대로 둔다."""
    m = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", color.strip())
    if not m:
        return False
    r, g, b = (int(x, 16) for x in m.groups())
    return max(r, g, b) - min(r, g, b) <= 6 and r >= 0xB0

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
                blk = self._p_block(p, h, plain, cur["lab"])
                blk["lead"] = cur.get("lead", 0)  # 문단 맨 앞의 공백 수(원문에서 들여 쓴 정도)
                blocks.append(blk)
            cur["html"], cur["plain"], cur["lab"], cur["started"], cur["lead"] = [], [], None, False, 0

        figs = []  # 이어서 나온 그림 개체들(같은 그림의 여러 장, 또는 설명이 달린 그림)
        floating = self._floating_items(p) if self.docno in (5, 6) else None  # 떠 있는 그림 + 글상자(알코올수측정법): 위치를 원문대로
        floating_done = False

        def emit_figs():
            """모인 그림들을 "그림(가운데 정렬) + 설명 줄" 블록으로 내보낸다. 글 한가운데에 얹힌 그림이 글과 한 문단으로 섞이지 않게 하고,
            그림 개체에 딸린 설명(caption: "그림 1. 브래그의 법칙 ...")이 빠지지 않게 한다."""
            if not figs:
                return
            fig = self._p_block(p, "".join(f["img"] for f in figs), "", None)
            fig.update(al="c", left=0, ind=0)
            caps = [f["cap"] for f in figs if f["cap"]]
            for c in [c for c in caps if c["top"]]:
                blocks.extend(dict(x) for x in c["blocks"])
            blocks.append(fig)
            for c in [c for c in caps if not c["top"]]:
                blocks.extend(dict(x) for x in c["blocks"])
            figs.clear()

        for run in p.findall(HP + "run"):
            cp = run.get("charPrIDRef")
            for ch in run:
                tag = _local(ch.tag)
                if floating and ch in floating[1]:
                    if not floating_done:
                        floating_done = True
                        emit_figs()
                        flush()
                        fl_html = self._figure_from_items(floating[0])
                        if fl_html:
                            blocks.append({"k": "fig", "html": fl_html[0], "plain": fl_html[1]})
                    continue
                if figs and tag != "pic":
                    emit_figs()
                if tag == "pic":
                    pos = ch.find(HP + "pos")
                    tac = pos.get("treatAsChar") if pos is not None else None
                    # 설명이 딸린 그림, 또는 글자처럼 놓이지 않고 떠 있는 그림(일반시험법/일반정보)은 글 사이에서 따로 한 줄로 놓는다
                    if ch.find(HP + "caption") is not None or (tac != "1" and self.docno in (5, 6)):
                        img = self._pic_img(ch)
                        if img:
                            flush()
                            figs.append({"img": img, "cap": self._pic_caption(ch)})
                            continue
                if tag == "container" and self.docno in (5, 6):
                    fig_html = self._container_figure(ch)
                    if fig_html:
                        flush()
                        fcap = self._pic_caption(ch) if ch.find(HP + "caption") is not None else None
                        if fcap and fcap["top"]:
                            blocks.extend(dict(x) for x in fcap["blocks"])
                        blocks.append({"k": "fig", "html": fig_html[0], "plain": fig_html[1]})  # 블록 HTML 을 그대로 쓰는 종류(<p> 안에 <div> 를 넣지 않는다)
                        if fcap and not fcap["top"]:
                            blocks.extend(dict(x) for x in fcap["blocks"])
                        continue
                if tag in ("container", "rect", "ellipse") and ch.find(HP + "caption") is not None:
                    tmp = {"html": [], "plain": [], "lab": None, "started": False}
                    self._shape(ch, tmp)  # 묶음 안의 그림/글을 같은 방식으로 꺼낸다
                    img = "".join(tmp["html"])
                    if img:
                        flush()
                        figs.append({"img": img, "cap": self._pic_caption(ch)})
                        continue
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
        emit_figs()
        flush()
        return blocks

    # ---- 그림 묶음(그림 + 글상자)을 원문 위치대로 ------------------------------------------------------
    @staticmethod
    def _mmul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    @classmethod
    def _obj_matrix(cls, el):
        """개체의 renderingInfo 에 차례로 적힌 변환 행렬(transMatrix, 그다음 scaMatrix/rotMatrix 쌍이 바깥 묶음부터 자기 자신까지)을 모두 곱한
        3x3 행렬. 바깥 묶음의 이동·크기가 이미 들어 있어서, 개체 자기 좌표(0..원래 크기)를 이 행렬로 옮기면 그림 묶음 맨 바깥 기준 위치와 크기가 된다."""
        m = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        ri = next((c for c in el if _local(c.tag) == "renderingInfo"), None)  # hc: 이름 공간이라 이름(local)으로 찾는다
        if ri is None:
            return m
        for mx in ri:
            try:
                e = [float(mx.get(k)) for k in ("e1", "e2", "e3", "e4", "e5", "e6")]
            except (TypeError, ValueError):
                continue
            m = cls._mmul(m, [[e[0], e[1], e[2]], [e[3], e[4], e[5]], [0.0, 0.0, 1.0]])
        return m

    def _fig_items(self, el, items):
        """그림 묶음(container) 안의 그림과 글상자를 (종류, x, y, 폭, 높이, 내용) 으로 모은다(좌표는 묶음 맨 바깥 기준)."""
        for ch in el:
            tag = _local(ch.tag)
            if tag == "container":
                self._fig_items(ch, items)
                continue
            org = ch.find(HP + "orgSz")
            if tag not in ("pic", "rect", "ellipse", "polygon", "textart") or org is None:
                continue
            m = self._obj_matrix(ch)
            try:
                ow, oh = float(org.get("width")), float(org.get("height"))
            except (TypeError, ValueError):
                continue
            x, y, w, h = m[0][2], m[1][2], abs(m[0][0] * ow), abs(m[1][1] * oh)
            if w <= 0 or h <= 0:
                continue
            if tag == "pic":
                img = self._pic_img(ch)
                if img:
                    items.append(("img", x, y, w, h, img))
                continue
            dt = ch.find(HP + "drawText")
            if dt is None:
                continue
            lines = []
            for dp in (dt.find(HP + "subList") if dt.find(HP + "subList") is not None else dt).findall(HP + "p"):
                bl = [b for b in self.para(dp) if b["k"] == "p"]
                raw = next(("".join(tt.itertext()) for tt in dp.iter(HP + "t") if "".join(tt.itertext()).strip()), "")
                lead = len(raw) - len(raw.lstrip(" \u3000"))  # 원문 문단 맨 앞의 공백(이어지는 줄을 앞 글자에 맞춰 들여 쓴 것)
                lines.append((bl[0]["al"] if bl else "j", "".join(b["html"] for b in bl), " ".join(b["plain"] for b in bl), lead))
            if any(t[2].strip() for t in lines):
                items.append(("box", x, y, w, h, lines))

    def _container_figure(self, cont):
        """그림과 글상자(범례, 치수, "그림 1. ..." 설명)가 같이 든 그림 묶음 -> (HTML, 평문). 글상자 위치를 원문대로 배치해, 설명이 그림 옆·아래의
        제자리에 오게 한다. 그림만 있거나 글상자만 있는 묶음은 None (기존 방식으로 그린다)."""
        items = []
        self._fig_items(cont, items)
        return self._figure_from_items(items)

    def _floating_items(self, p):
        """문단에 글자처럼 놓이지 않고 떠 있는(treatAsChar=0) 그림과 글상자가 같이 있으면(알코올수측정법의 장치 그림 + 범례 상자) 문단 기준 위치(pos)와
        크기(sz)로 항목을 모은다. (항목 목록, 쓴 요소 집합) 또는 None. 위치 기준이 서로 다르면 쓰지 않는다."""
        items, els, refs = [], set(), set()
        for run in p.findall(HP + "run"):
            for ch in run:
                tag = _local(ch.tag)
                if tag not in ("pic", "rect"):
                    continue
                pos, sz = ch.find(HP + "pos"), ch.find(HP + "sz")
                if pos is None or sz is None or pos.get("treatAsChar") == "1":
                    continue
                try:
                    def sg(v):
                        v = int(v)
                        return v - 2 ** 32 if v > 2 ** 31 else v
                    x, y, w, h = sg(pos.get("horzOffset") or 0), sg(pos.get("vertOffset") or 0), float(sz.get("width")), float(sz.get("height"))
                except (TypeError, ValueError):
                    continue
                if w <= 0 or h <= 0:
                    continue
                if tag == "pic":
                    img = self._pic_img(ch)
                    if img:
                        items.append(("img", x, y, w, h, img))
                        els.add(ch)
                        refs.add((pos.get("horzRelTo"), pos.get("vertRelTo")))
                    continue
                dt = ch.find(HP + "drawText")
                if dt is None:
                    continue
                lines = []
                for dp in (dt.find(HP + "subList") if dt.find(HP + "subList") is not None else dt).findall(HP + "p"):
                    bl = [b for b in self.para(dp) if b["k"] == "p"]
                    raw = next(("".join(tt.itertext()) for tt in dp.iter(HP + "t") if "".join(tt.itertext()).strip()), "")
                    lead = len(raw) - len(raw.lstrip(" \u3000"))
                    lines.append((bl[0]["al"] if bl else "j", "".join(b["html"] for b in bl), " ".join(b["plain"] for b in bl), lead))
                if any(t[2].strip() for t in lines):
                    items.append(("box", x, y, w, h, lines))
                    els.add(ch)
                    refs.add((pos.get("horzRelTo"), pos.get("vertRelTo")))
        if len(refs) != 1 or not any(i[0] == "img" for i in items) or not any(i[0] == "box" for i in items):
            return None
        return items, els

    def _figure_from_items(self, items):
        """(종류, x, y, 폭, 높이, 내용) 항목들 -> 그림 상자 (HTML, 평문). 위치와 크기는 상자 전체 크기에 대한 %."""
        if not any(i[0] == "img" for i in items) or not any(i[0] == "box" for i in items):
            return None
        x0 = min(i[1] for i in items)
        y0 = min(i[2] for i in items)
        x1 = max(i[1] + i[3] for i in items)
        y1 = max(i[2] + i[4] for i in items)
        W, H = x1 - x0, y1 - y0
        if W <= 0 or H <= 0:
            return None
        out, plains = [], []
        for kind, x, y, w, h, content in items:
            pos = f"left:{(x - x0) * 100 / W:.2f}%;top:{(y - y0) * 100 / H:.2f}%;width:{w * 100 / W:.2f}%;height:{h * 100 / H:.2f}%"
            if kind == "img":
                out.append(re.sub(r' style="[^"]*"', "", content).replace("<img ", f'<img style="{pos}" ', 1))
            else:
                lines = []
                for al, lh, lp, lead in content:
                    cls = "kp-fl" + (" kp-fc" if al == "c" else "")
                    pad = f' style="padding-left:{min(lead, 12) * 0.3:.1f}em"' if lead >= 2 and al != "c" else ""
                    if '<span class="kp-tab"></span>' in lh:  # 탭으로 벌린 "시험기 ... 보조판": 칸 양끝에 놓는다
                        segs = lh.split('<span class="kp-tab"></span>')
                        lines.append(f'<div class="{cls} kp-fsplit">' + "".join(f"<span>{sg}</span>" for sg in segs if sg.strip()) + "</div>")
                    else:
                        lines.append(f'<div class="{cls}"{pad}>{lh.strip() or "&nbsp;"}</div>')
                    if lp.strip():
                        plains.append(lp.strip())
                out.append(f'<div class="kp-fbox" style="{pos}">' + "".join(lines) + "</div>")
        html_ = f'<div class="kp-fig" style="width:min({W / 900:.2f}em,100%);aspect-ratio:{W:.0f}/{H:.0f}">' + "".join(out) + "</div>"
        return html_, _WS_RE.sub(" ", " ".join(plains)).strip()

    def _pic_caption(self, el):
        """그림(또는 그림 묶음) 개체에 딸린 설명(caption) -> {"blocks": [설명 블록 ...], "top": 그림 위인지} 또는 None.
        설명 안에 범례 표와 제목 줄이 같이 있어도(그림 3) 순서대로 모두 가져온다. 빈 줄은 버린다."""
        cap = el.find(HP + "caption")
        if cap is None:
            # 설명 개체는 없지만 그림 위에 얹힌 글상자 글자가 있으면 그것을 그림 아래 설명 줄로 쓴다
            dt = " ".join(_WS_RE.sub(" ", "".join(x.itertext())).strip() for x in el.iter(HP + "drawText")).strip()
            if not dt:
                return None
            txt = _fix_text(dt)
            return {"blocks": [{"k": "p", "html": html.escape(txt, quote=False), "plain": txt, "lab": None, "al": "c", "left": 0, "ind": 0}], "top": False}
        blocks_out = []
        for cp_ in (cap.find(HP + "subList") if cap.find(HP + "subList") is not None else cap).findall(HP + "p"):
            for cb in self.para(cp_):
                if cb["k"] == "p":
                    if cb["plain"].strip():
                        cb = dict(cb, al="c", left=0, ind=0, lab=None, html=re.sub(r"[  ]{2,}", " ", cb["html"]).strip())
                        blocks_out.append(cb)
                elif cb.get("html"):
                    blocks_out.append(cb)
        if not blocks_out:
            return None
        # 이어진 표(범례 표 둘 이상, 그림 3의 "A~E" 와 "F~K")는 원문처럼 한 줄에 나란히 놓는다
        merged = []
        for blk in blocks_out:
            if blk["k"] == "tbl" and merged and merged[-1]["k"] == "tbl":
                merged[-1] = {"k": "tbl", "html": merged[-1]["html"] + blk["html"], "plain": (merged[-1]["plain"] + " " + blk.get("plain", "")).strip(), "row": True}
            else:
                merged.append(dict(blk, row=False))
        blocks_out = [dict(x, html='<div class="kp-tblrow">' + x["html"] + "</div>") if x.get("row") else x for x in merged]
        return {"blocks": blocks_out, "top": (cap.get("side") or "BOTTOM").upper() == "TOP"}

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
            raw_text = text
            text = text.lstrip()  # 문단 맨 앞의 공백/탭(전각 공백 포함)은 내어쓰기로 대신하므로 버린다(버린 개수는 cur["lead"] 에 남긴다)
            cur["lead"] = cur.get("lead", 0) + len(raw_text) - len(text)
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
    def _pic_img(self, pic):
        """그림 개체 -> <img> HTML (이미지가 없으면 빈 문자열)."""
        tmp = {"html": [], "plain": [], "started": False}
        self._pic(pic, tmp, with_caption=False)
        return "".join(tmp["html"])

    def _pic(self, pic, cur, with_caption=True):
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
        cap = " ".join(_WS_RE.sub(" ", "".join(x.itertext())).strip() for x in pic.iter(HP + "drawText")).strip() if with_caption else ""
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
                cells.append((col, cs, rs, inner_html, va, sides, fill, tc.get("header") == "1", inner_plain))
                if inner_plain:
                    plain_parts.append(inner_plain)
            rows.append(cells)
        if not rows:
            return "", ""
        # 머리 행: 원문에서 머리 칸(header)으로 지정한 행. 지정이 없으면, 테두리가 있는 2열 이상 표의 첫 행(칸마다 글이 있을 때)을 머리 행으로 본다.
        hdr_rows = {i for i, cs_ in enumerate(rows) if any(c[7] for c in cs_)}
        if not hdr_rows and len(rows) >= 2 and len(rows[0]) >= 2:
            first = rows[0]
            # 칸의 절반 이상에 글이 있으면 머리 행으로 본다(왼쪽 위 귀퉁이 칸이 비어 있는 표가 많다). 테두리 없는 표(처방 배치 등)는 제외된다.
            if any(sum(c[5]) > 0 for c in first) and sum(1 for c in first if c[8].strip()) * 2 >= len(first):
                hdr_rows = {0}
        # 맨 윗줄이 표 제목 칸 하나(표 전체 폭으로 합친 칸, "표 1. ...", "완충액의 조성 (g)")인 표: 제목 줄과 그 아래 열 머리 줄(들)을 머리 행으로 본다.
        # (원문에 머리 칸 표시가 없고 첫 행이 한 칸뿐이라 위의 규칙에서는 머리 행을 못 찾는 표들이다.)
        if not hdr_rows and len(rows) >= 2 and len(rows[0]) == 1 and any(sum(c[5]) > 0 for r_ in rows for c in r_):
            hdr_rows = {0}
            # 둘째 줄은 열 이름처럼 짧은 칸들일 때만 머리로 본다("종류/절단제/특이성", "A B C D"). 긴 글이 든 줄은 자료 줄이다(무균시험법의 균주 표 등)
            if len(rows[1]) >= 2 and all(len(c[8].strip()) <= 16 or c[1] > 1 for c in rows[1]):
                hdr_rows.add(1)
                if len(rows) > 2 and any(c[1] > 1 for c in rows[1]) and len(rows[2]) >= 2:  # 위 칸을 묶은 머리(겔 형성틀의 부피 ...) 아래의 열 머리도
                    hdr_rows.add(2)
        # 머리가 두 줄 이상인 표: 원문은 맨 윗줄에만 머리 표시를 하고, 그 줄 머리 칸이 아래로 합쳐(rowspan) 두 번째 줄 이하까지 덮는다
        # ("제제구분/미생물"이 두 줄에 걸치고 "판정기준" 밑에 "14 일 후 | 28 일 후"가 놓인 표). 그 덮인 줄들도 머리 행으로 본다.
        # 왼쪽 위 빈 칸이 아래로 합쳐진 처방 배합표("덱스트란 40 ... / 전체량")나 왼쪽 이름칸 하나가 합쳐진 목록은 머리가 아니므로, 글이 있는 합친 칸이 둘 이상이거나
        # 글이 있는 가로로 합친 칸(판정기준)이 같이 있는 머리 줄에서만, 그리고 테두리가 있는 표에서만 넓힌다.
        if hdr_rows and any(sum(c[5]) > 0 for r_ in rows for c in r_):
            extra = set()
            for r_i in sorted(hdr_rows):
                spans = [c for c in rows[r_i] if c[2] > 1 and c[8].strip()]
                if any(c[8].strip().endswith("=") or c[8].strip().startswith("×") for c in rows[r_i]):
                    continue  # "과산화물가 = / 분자 / × 10" 처럼 표로 그린 분수식은 머리가 아니다(황납)
                if spans and (len(spans) >= 2 or any(c[1] > 1 and c[8].strip() for c in rows[r_i])):
                    for k in range(r_i + 1, min(len(rows), r_i + max(c[2] for c in spans))):
                        extra.add(k)
            # 머리 줄이 "① ② ③ ..." 번호뿐이면 그 아래 줄(항목 이름)도 머리다
            last_h = max(hdr_rows)
            if last_h + 1 < len(rows) and last_h + 1 not in hdr_rows:
                texts = [c[8].strip() for c in rows[last_h] if c[8].strip()]
                if len(texts) >= 3 and sum(1 for t_ in texts if re.fullmatch(r"[①-⑳㉑-㉟]", t_)) >= len(texts) - 1:
                    extra.add(last_h + 1)
            hdr_rows |= extra
        total = sum(widths.values())
        tw = 0
        szt = tbl.find(HP + "sz")
        if szt is not None and szt.get("width"):
            tw = int(szt.get("width"))
        pct = min(100.0, tw * 100.0 / self.col_w) if tw and self.col_w else 100.0
        out = [f'<div class="kp-tblwrap"><table class="kp-tbl" style="width:{pct:.1f}%">']
        if widths and total:
            out.append("<colgroup>" + "".join(f'<col style="width:{widths.get(c, 0) * 100.0 / total:.1f}%">' for c in range(max(widths) + 1)) + "</colgroup>")
        for ri, cells in enumerate(rows):
            out.append("<tr>")
            for col, cs, rs, inner, va, sides, fill, _hd, _pl in cells:
                is_head = ri in hdr_rows
                # 표 안의 첫 줄이 "표 1. 절단제의 예시" 같은 표 번호·설명 한 칸이면 그 줄은 표 이름이지 열 머리가 아니므로 배경색을 칠하지 않는다(글자는 굵게 그대로)
                title_row = len(cells) == 1 and bool(_TBL_TITLE_RE.match(_pl.strip()))
                if title_row:
                    fill = ""
                elif fill and _is_neutral_gray(fill):
                    fill = f"var(--kp-tbl-head,{fill})"  # 원문의 회색 배경칸(머리 칸 등)도 테마색(옅은 파랑)으로
                if is_head and not fill and not title_row:
                    fill = f"var(--kp-tbl-head,{_TBL_HEAD_BG})"  # 머리 행 배경: 테마색(kp.css 의 --kp-tbl-head), 없으면 밝은 회색
                attrs = ""
                if cs > 1:
                    attrs += f' colspan="{cs}"'
                if rs > 1:
                    attrs += f' rowspan="{rs}"'
                st = []
                for nm, w in zip(("left", "right", "top", "bottom"), sides):
                    st.append(f"border-{nm}:{w}px solid var(--kp-tbl-line,#555)" if w else f"border-{nm}:none")  # 테두리 색: 테마색(kp.css 의 --kp-tbl-line)
                if fill:
                    st.append(f"background:{fill}")
                if va in ("TOP", "BOTTOM"):
                    st.append(f"vertical-align:{va.lower()}")
                out.append(f'<td{attrs}{" class=kp-th" if is_head else ""} style="{";".join(st)}">{inner}</td>')
            out.append("</tr>")
        out.append("</table></div>")
        # 표 개체에 딸린 설명(caption: "표 1  배지성능시험 ... 시험용 균주")은 표 칸이 아니라 표 개체의 별도 요소라서 위 줄들에는 안 들어온다.
        # 원문의 위치(위/아래)대로 표 위/아래에 가운데 정렬한 줄로 보인다(위쪽은 "표 1. ..." 줄처럼 굵게).
        cap = tbl.find(HP + "caption")
        if cap is not None:
            ph, pp = [], []
            for cp_ in (cap.find(HP + "subList") if cap.find(HP + "subList") is not None else cap).findall(HP + "p"):
                for cb in self.para(cp_):
                    if cb["k"] == "p" and cb["plain"].strip():
                        ph.append(re.sub(r"[  ]{2,}", " ", cb["html"]).strip())
                        pp.append(_WS_RE.sub(" ", cb["plain"]).strip())
            if ph:
                top = (cap.get("side") or "TOP").upper() != "BOTTOM"
                cap_html = f'<p class="kp-p kp-c{" kp-h" if top else ""}">{"<br>".join(ph)}</p>'
                out = [cap_html] + out if top else out + [cap_html]
                plain_parts.insert(0 if top else len(plain_parts), " ".join(pp))
        return "".join(out), _WS_RE.sub(" ", " ".join(plain_parts)).strip()

    @staticmethod
    def _tab_items(pp):
        """글만 있는 문단이 탭으로 항목을 늘어놓은 줄이면 [(항목 글, 그 뒤 탭 폭), ...], 아니면 None.
        그림/수식/도형이 든 문단, 탭이 없는 문단, 항목이 하나뿐인 문단은 제외한다."""
        if any(_local(e.tag) in ("pic", "container", "rect", "ellipse", "polygon", "tbl", "equation", "ole") for e in pp.iter()):
            return None
        items, cur, ntab = [], "", 0
        for run in pp.findall(HP + "run"):
            for t in run.findall(HP + "t"):
                if t.text:
                    cur += t.text
                for ch in t:
                    if _local(ch.tag) == "tab":
                        items.append((cur, int(ch.get("width") or 0)))
                        cur, ntab = "", ntab + 1
                    if ch.tail:
                        cur += ch.tail
        items.append((cur, 0))
        if ntab < 1 or sum(1 for x in items if x[0].strip()) < 2 or len(items) > 10:
            return None
        return items

    @staticmethod
    def _tab_grid_html(rows):
        """탭으로 늘어놓은 줄들(여러 줄 [(글, 탭 폭)...])을 항목 시작 위치대로 열을 맞춘 표 HTML 로. 탭 칸은 원문이 탭마다 적어 둔 폭(hp:tab width)으로 계산한다:
        항목 시작 = 앞 항목의 (글자 폭 추정 + 탭 폭). 글자 폭은 정확하지 않아도, 줄마다 같은 열의 시작 위치는 가까워서(열 사이는 훨씬 벌어진다)
        가까운 것끼리 한 열로 묶으면 A, B, C 가 세로로 맞는다. 글이 없는 항목(연속한 탭 사이)은 위치 계산에만 쓰고 칸은 만들지 않는다."""
        def tw(text):  # 글자 폭 추정(hwpunit, 10 pt 기준)
            w = 0
            for c in text:
                w += 1000 if ord(c) > 0x2E80 else (300 if c == " " else 330 if c in ":,.;()" else 600)
            return w

        grid = []
        for items in rows:
            x, cells = 0, []
            for text, tabw in items:
                text = _fix_text(text).strip()
                if text:
                    cells.append((x, text))
                x += tw(text) + 300 + tabw
            grid.append(cells)
        starts = sorted(c[0] for cells in grid for c in cells)
        centers = []
        for v in starts:
            if centers and v - centers[-1][-1] < 2500:
                centers[-1].append(v)
            else:
                centers.append([v])
        bounds = [sum(c) / len(c) for c in centers]
        ordinal = len(bounds) > max((len(cells) for cells in grid), default=0)  # 위치 추정이 어긋나 열이 가장 긴 줄의 항목 수보다 많아지면 차례(1열, 2열 ...)로 맞춘다
        if ordinal:
            bounds = list(range(max(len(cells) for cells in grid)))
        out = []
        for cells in grid:
            row = [""] * len(bounds)
            for n, (x, text) in enumerate(cells):
                k = n if ordinal else min(range(len(bounds)), key=lambda q: abs(bounds[q] - x))
                row[k] = (row[k] + " " + html.escape(text, quote=False)).strip()
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>")
        return '<table class="kp-aligntbl kp-legend">' + "".join(out) + "</table>"

    def _cell_content(self, sub):
        if sub is None:
            return "", ""
        paras = sub.findall(HP + "p")
        tabbed = [self._tab_items(pp) for pp in paras]
        if any(t is not None for t in tabbed):
            # 표 칸 안에 탭으로 항목을 늘어놓은 범례 줄이 있으면(장치 그림 아래 "A : 모터통 ⇥ B : 안기둥 ⇥ C : 바깥기둥") 이어진 줄끼리 열을 맞추고,
            # 나머지 문단(그림, 긴 설명, 그림 이름)은 원래대로 둔다
            parts, plains, group = [], [], []
            legend = []  # 이어진 범례 줄들(왼쪽 정렬 한 줄 문단 + 열 맞춤 격자)

            def flush_legend():
                nonlocal legend
                if legend:
                    parts.append('<div class="kp-legendblk">' + "".join(legend) + "</div>" if len(legend) >= 2 else legend[0])
                    legend = []

            def flush_group():
                nonlocal group
                if group:
                    legend.append(self._tab_grid_html(group))
                    group = []

            for pp, items in zip(paras, tabbed):
                if items is not None:
                    group.append(items)
                    plains.append(" ".join(x[0].strip() for x in items if x[0].strip()))
                    continue
                flush_group()
                for b in self.para(pp):
                    if b["k"] == "p":
                        al = {"c": "center", "r": "right"}.get(b["al"], "")
                        line = f'<div class="kp-cp"{f" style=text-align:{al}" if al else ""}>{b["html"]}</div>'
                        if al or "<img" in b["html"]:
                            flush_legend()
                            parts.append(line)
                        else:
                            legend.append(line)
                    else:
                        flush_legend()
                        parts.append(b["html"])
                    if b["plain"]:
                        plains.append(b["plain"])
            flush_group()
            flush_legend()
            return "".join(parts), _WS_RE.sub(" ", " ".join(plains)).strip()
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
    elif b.get("term_hang"):
        hang = b["term_hang"]  # 용어 묶음(92-x): 첫 줄은 왼쪽 끝, 이어지는 줄은 딸린 단락(융점, 비선광도 ...)의 들여쓰기와 같은 위치
        ml, ti = left / 900.0 + hang, -hang
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
    if b.get("term_tight"):
        style.append("margin-bottom:0.1em")  # 같은 용어 묶음 안의 줄 사이는 좁게
    if b.get("term_ind"):
        style.append(f"padding-left:{b['term_ind']:.1f}em")  # 앞 용어에 딸린 단락의 들여쓰기(원문의 맨 앞 공백)
    if b.get("term_top"):
        style.append("margin-top:0.9em")  # 용어 단락(92-x 표준품·시약 등) 위 간격
    if b.get("term_bot"):
        style.append("margin-bottom:0.9em")  # 용어 단락 묶음 아래 간격
    if b.get("fm_top") or b.get("gap_top"):
        style.append("margin-top:1.75em")  # 계산식 위, 또는 일반시험법 최상위 항목 위의 빈 줄 한 줄(본문 줄 높이)
    if b.get("fm_bot"):
        style.append("margin-bottom:1.75em")  # 계산식 아래 빈 줄 한 줄
    if ml:
        # 밑줄이 있는 소제목(1.1. 정제 ...)은 들여쓰기를 바깥 여백이 아니라 안쪽 여백(padding)으로 둔다. 그러면 첫 줄 맨 앞(번호 "1.1.")이 상자의
        # 왼쪽 끝이 되어, 밑줄(상자 아래 테두리)이 번호 밑에서부터 이어진다. 바깥 여백이면 밑줄이 번호 뒤 글자 위치에서 시작한다.
        style.append(f"{'padding-left' if b.get('h2') else 'margin-left'}:{ml:.2f}em")
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
    if b.get("h3"):
        cls += " kp-h3"
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
        if b.get("hd") or b.get("top"):  # 소제목, 또는 지정한 상위 항목(_TOP_HEADINGS): 맨 바깥에 놓고 번호 계층을 처음부터 센다
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


# 그림/표 설명 줄: 문단 스타일이 그림제목·표제목·캡션이거나, "그림 1. 용적계", "표 3 ..." 처럼 그림/표 번호로 시작하는 짧은 줄(문장이 아닌 것)
_CAPTION_STYLE_RE = re.compile(r"그림제목|표제목|캡션")
_CAPTION_TEXT_RE = re.compile(r"^(그림|표)\s*\d+(?:\.\d+)?(?:\s*[.:)]|\s{1,3}(?=\S)|$)")


def _is_caption(b):
    sty = (b.get("sty") or "").lstrip("* ")
    if _CAPTION_STYLE_RE.search(sty):
        return True
    pl = b["plain"].strip()
    return bool(pl) and len(pl) <= 150 and bool(_CAPTION_TEXT_RE.match(pl)) and not pl.endswith(("다.", "한다", "된다"))


def _move_captions_up(blocks):
    """그림만 있는 문단 바로 아래에 설명이 없고, 바로 뒤 세 블록 안에 "그림 N. ..." 설명 줄이 따로 나오면(그림과 설명 사이에 본문이 끼어 있는 경우)
    그 설명 줄을 그림 바로 아래로 옮긴다."""
    def figure_only(b):
        return b["k"] == "p" and "<img" in b["html"] and not b["plain"].strip()

    def fig_caption(b):
        return b["k"] == "p" and bool(re.match(r"^그림\s*\d", b["plain"].strip())) and len(b["plain"].strip()) <= 160 and "<img" not in b["html"]

    out = list(blocks)
    i = 0
    while i < len(out):
        if figure_only(out[i]) and not (i + 1 < len(out) and fig_caption(out[i + 1])):
            for j in range(i + 1, min(len(out), i + 4)):
                if figure_only(out[j]) or out[j]["k"] != "p":
                    break  # 다른 그림이나 표(그림 설명에 딸린 범례 표 등)를 만나면 거기까지만 본다
                if fig_caption(out[j]) and not out[j].get("lab"):
                    out.insert(i + 1, out.pop(j))
                    break
        i += 1
    return out


def _center_figures_captions(blocks):
    """그림(그림만 있는 문단)과 그림 설명, 표 설명 줄을 가운데 정렬로 바꾼다(표 자체는 CSS 로 가운데에 놓는다)."""
    blocks = _move_captions_up(blocks)
    out = []
    for b in blocks:
        if b["k"] == "p" and b.get("al") != "c":
            figure_only = "<img" in b["html"] and not b["plain"].strip()
            if figure_only or _is_caption(b):
                b = dict(b, al="c")
        out.append(b)
    return out


_FORMULA_MERGE_MAX = 10 ** 9  # 합친 줄의 글자 수 상한: 두지 않는다(모든 수식줄을 길이와 상관없이 한 줄로 합친다. 화면에서는 폭에 맞춰 줄바꿈된다)


def _merge_formula_lines(blocks):
    """정량법 계산식을 한 줄로 합친다(생약 검색의 정량법과 같다):
       "가벡세이트메실산염 (...)의 양 (mg)"            "X의 양 (mg) = 표준품의 양 (mg) × Q_T/Q_S"
       "= 표준품의 양 (mg) × Q_T/Q_S"           ->   (가운데 정렬 한 줄)
    앞 줄이 "=" 로 끝나고 다음 줄이 수식뿐인 경우("아니시딘가 =" / 수식)도 합친다."""
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
            # 원문에서 "…의 양 (mg)" 줄이 가운데 정렬이 아니라 양쪽/왼쪽 정렬로 되어 있는 경우도 있다. 다음 줄이 "=" 로 시작하면 그 줄은
            # 앞 줄(식의 왼쪽)에 이어지는 식이므로, 앞 줄이 문장이 아닌 짧은 줄(마침표/"다"/":" 로 끝나지 않음)이면 가운데 정렬로 보고 합친다.
            if not centered and eq_first and 0 < len(a_pl) <= 110 and "=" not in a_pl and not a_pl.endswith((".", "다", ":", ";")):
                centered = True
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


# ---- 탭/공백으로 열을 맞춰 쓴 줄(표는 아니지만 표처럼 보이는 것) ---------------------------------------------------
# 원문은 "L-시스틴 <탭> 0.5 <탭> g" 처럼 탭(또는 긴 공백)으로 이름·숫자·단위를 맞춰 썼는데, 화면에서는 탭이 고정 폭(2em)이라 이름 길이에 따라
# 숫자와 단위의 가로 위치가 줄마다 어긋난다. 이런 줄이 이어지면 테두리 없는 표로 바꿔, 열(숫자, 단위)이 가장 긴 왼쪽 항목 기준으로 세로로 맞게 한다.
_COL_SEP_RE = re.compile(r'<span class="kp-tab"></span>|(?<=\S)[ \u00a0]{5,}(?=\S)')
_P_PART_RE = re.compile(r'^<p class="(kp-p[^"]*)"(?: style="([^"]*)")?>(.*)</p>$', re.S)
_VOID_TAGS = {"br", "img", "hr"}
_UNIT_PAT = r"(?:[kmμµ]?[gL]|mL|μL|mol|mmol|mol/L|mg/mL|μg/mL|%|vol％|vol%|℃|ppm|cm|mm|nm|IU|단위)"
_NUM_RE = re.compile(r"^[\d.,]+(?:\s*[~∼\-–]\s*[\d.,]+)?$")  # 수치("0.5") 또는 범위("13.0 ~ 20.0")
_UNIT_RE = re.compile(rf"^{_UNIT_PAT}$")
_NUM_UNIT_GAP_RE = re.compile(rf"(\d)(?:[  ]|&nbsp;){{2,}}(?={_UNIT_PAT}(?![A-Za-z가-힣]))")  # "5.0  g" 처럼 수치와 단위 사이의 공백이 둘 이상


def _tags_balanced(frag):
    stack = []
    for m in re.finditer(r"<(/?)(\w+)[^>]*?(/?)>", frag):
        closing, name, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if name in _VOID_TAGS or selfclose:
            continue
        if closing:
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
    return not stack


def _column_row(part):
    """문단 HTML 한 줄이 "이름 <탭> 값 <탭> 단위" 같은 열 맞춤 줄이면 (왼쪽 여백 em, [칸 HTML ...]), 아니면 None."""
    m = _P_PART_RE.match(part)
    if not m:
        return None
    cls, style, inner = m.groups()
    if {"kp-c", "kp-h", "kp-r", "kp-h2", "kp-h3"} & set(cls.split()) or "kp-mk" in inner:
        return None  # 가운데/오른쪽 정렬 줄, 제목, 번호 기호가 붙은 줄은 제외
    cells = _COL_SEP_RE.split(inner)
    if len(cells) > 12 or not all(_tags_balanced(c) for c in cells):
        return None
    plain = [re.sub(r"<[^>]+>", "", html.unescape(c)).replace("\u00a0", " ").strip() for c in cells]
    # \ud0ed\uc744 \uba87 \ubc88 \ub20c\ub800\ub294\uc9c0\ub294 \uc55e \uae00\uc790 \uae38\uc774\uc5d0 \ub530\ub77c \ub2ec\ub77c\uc9c0\ubbc0\ub85c(\ud0ed \uc704\uce58\uc5d0 \ub9de\ucd94\ub824\uace0 \uae38\uc774\uc5d0 \ub530\ub77c 1~3\ubc88) \uc5f0\uc18d\ud55c \uad6c\ubd84(\ube48 \uce78)\uc740 \ud55c \uad6c\ubd84\uc73c\ub85c \ubcf4\uace0,
    # \uae00\uc774 \uc788\ub294 \uce78\ub9cc \ucc28\ub840\ub300\ub85c 1\uc5f4, 2\uc5f4, ... \ub85c \uc13c\ub2e4
    kept = [(c, p_) for c, p_ in zip(cells, plain) if p_]
    if len(kept) < 2 or not plain[0] or len(kept) > 8:
        return None
    cells, plain = [c for c, _ in kept], [p_ for _, p_ in kept]
    # 수치와 단위는 별도 열로 떼지 않고 공백 하나만 두고 붙여 한 칸으로 쓴다("0.5", "g" -> "0.5 g", "5.0  g" -> "5.0 g")
    merged_cells, merged_plain = [], []
    for c, p_ in zip(cells, plain):
        c = _NUM_UNIT_GAP_RE.sub(lambda m: m.group(1) + " ", c)
        if merged_cells and _NUM_RE.match(merged_plain[-1]) and _UNIT_RE.match(p_):
            merged_cells[-1] = merged_cells[-1].rstrip() + " " + c.lstrip()
            merged_plain[-1] = merged_plain[-1] + " " + p_
        else:
            merged_cells.append(c)
            merged_plain.append(p_)
    cells, plain = merged_cells, merged_plain
    if len(cells) < 2:
        return None
    if len(plain[0]) > 70 or any(len(x) > 30 for x in plain[1:]):
        return None  # 긴 글은 열이 아니라 문장이다
    if any(re.search(r"다\.(\s|$)", x) for x in plain):
        return None  # 문장
    if len(plain[0]) >= 25 and re.match(r"[가-힣]", plain[1]) and len(plain[1]) >= 8:
        return None  # 긴 줄이 탭에서 끊겨 이어지는 글(줄바꿈 대신 쓴 탭)
    ml = re.search(r"margin-left:([\d.]+)em", style or "")
    return (ml.group(1) if ml else "0"), cells


_NUM_PARSE_RE = re.compile(r"^(?P<i>\d[\d,]*)(?P<f>\.\d+)?(?:[  ]*(?P<u>.+))?$")


def _aligned_rows_html(rows):
    """열 맞춤 표의 줄들(칸 HTML 목록)을 <tr> HTML 로 바꾼다. 수치가 든 열은 소수점이 세로로 맞게 "정수부(오른쪽 정렬) | 소수부(왼쪽 정렬) | 단위(왼쪽 정렬)" 세 칸으로
    나눈다. 그러면 소수점과 단위의 가로 위치가 모든 줄에서 같고, 수치와 단위 사이의 간격은 열 사이 여백이 정한다(공백 하나로 한정하지 않는다)."""
    ncol = max(len(r) for r in rows)
    numeric = []
    for k in range(ncol):
        vals = [r[k] for r in rows if len(r) > k]
        ok = sum(1 for c in vals if "<" not in c and _NUM_PARSE_RE.match(html.unescape(c).replace(" ", " ").strip()))
        numeric.append(len(vals) >= 1 and ok >= 1 and ok / len(vals) > 0.6)
    out = []
    for r in rows:
        tds = []
        for k, c in enumerate(r):
            m = None
            if numeric[k] and "<" not in c:
                m = _NUM_PARSE_RE.match(html.unescape(c).replace(" ", " ").strip())
            if numeric[k] and m:
                ip, fp, un = m.group("i"), m.group("f") or "", m.group("u") or ""
                tds.append(f'<td class="ni">{ip}</td><td class="nf">{fp}</td><td class="nu">{html.escape(un, quote=False)}</td>')
            elif numeric[k]:
                tds.append(f'<td colspan="3">{c}</td>')  # 수치 열 안의 글("적당량" 등)은 세 칸에 걸쳐 왼쪽에 놓는다
            else:
                tds.append(f"<td>{c}</td>")
        out.append("<tr>" + "".join(tds) + "</tr>")
    return "".join(out)


def _align_tab_columns(parts):
    """블록 HTML 조각 목록에서, 같은 왼쪽 여백으로 이어지는 열 맞춤 줄 둘 이상을 테두리 없는 표 하나로 바꾼다."""
    out, run = [], []

    def flush():
        nonlocal run
        if len(run) >= 2:
            ml = run[0][1][0]
            rows = _aligned_rows_html([r[1][1] for r in run])
            out.append(f'<table class="kp-aligntbl"{f" style=margin-left:{ml}em" if ml != "0" else ""}>{rows}</table>')
        else:
            out.extend(r[0] for r in run)
        run = []

    for part in parts:
        row = _column_row(part) if part.startswith("<p ") else None
        if row is None:
            flush()
            out.append(part)
            continue
        if run and run[0][1][0] != row[0]:
            flush()
        run.append((part, row))
    flush()
    return out


def _join_blocks_html(blocks, flat=False):
    parts = [block_html(b, heading=bool(b.get("hd")) if flat else False) for b in blocks]
    return "".join(_align_tab_columns(parts))


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
    head = _center_figures_captions(head)
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
        body = _join_blocks_html(_space_formula_lines(_apply_hierarchy(_merge_formula_lines(_center_figures_captions(bs)))))
        out.append(
            f'<div class="section"><div class="section-header"><div class="label">{html.escape(lab)}</div>'
            f'<span class="toggle-icon">−</span></div><div class="section-content">{body}</div></div>'
        )
    return "".join(out)


def _split_heading_marker(blocks):
    """굵은 상위 항목 이름 바로 뒤에 같은 줄로 하위 번호 항목("1) 제 1 액 ...")이 이어진 문단을 둘로 나눈다: "시 험 액 ⇥ 1) 제 1 액 염화나트륨 ..." ->
    "시 험 액" / "1) 제 1 액 염화나트륨 ...". 번호 항목은 나눈 뒤에 번호 계층(들여쓰기) 규칙을 그대로 받는다."""
    def has_marker(text):
        return any(rx.match(text) for _k, rx, _w in _HI_KINDS)

    out = []
    for b in blocks:
        if b["k"] != "p" or b.get("lab") or b.get("al") == "c":
            out.append(b)
            continue
        m = re.match(r'^\s*(<b>(.*?)</b>)((?:<span class="kp-tab"></span>|[\s\u00a0]|&nbsp;)*)(.*)$', b["html"], re.S)
        if m:
            title = _WS_RE.sub(" ", html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))).strip()
            rest_html = m.group(4)
            rest_plain = _WS_RE.sub(" ", html.unescape(re.sub(r"<[^>]+>", "", rest_html))).strip()
            if (
                title and re.search(r"[가-힣A-Za-z]", title) and len(title) <= 25 and not has_marker(title)
                and rest_plain and has_marker(rest_plain) and _tags_balanced(rest_html)
            ):
                out.append(dict(b, html=m.group(1), plain=title))
                nb = dict(b, html=rest_html, plain=rest_plain)
                nb.pop("top", None)
                out.append(nb)
                continue
        out.append(b)
    return out


def _mark_top_gaps(blocks, explicit=False):
    """일반시험법 항목 안의 최상위 항목 바로 위에 빈 줄 한 줄을 넣도록 표시한다(항목 맨 처음 문단은 제외).
    최상위 항목: 상위 항목 이름을 지정한 항목(_TOP_HEADINGS)은 지정한 이름들만, 지정하지 않은 항목은 소제목(hd)과 굵은 글씨로 시작하는 번호 없는
    바깥 문단("조 작 법", "시 험 액", "장치" ...)."""
    out, seen = [], False
    for b in blocks:
        top = False
        if b["k"] == "p" and b.get("al") != "c" and not b.get("lab"):
            if b.get("top"):
                top = True
            elif not explicit:
                if b.get("hd"):
                    top = True
                elif b.get("hi") is None and b.get("left", 0) <= 0:
                    m = re.match(r"^\s*<b>(.*?)</b>", b["html"], re.S)
                    if m:
                        lead = _WS_RE.sub(" ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()
                        top = bool(lead) and len(lead) <= 25 and bool(re.search(r"[가-힣A-Za-z]", lead)) and not any(rx.match(lead) for _k, rx, _w in _HI_KINDS)
        if top and seen:
            b = dict(b, gap_top=True)
        if b["k"] in ("p", "tbl", "fig"):
            seen = True
        out.append(b)
    return out


_TERM_GAP = "&nbsp;" * 3  # 용어(굵은 글씨)와 설명문 사이의 공백 세 개


def _term_gap(h):
    """이미 앞부분이 굵게 적힌 용어 단락(굵은 글씨 바로 뒤에 공백이 있는 것)의 용어-설명 사이 공백을 다섯 개로. 공백이 없거나 굵은 글씨가 전부이면 그대로."""
    m = re.match(r"^(\s*(?:<b>.*?</b>)+)[ \u00a0]+(?=\S)", h, re.S)
    return m.group(1) + _TERM_GAP + h[m.end():] if m else h


def _split_open_tags(frag):
    """frag 끝에 열려 있는 태그를 닫는 글자열과 다시 여는 글자열. 태그 짝이 맞지 않으면 (None, None)."""
    stack = []
    for m in re.finditer(r"<(/?)(\w+)([^>]*?)(/?)>", frag):
        closing, name, attrs, selfclose = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if name in _VOID_TAGS or selfclose:
            continue
        if closing:
            if not stack or stack.pop()[0] != name:
                return None, None
        else:
            stack.append((name, "<" + m.group(2) + attrs + ">"))
    return "".join("</%s>" % n for n, _o in reversed(stack)), "".join(o for _n, o in stack)


def _bold_term(h):
    """단락 HTML 맨 앞에서 공백 둘 이상으로 갈라지는 앞부분("가용성전분시액, 0.5 %  가용성전분을 ...")을 굵게 감싼 HTML. 앞부분이 없거나 너무 길거나(100자 넘음),
    쉼표 따위로 끝나(목록의 한 항목) 용어가 아니거나, 태그 짝이 맞지 않으면 None. 공백 둘 뒤에 바로 태그(<i> 등)가 오는 경우와, 공백만 굵게 쓴 경우도 찾는다."""
    h = re.sub(r"<b>([  ]+)</b>", lambda m: m.group(1), h)  # 공백만 굵게 쓴 것("용어<b>  </b>설명")은 그냥 공백으로
    text_len = 0
    for m in re.finditer(r"<[^>]+>|[^<]+", h):
        tok = m.group(0)
        if tok.startswith("<"):
            continue
        mm = re.search(r"(?<=\S)[  ]{2,}(?=\S|$)", tok)
        if mm is None and text_len > 0:
            mm = re.match(r"[ \u00a0]{2,}(?=\S|$)", tok)  # 앞 태그(</sub> 등) 바로 뒤에서 시작하는 공백 둘
        if mm:
            cut = m.start() + mm.start()
            front, rest = h[:cut], h[cut:]
            plain_front = html.unescape(re.sub(r"<[^>]+>", "", front)).strip()
            if not re.sub(r"<[^>]+>", "", rest).strip("  "):
                return None  # 뒤에 설명이 없다
            if 1 <= len(plain_front) <= 100 and plain_front[-1] not in ",;、" and "<b>" not in front:
                close, reopen = _split_open_tags(front)
                if close is None:
                    return None
                out = "<b>" + front + close + "</b>" + _TERM_GAP + reopen + rest.lstrip(" " + chr(0xa0))
                return re.sub(r"<(i|sub|sup)></" + chr(92) + "1>", "", out)  # 공백만 들어 있던 태그는 지운다
            return None
        text_len += len(tok)
        if text_len > 140:
            return None
    return None


_RELATED_INDENT_EM = 1.0  # 용어에 딸린 단락의 들여쓰기(맨 앞 공백 두 개). 용어 단락의 내어쓰기(이어지는 줄의 들여쓰기)도 같은 크기로 한다


_FORMULA_RE = re.compile(r"(?:[A-Z][a-z]?\d*|\d+|[()\[\]\u00b7.+\-,])+")  # 원소기호/화학식: Ag, NaCl, H2NC6H4N(C2H5)2\u00b7H2SO4\u00b7H2O


def _bold_formula(h):
    """단락(앞 공백 둘 이상으로 들여 쓴 줄) 맨 앞이 "원소기호 또는 화학식 + 공백 둘 + 설명"이면, 처음 나오는 공백 둘(맨 앞 공백은 이미 빠져 있다)을 용어와 설명의
    구분점으로 보고 화학식을 굵게 한 HTML. 아니면 None."""
    nh = _bold_term(h)
    if not nh:
        return None
    m = re.match(r"^<b>(.*?)</b>", nh, re.S)
    front = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
    if front and _FORMULA_RE.fullmatch(front) and re.search(r"[A-Z]", front):
        return nh
    return None


def _bold_name_formula(h):
    """"용어 + 공백 하나 + 화학식" 모양의 단락("파라톨루엔설폰클로라미드나트륨삼수화물 C7H7ClNNaO2S·3H2O", "인산테트라부틸암모늄 (C4H9)4NH2PO4 흰색의 가루로 ...")의
    용어를 굵게 한 HTML. 공백 둘이 없어 _bold_term이 못 찾는 경우다. 앞부분은 한글이 있는 한 단어(또는 화학식 전체가 뒤따르는 짧은 이름), 뒷부분은 화학식(뒤에 설명이
    이어져도 된다)일 때만. 아니면 None."""
    for m in re.finditer(r"(?<=\S)[ \u00a0](?=\S)", h):
        front, rest = h[:m.start()], h[m.end():]
        if "<" in front and not _tags_balanced(front):
            continue
        plain_rest = html.unescape(re.sub(r"<[^>]+>", "", rest)).strip()
        plain_front = html.unescape(re.sub(r"<[^>]+>", "", front)).strip()
        if not (re.search(r"[가-힣]", plain_front) and len(plain_front) <= 100 and "<b>" not in front
                and "=" not in plain_front and not plain_front[0].isdigit()):
            continue
        if _FORMULA_RE.fullmatch(plain_rest) and re.search(r"[A-Z]", plain_rest):
            return "<b>" + front + "</b>" + _TERM_GAP + rest
        if re.match(r"[\[〔](의약품각조|최순품|용량분석용|시약)", plain_rest):
            return "<b>" + front + "</b>" + _TERM_GAP + rest  # "이름 [의약품각조, 제 2 부]"
        # 화학식 뒤에 설명이 바로 이어지는 줄: "이름 화학식 설명 ..."
        mm = re.match(r"(\S+) (?=[가-힣])", plain_rest)
        mb = None if mm else re.match(r"(\S+)(?= \[)", plain_rest)  # 화학식 뒤에 [최순품] 따위가 오는 줄
        if mm and _FORMULA_RE.fullmatch(mm.group(1)) and re.search(r"[A-Z]", mm.group(1)) and re.search(r"[0-9()]", mm.group(1)) and " " not in plain_front:
            k = _formula_end(rest, len(mm.group(1)))
            if k is not None:
                return "<b>" + front + "</b>" + _TERM_GAP + rest[:k] + _TERM_GAP + rest[k:].lstrip(" \u00a0")
        if mb and _FORMULA_RE.fullmatch(mb.group(1)) and re.search(r"[A-Z]", mb.group(1)) and " " not in plain_front:
            return "<b>" + front + "</b>" + _TERM_GAP + rest
    return None


_REAGENT_NAME_RE = re.compile(
    r"(?:[^\s,\d]+ ){0,3}?[^\s,]*?(?:시액|완충액|용액|[가-힣]{2,}액)(?:\([^)]*\))?"  # 이름: 아세트산염완충액, 아이코노겐 (Eikonogene) 시액
    r"(?:, (?:pH [\d.]+|[\d.]+ ?(?:mol/L|mmol/L|mg/mL|g/L|%|vol%)|[가-힣]+용))*"  # 뒤에 붙는 조건: , pH 4.7  , 0.1 mol/L  , 전분소화력시험용
    r"(?=\s|$)"
)


def _bold_reagent_name(h):
    """원문에 용어와 설명 사이에 공백이 하나뿐인 시약·시액 줄("아세트산염완충액, pH 3.9 묽은아세트산 60 mL에 ...")에서, "...시액/완충액/용액"(뒤에 ", pH 3.9",
    ", 0.1 mol/L" 따위 조건이 붙어도 됨)까지를 용어로 보아 굵게 한 HTML. 아니면 None."""
    vis = html.unescape(re.sub(r"<[^>]+>", "", h)).replace(chr(0xa0), " ")
    m = _REAGENT_NAME_RE.match(vis.lstrip())
    if not m or len(m.group(0)) > 70:
        return None
    k = _formula_end(h, len(m.group(0)))
    if k is None or html.unescape(re.sub(r"<[^>]+>", "", h[:k])).replace(chr(0xa0), " ").strip() != m.group(0):
        return None
    rest = h[k:]
    if rest.strip() and not rest[0] in " " + chr(0xa0):
        return None
    return "<b>" + h[:k] + "</b>" + (_TERM_GAP + rest.lstrip(" " + chr(0xa0)) if rest.strip() else "")


def _bold_first_gap(h):
    """다른 규칙으로 용어를 찾지 못한 줄: 처음 나오는 공백 둘(없으면 처음 나오는 공백 하나) 앞을 용어로 보아 굵게 한 HTML. 쉼표로 끝나도 된다. 공백이 없으면 None."""
    for pat in (r"(?<=\S)[ \u00a0]{2,}(?=\S|$)", r"(?<=\S)[ \u00a0](?=\S|$)"):
        for m in re.finditer(r"<[^>]+>|[^<]+", h):
            tok = m.group(0)
            if tok.startswith("<"):
                continue
            mm = re.search(pat, tok)
            if mm is None and m.start() > 0 and re.match(r"[ \u00a0]+(?=\S|$)", tok) and re.sub(r"<[^>]+>", "", h[:m.start()]).strip():
                mm = re.match(r"[ \u00a0]{2,}(?=\S|$)" if "{2,}" in pat else r"[ \u00a0](?=\S|$)", tok)
            if mm is None:
                continue
            cut = m.start() + mm.start()
            front, rest = h[:cut], h[cut:]
            plain_front = html.unescape(re.sub(r"<[^>]+>", "", front)).strip()
            if not re.sub(r"<[^>]+>", "", rest).strip(" \u00a0") or not (1 <= len(plain_front) <= 100) or "<b>" in front:
                break
            close, reopen = _split_open_tags(front)
            if close is None:
                break
            out = "<b>" + front + close + "</b>" + _TERM_GAP + reopen + rest.lstrip(" \u00a0")
            return re.sub(r"<(i|sub|sup)></" + chr(92) + "1>", "", out)
    return None


def _formula_end(h, nchars):
    """HTML h 맨 앞에서 보이는 글자 nchars개가 끝나는 자리(태그는 글자로 세지 않는다). 태그 안에서 끝나면 None."""
    seen = 0
    for m in re.finditer(r"<[^>]+>|[^<]", h):
        tok = m.group(0)
        if tok.startswith("<"):
            continue
        seen += 1
        if seen == nchars:
            e = m.end()
            c = re.match(r"(?:</[^>]+>)+", h[e:])  # 바로 뒤의 닫는 태그(</sub> 등)까지
            if c:
                e += c.end()
            return e if _tags_balanced(h[:e]) else None
    return None



def _is_indented(b):
    """앞 용어 단락의 글자 시작 위치보다 들어가 시작하는 단락인가: 맨 앞에 공백이 둘 이상 있거나, 왼쪽 여백이 있거나, 첫 줄 들여쓰기(양수)가 있다."""
    return b.get("lead", 0) >= 2 or b.get("left", 0) >= 500 or b.get("ind", 0) > 0


def _style_terms(blocks):
    """일반시험법 92-x(표준품, 시약·시액, 용량분석용표준액 ...)의 용어 단락. 용어 단락은 용어(굵은 글씨, 또는 공백 둘 앞의 앞부분)로 시작하며 맨 왼쪽에서 시작한다.
    그 아래에서 용어 글자의 시작 위치보다 들어가 시작하는 단락(맨 앞 공백 둘 이상, 들여쓰기, 왼쪽 여백)은 앞 용어와 관련된 단락이다("  융점 : 180 ℃" 등).
    용어 단락과 그에 관련된 단락들을 한 묶음으로 보아, 용어를 굵게 하고 묶음의 위아래에 간격을 둔다. 설명 없이 용어만 적힌 줄(끝이 공백 둘)은 바로 다음
    줄이 들여 쓴 줄이면 용어 단락으로 본다. 들여 쓴 줄이 "화학식 + 공백 둘 + 설명"이면 그 공백 둘이 용어(화학식)와 설명의 구분점이다."""
    out = []
    in_group = False
    for n, b in enumerate(blocks):
        term = None
        if b["k"] == "p" and b.get("al") != "c" and not b.get("lab") and not _is_indented(b):
            if re.match(r"\s*<b>", b["html"]):
                term = dict(b, html=_term_gap(b["html"]), term_top=True)  # 원문이 이미 앞부분을 굵게 쓴 용어 단락("조    제", "표    정" ...)
            else:
                nh = _bold_term(b["html"])
                if nh:
                    term = dict(b, html=nh, term_top=True)
                elif _bold_name_formula(b["html"]) or (b.get("ind", 0) < 0 and _bold_reagent_name(b["html"])):
                    term = dict(b, html=_bold_name_formula(b["html"]) or _bold_reagent_name(b["html"]), term_top=True)  # "용어 화학식"(공백 하나)만 있는 줄
                elif (
                    b.get("ind", 0) < 0 and not any(rx.match(b["plain"].strip()) for _k, rx, _w in _HI_KINDS)
                    and not re.match(r"\d+\)", b["plain"].strip()) and not b["plain"].strip()[0].isdigit() and " : " not in b["plain"][:40]
                    and not (n + 1 < len(blocks) and (blocks[n + 1]["k"] == "tbl" or (blocks[n + 1]["k"] == "p" and (_is_indented(blocks[n + 1]) or blocks[n + 1].get("lead", 0) >= 1))))
                    and _bold_first_gap(b["html"]) and not re.match(r"[:：]", re.sub(r"<[^>]+>", "", _bold_first_gap(b["html"]).split("</b>", 1)[1]).replace("&nbsp;", "").strip())
                ):
                    term = dict(b, html=_bold_first_gap(b["html"]), term_top=True)  # 그 밖의 내어쓰기 단락: 처음 나오는 공백 둘(없으면 공백 하나)까지가 용어
                else:
                    nxt = blocks[n + 1] if n + 1 < len(blocks) else None
                    plain_h = html.unescape(re.sub(r"<[^>]+>", "", b["html"]))
                    if (
                        nxt is not None and ((nxt["k"] == "p" and (_is_indented(nxt) or nxt.get("lead", 0) >= 1)) or nxt["k"] == "tbl")
                        and (re.search(r"\S[ \u00a0]+$", plain_h) or (b.get("lead", 0) == 0 and len(plain_h.strip()) <= 40 and plain_h.strip()[-1] not in ".다:)")) and 1 <= len(plain_h.strip()) <= 100 and _tags_balanced(b["html"])
                    ):
                        term = dict(b, html="<b>" + b["html"].rstrip(" \u00a0") + "</b>", term_top=True)  # 용어만 있는 줄
        if term is not None:
            if in_group:
                _close_term_group(out)
            term["hi"] = None  # 번호 계층(_apply_hierarchy)이 앞 번호 항목의 내용으로 잘못 붙인 깊은 들여쓰기는 쓰지 않는다
            if term.get("ind", 0) < 0:
                term["term_hang"] = _RELATED_INDENT_EM  # 내어쓰기: 첫 줄은 왼쪽 끝, 이어지는 줄은 딸린 단락과 같은 들여쓰기 위치
            out.append(term)
            in_group = True
        elif in_group and b["k"] == "p" and (_is_indented(b) or b.get("lead", 0) >= 1):
            # 앞 용어와 관련된(들어간) 단락: 같은 묶음. 원문처럼 들여 쓰고(맨 앞 공백 개수만큼), 앞 줄과의 간격은 좁힌다
            if out and out[-1]["k"] == "p":
                out[-1] = dict(out[-1], term_tight=True)
            lead = b.get("lead", 0)
            rel = dict(b, term_ind=min(max(lead, 2), 6) * (_RELATED_INDENT_EM / 2)) if lead >= 2 else dict(b)
            rel["hi"] = None
            if rel.get("ind", 0) < 0 and not any(rx.match(rel["plain"].strip()) for _k, rx, _w in _HI_KINDS):
                rel["ind"] = 0  # "0.1mol/L ..." 따위를 번호 기호로 오인해 깊게 내어쓰지 않는다
            fh = _bold_formula(rel["html"])
            if fh:
                rel["html"] = fh  # "  화학식  설명": 화학식이 용어, 공백 둘이 용어와 설명의 구분점
            out.append(rel)  # 딸린 단락은 첫 줄도 이어지는 줄도 같은 들여쓰기(내어쓰기 없음)
        elif b["k"] == "p" and b.get("al") == "c" and out and _is_bold_title(b):
            # 가운데 정렬한 굵은 제목("0.1 mol/L 과염소산" ...): 앞 묶음을 닫고, 제목 위에 빈 줄 한 줄
            if in_group:
                _close_term_group(out)
            in_group = False
            out.append(dict(b, gap_top=True, fm_top=False, fm_bot=False))  # 제목 아래에는 빈 줄을 두지 않는다(앞 계산식 때문에 붙은 아래 간격도 뺀다)
        elif in_group and (b["k"] != "p" or b.get("al") == "c"):
            out.append(b)  # 묶음 안의 계산식(가운데 줄)·표·그림은 묶음을 끊지 않는다. 그 뒤에 들여 쓴 줄이 이어지면 같은 묶음
        else:
            if in_group:
                _close_term_group(out)
            in_group = False
            out.append(b)
    if in_group:
        _close_term_group(out)
    return out


def _is_bold_title(b):
    """전체가 굵은 글씨인 짧은 단락(가운데 정렬 제목)."""
    h = b["html"].strip()
    if not (h.startswith("<b>") and h.endswith("</b>")) or len(b["plain"].strip()) > 80:
        return False
    rest = re.sub(r"<b>.*?</b>", "", h, flags=re.S)  # 굵은 글씨가 여러 조각(<b>..</b><b>..</b>)으로 나뉘어 있어도 전체가 굵으면 제목
    return not re.sub(r"<[^>]+>|&nbsp;|\s", "", rest)


def _close_term_group(out):
    """용어 묶음의 마지막 본문 단락(뒤의 계산식·표·그림은 건너뛴다) 아래에 묶음 간격을 준다."""
    for n in range(len(out) - 1, -1, -1):
        if out[n]["k"] == "p" and out[n].get("al") != "c":
            out[n] = dict(out[n], term_bot=True)
            return


def _flat_html(blocks, top_gaps=False, explicit_tops=False, terms=False):
    blocks = _split_heading_marker(blocks)
    # 일반시험법/일반정보 등 본문도 의약품각조와 같이 수식줄을 한 줄로 합치고, 계산식 덩어리의 위아래에 빈 줄 한 줄씩 띄운다
    bs = _space_formula_lines(_apply_hierarchy(_merge_formula_lines(_center_figures_captions(blocks))))
    if terms:
        bs = _style_terms(bs)
    if top_gaps and not terms:  # 92-x(표준품, 시약·시액 ...)는 용어 묶음 간격(_style_terms)을 쓰고, 최상위 항목 빈 줄은 쓰지 않는다
        bs = _mark_top_gaps(bs, explicit_tops)
    return _join_blocks_html(bs, flat=True)


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
            # 분류 이름은 번호까지 그대로 둔다("2) 생물학적제제 등"): 목록의 품목 이름 아래 줄에 이 분류가 나온다
            title = _WS_RE.sub(" ", b["plain"]).strip()
            if title and docno == 4:
                group = title
                cur = None
            i += 1
            continue
        # 제목 스타일이 "바탕글"/"의약품명"(별표 없음)/"항목"/"머리말" 등으로 달라 위에서 못 알아본 새 품목: 앞 품목의 항목명(성상, 저장법 ...)이
        # 이미 나온 뒤에, 큰 글자(1050~1300)의 가운데 정렬 한글 제목 한 줄 + 바로 다음 같은 모양의 영문 제목 한 줄이 오면 새 품목이다.
        # (수식 줄은 글자가 더 크고 "=" 가 있어 걸리지 않는다.)
        if cur is not None and i + 1 < n and _is_title_pair(blocks[i], blocks[i + 1]) and any(x.get("lab") for x in cur["blocks"]):
            ko, en = _WS_RE.sub(" ", b["plain"]).strip(), _WS_RE.sub(" ", blocks[i + 1]["plain"]).strip()
            cur = _new_entry(docno, ko, en, group=group, part=part)
            entries.append(cur)
            i += 2
            continue
        if cur is not None:
            cur["blocks"].append(b)
        i += 1
    return entries


def _is_title_pair(b, nb):
    """큰 글자(1050~1300) 가운데 정렬 한글 제목 줄(b) + 바로 다음 같은 모양의 영문 제목 줄(nb)인가."""
    def big_center(x):
        return x["k"] == "p" and 1050 <= x.get("vs", 0) < 1300 and x.get("al") == "c" and not x.get("lab")
    if not (big_center(b) and big_center(nb)):
        return False
    ko, en = b["plain"].strip(), nb["plain"].strip()
    return (
        bool(ko) and bool(en) and len(ko) < 80 and len(en) < 160
        and bool(_HANGUL_RE.search(ko)) and not _LATIN_RE.match(ko[:1]) and "=" not in ko
        and bool(_LATIN_RE.search(en)) and not _HANGUL_RE.search(en) and "=" not in en
    )


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
        if b["k"] == "p" and len(pl) < 90 and re.match(r"^\d{1,2}\.\d{1,2}\.\d{1,2}\.\s*[가-힣]", pl):
            # "1.1.1. 트로키제" 같은 3단계 제목줄: 왼쪽 정렬 소제목(원문에서 가운데 정렬이거나 들여써 있어도)
            b = dict(b, hd=True, h3=True, al="l", left=0, ind=0)
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


# 번호 없이 굵은 글씨로만 적힌 상위 항목이 바로 앞 "N)" 번호 항목의 내용으로 잘못 들어가(들여쓰기가 깊어져) 하위 항목처럼 보이는 일반시험법 항목:
# 항목별로 상위 항목 이름을 지정하면 그 문단은 맨 바깥(깊이 0)에 놓이고, 그 아래 번호 계층은 새로 시작한다.
# 장치 그림과 범례(표 칸 안의 그림, 범례 줄)를 가운데 정렬해 보일 일반시험법 항목: 항목 본문 전체를 kp-center-figs 로 감싼다(CSS 가 그림과 범례 덩어리를 가운데에 둔다).
_CENTER_FIGURE_ENTRIES = {(5, "암모늄시험법")}

_TOP_HEADINGS = {
    (5, "무균시험법"): [
        "미생물오염에 대한 예방조치", "배지와 배양온도", "배지의 적합성", "측정법의 적합성시험", "검체의 무균시험", "관찰과 결과의 판정",
        "무균시험 적합이 요구되는 주사제, 안연고제, 점안제 및 다른 비주사제에 대한 시험 적용",
    ],
    (5, "생약시험법"): ["검체 채취", "분석용 검체 조제", "현미경 시험", "순도시험"],
    (5, "소화력시험법"): ["전분소화력시험법", "단백소화력시험법", "지방소화력시험법"],
    (5, "금속(원소)불순물시험법"): ["검체의 전처리", "시험법", "시험법의 밸리데이션 요건", "용어"],  # 원문에서 "시 험 법"처럼 글자 사이를 띄운 것도 공백을 뺀 이름으로 맞춘다
}


def _mark_top_headings(docno, entries):
    for e in entries:
        heads = _TOP_HEADINGS.get((docno, e["ko"]))
        if not heads:
            continue
        want = {re.sub(r"\s+", "", h) for h in heads}
        for b in e["blocks"]:
            if b["k"] != "p":
                continue
            m = re.match(r"\s*<b>(.*?)</b>", b["html"], re.S)  # 문단 맨 앞의 굵은 글씨가 상위 항목 이름이어야 한다(본문 속 같은 말은 제외)
            lead = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) if m else ""
            if lead in want:
                b["top"] = True


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

    _mark_top_headings(docno, entries)
    out = []
    for e in entries:
        bs = e.pop("blocks")
        body_html = _sections_html(bs) if kind == "drug" else _flat_html(bs, top_gaps=(docno == 5), explicit_tops=((docno, e["ko"]) in _TOP_HEADINGS), terms=(docno == 5 and e["no"].startswith("92-")))
        if (docno, e["ko"]) in _CENTER_FIGURE_ENTRIES:
            body_html = '<div class="kp-center-figs">' + body_html + "</div>"
        e["html_z"] = zlib.compress(body_html.encode("utf-8"), 6)
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
