# -*- coding: utf-8 -*-
"""
생약시험법.hwpx(일반시험법 "35. 생약시험법")에서 생약 상세 화면이 연결하는 항목만
뽑아 계층 구조 HTML로 만드는 모듈.

이 문서는 항목 구조를 문단 모양(들여쓰기)이 아니라 번호 기호로만 표시한다
(들여쓰기는 공백 문자). 그래서 문단을 순서대로 읽으면서 앞머리 기호로 깊이를
정하고, 기호가 같은 깊이는 형제로, 더 깊은 기호는 자식으로 묶어 중첩 구조를 만든다.

    가.  ->  (제 1 법)  ->  1)  ->  가)  ->  ①  ->  ㉮

기호가 없는 문단(설명 문장, "조작조건" 아래의 "검출기 : ..." 같은 줄, 표, 그림)은
바로 앞 기호 항목의 본문으로 붙인다. 표/수식/아래첨자/이탤릭은 parse_hwpx 의
기존 변환 함수를 그대로 쓴다.

뽑는 항목:
  ibmul(순도시험 가. 이물), heavy(나. 중금속), pesticide(다. 잔류농약),
  so2(라. 이산화황), mycotoxin(마. 곰팡이독소),
  loss(건조감량), ash(회분), acid_ash(산불용성회분)
"""

import html
import re
import zipfile
from xml.etree import ElementTree as ET

from parse_hwpx import (
    NS,
    _EQ_SUB_L,
    _EQ_SUB_R,
    _ITALIC_L,
    _ITALIC_R,
    _TABLE_LINE_RE,
    _apply_subscript_markup,
    _convert_equation,
    _detect_bold_charpr_ids,
    _detect_italic_charpr_ids,
    _detect_subscript_charpr_ids,
    _local,
    _render_text_line_html,
    _rows_to_table_html,
    _table_to_text,
)

HC_NS = "http://www.hancom.co.kr/hwpml/2011/core"
_IMG_MARK = "\x07"  # 그림 자리 표시: "\x07image1\x07"
_EQ_MARK = "\x08"  # 수식 자리 표시: "\x08번호\x08" (번호는 _EQ_HTML 목록의 위치)
_EQ_HTML = []

def _equation_html(script: str) -> str:
    """hwpx 수식 스크립트를 HTML로 바꾼다(분수는 위아래로 쌓은 분수). 변환 규칙은
    생약 상세 화면의 수식과 같게 parse_hwpx 의 변환기를 그대로 쓴다."""
    return _apply_subscript_markup(html.escape(_convert_equation(script), quote=False))


_KOR = "가나다라마바사아자차카타파하"
# (깊이, 정규식). 깊이는 숫자가 클수록 안쪽이다.
_MARKERS = [
    (1, re.compile(rf"^([{_KOR}]\.)\s*")),
    (2, re.compile(r"^(\(제\s*\d+\s*법\))\s*")),
    (3, re.compile(r"^(\d{1,2}\))\s*")),
    (4, re.compile(rf"^([{_KOR}]\))\s*")),
    (5, re.compile(r"^([①-⑳])\s*")),
    (6, re.compile(r"^([㉮-㉻])\s*")),
]
_MARKER_CHARS = _EQ_SUB_L + _EQ_SUB_R + _ITALIC_L + _ITALIC_R


def _plain(s: str) -> str:
    s = re.sub(_EQ_MARK + r"\d+" + _EQ_MARK, "", s)
    for ch in _MARKER_CHARS + _IMG_MARK:
        s = s.replace(ch, "")
    return s


def _paragraph_text(p, bold_ids, sub_ids, italic_ids):
    """문단 하나를 마커(아래첨자/이탤릭/그림)가 섞인 문자열로 만든다."""
    parts = []
    for run in p.findall("hp:run", NS):
        cp = run.get("charPrIDRef")
        for child in run:
            tag = _local(child.tag)
            if tag == "t":
                text = "".join(child.itertext())
                if not text:
                    continue
                if cp in sub_ids:
                    text = _EQ_SUB_L + text + _EQ_SUB_R
                if cp in italic_ids:
                    text = _ITALIC_L + text + _ITALIC_R
                parts.append(text)
            elif tag == "equation":
                script = child.find("hp:script", NS)
                if script is not None and script.text:
                    _EQ_HTML.append(_equation_html(script.text))
                    parts.append(f"{_EQ_MARK}{len(_EQ_HTML) - 1}{_EQ_MARK}")
            elif tag == "tbl":
                table_text = _table_to_text(child, sub_ids, italic_ids)
                if table_text:
                    parts.append("\n" + table_text + "\n")
            elif tag == "pic":
                img = child.find(f".//{{{HC_NS}}}img")
                if img is not None and img.get("binaryItemIDRef"):
                    parts.append("\n" + _IMG_MARK + img.get("binaryItemIDRef") + _IMG_MARK + "\n")
                # 그림 위에 얹힌 글상자 글자(예: "* 숫자는 mm를 표시")도 그림 아래 줄로 붙인다.
                caption = "".join("".join(t.itertext()) for t in child.iter(f"{{{NS['hp']}}}t")).strip()
                if caption:
                    parts.append(caption + "\n")
    return "".join(parts)


