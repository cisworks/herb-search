# -*- coding: utf-8 -*-
"""
hwpx(한글 hwpx) 형식의 생약(한약)/의약품 규격 문서를 파싱해서
품목별 구조화 데이터(리스트[dict])로 변환하는 모듈.

문서 하나(section*.xml)는 아래 패턴이 반복되는 구조로 되어 있다고 가정한다.
  1) 품명 줄들 (국문명(한자), 영문명, 라틴 생약명 등 - 문서마다 줄 수/순서가 다름)
  2) 기원/정의 문단 ("이 약은 ~ 이다." 등)
  3) 굵게 강조된 "항목명"(성상, 확인시험, 순도시험, 건조감량, 회분,
     산불용성회분, 엑스함량, 정량법, 저장법 등)과 그 본문이 반복

실제로 식약처에서 배포되는 hwpx 파일들을 열어 보면 이 굵은 "항목명"에 쓰이는
스타일 ID(charStyleIDRef)가 파일마다 다르고, 품명 줄을 구분하는 색인(indexmark)
사용 방식도 문서마다 제각각이다(품목당 정확히 2개씩 짝지어지는 문서가 있는가
하면, 학명·과명까지 전부 색인이 걸려 있어 품목당 5~10개씩 나오는 문서도 있다).
그래서 이 모듈은 두 가지 방법으로 구조를 "그 문서 안에서" 스스로 찾아낸다.

  - 항목명 스타일: header.xml 의 문자 스타일 카탈로그에서 이름이 "항목명"
    (또는 "* 항목명")인 스타일을 찾아 그 id를 항목 제목 스타일로 사용한다.
    (스타일 이름은 문서를 만든 한글 서식 파일이 공통으로 쓰는 이름이라
    번호(id)보다 훨씬 안정적이다.)
  - 품명 줄: 스타일에 의존하지 않고, "문장이 아니라 이름처럼 생긴 아주 짧은
    문단"인지를 텍스트 패턴으로 판단한다 (국문/한자 이름, 라틴 학명 등).
    문장은 보통 길고 '~다/~다.' 로 끝나므로 이 규칙으로 충분히 구분된다.
"""

import re
import sys
import json
import html
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
}

DEFAULT_HEADER_STYLE_IDS = {"23"}  # header.xml 조회가 실패했을 때의 대비값

_HANGUL_RE = re.compile(r"[가-힣]")
# "2)", "가)" 처럼 순수 번호/가나다 표시가 우연히 항목명과 같은 스타일을
# 쓰는 문서가 있다(예: 대황 정량법의 "2)"). 이런 순수 마커는 스타일만으로
# 헤더로 인정하지 않는다.
_BARE_MARKER_RE = re.compile(r"^\d{1,2}\)\s*$|^[가나다라마바사아자차카타파하]\)\s*$")
_HANJA_RANGE = r"一-鿿㐀-䶿豈-﫿\U00020000-\U0002fa1f"
_NUM_PREFIX_RE = re.compile(r"^\d+\.\s*")
_HANJA_ONLY_RE = re.compile(rf"^\([{_HANJA_RANGE}]{{1,20}}\)[,]?$")
_KOREAN_TITLE_RE = re.compile(
    rf"^[가-힣][가-힣{_HANJA_RANGE}A-Za-z0-9·‧․,/()\-\s]{{0,49}}$"
)
_LATIN_TITLE_RE = re.compile(r"^[A-Z][A-Za-z .\-]{1,79}$")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


# hwpx 수식 스크립트의 아래첨자("_{...}")를 최종 HTML에서 <sub> 로 표시하기
# 위한 마커. 일반 텍스트에는 나오지 않는 제어문자를 써서, 뒤에 이어지는
# 항목/문단 분리 정규식들(콜론·이중공백·번호 등 판정)과 절대 겹치지 않게 한다.
_EQ_SUB_L, _EQ_SUB_R = "\x02", "\x03"

# 원문에서 <hh:italic/> 서식이 지정된 run(주로 라틴 학명)을 최종 HTML에서
# <i> 로 표시하기 위한 마커. 아래첨자 마커와 겹치지 않는 별도의 제어문자를 쓴다.
_ITALIC_L, _ITALIC_R = "\x04", "\x05"

# 표 셀 하나 안에 문단이 여러 개 있는 경우(예: 녹용절편 PCR 조건표에서 한
# 칸에 "변성"/"결합"/"증폭"을 세 줄로 적은 경우)의 줄바꿈 표시. 문서 전체의
# 줄 구분자인 "\n"과 겹치면 그 표 줄 전체가 별개의 줄로 쪼개져 버리므로,
# 셀 안에서만 쓰는 별도의 제어문자를 쓰고 표를 <table>로 그릴 때 <br>로
# 바꾼다.
_CELL_LINE_BREAK = "\x06"

# 표의 병합 칸 표시: 위 칸에 합쳐진 칸(세로 병합), 왼쪽 칸에 합쳐진 칸(가로 병합).
# 정규식의 \s 에 걸리지 않고 strip() 되지 않는 제어문자를 쓴다.
_MERGED_UP, _MERGED_LEFT = "\x17", "\x18"

# 수식의 분수/윗첨자/막대(bar)/제곱근을 나타내는 마커. 파이썬 정규식의 \s 에 걸리지 않는
# 제어문자만 골랐다(\x1c~\x1f 와 \x0b, \x0c 는 공백으로 취급되므로 쓰지 않는다).
#   분수  : \x0e 분자 \x0f 분모 \x10   (분자/분모 안에 다시 분수가 올 수 있다)
#   윗첨자: \x11 ... \x12   막대: \x13 ... \x14   제곱근: \x15 ... \x16
_FR_L, _FR_M, _FR_R = "\x0e", "\x0f", "\x10"
_SUP_L, _SUP_R = "\x11", "\x12"
_BAR_L, _BAR_R = "\x13", "\x14"
_SQRT_L, _SQRT_R = "\x15", "\x16"
_EQ_STRUCT_CHARS = _FR_L + _FR_M + _FR_R + _SUP_L + _SUP_R + _BAR_L + _BAR_R + _SQRT_L + _SQRT_R

_MARKUP_CHARS = _EQ_SUB_L + _EQ_SUB_R + _ITALIC_L + _ITALIC_R + _EQ_STRUCT_CHARS


def _strip_markup_with_map(s: str):
    """아래첨자/이탤릭 마커 제어문자를 뺀 문자열과, 그 결과 문자열의 각
    인덱스가 원본 문자열의 몇 번째 인덱스였는지 매핑을 함께 반환한다.

    "이 약은"/"현미경" 같은 문단 구조 판정용 정규식은 "Glycyrrhiza
    korshinskyi Grig. 이 약은 ~"처럼 학명이 이탤릭 마커로 감싸인 줄에서
    ^로 시작하는 매칭에 실패해 하위 항목으로 못 쪼개지는 문제가 있었다.
    이 함수로 마커를 뺀 문자열에 대고 매칭한 다음, 매칭 위치를 원본
    문자열 위치로 되돌려 써야(마커를 그대로 남겨 이탤릭 표시가 유지된
    채로) 두 문제를 동시에 해결한다."""
    stripped_chars = []
    index_map = []
    for i, ch in enumerate(s):
        if ch in _MARKUP_CHARS:
            continue
        stripped_chars.append(ch)
        index_map.append(i)
    index_map.append(len(s))
    return "".join(stripped_chars), index_map


_EQ_FONT_WORDS = {"rm", "it", "bold", "sf", "tt"}  # 글꼴 지정 키워드(화면에 나오지 않음)
_EQ_SYMBOLS = {  # 그리스 문자/기호 키워드
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "zeta": "ζ",
    "SUM": "∑", "sum": "∑", "times": "×", "TIMES": "×", "cdot": "·", "pm": "±",
    "leq": "≤", "geq": "≥", "LEQ": "≤", "GEQ": "≥",
    # "ALPHA"는 그리스 대문자 알파(Α)인데 이 서체에서는 라틴 "A"와 똑같이 보이므로
    # 원문 의도대로 "A"로 쓴다(그대로 두면 "AT" 앞에 글자 그대로 "ALPHA"가 붙어 나온다).
    "ALPHA": "A",
}
_EQ_WORD_RE = re.compile(r"[A-Za-z]+")
_EQ_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_EQ_HANGUL_RE = re.compile(r"[가-힣]+")


class _EquationParser:
    """한글(HWP) 수식 스크립트를 마커가 섞인 텍스트로 바꾸는 재귀 하강 파서.

    지원: "{분자} over {분모}"(분수, 중첩 가능), 아래/윗첨자("_", "^"), TIMES/times,
    LEFT/RIGHT 괄호, bar(위 막대), sqrt(제곱근), SUM, 그리스 문자, rm/it 같은 글꼴
    키워드(무시), eqalign{... # ...}(줄바꿈 "#"은 공백), "~"/"`"(간격 -> 공백).
    "itY"/"rmS"처럼 글꼴 키워드가 글자에 붙어 있는 것도 떼어 낸다.
    """

    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    # -- 도우미 --
    def _skip_gap(self):
        while self.i < self.n and self.s[self.i] in " \t\r\n~`#":
            self.i += 1

    @staticmethod
    def _collapse(pieces) -> str:
        return re.sub(r" {2,}", " ", "".join(pieces)).strip()

    def _attach_postfix(self, out):
        """방금 만든 조각 뒤에 이어지는 "_x"/"^x" 를 그 조각에 붙인다."""
        while True:
            j = self.i
            while j < self.n and self.s[j] in " \t\r\n":
                j += 1
            if j >= self.n or self.s[j] not in "_^":
                return
            kind = self.s[j]
            self.i = j + 1
            operand = re.sub(r"\s+", "", self.parse_operand())
            while out and out[-1].strip() == "":
                out.pop()
            if not operand:
                continue
            if not out:
                out.append("")
            left, right = (_EQ_SUB_L, _EQ_SUB_R) if kind == "_" else (_SUP_L, _SUP_R)
            out[-1] += left + operand + right

    # -- 피연산자 --
    def parse_operand(self) -> str:
        """다음 피연산자 하나(묶음 {…}, 단어, 숫자, 공백 없는 덩어리)를 읽는다."""
        self._skip_gap()
        if self.i >= self.n:
            return ""
        c = self.s[self.i]
        if c == "}":  # 닫는 중괄호는 바깥 묶음의 끝이므로 먹지 않는다
            return ""
        if c == "{":
            self.i += 1
            return self.parse_seq(True)
        m = _EQ_WORD_RE.match(self.s, self.i)
        if m:
            word = m.group()
            if word in _EQ_FONT_WORDS:
                self.i = m.end()
                return self.parse_operand()
            if word in ("bar", "sqrt", "eqalign", "over", "LEFT", "RIGHT"):
                # 단독 피연산자 자리에서도 같은 규칙으로 해석하도록 한 항목만 읽는다.
                out = []
                self._parse_one(out)
                return "".join(out)
            self.i = m.end()
            return self._strip_font_prefix(word)
        m = _EQ_NUM_RE.match(self.s, self.i)
        if m:
            self.i = m.end()
            return m.group()
        j = self.i
        while j < self.n and self.s[j] not in " \t\r\n~`{}_^#":
            j += 1
        j = max(j, self.i + 1)
        token = self.s[self.i : j]
        self.i = j
        return token

    @staticmethod
    def _strip_font_prefix(word: str) -> str:
        while len(word) > 2 and word[:2] in ("rm", "it") and word not in _EQ_SYMBOLS:
            word = word[2:]
        return word

    # -- 한 항목 --
    def _parse_one(self, out):
        """현재 위치의 항목 하나를 읽어 out 에 붙인다. 읽었으면 True."""
        s, c = self.s, self.s[self.i]
        if c in " \t\r\n~`#":
            out.append(" ")
            self.i += 1
            return True
        if c == "{":
            self.i += 1
            out.append(self.parse_seq(True))
            return True
        if c in "_^":
            self._attach_postfix(out)
            return True
        m = _EQ_WORD_RE.match(s, self.i)
        if m:
            word = m.group()
            self.i = m.end()
            if word == "over":
                while out and out[-1].strip() == "":
                    out.pop()
                numerator = out.pop() if out else ""
                sub_out = []
                den = self.parse_operand()
                sub_out.append(den)
                self._attach_postfix(sub_out)
                out.append(_FR_L + numerator.strip() + _FR_M + "".join(sub_out).strip() + _FR_R)
            elif word in ("LEFT", "RIGHT"):
                self._skip_gap()
                if self.i < self.n and s[self.i] in "([{|)]}":
                    ch = s[self.i]
                    self.i += 1
                    out.append(ch)
                elif self.i < self.n and s[self.i] == ".":
                    self.i += 1
            elif word in _EQ_FONT_WORDS:
                pass
            elif word == "bar":
                out.append(_BAR_L + self.parse_operand() + _BAR_R)
            elif word == "sqrt":
                out.append(_SQRT_L + self.parse_operand() + _SQRT_R)
            elif word == "eqalign":
                out.append(self.parse_operand())
            elif word in _EQ_SYMBOLS:
                out.append(_EQ_SYMBOLS[word])
            else:
                out.append(self._strip_font_prefix(word))
            return True
        m = _EQ_NUM_RE.match(s, self.i) or _EQ_HANGUL_RE.match(s, self.i)
        if m:
            out.append(m.group())
            self.i = m.end()
            return True
        out.append(c)
        self.i += 1
        return True

    def parse_seq(self, in_group: bool) -> str:
        out = []
        while self.i < self.n:
            if self.s[self.i] == "}":
                self.i += 1
                if in_group:
                    break
                continue
            self._parse_one(out)
        return self._collapse(out)


def _convert_equation(script_text: str) -> str:
    """hwpx 수식 스크립트를 마커(아래/윗첨자, 분수, 막대, 제곱근)가 포함된 텍스트로 변환한다.
    예: "{A  _{rm Ta}} over {A  _{rm Sa}}" -> "\\x0eA\\x02Ta\\x03\\x0fA\\x02Sa\\x03\\x10"
    (분수는 나중에 _apply_subscript_markup 이 위아래로 쌓은 분수 HTML 로 바꾼다.)
    """
    return _EquationParser(script_text or "").parse_seq(False)


_FRAC_RE = re.compile(f"{_FR_L}([^{_FR_L}{_FR_M}{_FR_R}]*){_FR_M}([^{_FR_L}{_FR_M}{_FR_R}]*){_FR_R}")
_SUP_RE = re.compile(f"{_SUP_L}([^{_SUP_L}{_SUP_R}]*){_SUP_R}")
_BAR_RE = re.compile(f"{_BAR_L}([^{_BAR_L}{_BAR_R}]*){_BAR_R}")
_SQRT_RE = re.compile(f"{_SQRT_L}([^{_SQRT_L}{_SQRT_R}]*){_SQRT_R}")


def _apply_equation_markup(s: str) -> str:
    """분수/윗첨자/막대/제곱근 마커를 HTML 로 바꾼다. 안쪽(가장 깊이 중첩된) 것부터 차례로
    바꾸므로 분수 안의 분수도 처리된다."""
    prev = None
    while prev != s:
        prev = s
        s = _FRAC_RE.sub(
            lambda m: f'<span class="eq-frac"><span class="eq-num">{m.group(1).strip()}</span>'
            f'<span class="eq-den">{m.group(2).strip()}</span></span>',
            s,
        )
        s = _SUP_RE.sub(r"<sup>\1</sup>", s)
        s = _BAR_RE.sub(r'<span class="eq-bar">\1</span>', s)
        s = _SQRT_RE.sub(r'<span class="eq-sqrt">√<span class="eq-sqrt-in">\1</span></span>', s)
    for ch in _EQ_STRUCT_CHARS:  # 짝이 맞지 않아 남은 마커는 버린다
        s = s.replace(ch, "")
    return s


def _plain_markup(s: str) -> str:
    """검색/본문용 평문으로 돌린다: 수식 마커를 없애고 분수는 "분자/분모", 윗첨자는 "^"로 쓴다."""
    s = s.replace(_FR_M, "/").replace(_SUP_L, "^")
    for ch in _EQ_SUB_L + _EQ_SUB_R + _ITALIC_L + _ITALIC_R + _FR_L + _FR_R + _SUP_R + _BAR_L + _BAR_R + _SQRT_L + _SQRT_R:
        s = s.replace(ch, "")
    return s


def _cell_text(tc_elem, subscript_charpr_ids=frozenset(), italic_charpr_ids=frozenset()):
    """표 셀의 텍스트를 모은다. 셀 안의 run이 아래첨자 서식(charPr)을 쓰면
    그 부분을 나중에 <sub>로 바꿀 수 있도록 마커로 감싼다(예: "벤조피렌-d12"에서
    "12"만 별도 run으로 아래첨자 지정된 경우). 이탤릭 서식(주로 학명)도
    같은 방식으로 마커를 씌운다. 셀 안에 문단(<hp:p>)이 여러 개 있으면(예:
    한 칸에 "변성"/"결합"/"증폭"처럼 줄을 나눠 적은 경우), 그 문단 경계를
    _CELL_LINE_BREAK 로 표시해서 원래 줄바꿈이 살아남게 한다."""
    lines = []
    for p in tc_elem.iter(f"{{{NS['hp']}}}p"):
        parts = []
        for run in p.findall("hp:run", NS):
            char_pr = run.get("charPrIDRef")
            is_sub = char_pr in subscript_charpr_ids
            is_italic = char_pr in italic_charpr_ids
            for t in run.findall("hp:t", NS):
                text = "".join(t.itertext())
                if not text:
                    continue
                if is_sub:
                    text = _EQ_SUB_L + text + _EQ_SUB_R
                if is_italic:
                    text = _ITALIC_L + text + _ITALIC_R
                parts.append(text)
        line = re.sub(r"\s+", " ", "".join(parts)).strip()
        if line:
            lines.append(line)
    return _CELL_LINE_BREAK.join(lines)


def _table_to_text(tbl_elem, subscript_charpr_ids=frozenset(), italic_charpr_ids=frozenset()):
    """<hp:tbl> 표를 "셀 | 셀 | 셀" 형태의 여러 줄 텍스트로 변환한다.
    세로로 병합된(rowSpan>1) 셀은 그 아래 행의 XML에 아예 다시 나오지
    않으므로, 셀 순서만 보고 이어붙이면 그 행의 나머지 값들이 왼쪽으로
    밀려 버린다(예: 벤조피렌 분석 표에서 물질명 셀이 2행에 걸쳐 병합된
    경우). 각 셀의 실제 열 위치(<hp:cellAddr colAddr>)를 읽어 그 자리에
    끼워 넣어야 병합으로 빠진 자리가 빈 칸으로 남고 나머지 값이 제자리를
    지킨다.
    """
    rows_cells = []
    max_cols = 0
    spans = []  # [(행 번호, 열, rowSpan, colSpan)] - 병합 칸을 표시하려고 모아 둔다
    for r_idx, tr in enumerate(tbl_elem.findall("hp:tr", NS)):
        row = []
        for tc in tr.findall("hp:tc", NS):
            addr = tc.find("hp:cellAddr", NS)
            col = int(addr.get("colAddr")) if addr is not None and addr.get("colAddr") else len(row)
            while len(row) <= col:
                row.append("")
            row[col] = _cell_text(tc, subscript_charpr_ids, italic_charpr_ids)
            span = tc.find("hp:cellSpan", NS)
            if span is not None:
                row_span = int(span.get("rowSpan") or 1)
                col_span = int(span.get("colSpan") or 1)
                if row_span > 1 or col_span > 1:
                    spans.append((r_idx, col, row_span, col_span))
        rows_cells.append(row)
        max_cols = max(max_cols, len(row))

    # 병합으로 가려진 칸은 빈 칸이 아니라 "병합됨" 표시(_MERGED_UP/_MERGED_LEFT)로 채워서,
    # 표를 그릴 때 위/왼쪽 칸을 실제로 합쳐 보여 줄 수 있게 한다.
    for row in rows_cells:
        row.extend([""] * (max_cols - len(row)))
    for r_idx, col, row_span, col_span in spans:
        for dr in range(row_span):
            for dc in range(col_span):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r_idx + dr, col + dc
                if rr < len(rows_cells) and cc < max_cols:
                    rows_cells[rr][cc] = _MERGED_UP if dc == 0 else _MERGED_LEFT

    rows = []
    for row in rows_cells:
        if any(c.strip() for c in row):
            rows.append(" | ".join(row))
    return "\n".join(rows)


def _paragraph_segments(
    p_elem,
    header_style_ids,
    subheader_style_ids=frozenset(),
    bold_charpr_ids=frozenset(),
    subscript_charpr_ids=frozenset(),
    italic_charpr_ids=frozenset(),
):
    """
    <hp:p> 하나를 순서대로 훑어서
    [("header", "성상"), ("text", " "), ("text", "이 약은 ...")] 같은
    (종류, 텍스트) 세그먼트 리스트와, 이 문단에 소항목 스타일
    ("1)", "조작조건" 등)이 하나라도 있었는지를 함께 반환한다.
    소항목 스타일은 새 섹션을 만들지는 않지만(본문에 그대로 이어붙임),
    "짧은 이름 문단"으로 오인되어 새 품목으로 잘못 쪼개지지 않도록
    제목 후보 판정에서는 제외해야 하기 때문이다.
    """
    segments = []
    has_subheader = False
    pending_eq = ""  # 중괄호가 덜 닫힌 채 끝난 수식(문서에서 수식 개체 둘로 쪼개진 경우)
    for run in p_elem.findall("hp:run", NS):
        # 스타일(charStyleIDRef)이 아예 없이 charPrIDRef 직접 서식(굵게)만으로
        # 항목명을 표시하는 문서가 있다("확인시험  1)"처럼 번호가 같은 런에
        # 붙어 있는 경우 등). 이때는 이 run이 실제로 "굵게" 서식인지
        # header.xml 에서 확인한 뒤에만 항목명 어휘 매칭을 시도한다 - 그래야
        # "비중 : 5.17 ～ 5.18" 처럼 그냥 본문에 항목명과 같은 단어가 나올 때
        # 헤더로 오인하지 않는다.
        run_charpr = run.get("charPrIDRef")
        run_is_bold = run_charpr in bold_charpr_ids
        run_is_subscript = run_charpr in subscript_charpr_ids
        run_is_italic = run_charpr in italic_charpr_ids
        for child in run:
            tag = _local(child.tag)
            if tag == "t":
                char_style = child.get("charStyleIDRef")
                text = "".join(child.itertext())
                if not text:
                    continue
                is_header_styled = char_style in header_style_ids
                known_label, known_rest = (None, None)
                if is_header_styled or run_is_bold:
                    known_label, known_rest = _split_known_header(text)
                if known_label is not None:
                    segments.append(("header", known_label))
                    if known_rest.strip():
                        segments.append(("text", known_rest))
                elif is_header_styled and text.strip() and not _BARE_MARKER_RE.match(text.strip()):
                    # 공백 한 칸짜리 런이나 "2)" 같은 순수 번호 마커가 우연히
                    # 항목명과 같은 스타일을 쓰는 경우가 있어, 그런 내용은
                    # 스타일만 보고 헤더로 인정하지 않는다.
                    segments.append(("header", text))
                else:
                    if char_style in subheader_style_ids:
                        has_subheader = True
                    # "AS"/"AIS"/"ASAM"/"ASAMIS" 처럼 "A" 뒤에 아래첨자 서식
                    # (charPr의 <hh:subscript/>)만 지정된 run이 있다 - 원문
                    # 서식 그대로 마커로 감싸서 나중에 <sub>로 렌더링한다.
                    if run_is_subscript:
                        text = _EQ_SUB_L + text + _EQ_SUB_R
                    # 학명 등 이탤릭 서식(charPr의 <hh:italic/>)이 지정된
                    # run도 마찬가지로 마커로 감싸서 나중에 <i>로 렌더링한다.
                    if run_is_italic:
                        text = _ITALIC_L + text + _ITALIC_R
                    segments.append(("text", text))
            elif tag == "equation":
                script = child.find("hp:script", NS)
                if script is not None and script.text:
                    # 치자/현호색처럼 한 수식이 "…{A _{eqalign{rm S#" 와 "it}}}" 두 개체로
                    # 쪼개져 있으면, 중괄호가 다 닫힐 때까지 이어 붙여서 하나로 변환한다.
                    pending_eq += script.text
                    if pending_eq.count("{") > pending_eq.count("}"):
                        continue
                    segments.append(("text", _convert_equation(pending_eq)))
                    pending_eq = ""
            elif tag == "tbl":
                table_text = _table_to_text(child, subscript_charpr_ids, italic_charpr_ids)
                if table_text:
                    segments.append(("text", "\n" + table_text + "\n"))
    if pending_eq:
        segments.append(("text", _convert_equation(pending_eq)))
    return segments, has_subheader


def _strip_number_prefix(text: str) -> str:
    return _NUM_PREFIX_RE.sub("", text.strip())


_UNIT_PAREN_RE = re.compile(r"\((mg|mL|μg|kg|g|ppm|vol%)\)", re.IGNORECASE)
_CHEM_FORMULA_RE = re.compile(r"[A-Z]\d")


def _looks_like_formula_or_measurement(text: str) -> bool:
    """"푸에라린 (C21H20O9) 의 양 (mg)" 같은 화학식/단위 라벨을 걸러낸다."""
    if _CHEM_FORMULA_RE.search(text):
        return True
    if _UNIT_PAREN_RE.search(text):
        return True
    if any(u in text for u in ("%", "℃", "ppm", "피크면적")):
        return True
    if " - " in text or " : " in text or ":" in text:
        # "이동상 A - 메탄올", "검출기 : ..." 같은 "라벨 - 값" 서술형 줄
        return True
    return False


def _looks_like_korean_title(text: str) -> bool:
    t = _strip_number_prefix(text)
    if not t or len(t) > 50:
        return False
    if len(t.split()) > 8:
        return False
    if not _KOREAN_TITLE_RE.match(t):
        return False
    if t.endswith("."):
        return False
    if t[-1] == "다":  # 문장은 거의 항상 "~다/~다." 로 끝나므로 제외
        return False
    if _looks_like_formula_or_measurement(t):
        return False
    if _LIST_MARKER_RE.search(t):
        # "유전자 분리 및 증폭반응 (PCR, ...) 가) 유전자 분리" 처럼 시험절차
        # 중간의 "가)"/"1)" 같은 목록 표시가 우연히 섞여 있으면, 진짜
        # 이름(이명) 줄이 아니라 본문 중간이 잘못 잘려 나온 것이다.
        return False
    return True