def _marker_of(text: str):
    """앞머리 기호가 있으면 (깊이, 기호, 나머지 글)을, 없으면 None을 돌려준다."""
    stripped = text.lstrip()
    plain = _plain(stripped)
    for depth, rx in _MARKERS:
        m = rx.match(plain)
        if not m:
            continue
        # 마커 문자가 섞인 원문에서 같은 길이만큼 잘라내야 이탤릭/아래첨자가 남는다.
        consumed, i = 0, 0
        while consumed < m.end() and i < len(stripped):
            if stripped[i] not in _MARKER_CHARS and stripped[i] != _IMG_MARK:
                consumed += 1
            i += 1
        return depth, m.group(1), stripped[i:]
    return None


# 항목별 표시 옵션(곰팡이독소 항목에서만 켠다): merge_eq = "=" 로 시작하는 수식 줄을 바로
# 위 줄과 한 줄로 합쳐 가운데 정렬, plain_tables = 표 첫 행을 제목 행으로 꾸미지 않고
# 이탤릭도 쓰지 않음.
_OPTS = {}

_IMG_RE = re.compile(_IMG_MARK + r"(\w+)" + _IMG_MARK)
_EQ_RE = re.compile(_EQ_MARK + r"(\d+)" + _EQ_MARK)


def _render_body(text: str) -> str:
    """기호 없는 본문(표/수식/그림 포함)을 HTML로 바꾼다. 표는 <table>로, 그림은
    가운데 정렬 블록으로, 수식만 있는 줄은 가운데 정렬 블록으로 그린다."""
    lines = [ln for ln in text.strip("\n").split("\n")]
    pieces = []  # [(블록 여부, html)]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _TABLE_LINE_RE.match(ln):
            j = i
            while j < len(lines) and _TABLE_LINE_RE.match(lines[j]):
                j += 1
            table = _rows_to_table_html(lines[i:j])
            if _OPTS.get("plain_tables"):
                # 첫 행을 제목 행으로 꾸미지 않고, 셀 안의 이탤릭도 쓰지 않는다.
                table = re.sub(r"</?i>", "", table).replace(
                    'class="orig-table"', 'class="orig-table no-head"'
                )
            pieces.append((True, table))
            i = j
            continue
        stripped = ln.strip()
        i += 1
        if not stripped:
            continue
        img = _IMG_RE.fullmatch(stripped)
        if img:
            pieces.append((True, f'<div class="tm-imgwrap"><img class="tm-img" src="/api/test_method_image/{img.group(1)}" alt="그림"></div>'))
            continue
        eq_only = _EQ_RE.fullmatch(stripped)
        if eq_only:
            pieces.append((True, f'<div class="tm-eq">{_EQ_HTML[int(eq_only.group(1))]}</div>'))
            continue
        rendered = _render_text_line_html(stripped)
        rendered = _EQ_RE.sub(lambda m: f'<span class="tm-eq-inline">{_EQ_HTML[int(m.group(1))]}</span>', rendered)
        rendered = _IMG_RE.sub("", rendered)
        pieces.append((False, rendered))
    out = []
    prev_block = True
    for is_block, piece in pieces:
        if out and not is_block and not prev_block:
            out.append("<br>")
        out.append(piece)
        prev_block = is_block
    return "".join(out)


def _text_html(text: str) -> str:
    return _render_body(text.strip())


class _Node:
    def __init__(self, depth, marker, rest):
        self.depth = depth
        self.marker = marker
        self.rest = rest
        self.items = []  # 본문(str)과 하위 _Node 가 나온 순서대로 섞여 있다


def _build_tree(paragraphs):
    """[(text)] -> 최상위 항목 리스트(루트 아래 _Node들). 기호 앞의 본문은 root.items."""
    root = _Node(0, "", "")
    stack = [root]
    for text in paragraphs:
        mk = _marker_of(text)
        if mk is None:
            if _plain(text).strip() or _IMG_MARK in text or _EQ_MARK in text:
                stack[-1].items.append(text)
            continue
        depth, marker, rest = mk
        while stack[-1].depth >= depth:
            stack.pop()
        node = _Node(depth, marker, rest)
        stack[-1].items.append(node)
        stack.append(node)
    return root


_SHORT_HEADING_MAX = 40


def _render_node(node: _Node) -> str:
    rest_plain = _plain(node.rest).strip()
    is_heading = (
        len(rest_plain) <= _SHORT_HEADING_MAX
        and "\n" not in node.rest.strip()
        and "ppm" not in rest_plain
        and not rest_plain.endswith((".", "다", "이하"))
    )
    head = f'<span class="tm-mk">{html.escape(node.marker)}</span> '
    title, body = _split_title_body(node.rest)
    if body and len(_plain(title).strip()) <= 12:
        # "1) 묽은에탄올엑스  따로 규정이 없는 한 ..." 처럼 소제목과 본문이 한 문단에 붙은 경우
        head += f"<strong>{_text_html(title)}</strong> {_text_html(body)}"
    elif rest_plain:
        head += _text_html(node.rest) if not is_heading else f"<strong>{_text_html(node.rest)}</strong>"
    inner = _render_items(node.items)
    return (
        f'<div class="tm-node tm-d{node.depth}">'
        f'<div class="tm-head">{head}</div>{inner}</div>'
    )


def _render_items(items) -> str:
    out = []
    prev_body = None  # 바로 앞 항목이 본문(str)이면 그 원문
    for it in items:
        if isinstance(it, _Node):
            out.append(_render_node(it))
            prev_body = None
            continue
        plain = _plain(it).strip()
        if _OPTS.get("merge_eq") and prev_body is not None and plain.startswith("=") and _EQ_MARK in it:
            # "검체 중 총 아플라톡신(...)의 양" 줄과 "= (수식)" 줄을 한 줄로 합쳐 가운데 정렬한다.
            out[-1] = f'<div class="tm-eq">{_render_body(prev_body)} {_render_body(it)}</div>'
            prev_body = None
            continue
        out.append(f'<div class="tm-body">{_render_body(it)}</div>')
        prev_body = it
    return "".join(out)


def _split_title_body(rest: str):
    """"이물  따로 규정이 없는 한 ..." 처럼 제목과 본문이 두 칸 이상 공백으로 한 문단에
    붙어 있으면 둘로 가른다."""
    m = re.search(r"\s{2,}", _plain(rest).strip())
    if not m:
        return rest.strip(), ""
    stripped = rest.strip()
    # 마커 문자를 건너뛰며 같은 위치를 찾는다.
    count, i = 0, 0
    while i < len(stripped) and count < m.start():
        if stripped[i] not in _MARKER_CHARS and stripped[i] != _IMG_MARK:
            count += 1
        i += 1
    return stripped[:i], stripped[i:].lstrip()


_LABEL_ONLY = {
    "loss": re.compile(r"^\s*건\s*조\s*감\s*량\s*"),
    "ash": re.compile(r"^\s*회\s*분\s*"),
    "acid_ash": re.compile(r"^\s*산\s*불\s*용\s*성\s*회\s*분\s*"),
    "extract": re.compile(r"^\s*엑\s*스\s*함\s*량\s*"),
    "oil": re.compile(r"^\s*정\s*유\s*함\s*량\s*"),
}
_LABEL_TITLE = {
    "loss": "건조감량",
    "ash": "회분",
    "acid_ash": "산불용성회분",
    "extract": "엑스함량",
    "oil": "정유함량",
}

# 순도시험 아래 "가. 이물 ~ 마. 곰팡이독소"의 제목(공백 정리 후)
_PURITY_KEYS = {
    "ibmul": "이물",
    "heavy": "중금속",
    "pesticide": "잔류농약",
    "so2": "이산화황",
    "mycotoxin": "곰팡이독소",
}