def _looks_like_latin_title(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 80:
        return False
    if len(t.split()) > 10:
        return False
    if _HANGUL_RE.search(t):
        return False
    if t.endswith("."):
        return False
    if not _LATIN_TITLE_RE.match(t):
        return False
    return True


def _looks_like_hanja_only(text: str) -> bool:
    return bool(_HANJA_ONLY_RE.match(text.strip()))


# 순도시험 등에서 쓰이는 2단계 목록 표기: "1) 이물", "2) 중금속" (번호순)
# 아래 "가) 납", "나) 비소" (가나다순) 이 하위 항목으로 온다.
# 숫자 표시("1)", "2)")는 반드시 문단(줄) 맨 앞에서만 항목 표시로 인정한다.
# "희석시킨 에탄올(7 → 10)", "혼합액(15 : 2 : 1 : 1)" 처럼 문장 중간의 비율
# 표기 끝자락이 "10)", "1)" 로 끝나 마커처럼 오인되는 것을 막기 위함이다.
# 가나다 표시("가)", "나)")는 "2) 중금속  가) 납" 처럼 숫자 표시와 같은 줄에
# 바로 이어 나오는 경우가 많아 기존처럼 공백 뒤에서도 인정한다.
# "주 1) ~ / 2) ~" 처럼 각주 목록의 번호 줄은 "주"로 시작하기도 한다(녹용절편,
# 오르소시폰가루 등). 그런 줄도 번호 표시로 인정하되, "주"가 실제로 붙어
# 있었는지는 juprefix 그룹으로 따로 구분해 둔다(각주 목록의 시작인지
# 판단하는 데 쓴다 - _parse_numbered_hierarchy 참조).
_LIST_MARKER_RE = re.compile(
    r"(?:^|(?<=\n))(?P<juprefix>주\s*)?(?P<num>\d{1,2})\)\s*"
    r"|(?:^|(?<=\s))(?P<kor>[가나다라마바사아자차카타파하])\)\s*"
)

# "가) 또는 나)의 방법으로 시험할 때..."처럼, 뒤에 나오는 하위 항목을
# 안내 문장 속에서 "언급"만 한 것인데도 가나다 마커로 잘못 인식되는
# 경우가 있다(활석의 "8) 석면" 도입부 등). 실제 하위 항목 표시는 항상
# "가) 납"처럼 ")" 뒤에 공백을 두고 바로 설명이 이어지므로, ")" 뒤에
# 공백 없이 조사가 붙거나("나)의", "다)에서") 다음 단어가 "또는"이면
# 언급으로 보고 걸러낸다.
def _is_spurious_kor_mention(text: str, m: "re.Match") -> bool:
    paren_pos = m.start("kor") + 1
    after = paren_pos + 1
    if after >= len(text) or text[after] not in " \t\n":
        return True
    rest = text[after:].lstrip(" \t")
    return rest.startswith("또는")


def _insert_colon_before_origin(text: str) -> str:
    """"이물  이 약은 ~" 처럼 소제목 뒤에 바로 정의문이 이어지면
    "이물 : 이 약은 ~" 처럼 콜론으로 구분해 준다."""
    m = _VARIETY_LABEL_RE.match(text)
    if m and m.group("label").strip():
        label = m.group("label").strip()
        rest = text[m.end():].strip()
        if rest:
            return f"{label} : {rest}"
    return text


# 순도시험 항목을 굵게 표시할 때, "~ 이 약은/이것은/이 약을/이 약에 ~" 류
# 문장이 시작되는 지점까지만 굵게 하기 위한 트리거. "이 약" 뒤에 어떤
# 조사(은/을/의/에/이 등)가 오든 모두 문장 시작으로 인정한다.
_BOLD_SENTENCE_TRIGGER_RE = re.compile(r"이\s*약[은을의에이]|이것은|본품은|이\s*제제는")
_DOUBLE_SPACE_RE = re.compile(r"\s{2,}")
# "B1", "G2" 같은 화합물 코드 안의 숫자는 건너뛰고, 공백 뒤에 오는(=독립된
# 수치로 시작하는) 숫자만 "수치 시작" 경계로 인정한다.
_LEADING_DIGIT_RE = re.compile(r"(?:^|(?<=\s))\d")
# "~ 한다.", "~ 이다." 처럼 문장이 이미 한 번 끝난 뒤에 나오는 콜론/숫자는
# (예: 중금속 개별정량법처럼 긴 시험절차 중간에 있는 부제목이나 수치) 더 이상
# "라벨" 이 아니라 절차 설명의 일부이므로, 그 앞에서 문장이 끝난 적이 있으면
# 그 콜론/숫자는 분리 기준으로 쓰지 않는다.
_SENTENCE_END_RE = re.compile(r"다\.(?:\s|$)")


def _split_bold_label(text: str):
    """
    항목 텍스트에서 "굵게 표시할 라벨 부분"과 "나머지"를 나눈다.
    - "이물 : 이 약은 ~" -> ("이물 :", "이 약은 ~")
    - "이산화황  30 ppm 이하." -> ("이산화황", "30 ppm 이하.")
    - "중금속" (뒤에 아무 것도 없음) -> ("중금속", "")
    - 긴 개별 시험절차처럼 뚜렷한 "라벨"이 없으면 아무 것도 굵게 하지 않는다.
    """
    idx = text.find(":")
    if idx != -1 and not _SENTENCE_END_RE.search(text[:idx]):
        return text[: idx + 1].strip(), text[idx + 1 :].strip()

    # 원문 서식 자체가 "항목명  본문" 처럼 항목명 뒤에 공백 두 칸 이상으로
    # 구분해 놓는 경우가 많다(예: "곰팡이독소  총 아플라톡신(...) 15.0 ppb").
    # 이 구분이 있으면 그 뒤에 더 짧은 진짜 수치가 나오더라도 이 지점에서
    # 자른다.
    m = _DOUBLE_SPACE_RE.search(text)
    if m and not _SENTENCE_END_RE.search(text[: m.start()]):
        bold, rest = text[: m.start()].strip(), text[m.end() :].strip()
        if bold and rest:
            return bold, rest

    m = _BOLD_SENTENCE_TRIGGER_RE.search(text)
    if m and m.start() <= 70 and not _SENTENCE_END_RE.search(text[: m.start()]):
        return text[: m.start()].strip(), text[m.start() :].strip()

    m = _LEADING_DIGIT_RE.search(text)
    if m and m.start() <= 70 and not _SENTENCE_END_RE.search(text[: m.start()]):
        return text[: m.start()].strip(), text[m.start() :].strip()

    if len(text) <= 30:
        return text.strip(), ""

    return "", text.strip()


def _split_section_label(text: str):
    """
    번호 목록("1)","2)")이 전혀 없는 순도시험/정량법 섹션 전체(예: 자석의
    "중금속  이 약의 가루(미세말) 1.0 g을...")에 대해, 앞부분을 라벨로
    인식할지 판단한다.

    _split_bold_label 은 이미 한 항목으로 잘라져 나온 짧은 텍스트를
    대상으로 하지만, 여기서는 섹션 전체(종종 길고 절차적인 문단)를
    대상으로 하므로 훨씬 엄격하게 판정해야 한다. 그렇지 않으면 "이 약의
    가루 약 1 g을 정밀하게 달아 메탄올‧물혼합액(7 : 3)을..." 같은 정량법
    절차 문장 안의 콜론/숫자를 라벨 경계로 잘못 짚어 문장 전체가 굵게
    처리되는 문제가 생긴다. 따라서 원문 서식이 실제로 "항목명(공백 두
    칸)본문" 형태로 되어 있는, 아주 이른 위치의 공백 두 칸 구분만
    라벨 경계로 인정하고, 그마저도 라벨 부분이 짧은 단어 수준일 때만
    받아들인다.
    """
    m = _DOUBLE_SPACE_RE.search(text)
    if not m or m.start() > 15 or _SENTENCE_END_RE.search(text[: m.start()]):
        return "", text
    bold, rest = text[: m.start()].strip(), text[m.end() :].strip()
    if not bold or not rest:
        return "", text
    if len(bold) > 12 or len(bold.split()) > 2:
        return "", text
    return bold, rest


# "대한민국약전 「두충」의 순도시험 1), 2)에 따른다." 처럼 다른 생약의
# 순도시험 기준을 그대로 따른다고 적힌 문장에서, 「」/｢｣ 안의 생약명을
# 찾아내 그 생약 페이지로 연결되는 링크를 만들기 위한 패턴.
_PURITY_REF_RE = re.compile(r"[「｢]([^」｣]+)[」｣]\s*의\s*순도시험")


def _detect_purity_reference(text: str):
    m = _PURITY_REF_RE.search(text)
    return m.group(1).strip() if m else None


# 녹용절편처럼 "라) 결과 확인 및 판정" 같은 가나다 항목 끝에 "주 1) ~ / 2) ~
# / 3) ~" 처럼 용어를 설명하는 각주 목록이 붙는 문서가 있다. "주"로 시작하는
# 이 각주 번호는 순도시험 전체의 새 최상위 항목이 아니라, 바로 앞의
# 가나다(또는 숫자) 항목의 하위 항목으로 묶여야 한다("주"가 각주 첫 줄에만
# 붙기도 하고("주 1)" 그 다음은 "2)"), 매 줄에 반복되기도 한다("주1)","주2)")
# - 두 경우 모두 지원한다. "주" 표시 자체는 _LIST_MARKER_RE의 juprefix
# 그룹으로 인식한다).


def _parse_numbered_hierarchy(text: str, bold_labels: bool = False):
    """
    "1) 이물 ...  2) 중금속  가) 납 ...  나) 비소 ..." 같은 순도시험류 본문을
    번호(1,2,3..)를 상위 항목으로, 가나다(가,나,다..)를 그 하위 항목으로 하는
    계층 구조로 쪼갠다. 마커를 하나도 못 찾으면 빈 리스트를 반환한다
    (그러면 화면에서는 기존처럼 평문으로 표시된다).
    bold_labels=True 이면 숫자(1,2,3..) 최상위 항목에 한해 "항목명 부분만
    굵게" 표시하기 위한 bold/rest 필드를 채운다 (순도시험 전용). 가나다
    하위 항목은 굵게 처리하지 않는다.
    """
    matches = list(_LIST_MARKER_RE.finditer(text))
    matches = [m for m in matches if not (m.group("kor") and _is_spurious_kor_mention(text, m))]
    if not matches:
        return []

    def make_node(marker, content, bold=False):
        node = {"marker": marker, "text": content, "children": []}
        if bold:
            b, rest = _split_bold_label(content)
            node["bold"] = b
            node["rest"] = rest
        ref_name = _detect_purity_reference(content)
        if ref_name:
            node["ref_name"] = ref_name
        return node

    def make_footnote_node(marker, content):
        label, rest = _split_colon_line(content)
        if label:
            return {"marker": marker, "text": content, "children": [], "bold": label, "rest": rest}
        return {"marker": marker, "text": content, "children": []}

    items = []
    current_l1 = None
    current_l2 = None
    footnote_next = None  # "주" 각주 목록에서 다음에 와야 할 번호. 각주 목록 밖이면 None.
    for idx, m in enumerate(matches):
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        content = _insert_colon_before_origin(text[content_start:content_end].strip())
        if m.group("num"):
            num_val = int(m.group("num"))
            starts_footnote = m.group("juprefix") is not None
            if starts_footnote or num_val == footnote_next:
                footnote_next = num_val + 1
                node = make_footnote_node(f"{m.group('num')})", content)
                parent = current_l2 or current_l1
                if parent is not None:
                    parent["children"].append(node)
                else:
                    items.append(node)
                continue
            footnote_next = None
            node = make_node(f"{m.group('num')})", content, bold=bold_labels)
            items.append(node)
            current_l1 = node
            current_l2 = None
        else:
            footnote_next = None
            node = make_node(f"{m.group('kor')})", content)
            if current_l1 is not None:
                current_l1["children"].append(node)
            else:
                items.append(node)  # "가)" 로 바로 시작하는 예외적인 경우
            current_l2 = node

    lead = text[: matches[0].start()].strip()
    if lead:
        items.insert(0, make_node("", lead))
    return items


# 정량법 끝부분에 거의 항상 붙는 "조작조건"(검출기/칼럼/이동상/유량 등)과
# "시스템적합성"(시스템의 성능/재현성 등) 두 소제목. 원문에서는 번호 없이
# 그냥 독립된 줄로만 나온다. 감초처럼 "1) 성분A ~ 조작조건 ~ 시스템적합성
# 2) 성분B ~ 조작조건 ~ 시스템적합성" 형태로 성분마다 반복되기도 한다.
_QUANT_SUBHEAD_LABELS = ("조작조건", "시스템적합성")


def _split_colon_line(line: str):
    """"검출기 : 자외부흡광광도계 (측정파장 254 nm)" 같은 줄을
    (굵게 표시할 라벨, 나머지) 로 나눈다. 콜론이 없으면 (None, None).
    "이동상 B - ~혼합액(100 : 75 : 1)"처럼 괄호 안의 비율 표기에 쓰인
    콜론은 라벨 구분자가 아니므로, 괄호 밖에 있는 콜론만 찾는다."""
    depth = 0
    idx = -1
    for i, ch in enumerate(line):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            idx = i
            break
    if idx == -1:
        return None, None
    label, rest = line[: idx + 1].strip(), line[idx + 1 :].strip()
    if not label or not rest:
        return None, None
    return label, rest


def _build_opcond_children(body_lines):
    """"조작조건"/"시스템적합성" 아래의 줄들을 콜론이 있는 줄(예: "검출기 : ~")
    단위로 하위 항목화한다. "이동상 : ~" 다음에 오는, 콜론이 없는 줄("이동상
    A - 메탄올" 등)이나 표는 그 바로 위 콜론 항목("이동상")의 하위 항목으로
    한 단계 더 들어간다.
    """
    children = []
    current = None  # 콜론 없는 줄/표를 받아줄, 가장 최근에 만든 콜론 항목
    table_buf = []

    def flush_table():
        if table_buf:
            node = {"marker": "", "text": "\n".join(table_buf), "children": []}
            (current["children"] if current is not None else children).append(node)
            table_buf.clear()

    for ln in body_lines:
        if not ln.strip():
            continue
        if _TABLE_LINE_RE.match(ln):
            table_buf.append(ln)
            continue
        flush_table()
        label, rest = _split_colon_line(ln)
        if label:
            node = {"marker": "", "text": ln, "children": [], "bold": label, "rest": rest}
            children.append(node)
            current = node
        else:
            node = {"marker": "", "text": ln, "children": []}
            (current["children"] if current is not None else children).append(node)
    flush_table()
    return children


# 정량법에서 "○ 내부표준액 ~", "○ 시약 ․시액." 처럼 "○"로 시작하는
# 글머리표. 사향처럼 시약 하나하나에 순도 규격(비선광도/유연물질 등)이
# 딸려 있는 문서에서, 조작조건과 시약 항목들이 서로를 집어삼키지 않도록
# 이것도 조작조건/시스템적합성과 같은 층의 구분자로 인정한다.
_BULLET_MARK_RE = re.compile(r"^○\s*")
_BULLET_LABEL_RE = re.compile(
    r"(내부표준액|내부표준용액|표준원액|표준액|검액|대조액|TMS\s*화제|"
    r"시약\s*[·․,]?\s*시액|시약|시액)"
)


def _split_bullet_label(body: str):
    """"○"를 뗀 나머지("내부표준액 시클로펜타데카논의...")에서 정량법에
    관용적으로 쓰이는 짧은 라벨(내부표준액/시약·시액 등)을 찾아
    ("○ 라벨", 나머지)로 나눈다. 못 찾으면 ("○", 전체)를 그대로 돌려준다."""
    m = _BULLET_LABEL_RE.match(body)
    if not m:
        return "○", body
    rest = re.sub(r"^[\s:：.]+", "", body[m.end() :])
    label = re.sub(r"\s+", "", m.group(1))
    return f"○ {label}", rest.strip()


def _split_reagent_line(line: str):
    """"  l-무스콘, 박층크로마토그래프용  C16H30O  무색 ～ ..." 처럼 "○
    시약·시액" 아래 들여쓰기로 시작하는 시약 항목 한 줄을 (이름(등급 등),
    나머지 설명)으로 나눈다. 이름 뒤에 화학식(예: "C16H30O")이 바로 오면
    그것까지 이름에 포함하고, 화학식이 없으면 "다만," 앞까지, 그것도 없으면
    첫 이중공백 앞까지를 이름으로 본다. 아래첨자/이탤릭 마커가 화학식
    문자 사이에 끼어 있으면 정규식이 못 찾으므로, 마커를 뺀 문자열로 찾은
    다음 원본 문자열 위치로 되돌린다."""
    stripped, idx_map = _strip_markup_with_map(line)
    lead_ws = len(stripped) - len(stripped.lstrip())
    core = stripped[lead_ws:]

    end_in_stripped = None
    m = _FORMULA_TOKEN_RE.search(core)
    if m and m.start() <= 40:
        formula_end = m.end()
        # "C16H30O"의 마지막 "O"처럼, 화학식 끝에 숫자 없이 원소기호 하나가
        # 더 붙는 경우까지 포함한다.
        m_tail = re.match(r"[A-Z][a-z]?(?!\d)", core[formula_end:])
        if m_tail:
            formula_end += m_tail.end()
        end_in_stripped = lead_ws + formula_end
    else:
        idx = core.find("다만,")
        if idx != -1:
            end_in_stripped = lead_ws + idx
        else:
            m2 = _DOUBLE_SPACE_RE.search(core)
            if m2:
                end_in_stripped = lead_ws + m2.start()

    if end_in_stripped is None:
        return line.strip(), ""
    orig_end = idx_map[end_in_stripped]
    label = re.sub(r"\s+", " ", line[:orig_end]).strip()
    rest = line[orig_end:].strip(" .").strip()
    return label, rest


def _build_reagent_children(body_lines):
    """"○ 시약·시액" 아래에서, 들여쓰기로 시작하는 줄마다 새 시약 항목을
    만들고, 그 뒤에 들여쓰기 없이 이어지는 줄들(물성/순도 규격 등)을 그
    시약의 하위 항목으로 묶는다. 그 안에 다시 "조작조건"이 나오면(예:
    사향의 "정량용" 시약이 통과해야 하는 별도 시험법) 그 뒤의 검출기/칼럼
    같은 줄들은 조작조건의 하위 항목으로 넣는다."""
    items = []
    current = None
    current_opcond = None
    for ln in body_lines:
        if not ln.strip():
            continue
        indented = _strip_markup_with_map(ln)[0].startswith("  ")
        if indented:
            label, rest = _split_reagent_line(ln)
            node = {"marker": "", "text": ln.strip(), "children": [], "bold": label, "rest": rest}
            items.append(node)
            current = node
            current_opcond = None
            continue
        target = current["children"] if current is not None else items
        if ln.strip() == "조작조건":
            opcond_node = {"marker": "", "text": "조작조건", "children": [], "bold": "조작조건", "rest": ""}
            target.append(opcond_node)
            current_opcond = opcond_node
            continue
        label, rest = _split_colon_line(ln)
        if not label:
            label, rest = _split_bold_label(ln)
        node = (
            {"marker": "", "text": ln, "children": [], "bold": label, "rest": rest}
            if label and rest
            else {"marker": "", "text": ln, "children": []}
        )
        (current_opcond["children"] if current_opcond is not None else target).append(node)
    return items


def _split_bullet_sections(lines, bullet_positions):
    """"○ 내부표준액 ~", "○ 시약·시액." 같은 글머리표 단위로 나눈다. 각
    글머리표 구간 안에 다시 "조작조건"/"시스템적합성"이 있으면 그 구간의
    하위 항목으로 한 단계 더 들어가고(예: 사향의 "○ 내부표준액" 아래
    "조작조건"), "시약·시액" 구간은 들여쓰기로 시작하는 시약 항목 단위로
    다시 나눈다."""
    lead = "\n".join(lines[: bullet_positions[0]]).strip("\n")
    items = []
    for idx, pos in enumerate(bullet_positions):
        end = bullet_positions[idx + 1] if idx + 1 < len(bullet_positions) else len(lines)
        body_after_mark = _BULLET_MARK_RE.sub("", lines[pos].strip())
        label, rest = _split_bullet_label(body_after_mark)
        body_lines = lines[pos + 1 : end]

        if "시약" in label or "시액" in label:
            children = _build_reagent_children(body_lines)
        else:
            inner_head_positions = [
                i for i, ln in enumerate(body_lines) if ln.strip() in _QUANT_SUBHEAD_LABELS
            ]
            if inner_head_positions:
                inner_lead = "\n".join(body_lines[: inner_head_positions[0]]).strip("\n")
                children = []
                current_opcond = None
                for jdx, jpos in enumerate(inner_head_positions):
                    jend = (
                        inner_head_positions[jdx + 1]
                        if jdx + 1 < len(inner_head_positions)
                        else len(body_lines)
                    )
                    jlabel = body_lines[jpos].strip()
                    jnode = {
                        "marker": "",
                        "text": jlabel,
                        "children": _build_opcond_children(body_lines[jpos + 1 : jend]),
                        "bold": jlabel,
                        "rest": "",
                    }
                    if jlabel == "조작조건":
                        children.append(jnode)
                        current_opcond = jnode
                    elif current_opcond is not None:
                        current_opcond["children"].append(jnode)
                    else:
                        children.append(jnode)
                if inner_lead:
                    rest = f"{rest} {inner_lead}".strip() if rest else inner_lead
            else:
                children = []
                extra = "\n".join(body_lines).strip()
                if extra:
                    rest = f"{rest} {extra}".strip() if rest else extra

        items.append(
            {
                "marker": "",
                "text": f"{label} {rest}".strip(),
                "children": children,
                "bold": label,
                "rest": rest,
            }
        )
    return lead, items


def _split_opcond_sections(text: str):
    """텍스트 하나(성분 하나 분량)를 "조작조건"/"시스템적합성"(및 "○"
    글머리표가 있으면 그것) 기준으로 (그 앞의 절차 설명, [하위 항목
    리스트]) 로 나눈다. 표시가 전혀 없으면 (text, []) 를 그대로 돌려준다.
    "시스템적합성"은 "조작조건"과 같은 층이 아니라 그 바로 아래 하위
    항목으로 들어간다."""
    lines = text.split("\n")
    bullet_positions = [i for i, ln in enumerate(lines) if _BULLET_MARK_RE.match(ln.strip())]
    if bullet_positions:
        return _split_bullet_sections(lines, bullet_positions)

    head_positions = [i for i, ln in enumerate(lines) if ln.strip() in _QUANT_SUBHEAD_LABELS]
    if not head_positions:
        return text, []

    lead = "\n".join(lines[: head_positions[0]]).strip("\n")
    sub_items = []
    current_opcond = None
    for idx, pos in enumerate(head_positions):
        label = lines[pos].strip()
        end = head_positions[idx + 1] if idx + 1 < len(head_positions) else len(lines)
        node = {
            "marker": "",
            "text": label,
            "children": _build_opcond_children(lines[pos + 1 : end]),
            "bold": label,
            "rest": "",
        }
        if label == "조작조건":
            sub_items.append(node)
            current_opcond = node
        elif current_opcond is not None:
            # "시스템적합성" 은 조작조건의 하위 항목이다.
            current_opcond["children"].append(node)
        else:
            # 조작조건 없이 시스템적합성만 있는 예외적인 경우 대비.
            sub_items.append(node)
    return lead, sub_items


def _parse_quantitation_hierarchy(text: str):
    """정량법 본문을 계층화한다.
    - "1) 성분A ~ 조작조건 ~ 시스템적합성  2) 성분B ~" 처럼 번호 매긴
      성분이 여러 개면, 번호를 상위 항목으로 하고 그 안에서 각각
      "조작조건"/"시스템적합성"을 다시 하위 항목으로 묶는다.
    - 번호가 아예 없으면(성분이 하나뿐인 경우) "조작조건"/"시스템적합성"을
      바로 상위 항목으로 묶는다.
    - "조작조건"/"시스템적합성" 표시가 전혀 없으면 빈 리스트를 반환한다
      (기존처럼 평문으로 표시됨).
    """
    numbered_items = _parse_numbered_hierarchy(text, bold_labels=True)
    if numbered_items:
        any_opcond = False
        for node in numbered_items:
            lead, sub_items = _split_opcond_sections(node.get("text", ""))
            if not sub_items:
                continue
            any_opcond = True
            node["text"] = lead
            if "bold" in node:
                # 앞부분(lead)만으로 굵게 표시할 라벨을 다시 계산한다 -
                # "글리시리진산  이 약의 가루~" 처럼 이중공백 라벨은 앞쪽에서
                # 바로 잘리므로, 뒤에 조작조건 텍스트가 있든 없든 결과가 같다.
                b, rest = _split_bold_label(lead)
                node["bold"] = b
                node["rest"] = rest
            node["children"] = node.get("children", []) + sub_items
        return numbered_items if any_opcond else []

    lead, sub_items = _split_opcond_sections(text)
    if not sub_items:
        return []
    items = []
    if lead.strip():
        items.append({"marker": "", "text": lead, "children": []})
    items.extend(sub_items)
    return items


# 오매/초과의 벤조피렌 시험처럼 "4) 벤조피렌 ~ (제 1 법) ~ 가) ~ ① ~
# 조작조건 : ~" 순서로 번호 - 시험법 - (가나다/동그라미숫자/조작조건)까지
# 계층이 나뉘는 순도시험 항목이 있다. 이 조합(시험법 표시와 동그라미숫자
# 표시가 함께 나옴)이 있는 문서에서만 아래 계층 파서를 쓰고, 그 외 문서는
# 기존 2단계 파서(_parse_numbered_hierarchy)를 그대로 쓴다. 가나다("가)"),
# 동그라미숫자("①"), "조작조건"은 서로 같은 계층으로 취급한다 - 원문에서
# "가) 검액 조제 / ① 추출 / ② 정제 / 나) 표준액 조제 / ... / 라) 시험조작 /
# ① ~ / 조작조건 / ② 정성시험 / ③ 정량시험" 처럼 한 시험법 안에서 나란히
# 이어지는 절차 표시이지, "가)" 아래에 "①"이 종속되는 구조가 아니기 때문이다.
_BEOPN_RE = re.compile(r"\(제\s*\d{1,2}\s*법\)")
_CIRCLED_RE = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")
_DEEP_MARKER_RE = re.compile(
    r"(?:^|(?<=\n))(?P<num>\d{1,2})\)\s*"
    r"|(?:^|(?<=\n))\(제\s*(?P<beopn>\d{1,2})\s*법\)\s*"
    r"|(?:^|(?<=\s))(?P<kor>[가나다라마바사아자차카타파하])\)\s*"
    r"|(?:^|(?<=\n))(?P<circled>[①②③④⑤⑥⑦⑧⑨⑩])\s*"
    r"|(?:^|(?<=\n))(?P<opcond>조작조건|시스템적합성)(?=\s*(?:\n|$))"
)
_DEEP_MARKER_RANK = {"num": 0, "beopn": 1, "kor": 2, "circled": 2, "opcond": 2}


def _has_deep_markers(text: str) -> bool:
    return bool(_BEOPN_RE.search(text)) and bool(_CIRCLED_RE.search(text))


def _parse_deep_numbered_hierarchy(text: str):
    """번호/시험법/가나다/동그라미숫자/조작조건 표시를 만나는 순서대로
    훑으면서, 각 표시를 그보다 앞서 나온 "더 얕은"(랭크가 작은) 표시의
    자식으로 붙여 나간다. "(제 1 법) 또는 (제 2 법)에 따라 시험한다." 처럼
    한 줄에 시험법 표시가 두 번 나오는 안내 문장은 실제 항목 구분이 아니므로
    표시로 인정하지 않는다.
    """
    raw_matches = []  # [(start, kind, marker_text, end), ...]
    for m in _DEEP_MARKER_RE.finditer(text):
        if m.group("num"):
            raw_matches.append((m.start(), "num", f"{m.group('num')})", m.end()))
        elif m.group("beopn"):
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            if _BEOPN_RE.search(text[m.end():line_end]):
                continue  # "(제 1 법) 또는 (제 2 법)..." 같은 안내 문장
            raw_matches.append((m.start(), "beopn", f"(제 {m.group('beopn')} 법)", m.end()))
        elif m.group("kor"):
            if _is_spurious_kor_mention(text, m):
                continue
            raw_matches.append((m.start(), "kor", f"{m.group('kor')})", m.end()))
        elif m.group("circled"):
            raw_matches.append((m.start(), "circled", m.group("circled"), m.end()))
        elif m.group("opcond"):
            raw_matches.append((m.start(), "opcond", m.group("opcond"), m.end()))

    if not raw_matches:
        return []

    def make_node(kind, marker, content, bold=False):
        if kind == "opcond":
            children = _build_opcond_children(content.split("\n"))
            return {"marker": "", "text": marker, "children": children, "bold": marker, "rest": ""}
        content = _insert_colon_before_origin(content)
        node = {"marker": marker, "text": content, "children": []}
        if bold:
            b, rest = _split_bold_label(content)
            node["bold"] = b
            node["rest"] = rest
        ref_name = _detect_purity_reference(content)
        if ref_name:
            node["ref_name"] = ref_name
        return node

    items = []
    stack = []  # [(rank, node), ...]
    for idx, (start, kind, marker, end) in enumerate(raw_matches):
        content_end = raw_matches[idx + 1][0] if idx + 1 < len(raw_matches) else len(text)
        content = text[end:content_end].strip()
        rank = _DEEP_MARKER_RANK[kind]
        node = make_node(kind, marker, content, bold=(kind == "num"))
        while stack and stack[-1][0] >= rank:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            items.append(node)
        stack.append((rank, node))

    lead = text[: raw_matches[0][0]].strip()
    if lead:
        items.insert(0, make_node("plain", "", lead))
    return items


# 성상 본문에서 "가자  이 약은 ...", "융모가자  이 약은 ..." 처럼
# 생약명/식물명이 "이 약은"(또는 "이것은") 문장 앞에 붙어 변종·이명을
# 구분하는 경우를 찾아내기 위한 패턴.
_VARIETY_LABEL_RE = re.compile(
    r"^(?P<label>[가-힣][가-힣" + _HANJA_RANGE + r"()（）·,、및\s]{0,40}?"
    r"|[A-Z][A-Za-z .]{1,40}?)\s*(?=이\s*약은|이것은)"
)
_ORIGIN_SENTENCE_RE = re.compile(r"^(이\s*약은|이것은|이\s*약의)")

# "...약간 함몰되어 있다. 이 약의 횡단면을 현미경으로 볼 때 ~",
# "...확대경으로 볼 때 ~" 처럼 이 단어들이 나오는 문장이 앞 문장과 같은
# 문단(줄)에 붙어 있는 경우, 그 직전 문장이 끝나는 "다. " 지점을 찾아
# 별도 줄로 떼어낸다.
_MICROSCOPE_TRIGGERS = ("현미경", "확대경")
_SENTENCE_END_SPLIT_RE = re.compile(r"다\.\s*")


def _split_before_microscope(line: str):
    """
    문단을 쪼개서 [(텍스트, 강제로_새_블록인지), ...] 를 반환한다.
    "확대경"/"현미경" 이 줄 맨 앞이 아니라 "횡단면을 확대경으로 볼 때" 처럼
    문장 중간에서 시작하더라도, 떼어낸 조각은 항상 새 블록으로 표시해서
    뒤에서 "이 약은/현미경/확대경으로 시작하지 않으니 이어붙인다" 는
    판정에 걸려 다시 합쳐지지 않도록 한다.
    한 줄에 트리거가 여러 번 나올 때, 앞쪽 등장이 "~다. " 로 끝나는
    문장 경계를 찾지 못해도 포기하지 않고 그 다음 등장을 계속 찾는다
    (예: "...확대경으로 보면 ~있다. 질은 ~않는다. 이 약의 ~현미경으로 ~"
    처럼 분리 기준이 없는 등장 뒤에 분리 가능한 등장이 또 있는 경우).
    """
    pieces = []
    text = line
    search_from = 0
    made_split = False
    while True:
        positions = [p for p in (text.find(t, search_from) for t in _MICROSCOPE_TRIGGERS) if p != -1]
        if not positions:
            break
        idx = min(positions)
        if idx == 0:
            break  # 이미 줄 맨 앞부터 시작하는 문장이라 더 나눌 필요 없음
        prefix = text[:idx]
        split_pos = None
        for m in _SENTENCE_END_SPLIT_RE.finditer(prefix):
            split_pos = m.end()
        if split_pos is None:
            search_from = idx + 1  # 이 등장은 분리 기준이 없음 - 다음 등장을 계속 찾는다
            continue
        before, after = text[:split_pos].rstrip(), text[split_pos:].lstrip()
        if not before or not after:
            search_from = idx + 1
            continue
        pieces.append((before, made_split))
        made_split = True
        text = after
        search_from = 0
    pieces.append((text, made_split))
    return pieces


def _parse_seongsang_blocks(text: str):
    """
    성상 본문을 "이 약은/이것은/이 약의" 문장 단위로 쪼갠다. 그 문장 앞에
    생약명·식물명(이명/변종명)이 붙어 있으면 그 이름을 표시로 삼아
    하위 항목으로 구분하고, 이름이 없으면 표시 없는 문단으로 취급한다.
    "현미경으로 볼 때 ~" 문장이 앞 문장과 한 줄에 붙어 있으면 미리 떼어내
    별도 문단으로 만든다. 원문 순서를 그대로 유지하므로, 두 번째 이후에
    나오는 "이 약은" 류 문장은 화면에서 자연스럽게 앞 문단과 줄바꿈으로
    분리되어 보인다.
    """
    raw_lines = []
    for raw_line in text.split("\n"):
        raw_lines.extend(_split_before_microscope(raw_line))

    blocks = []
    for raw_line, force_new_block in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        if force_new_block:
            # "확대경"/"현미경" 문장이 문단 중간에서 시작해 떼어낸 조각이므로,
            # "이 약은"으로 시작하지 않더라도 무조건 새 블록으로 취급한다.
            blocks.append({"marker": "", "text": line, "children": []})
            continue
        # 아래첨자/이탤릭 마커가 줄 맨 앞(예: 이탤릭 처리된 학명)에 끼어
        # 있으면 "^"로 시작하는 아래 판정들이 실패하므로, 마커를 뺀
        # 문자열로 판정하고 위치만 원본 문자열 기준으로 되돌린다.
        stripped, idx_map = _strip_markup_with_map(line)
        m = _VARIETY_LABEL_RE.match(stripped)
        if m and m.group("label").strip():
            rest_start = idx_map[m.end()]
            # marker는 stripped가 아니라 원본 line에서 그대로 잘라내
            # 이탤릭 마커(학명 표시)가 남아 있게 한다 - _enrich_item_html이
            # 이를 보고 marker_html을 만들고 marker 자체는 평문으로 정리한다.
            blocks.append({"marker": line[:rest_start].strip(), "text": line[rest_start:].strip(), "children": []})
            continue
        if _ORIGIN_SENTENCE_RE.match(stripped) or stripped.startswith(_MICROSCOPE_TRIGGERS):
            blocks.append({"marker": "", "text": line, "children": []})
            continue
        if blocks:
            blocks[-1]["text"] = (blocks[-1]["text"] + " " + line).strip()
        else:
            blocks.append({"marker": "", "text": line, "children": []})
    return blocks


# 제법 본문에서 "염부자(鹽附子)  6 〜 8월 사이에 ~", "부자편(附子片)  염부자를
# 가지고 ~"처럼 가공법에 따른 이름(한자 포함)이 문단 맨 앞에 붙어 그 문단을
# 구분하는 경우를 찾는다(부자(附子)의 제법). 성상의 _VARIETY_LABEL_RE와
# 달리 "이 약은" 같은 문장 트리거 없이, 라벨 뒤에 공백을 두고 바로 설명이
# 이어지는 형태로 식별한다.
_PROCESS_LABEL_RE = re.compile(
    r"^(?P<label>[가-힣][가-힣0-9]{0,8}\([" + _HANJA_RANGE + r"]{1,10}\))\s+(?=\S)"
)


def _parse_process_blocks(text: str):
    """제법 본문을 염부자(鹽附子)/부자편(附子片)/포부자(炮附子)처럼 가공법
    이름이 붙은 문단 단위로 쪼갠다. 그런 라벨이 둘 이상 없으면(대부분의
    생약은 제법이 라벨 없는 한 문단이다) 빈 리스트를 반환해 기존처럼
    평문으로 표시되게 한다."""
    blocks = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        m = _PROCESS_LABEL_RE.match(line)
        if m:
            blocks.append({"marker": m.group("label"), "text": line[m.end():].strip(), "children": []})
        elif blocks:
            blocks[-1]["text"] = (blocks[-1]["text"] + " " + line).strip()
        else:
            blocks.append({"marker": "", "text": line, "children": []})
    labeled = sum(1 for b in blocks if b["marker"])
    return blocks if labeled >= 2 else []


def _split_name(korean_name: str):
    # "개자(芥子) 겨자, Mustard Seed" 처럼 한자 뒤에 이명이 더 붙는 경우가
    # 있어, 문자열 끝이 아니라 맨 처음 나오는 "이름(한자)" 괄호 쌍만
    # name_only/hanja 로 뽑아낸다 (뒤에 붙는 이명은 korean_name 전체에는
    # 그대로 남아있으므로 화면 표시에는 영향이 없다).
    m = re.match(r"^(.*?)\(([^()]*)\)", korean_name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return korean_name.strip(), ""


def _finalize_title(entry, title_lines):
    korean_parts, latin_parts = [], []
    for line in title_lines:
        if _looks_like_latin_title(line):
            latin_parts.append(line)
        else:
            korean_parts.append(line)
    korean_name = " ".join(korean_parts).strip()
    # 첫 줄은 정식 명칭, 그 뒤에 붙는 줄(들)은 이명(異名)이다. 화면에서
    # 이명만 작은 글자로 구분해서 보여주기 위해 따로 저장해 둔다.
    name_primary = korean_parts[0].strip() if korean_parts else ""
    synonym_name = " ".join(korean_parts[1:]).strip()
    english_name, latin_name = "", ""
    if len(latin_parts) >= 2:
        english_name, latin_name = latin_parts[0], latin_parts[1]
    elif len(latin_parts) == 1:
        latin_name = latin_parts[0]
    name_only, hanja = _split_name(korean_name)
    entry["korean_name"] = korean_name
    entry["name_primary"] = name_primary
    entry["synonym_name"] = synonym_name
    entry["name_only"] = name_only
    entry["hanja"] = hanja
    entry["english_name"] = english_name
    entry["latin_name"] = latin_name


def _new_blank_entry():
    return {
        "korean_name": "",
        "name_primary": "",
        "synonym_name": "",
        "name_only": "",
        "hanja": "",
        "english_name": "",
        "latin_name": "",
        "definition": "",
        "definition_parts": [],  # [{"text": str, "html": str, "small": bool}]
        "sections": [],  # [{"label": str, "text": str}]
    }


# "이 약은 정량할 때 ~ 함유한다." 같은 정량(함량 규정) 문장을 기원문과
# 구분해서 작게 표시하기 위한 판정. "정량"이라는 말이 들어간 문장은 모두
# 같은 방식(빈 줄 구분 + 작은 글자)으로 처리한다.
def _is_quantitative_sentence(text: str) -> bool:
    return "정량" in text


# "이 약은 ~이다. 이 약은 정량할 때 ~함유한다." 처럼 기원문과 정량문이
# 문단을 나누지 않고 한 문단에 그대로 이어 붙어 있는 문서가 있다(예:
# 녹반, 백반, 석고, 아교 등). 이런 경우에도 정량문만 완전히 분리해서
# 아래에 작게 표시하기 위해, "정량"이 처음 나오는 문장이 시작하는 위치를
# 찾는다. "정량"을 포함한 문장 앞의 마지막 "~다. " 경계가 그 지점이다.
def _find_quantitative_split(text: str):
    idx = text.find("정량")
    if idx == -1:
        return None
    split_at = 0
    for m in _SENTENCE_END_SPLIT_RE.finditer(text[:idx]):
        split_at = m.end()
    return split_at or None


def _split_runs_at(runs, split_at: int):
    """[(text, is_italic), ...] 를 전체 텍스트 기준 split_at 위치에서 둘로 나눈다."""
    before, after = [], []
    pos = 0
    for text, italic in runs:
        end = pos + len(text)
        if end <= split_at:
            before.append((text, italic))
        elif pos >= split_at:
            after.append((text, italic))
        else:
            cut = split_at - pos
            if text[:cut]:
                before.append((text[:cut], italic))
            if text[cut:]:
                after.append((text[cut:], italic))
        pos = end
    return before, after


# "C42H62O16" 같은 화학식에서 원소기호 뒤 숫자를 <sub> 로 감싸기 위한 패턴.
# 원소기호+숫자가 2 번 이상 연달아 나올 때만 화학식으로 간주해서,
# "Rg1" 처럼 화합물 약칭에 붙은 숫자는 건드리지 않는다.
_FORMULA_TOKEN_RE = re.compile(r"(?:[A-Z][a-z]?\d{1,4}){2,}")
_ELEMENT_DIGIT_RE = re.compile(r"([A-Z][a-z]?)(\d{1,4})")

# "총 빌리루빈 또는 유리빌리루빈의 양 (mg)" 처럼 화학식 없이 "~의 양 (단위)"
# 로만 끝나는 계산식 라벨 줄도 있다(우황의 빌리루빈처럼 화합물 하나를 특정하지
# 않고 서술하는 경우). 화학식 토큰이 없어도 이 형태로 끝나면 바로 아래 "="
# 계산식 줄과 한 덩어리로 묶일 라벨로 인정한다.
_QUANT_LABEL_RE = re.compile(r"양\s*\([^()]*\)\s*$")

# C, H, O, N 뒤에 바로 숫자가 오면 그 자체로 화학식의 원자 개수 표시이므로,
# "FeSO4"나 "7H2O"처럼 원소기호+숫자 쌍이 한 번만 나와 위 규칙(2번 이상)에
# 걸리지 않는 경우에도 항상 아래첨자로 표시한다. 이 네 원소는 "Rg1"처럼
# 화합물 약칭에 붙는 문자(R 등)와 겹치지 않아 안전하다. 다만 "셀룰로오스
# MN300"(박층크로마토그래프용 제품명)처럼 "N" 앞에 "M"이 붙어 있으면
# 화학식이 아니라 제품 규격명이므로 제외한다.
_CHON_DIGIT_RE = re.compile(r"(?<!M)([CHON])(\d{1,4})")

# "(FeSO4·7H2O : 278.01)", "[KAl2(AlSi3O10)(OH)2]" 처럼 괄호 안에
# 화학식·분자량이 있는 경우, 괄호 안에서는 원소기호(C/H/O/N 뿐 아니라 Fe,
# Na, Ca, Al, F, S, Si 등 모두)+숫자를 예외 없이 아래첨자로 표시한다.
# 괄호 밖에서는 "진세노시드 Rg1" 같은 화합물 약칭과 구분이 안 되므로
# 이 규칙을 적용하지 않는다. "[...(...)...]" 처럼 괄호가 중첩된 경우도
# 있어(운모 등), 정규식 하나로는 안쪽/바깥쪽을 함께 처리할 수 없다 - 가장
# 바깥쪽 괄호 쌍을 직접 스캔해서 그 안의 내용 전체(중첩된 괄호 포함)에
# 한 번에 적용한다.
def _subscript_inside_brackets(text: str) -> str:
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "([":
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] in "([":
                    depth += 1
                elif text[j] in ")]":
                    depth -= 1
                j += 1
            if depth == 0:
                inner = text[i + 1 : j - 1]
                if "아플라톡신" not in inner:
                    # "아플라톡신 B1, B2, G1 및 G2의 합" 처럼 독소 이름(B1 등)이지
                    # 화학식이 아닌 경우는 그대로 둔다.
                    inner = _ELEMENT_DIGIT_RE.sub(r"\1<sub>\2</sub>", inner)
                out.append(text[i] + inner + text[j - 1])
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _paragraph_runs_with_italic(p_elem, italic_charpr_ids):
    """
    문단을 <hp:run> 단위로 훑어서 [(text, is_italic), ...] 을 반환한다.
    이탤릭 여부는 run의 charPrIDRef 가 header.xml 에서 <hh:italic/> 이 붙은
    문자 모양(charPr)을 가리키는지로 판단한다. 학명(라틴 속명·종소명)에는
    보통 이 서식이 원본 문서에 이미 지정되어 있어, 이를 그대로 활용한다.
    """
    runs = []
    pending_eq = ""
    for run in p_elem.findall("hp:run", NS):
        is_italic = run.get("charPrIDRef") in italic_charpr_ids
        for child in run:
            tag = _local(child.tag)
            if tag == "t":
                text = "".join(child.itertext())
                if text:
                    runs.append((text, is_italic))
            elif tag == "equation":
                script = child.find("hp:script", NS)
                if script is not None and script.text:
                    pending_eq += script.text
                    if pending_eq.count("{") > pending_eq.count("}"):
                        continue
                    runs.append((_convert_equation(pending_eq), False))
                    pending_eq = ""
    if pending_eq:
        runs.append((_convert_equation(pending_eq), False))
    return runs


_EQ_SUB_MARKER_RE = re.compile(f"{_EQ_SUB_L}(.*?){_EQ_SUB_R}")
_ITALIC_MARKER_RE = re.compile(f"{_ITALIC_L}(.*?){_ITALIC_R}")


# "피크면적 AT 및 AS를 측정한다" 처럼 수식이 아니라 평문 설명에 그대로 쓰인
# "AT"/"AS"(검액/표준액 피크면적) 및 "ATa/ASb" 같은 다성분 변형, "QT/QS" 를
# 찾아 뒤 글자를 아래첨자로 표시하기 위한 패턴.
_PEAK_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([AQ])([TS])([a-e])?(?![A-Za-z0-9])")

# "(OH)2" 처럼 원소기호가 아니라 (OH) 이온 묶음 뒤에 개수가 붙는 표기도
# 아래첨자로 표시한다.
_OH_GROUP_RE = re.compile(r"(\(OH\))(\d{1,4})")


def _apply_subscript_markup(escaped_text: str) -> str:
    """이미 HTML 이스케이프된 문자열에 아래첨자/이탤릭 표시를 적용한다.
    - 수식(계산식)에서 온 \\x02..\\x03 마커 -> <sub>
    - 이탤릭 서식(주로 학명)에서 온 \\x04..\\x05 마커 -> <i>
    - "C42H62O16" 같은 화학식의 숫자 -> <sub>
    - 평문에 그대로 쓰인 "AT"/"AS"/"ATa"/"ASb" 등의 피크면적 표시 -> <sub>
    """
    s = _ITALIC_MARKER_RE.sub(r"<i>\1</i>", escaped_text)
    s = _EQ_SUB_MARKER_RE.sub(r"<sub>\1</sub>", s)
    s = _subscript_inside_brackets(s)
    s = _OH_GROUP_RE.sub(r"\1<sub>\2</sub>", s)
    s = _FORMULA_TOKEN_RE.sub(
        lambda m: _ELEMENT_DIGIT_RE.sub(r"\1<sub>\2</sub>", m.group(0)), s
    )
    s = _CHON_DIGIT_RE.sub(r"\1<sub>\2</sub>", s)
    s = _PEAK_LABEL_RE.sub(
        lambda m: f"{m.group(1)}<sub>{m.group(2)}{m.group(3) or ''}</sub>", s
    )
    return _apply_equation_markup(s)


def _render_definition_html(runs) -> str:
    """[(text, is_italic), ...] 를 안전한 HTML로 렌더링한다."""
    parts = []
    for text, is_italic in runs:
        escaped = html.escape(text, quote=False)
        parts.append(f"<i>{escaped}</i>" if is_italic else escaped)
    joined = "".join(parts)
    # 화학식이 여러 run 으로 쪼개져 있을 수 있어(예: "C" / "21" / "H20O9"),
    # 개별 run이 아니라 다 이어붙인 문자열 전체를 대상으로 찾는다. 기원문이든
    # 정량문이든 화학식/수식 표시는 항상 같은 방식으로 처리한다.
    return _apply_subscript_markup(joined)


# "셀 | 셀 | 셀" 처럼 표시된 줄(<hp:tbl> 표에서 나온 줄)을 찾기 위한 패턴.
# 이 문서들의 일반 본문에는 이 형태(공백+세로줄+공백)가 나오지 않는다.
_TABLE_LINE_RE = re.compile(r".+ \| .+")

# "= 테뉴이폴린표준품의 양(mg) ×A T / A S × 2" 같은 정량법 계산식 줄을
# 찾기 위한 패턴("/"는 "mL/분" 같은 단위 표기에도 흔히 나와 그것만으로는
# 계산식으로 보지 않는다). "="가 있으면 항상 계산식이다. "×"만 있고 "="가
# 없는 줄은 "= ~ × A" 처럼 한 계산식이 여러 줄로 이어지는 경우("×"로
# 시작하는 이어지는 줄)에만 나오므로 한글이 섞여 있지 않을 때만 계산식으로
# 본다 - "Supelcosil ... (4.6 × 250 mm, 5 μm) 또는 이와 동등한 것"이나
# "PCR용 완충액(10 × amplification buffer)"처럼 "×"가 그냥 곱하기 기호로
# 쓰인 한글 설명문과 구분하기 위함이다.
_FORMULA_EQ_RE = re.compile(r"=")
_FORMULA_TIMES_ONLY_RE = re.compile(r"×")


def _is_formula_line(line: str) -> bool:
    if _FORMULA_EQ_RE.search(line):
        return True
    return bool(_FORMULA_TIMES_ONLY_RE.search(line)) and not _HANGUL_RE.search(line)

# 라벨 줄과 "=" 계산식 줄을 하나로 합쳤을 때 이 길이(글자 수)를 넘으면
# 화면 폭에서 줄바꿈이 일어나 가운데 정렬이 어색해지므로(예: 숙지황처럼
# 화합물 이름이 길어서 라벨+계산식이 아주 긴 경우) 합치지 않고 원래처럼
# 두 줄로 띄운다.
_FORMULA_MERGE_MAX_LEN = 70


def _render_text_line_html(line: str) -> str:
    return _apply_subscript_markup(html.escape(line, quote=False))


_TABLE_CELL_SPLIT_RE = re.compile(r" ?\| ?")


def _rows_to_table_html(table_lines) -> str:
    # 1단계: 줄마다 칸으로 나누고, 병합 표시(_MERGED_UP/_MERGED_LEFT)가 붙은 칸은 그 칸을
    # 만들지 않는 대신 원래 칸의 rowspan/colspan 을 늘린다.
    grid = []  # 행마다 [{"text":..., "rs":1, "cs":1, "skip":False}, ...]
    for ln in table_lines:
        # 병합된(rowSpan) 첫 칸이 빈 채로 남은 줄("| 252.0 | 226.0 | 24")은
        # 줄 정리 단계에서 앞의 구분용 공백이 strip() 되어 맨 앞이 "|"로
        # 시작하므로, 앞뒤 공백이 없어도 "|" 하나로 칸을 나눈다.
        cells = [c.strip() for c in _TABLE_CELL_SPLIT_RE.split(ln)]
        grid.append([{"text": c, "rs": 1, "cs": 1, "skip": False} for c in cells])
    origin_by_col = {}  # 열 -> 지금까지 그 열에서 가장 최근의 병합 원본 칸
    for row in grid:
        left_origin = None
        for col, cell in enumerate(row):
            text = cell["text"]
            if text == _MERGED_UP and col in origin_by_col:
                origin_by_col[col]["rs"] += 1
                cell["skip"] = True
            elif text == _MERGED_LEFT and left_origin is not None:
                left_origin["cs"] += 1
                cell["skip"] = True
            else:
                if text in (_MERGED_UP, _MERGED_LEFT):
                    cell["text"] = ""  # 합칠 원본이 없으면 빈 칸으로 둔다
                origin_by_col[col] = cell
                left_origin = cell
    rows_html = []
    for row in grid:
        cells_html = []
        for cell in row:
            if cell["skip"]:
                continue
            attrs = ""
            if cell["rs"] > 1:
                attrs += f' rowspan="{cell["rs"]}"'
            if cell["cs"] > 1:
                attrs += f' colspan="{cell["cs"]}"'
            inner = _render_text_line_html(cell["text"]).replace(_CELL_LINE_BREAK, "<br>")
            cells_html.append(f"<td{attrs}>{inner}</td>")
        rows_html.append(f"<tr>{''.join(cells_html)}</tr>")
    return f'<table class="orig-table">{"".join(rows_html)}</table>'


def _render_rich_html(text: str) -> str:
    """평문 섹션/항목 텍스트를 화면 표시용 HTML로 변환한다.
    - 방정식에서 나온 아래첨자, 화학식 숫자를 <sub> 로 표시한다.
    - hwpx 원본의 표(<hp:tbl>)에서 나와 "셀 | 셀" 형태로 저장된 연속된 줄은
      원본처럼 보이도록 실제 <table> 로 다시 조립해서 그대로 붙인다.
    """
    if not text:
        return ""
    lines = text.split("\n")
    n = len(lines)

    # 1단계: 줄들을 "table"/"formula"/"text" 구간(block)으로 나눈다. "= "가
    # 있는 계산식 줄 바로 위, 화학식이 들어있는 라벨 줄(들)은 그 앞의 text
    # 구간에서 떼어내 같은 formula 구간으로 옮긴다("OO의 양 (mg)" + "=
    # ~" 를 한 덩어리로 묶기 위함). 이렇게 구간을 먼저 확정해 두면, 나중에
    # "라벨이 사실은 다음 계산식 줄의 일부였다"는 이유로 간격 표시를
    # 잘못 끼워 넣는 문제가 생기지 않는다.
    blocks = []  # [(kind, [line_index, ...]), ...]
    i = 0
    while i < n:
        if _TABLE_LINE_RE.match(lines[i]):
            start = i
            while i < n and _TABLE_LINE_RE.match(lines[i]):
                i += 1
            blocks.append(("table", list(range(start, i))))
            continue
        if _is_formula_line(lines[i]):
            pulled = []
            if blocks and blocks[-1][0] == "text":
                text_idxs = blocks[-1][1]
                k = len(text_idxs) - 1
                while k >= 0 and lines[text_idxs[k]].strip():
                    # 화학식 숫자가 실제 아래첨자 서식(<hh:subscript/>)으로
                    # 지정된 경우 "C19H20O5" 사이에 마커 문자가 끼어들어
                    # _FORMULA_TOKEN_RE가 못 찾는다("총 데쿠르신 [...화학식...]
                    # 및" 처럼 라벨이 두 줄에 걸쳐 있을 때 특히 문제가 된다) -
                    # 마커를 뺀 문자열로 판정한다.
                    candidate = _strip_markup_with_map(lines[text_idxs[k]])[0]
                    # 라벨 줄은 항상 짧다("에스트라골 (C10H12O)의 양(mg)" 등).
                    # 길이 제한이 없으면 화학식 토큰을 "지나가는 말로" 언급한
                    # 긴 서술 문단(예: "...에스트라골(C10H12O : 148.20)이
                    # 10.0 % 이하이다." 로 끝나는 절차 설명)까지 수식줄
                    # 라벨로 잘못 끌려 들어와 그 문단 전체가 가운데 정렬되어
                    # 버린다.
                    if len(candidate) > 80 or not (
                        _FORMULA_TOKEN_RE.search(candidate) or _QUANT_LABEL_RE.search(candidate)
                    ):
                        break
                    pulled.append(text_idxs[k])
                    k -= 1
                if pulled:
                    pulled.reverse()
                    remaining = text_idxs[: k + 1]
                    if remaining:
                        blocks[-1] = ("text", remaining)
                    else:
                        blocks.pop()
            blocks.append(("formula", pulled + [i]))
            i += 1
            continue
        if blocks and blocks[-1][0] == "text":
            blocks[-1][1].append(i)
        else:
            blocks.append(("text", [i]))
        i += 1

    # 2단계: 구간을 HTML로 조립한다. 계산식 구간과 그 외 구간이 만나는
    # 경계에서만 한 줄을 띄우고, 계산식 구간끼리 이어질 때는 띄우지 않는다.
    # "계산식 구간"은 <div>(블록 요소)라서, 그 사이에 <br>를 하나라도
    # 넣으면(구분자로 흔히 쓰는 빈 문자열을 <br>로 이어붙이는 방식) 그
    # <br> 자체가 줄 하나를 더 차지해 버려 "간격 없음"이 아니라 빈 줄이
    # 하나 생겨 버린다. 그래서 계산식끼리 이어질 때는 구분자를 아예
    # 넣지 않고, 그 외의 경우에만 <br>로 잇는다.
    out = []  # [(kind, html), ...]
    for kind, idxs in blocks:
        if kind == "table":
            piece = _rows_to_table_html([lines[k] for k in idxs])
        elif kind == "formula":
            raw_merged = _strip_markup_with_map(" ".join(lines[k] for k in idxs))[0]
            join_with = " " if len(raw_merged) <= _FORMULA_MERGE_MAX_LEN else "<br>"
            piece = f'<div class="formula-line">{join_with.join(_render_text_line_html(lines[k]) for k in idxs)}</div>'
        else:
            piece = "<br>".join(_render_text_line_html(lines[k]) for k in idxs)
        out.append((kind, piece))

    # 3단계: 구간 사이를 이어붙인다. 계산식 구간(<div>, 블록 요소)은 그
    # 자체로 줄이 바뀌므로, 그 바로 앞/뒤에서는 <br> 하나만으로 빈 줄 한
    # 개가 만들어진다(반면 일반 텍스트는 줄바꿈이 저절로 일어나지 않으므로
    # <br> 두 개 - 줄바꿈 + 빈 줄 - 가 필요하다). 계산식끼리 이어질 때는
    # 아예 구분자를 넣지 않는다.
    html_parts = []
    prev_kind = None
    for kind, piece in out:
        if prev_kind is not None:
            gap_needed = (prev_kind == "formula") != (kind == "formula")
            if gap_needed:
                sep = "<br>" if prev_kind in ("formula", "table") else "<br><br>"
            elif prev_kind == "formula" and kind == "formula":
                sep = ""
            else:
                sep = "<br>"
            html_parts.append(sep)
        html_parts.append(piece)
        prev_kind = kind
    if prev_kind == "formula":
        # 마지막 구간이 계산식이면, 뒤에 이어지는 내용이 이 텍스트 안에는
        # 없더라도(예: "조작조건"이 별도 하위 항목으로 분리되어 바로 뒤에
        # 옴) 계산식 아래에 항상 빈 줄 하나를 남긴다.
        html_parts.append("<br>")
    return "".join(html_parts)


def _enrich_item_html(items):
    """계층형 항목(순도시험/정량법/확인시험/성상)의 bold/rest/text 필드에
    표시용 HTML 필드(bold_html/rest_html/text_html)를 덧붙인다. marker에
    아래첨자/이탤릭 마커가 섞여 있으면(예: 성상의 "Glycyrrhiza korshinskyi
    Grig." 처럼 학명이 그대로 표시(marker)로 쓰이는 경우) marker_html도
    만들고, marker 자체는 마커 문자를 뺀 평문으로 정리한다."""
    for it in items:
        marker_raw = it.get("marker", "")
        if any(c in marker_raw for c in _MARKUP_CHARS):
            it["marker_html"] = _render_rich_html(marker_raw)
            it["marker"] = _plain_markup(marker_raw)
        if "bold" in it:
            it["bold_html"] = _render_rich_html(it.get("bold", ""))
            it["rest_html"] = _render_rich_html(it.get("rest", ""))
        else:
            it["text_html"] = _render_rich_html(it.get("text", ""))
        if it.get("children"):
            _enrich_item_html(it["children"])


def _build_section_items(label, text):
    """라벨(순도시험/정량법/확인시험/성상)에 맞는 계층 파서로 items를
    만든다. 못 찾으면 빈 리스트."""
    if label in ("순도시험", "정량법"):
        items = _parse_deep_numbered_hierarchy(text) if _has_deep_markers(text) else []
        if not items and label == "정량법":
            items = _parse_quantitation_hierarchy(text)
        if not items:
            items = _parse_numbered_hierarchy(text, bold_labels=True)
        if not items:
            # "1)" 같은 번호가 전혀 없어도 "중금속  이 약의 가루 ~" 처럼
            # "라벨 + 설명" 한 문장뿐인 섹션이 있다(예: 자석). 이런 경우
            # 그 라벨을 표시 없는 하위 항목 하나로 인식한다.
            b, rest = _split_section_label(text)
            if b and rest:
                node = {"marker": "", "text": text, "children": [], "bold": b, "rest": rest}
                ref_name = _detect_purity_reference(text)
                if ref_name:
                    node["ref_name"] = ref_name
                items = [node]
        return items
    if label == "확인시험":
        return _parse_numbered_hierarchy(text)
    if label == "성상":
        items = _parse_seongsang_blocks(text)
        return items if len(items) >= 2 else []
    if label == "제법":
        return _parse_process_blocks(text)
    return []


def _finalize_sections(sections):
    """항목(entry)의 섹션 리스트를 마무리한다.

    사향처럼 정량법 안에서 시약의 순도 규격을 설명하려고 "순도시험"이라는
    항목명을 재사용하는 문서가 있다 - 이걸 원문 서식만으로는 새 섹션과
    구분할 수 없어서, 우선은 늘 새 섹션으로 나뉜다. 이미 진짜 순도시험
    섹션이 앞에 있는데 정량법 뒤에 "순도시험"이 또 나오면, 그건 새 섹션이
    아니라 정량법에 딸린 내용이므로 정량법에 도로 합친다. 이 판단은 모든
    섹션의 라벨을 다 본 뒤에야 할 수 있으므로, html/items 계산은 여기서
    (합칠 건 합친 다음) 한 번에 한다."""
    seen_real_purity = False
    seen_quant = False
    merged = []
    for sec in sections:
        label = sec["label"]
        if label == "순도시험" and seen_quant and seen_real_purity and merged:
            merged[-1]["text"] = (merged[-1]["text"] + "\n" + sec["text"]).strip()
            continue
        if label == "순도시험" and not seen_quant:
            seen_real_purity = True
        if label == "정량법":
            seen_quant = True
        merged.append(sec)

    for section in merged:
        label = section["label"]
        text = section["text"]
        section["html"] = _render_rich_html(text)
        items = _build_section_items(label, text)
        if items:
            _enrich_item_html(items)
            section["items"] = items
        else:
            # "1)" 같은 목록 표시가 없어 계층화되지 않은 섹션이라도(예:
            # "대한민국약전 「두충」의 순도시험 1) 에 따른다." 한 문장뿐인
            # 경우), 다른 생약 참조는 감지해서 링크를 만들 수 있게 한다.
            ref_name = _detect_purity_reference(text)
            if ref_name:
                section["ref_name"] = ref_name
    return merged


_KNOWN_HEADER_LABELS = {
    "성상", "확인시험", "순도시험", "건조감량", "강열감량", "강열잔분", "회분",
    "산불용성회분", "엑스함량", "정량법", "저장법", "제법", "정의", "기원",
    "비고", "비중", "무균시험", "산가", "불용성이물시험", "미생물한도",
    "엔도톡신", "정유함량", "요오드가", "융점", "수분", "비누화가", "점도",
    "굴절률", "비선광도", "pH", "포제",
}

# "확인시험  1)" 처럼 항목명 바로 뒤에 "1)" 등이 같은 런(run)에 붙어 나오는
# 문서가 있다. 이런 경우 전체 텍스트가 표준 어휘와 정확히 일치하지 않아
# 놓치므로, 글자 사이 공백을 허용하며 항목명으로 "시작"하는지 검사하고
# 그 뒤에 남는 부분(예: "1)")은 헤더가 아니라 본문으로 돌려보낸다.
_KNOWN_HEADER_PATTERNS = [
    (label, re.compile(r"^\s*" + r"\s*".join(re.escape(ch) for ch in label)))
    for label in sorted(_KNOWN_HEADER_LABELS, key=len, reverse=True)
]


def _split_known_header(text: str):
    """text 가 표준 항목명으로 시작하면 (라벨, 나머지) 를, 아니면 (None, None) 을 반환한다."""
    for label, pattern in _KNOWN_HEADER_PATTERNS:
        m = pattern.match(text)
        if m:
            return label, text[m.end():]
    return None, None


def _detect_header_style_ids_by_content(section_xml_bytes_list, min_distinct_labels=3):
    """
    header.xml 의 스타일 이름 표기가 문서마다 제각각이라("항목명" 대신
    자동 생성된 "11" 같은 이름을 쓰는 문서도 있다), 실제 본문에서
    "성상", "확인시험" 같은 표준 약전 항목명 어휘가 어떤 charStyleIDRef로
    쓰였는지를 직접 세어서 그 스타일 id를 알아낸다.
    """
    style_labels = {}  # charStyleIDRef -> set(matched label text)
    for xml_bytes in section_xml_bytes_list:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            continue
        for t in root.iter(f"{{{NS['hp']}}}t"):
            char_style = t.get("charStyleIDRef")
            if not char_style:
                continue
            text = re.sub(r"\s+", "", "".join(t.itertext()))
            if text in _KNOWN_HEADER_LABELS:
                style_labels.setdefault(char_style, set()).add(text)
    return {sid for sid, labels in style_labels.items() if len(labels) >= min_distinct_labels}


def _detect_named_char_style_ids(header_xml_bytes, target_name):
    """header.xml 의 문자 스타일 카탈로그에서 이름이 target_name 인 스타일의 id를 찾는다."""
    ids = set()
    try:
        root = ET.fromstring(header_xml_bytes)
    except ET.ParseError:
        return ids
    for style in root.findall(".//hh:style", NS):
        if style.get("type") != "CHAR":
            continue
        name = (style.get("name") or "").strip().lstrip("*").strip()
        if name == target_name:
            sid = style.get("id")
            if sid:
                ids.add(sid)
    return ids


def _detect_italic_charpr_ids(header_xml_bytes):
    """header.xml 의 문자 모양(charPr) 카탈로그에서 <hh:italic/> 이 붙은 id를 모은다."""
    ids = set()
    try:
        root = ET.fromstring(header_xml_bytes)
    except ET.ParseError:
        return ids
    for charpr in root.findall(".//hh:charPr", NS):
        if charpr.find("hh:italic", NS) is not None:
            cid = charpr.get("id")
            if cid:
                ids.add(cid)
    return ids


def _detect_bold_charpr_ids(header_xml_bytes):
    """header.xml 의 문자 모양(charPr) 카탈로그에서 <hh:bold/> 가 붙은 id를 모은다."""
    ids = set()
    try:
        root = ET.fromstring(header_xml_bytes)
    except ET.ParseError:
        return ids
    for charpr in root.findall(".//hh:charPr", NS):
        if charpr.find("hh:bold", NS) is not None:
            cid = charpr.get("id")
            if cid:
                ids.add(cid)
    return ids


def _detect_subscript_charpr_ids(header_xml_bytes):
    """header.xml 의 문자 모양(charPr) 카탈로그에서 <hh:subscript/> 가 붙은 id를
    모은다. "AS"/"AIS"/"ASAM"/"ASAMIS"나 "벤조피렌-d12"의 "12"처럼, 원문에서
    이미 실제 아래첨자 서식으로 지정해 둔 run을 그대로 활용하기 위함이다."""
    ids = set()
    try:
        root = ET.fromstring(header_xml_bytes)
    except ET.ParseError:
        return ids
    for charpr in root.findall(".//hh:charPr", NS):
        if charpr.find("hh:subscript", NS) is not None:
            cid = charpr.get("id")
            if cid:
                ids.add(cid)
    return ids


_DEFINITION_STARTERS = ("이 약은", "이것은", "본품은", "이 제제는", "이 약의")


def _starts_like_definition(text: str) -> bool:
    t = text.strip()
    return any(t.startswith(s) for s in _DEFINITION_STARTERS)


def parse_hwpx_bytes_sections(
    section_xml_bytes_list,
    header_style_ids=None,
    subheader_style_ids=None,
    italic_charpr_ids=None,
    bold_charpr_ids=None,
    subscript_charpr_ids=None,
):
    """여러 section*.xml 바이트를 순서대로 이어붙여 파싱한다."""
    if not header_style_ids:
        header_style_ids = DEFAULT_HEADER_STYLE_IDS
    if not subheader_style_ids:
        subheader_style_ids = frozenset()
    if not italic_charpr_ids:
        italic_charpr_ids = frozenset()
    if not bold_charpr_ids:
        bold_charpr_ids = frozenset()
    if not subscript_charpr_ids:
        subscript_charpr_ids = frozenset()

    # --- 1단계: 빈 문단을 걸러내고, 각 문단을 미리 분석해 둔다. ---------------
    flat = []
    for xml_bytes in section_xml_bytes_list:
        root = ET.fromstring(xml_bytes)
        # 최상위(hs:sec)의 직계 문단만 순회한다. `.//hp:p` 로 전체를 훑으면
        # 각주/텍스트상자 등에 중첩된 <hp:p> 까지 끼어들어 본문 중간에
        # 엉뚱한 줄바꿈이 섞여 들어가므로, 문서 흐름과 동일한 직계 자식만 사용한다.
        for p in root.findall("hp:p", NS):
            segments, has_subheader = _paragraph_segments(
                p, header_style_ids, subheader_style_ids, bold_charpr_ids,
                subscript_charpr_ids, italic_charpr_ids,
            )
            plain_text = "".join(t for k, t in segments if k == "text").strip()
            has_header = any(k == "header" for k, _ in segments)
            if not plain_text and not has_header:
                continue  # 완전히 빈 문단(줄 간격용) - 건너뛴다
            is_candidate = (
                not has_header
                and not has_subheader
                and plain_text
                and (_looks_like_korean_title(plain_text) or _looks_like_latin_title(plain_text))
            )
            is_hanja_only = not has_header and plain_text and _looks_like_hanja_only(plain_text)
            flat.append(
                {
                    "elem": p,
                    "segments": segments,
                    "plain_text": plain_text,
                    "has_header": has_header,
                    "is_candidate": is_candidate,
                    "is_hanja_only": is_hanja_only,
                }
            )

    # --- 2단계: 진짜 "품목 제목 시작"만 확정한다. -----------------------------
    # 이름처럼 생긴 짧은 문단이라도 (a) 바로 다음 문단이 또 이름처럼 생겼거나
    # (b) 바로 다음 문단이 "이 약은 ~" 형태의 정의문으로 시작할 때만 품목의
    # 시작으로 인정한다. "조작조건", "이동상" 같은 실험절차 소제목은 둘 중
    # 어느 조건도 만족하지 못해 자연스럽게 걸러진다.
    confirmed_start = [False] * len(flat)
    in_block = False
    for i, item in enumerate(flat):
        if item["is_hanja_only"]:
            continue  # 한자만 있는 연결 줄은 블록을 끊지 않는다 (예: "갈화" + "(葛花)")
        if not item["is_candidate"]:
            in_block = False
            continue
        if in_block:
            continue  # 이미 확정된 제목 블록의 연속 줄
        nxt = flat[i + 1] if i + 1 < len(flat) else None
        ok = bool(nxt) and (nxt["is_candidate"] or nxt["is_hanja_only"] or _starts_like_definition(nxt["plain_text"]))
        if ok:
            confirmed_start[i] = True
            in_block = True
        # ok 가 아니면 고립된 후보로 보고 제목으로 인정하지 않는다 (in_block=False 유지)

    # --- 3단계: 확정된 제목 시작을 기준으로 실제 항목을 조립한다. -------------
    entries = []
    entry = None
    title_lines = []
    state = "pre"  # "title" / "definition" / "body"
    current_section = None

    def flush_section():
        nonlocal current_section
        if entry is not None and current_section is not None:
            # 같은 문단 안의 조각들은 구분자 없이 그대로 이어붙이고,
            # 문단이 바뀔 때만("\n" 마커) 줄바꿈을 준다.
            raw = "".join(current_section["text"])
            lines = [ln.strip() for ln in raw.split("\n")]
            cleaned = []
            for ln in lines:
                if ln == "" and (not cleaned or cleaned[-1] == ""):
                    continue
                cleaned.append(ln)
            while cleaned and cleaned[0] == "":
                cleaned.pop(0)
            while cleaned and cleaned[-1] == "":
                cleaned.pop()
            label = current_section["label"]
            text = "\n".join(cleaned)
            # html/items 계산은 _finalize_sections 에서 (필요하면 순도시험
            # 재사용 섹션을 정량법에 합친 다음) 한꺼번에 한다.
            entry["sections"].append({"label": label, "text": text})
        current_section = None

    def close_entry():
        nonlocal entry, title_lines, state, current_section
        flush_section()
        if entry is not None:
            _finalize_title(entry, title_lines)
            entry["definition"] = entry["definition"].strip()
            entry["sections"] = _finalize_sections(entry["sections"])
            if entry["korean_name"] or entry["sections"]:
                entries.append(entry)
        entry = None
        title_lines = []
        state = "pre"

    def start_new_entry():
        nonlocal entry, title_lines, state
        close_entry()
        entry = _new_blank_entry()
        title_lines = []
        state = "title"

    in_title_block = False
    for i, item in enumerate(flat):
        plain_text = item["plain_text"]
        has_header = item["has_header"]
        segments = item["segments"]

        if confirmed_start[i]:
            start_new_entry()
            in_title_block = True
            title_lines.append(_strip_number_prefix(plain_text))
            continue

        if in_title_block and item["is_candidate"]:
            title_lines.append(_strip_number_prefix(plain_text))
            continue

        if in_title_block and item["is_hanja_only"] and title_lines:
            # 한자만 따로 떨어진 줄 -> 직전 이름 줄에 이어붙인다 (예: "갈화" + "(葛花)")
            title_lines[-1] = title_lines[-1] + plain_text
            continue

        in_title_block = False

        if entry is None:
            continue  # 문서 맨 앞부분(첫 품목이 시작되기 전) - 무시

        if state == "title":
            state = "definition"

        if not has_header:
            if state == "definition":
                if plain_text:
                    runs = _paragraph_runs_with_italic(item["elem"], italic_charpr_ids)
                    runs_text = "".join(t for t, _ in runs)
                    # 기원문과 정량문이 문단을 나누지 않고 한 문단에 이어 붙어
                    # 있는 문서가 있다(예: 녹반, 백반, 석고). 이 경우에도
                    # 정량문을 완전히 분리해서 아래에 작게 표시한다.
                    split_at = _find_quantitative_split(runs_text) if "정량" in runs_text else None
                    if split_at:
                        run_groups = _split_runs_at(runs, split_at)
                    else:
                        run_groups = [runs]
                    for run_piece in run_groups:
                        text_piece = "".join(t for t, _ in run_piece).strip()
                        if not text_piece:
                            continue
                        quantitative = _is_quantitative_sentence(text_piece)
                        if not entry["definition"]:
                            sep = ""
                        elif quantitative:
                            sep = "\n\n"  # 기원문과 정량 함유량 문장을 빈 줄로 구분
                        else:
                            sep = "\n"
                        entry["definition"] += sep + text_piece
                        entry["definition_parts"].append(
                            {
                                "text": text_piece,
                                "html": _render_definition_html(run_piece),
                                "small": quantitative,
                            }
                        )
            elif current_section is not None:
                for kind, text in segments:
                    if kind == "text":
                        current_section["text"].append(text)
                current_section["text"].append("\n")
            continue

        # 헤더(항목명)가 포함된 문단: 새 섹션 시작 + 같은 문단 내 나머지 텍스트 처리
        state = "body"
        for kind, text in segments:
            if kind == "header":
                flush_section()
                label = re.sub(r"\s+", "", text)  # "성    상" -> "성상"
                current_section = {"label": label, "text": []}
            elif current_section is not None:
                current_section["text"].append(text)
        if current_section is not None:
            current_section["text"].append("\n")

    close_entry()
    _clean_plain_fields(entries)
    return entries


def _clean_plain_fields(obj):
    """표시용 *_html 이 아닌 평문 필드(text, bold, rest, marker 등)에 남은 수식/서식 마커를
    평문으로 바꾼다(HTML 은 이미 만들어졌고, 평문은 검색과 대체 표시에만 쓰인다)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                if not key.endswith("html"):
                    obj[key] = _plain_markup(value)
            else:
                _clean_plain_fields(value)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            if isinstance(value, str):
                obj[i] = _plain_markup(value)
            else:
                _clean_plain_fields(value)


def parse_hwpx(path):
    """.hwpx 파일 경로를 받아 품목 리스트를 반환한다."""
    path = Path(path)
    with zipfile.ZipFile(path, "r") as zf:
        section_files = sorted(
            n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)
        )
        if not section_files:
            raise ValueError(f"{path.name}: Contents/section*.xml 을 찾을 수 없습니다.")
        section_bytes = [zf.read(n) for n in section_files]

        header_style_ids = set()
        subheader_style_ids = set()
        italic_charpr_ids = set()
        bold_charpr_ids = set()
        subscript_charpr_ids = set()
        if "Contents/header.xml" in zf.namelist():
            header_xml_bytes = zf.read("Contents/header.xml")
            header_style_ids = _detect_named_char_style_ids(header_xml_bytes, "항목명")
            subheader_style_ids = _detect_named_char_style_ids(header_xml_bytes, "소항목명")
            italic_charpr_ids = _detect_italic_charpr_ids(header_xml_bytes)
            bold_charpr_ids = _detect_bold_charpr_ids(header_xml_bytes)
            subscript_charpr_ids = _detect_subscript_charpr_ids(header_xml_bytes)

        # 스타일 이름표(예: "항목명")로 못 찾았거나 실제 본문 사용과 어긋날 수
        # 있으므로, 표준 항목명 어휘가 실제로 어떤 스타일을 쓰는지 본문에서
        # 직접 확인해 우선시한다 (문서마다 스타일 이름 표기가 제각각이기 때문).
        content_based_ids = _detect_header_style_ids_by_content(section_bytes)
        if content_based_ids:
            header_style_ids = content_based_ids
        elif not header_style_ids:
            header_style_ids = DEFAULT_HEADER_STYLE_IDS

    return parse_hwpx_bytes_sections(
        section_bytes,
        header_style_ids,
        subheader_style_ids,
        italic_charpr_ids,
        bold_charpr_ids,
        subscript_charpr_ids,
    )


def parse_hwpx_files(paths):
    """여러 hwpx 파일을 파싱해서 하나의 리스트로 합친다."""
    all_entries = []
    for p in paths:
        try:
            all_entries.extend(parse_hwpx(p))
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] {p} 파싱 실패: {exc}", file=sys.stderr)
    return all_entries


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "가자.hwpx"
    data = parse_hwpx(target)
    print(f"총 {len(data)}개 품목 파싱됨\n")
    print(json.dumps(data, ensure_ascii=False, indent=2))