def parse_test_methods(path):
    """{키: {"title": str, "html": str}} 를 돌려준다. 못 읽으면 빈 dict."""
    with zipfile.ZipFile(path) as z:
        header = z.read("Contents/header.xml")
        root = ET.fromstring(z.read("Contents/section0.xml"))
    _EQ_HTML.clear()
    bold_ids = _detect_bold_charpr_ids(header)
    sub_ids = _detect_subscript_charpr_ids(header)
    italic_ids = _detect_italic_charpr_ids(header)

    texts = [
        _paragraph_text(p, bold_ids, sub_ids, italic_ids) for p in root.findall("hp:p", NS)
    ]
    plains = [_plain(t).strip() for t in texts]

    # "순도시험" 제목 문단 이후의 가./나./... 최상위 항목 위치를 찾는다.
    purity_at = next(i for i, p in enumerate(plains) if p == "순도시험")
    heads = []  # [(index, 제목)]
    for i in range(purity_at + 1, len(texts)):
        mk = _marker_of(texts[i])
        if mk and mk[0] == 1:
            title = _split_title_body(mk[2])[0]
            heads.append((i, re.sub(r"\s+", "", _plain(title))))
    # 순도시험 항목 구간은 "바. 벤조피렌"처럼 이후 항목 시작 전까지다.

    out = {}
    for key, name in _PURITY_KEYS.items():
        pos = next((n for n, (_, t) in enumerate(heads) if t.startswith(name)), None)
        if pos is None:
            continue
        start = heads[pos][0]
        end = heads[pos + 1][0] if pos + 1 < len(heads) else len(texts)
        # 이 항목 뒤에 나오는 비(非)순도시험 문단(건조감량 등)이 구간에 섞이지 않게,
        # 다음 최상위 항목이 없으면 건조감량 문단 앞에서 끊는다.
        loss_at = next((i for i in range(start, len(texts)) if _LABEL_ONLY["loss"].match(plains[i])
                        and len(plains[i]) > 20 and not _marker_of(texts[i])), len(texts))
        end = min(end, loss_at)
        mk = _marker_of(texts[start])
        title_part, body_part = _split_title_body(mk[2])
        paragraphs = ([body_part] if _plain(body_part).strip() else []) + texts[start + 1 : end]
        tree = _build_tree(paragraphs)
        _OPTS.clear()
        if key == "mycotoxin":
            _OPTS.update(merge_eq=True, plain_tables=True)
        out[key] = {
            "title": f"{mk[1]} {_plain(title_part).strip()}",
            "html": _render_items(tree.items),
        }
        _OPTS.clear()

    # 건조감량/회분/산불용성회분: 문단 하나에 제목과 본문이 이어 붙어 있다.
    def is_label_para(i):
        return any(r.match(plains[i]) and len(plains[i]) > 12 for r in _LABEL_ONLY.values())

    for key, rx in _LABEL_ONLY.items():
        idx = next(
            (i for i in range(purity_at, len(texts)) if rx.match(plains[i]) and len(plains[i]) > 12),
            None,
        )
        if idx is None:
            continue
        body = _plain_label_strip(texts[idx], rx)
        # 같은 항목에 이어지는 문단(번호 항목/그림/표 등)은 다음 항목 제목 전까지.
        # 다음 항목은 다른 시험 제목 문단이거나 "사. 색소"처럼 가./나. 최상위 항목이다.
        extra = []
        j = idx + 1
        while j < len(texts):
            if is_label_para(j):
                break
            mk = _marker_of(texts[j])
            if mk and mk[0] == 1:
                break
            extra.append(texts[j])
            j += 1
        tree = _build_tree([body] + extra)
        out[key] = {"title": _LABEL_TITLE[key], "html": _render_items(tree.items)}
    return out


def _plain_label_strip(text: str, rx) -> str:
    """원문(마커 포함)에서 맨 앞 제목 글자만 떼어낸다."""
    plain = _plain(text)
    m = rx.match(plain)
    if not m:
        return text
    consumed, i = 0, 0
    while consumed < m.end() and i < len(text):
        if text[i] not in _MARKER_CHARS and text[i] != _IMG_MARK:
            consumed += 1
        i += 1
    return text[i:]


def read_test_method_image(path, name):
    """BinData 그림을 (바이트, MIME)으로 돌려준다. bmp는 PNG로 바꿔 보낸다."""
    import io

    with zipfile.ZipFile(path) as z:
        match = next((n for n in z.namelist() if re.fullmatch(rf"BinData/{re.escape(name)}\.\w+", n)), None)
        if match is None:
            return None
        data = z.read(match)
    ext = match.rsplit(".", 1)[-1].lower()
    if ext in ("jpg", "jpeg"):
        return data, "image/jpeg"
    if ext == "png":
        return data, "image/png"
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img.thumbnail((1400, 1400))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue(), "image/png"


if __name__ == "__main__":
    import sys

    result = parse_test_methods(sys.argv[1] if len(sys.argv) > 1 else "생약시험법.hwpx")
    for k, v in result.items():
        print(k, v["title"], len(v["html"]))
