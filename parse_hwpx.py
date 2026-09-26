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


def _convert_equation(script_text: str) -> str:
    """hwpx 수식 스크립트를 아래첨자 마커가 포함된 텍스트로 변환한다.
    예: "{A  _{rm Ta}} over {A  _{rm Sa}}" -> "A  \\x02Ta\\x03 / A  \\x02Sa\\x03"
    """
    s = script_text or ""

    def _sub_repl(m):
        inner = m.group(1)
        inner = re.sub(r"^\s*rm", "", inner)  # "rm"(정체) 서식 키워드 제거
        inner = re.sub(r"\bit\b", "", inner)  # "it"(이탤릭) 서식 키워드 제거
        inner = re.sub(r"\s+", "", inner).strip()
        return _EQ_SUB_L + inner + _EQ_SUB_R if inner else ""

    s = re.sub(r"_\{([^{}]*)\}", _sub_repl, s)
    s = re.sub(r"\s+over\s+", " / ", s)
    s = s.replace("{", "").replace("}", "")
    s = s.replace("_", "")  # 위 패턴으로 잡히지 않은 잔여 "_" (빈 수식 등) 제거
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s


def _cell_text(tc_elem):
    text = "".join(tc_elem.itertext())
    return re.sub(r"\s+", " ", text).strip()


def _table_to_text(tbl_elem):
    """<hp:tbl> 표를 "셀 | 셀 | 셀" 형태의 여러 줄 텍스트로 변환한다."""
    rows = []
    for tr in tbl_elem.findall("hp:tr", NS):
        cells = [_cell_text(tc) for tc in tr.findall("hp:tc", NS)]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _paragraph_segments(p_elem, header_style_ids, subheader_style_ids=frozenset(), bold_charpr_ids=frozenset()):
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
    for run in p_elem.findall("hp:run", NS):
        # 스타일(charStyleIDRef)이 아예 없이 charPrIDRef 직접 서식(굵게)만으로
        # 항목명을 표시하는 문서가 있다("확인시험  1)"처럼 번호가 같은 런에
        # 붙어 있는 경우 등). 이때는 이 run이 실제로 "굵게" 서식인지
        # header.xml 에서 확인한 뒤에만 항목명 어휘 매칭을 시도한다 - 그래야
        # "비중 : 5.17 ～ 5.18" 처럼 그냥 본문에 항목명과 같은 단어가 나올 때
        # 헤더로 오인하지 않는다.
        run_is_bold = run.get("charPrIDRef") in bold_charpr_ids
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
                    segments.append(("text", text))
            elif tag == "equation":
                script = child.find("hp:script", NS)
                if script is not None and script.text:
                    segments.append(("text", _convert_equation(script.text)))
            elif tag == "tbl":
                table_text = _table_to_text(child)
                if table_text:
                    segments.append(("text", "\n" + table_text + "\n"))
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
_LIST_MARKER_RE = re.compile(
    r"(?:^|(?<=\n))(?P<num>\d{1,2})\)\s*"
    r"|(?:^|(?<=\s))(?P<kor>[가나다라마바사아자차카타파하])\)\s*"
)


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

    items = []
    current_l1 = None
    for idx, m in enumerate(matches):
        content_start = m.end()
        content_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        content = _insert_colon_before_origin(text[content_start:content_end].strip())
        if m.group("num"):
            node = make_node(f"{m.group('num')})", content, bold=bold_labels)
            items.append(node)
            current_l1 = node
        else:
            node = make_node(f"{m.group('kor')})", content)
            if current_l1 is not None:
                current_l1["children"].append(node)
            else:
                items.append(node)  # "가)" 로 바로 시작하는 예외적인 경우

    lead = text[: matches[0].start()].strip()
    if lead:
        items.insert(0, make_node("", lead))
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
        m = _VARIETY_LABEL_RE.match(line)
        if m and m.group("label").strip():
            blocks.append({"marker": m.group("label").strip(), "text": line[m.end():].strip(), "children": []})
            continue
        if _ORIGIN_SENTENCE_RE.match(line) or line.startswith(_MICROSCOPE_TRIGGERS):
            blocks.append({"marker": "", "text": line, "children": []})
            continue
        if blocks:
            blocks[-1]["text"] = (blocks[-1]["text"] + " " + line).strip()
        else:
            blocks.append({"marker": "", "text": line, "children": []})
    return blocks


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


def _paragraph_runs_with_italic(p_elem, italic_charpr_ids):
    """
    문단을 <hp:run> 단위로 훑어서 [(text, is_italic), ...] 을 반환한다.
    이탤릭 여부는 run의 charPrIDRef 가 header.xml 에서 <hh:italic/> 이 붙은
    문자 모양(charPr)을 가리키는지로 판단한다. 학명(라틴 속명·종소명)에는
    보통 이 서식이 원본 문서에 이미 지정되어 있어, 이를 그대로 활용한다.
    """
    runs = []
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
                    runs.append((_convert_equation(script.text), False))
    return runs


_EQ_SUB_MARKER_RE = re.compile(f"{_EQ_SUB_L}(.*?){_EQ_SUB_R}")


# "피크면적 AT 및 AS를 측정한다" 처럼 수식이 아니라 평문 설명에 그대로 쓰인
# "AT"/"AS"(검액/표준액 피크면적) 및 "ATa/ASb" 같은 다성분 변형, "QT/QS" 를
# 찾아 뒤 글자를 아래첨자로 표시하기 위한 패턴.
_PEAK_LABEL_RE = re.compile(r"(?<![A-Za-z0-9])([AQ])([TS])([a-e])?(?![A-Za-z0-9])")


def _apply_subscript_markup(escaped_text: str) -> str:
    """이미 HTML 이스케이프된 문자열에 아래첨자 표시를 적용한다.
    - 수식(계산식)에서 온 \\x02..\\x03 마커 -> <sub>
    - "C42H62O16" 같은 화학식의 숫자 -> <sub>
    - 평문에 그대로 쓰인 "AT"/"AS"/"ATa"/"ASb" 등의 피크면적 표시 -> <sub>
    """
    s = _EQ_SUB_MARKER_RE.sub(r"<sub>\1</sub>", escaped_text)
    s = _FORMULA_TOKEN_RE.sub(
        lambda m: _ELEMENT_DIGIT_RE.sub(r"\1<sub>\2</sub>", m.group(0)), s
    )
    s = _PEAK_LABEL_RE.sub(
        lambda m: f"{m.group(1)}<sub>{m.group(2)}{m.group(3) or ''}</sub>", s
    )
    return s


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
# 찾기 위한 패턴. "="나 "×"가 있으면 계산식으로 보고 가운데 정렬한다("/"는
# "mL/분" 같은 단위 표기에도 흔히 나와 그것만으로는 계산식으로 보지 않는다).
_FORMULA_LINE_RE = re.compile(r"[=×]")


def _render_text_line_html(line: str) -> str:
    return _apply_subscript_markup(html.escape(line, quote=False))


def _rows_to_table_html(table_lines) -> str:
    rows_html = []
    for ln in table_lines:
        cells = [c.strip() for c in ln.split(" | ")]
        cells_html = "".join(f"<td>{_render_text_line_html(c)}</td>" for c in cells)
        rows_html.append(f"<tr>{cells_html}</tr>")
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
    out = []
    i = 0
    formula_count = 0
    while i < len(lines):
        if _TABLE_LINE_RE.match(lines[i]):
            table_lines = []
            while i < len(lines) and _TABLE_LINE_RE.match(lines[i]):
                table_lines.append(lines[i])
                i += 1
            out.append(_rows_to_table_html(table_lines))
            continue
        if _FORMULA_LINE_RE.search(lines[i]):
            # "= " 계산식 바로 위에 화학식이 들어있는 줄들("OO의 양 (mg)" 라벨,
            # 여러 줄에 걸쳐 있을 수 있음)을 모두 끌어와 계산식과 한 덩어리로
            # 묶어 함께 가운데 정렬한다. 첫 번째 계산식 앞에서만 앞 문단과
            # 한 줄 띄워 구분한다.
            label_lines = []
            j = i - 1
            while (
                j >= 0
                and out
                and lines[j].strip()
                and not _TABLE_LINE_RE.match(lines[j])
                and not _FORMULA_LINE_RE.search(lines[j])
                and _FORMULA_TOKEN_RE.search(lines[j])
            ):
                label_lines.append(out.pop())
                j -= 1
            label_lines.reverse()
            block_parts = label_lines + [_render_text_line_html(lines[i])]
            formula_count += 1
            if formula_count == 1 and out:
                out.append("")
            out.append(f'<div class="formula-line">{"<br>".join(block_parts)}</div>')
            i += 1
            continue
        out.append(_render_text_line_html(lines[i]))
        i += 1
    return "<br>".join(out)


def _enrich_item_html(items):
    """계층형 항목(순도시험/정량법/확인시험/성상)의 bold/rest/text 필드에
    표시용 HTML 필드(bold_html/rest_html/text_html)를 덧붙인다."""
    for it in items:
        if "bold" in it:
            it["bold_html"] = _render_rich_html(it.get("bold", ""))
            it["rest_html"] = _render_rich_html(it.get("rest", ""))
        else:
            it["text_html"] = _render_rich_html(it.get("text", ""))
        if it.get("children"):
            _enrich_item_html(it["children"])


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

    # --- 1단계: 빈 문단을 걸러내고, 각 문단을 미리 분석해 둔다. ---------------
    flat = []
    for xml_bytes in section_xml_bytes_list:
        root = ET.fromstring(xml_bytes)
        # 최상위(hs:sec)의 직계 문단만 순회한다. `.//hp:p` 로 전체를 훑으면
        # 각주/텍스트상자 등에 중첩된 <hp:p> 까지 끼어들어 본문 중간에
        # 엉뚱한 줄바꿈이 섞여 들어가므로, 문서 흐름과 동일한 직계 자식만 사용한다.
        for p in root.findall("hp:p", NS):
            segments, has_subheader = _paragraph_segments(p, header_style_ids, subheader_style_ids, bold_charpr_ids)
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
            section = {"label": label, "text": text, "html": _render_rich_html(text)}
            if label in ("순도시험", "정량법"):
                items = _parse_numbered_hierarchy(text, bold_labels=True)
                if not items:
                    # "1)" 같은 번호가 전혀 없어도 "중금속  이 약의 가루 ~"
                    # 처럼 "라벨 + 설명" 한 문장뿐인 섹션이 있다(예: 자석).
                    # 이런 경우 그 라벨을 표시 없는 하위 항목 하나로 인식한다.
                    b, rest = _split_section_label(text)
                    if b and rest:
                        node = {"marker": "", "text": text, "children": [], "bold": b, "rest": rest}
                        ref_name = _detect_purity_reference(text)
                        if ref_name:
                            node["ref_name"] = ref_name
                        items = [node]
                if items:
                    _enrich_item_html(items)
                    section["items"] = items
            elif label == "확인시험":
                items = _parse_numbered_hierarchy(text)
                if items:
                    _enrich_item_html(items)
                    section["items"] = items
            elif label == "성상":
                items = _parse_seongsang_blocks(text)
                if len(items) >= 2:
                    _enrich_item_html(items)
                    section["items"] = items
            if "items" not in section:
                # "1)" 같은 목록 표시가 없어 계층화되지 않은 섹션이라도(예:
                # "대한민국약전 「두충」의 순도시험 1) 에 따른다." 한 문장뿐인
                # 경우), 다른 생약 참조는 감지해서 링크를 만들 수 있게 한다.
                ref_name = _detect_purity_reference(text)
                if ref_name:
                    section["ref_name"] = ref_name
            entry["sections"].append(section)
        current_section = None

    def close_entry():
        nonlocal entry, title_lines, state, current_section
        flush_section()
        if entry is not None:
            _finalize_title(entry, title_lines)
            entry["definition"] = entry["definition"].strip()
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
    return entries


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
        if "Contents/header.xml" in zf.namelist():
            header_xml_bytes = zf.read("Contents/header.xml")
            header_style_ids = _detect_named_char_style_ids(header_xml_bytes, "항목명")
            subheader_style_ids = _detect_named_char_style_ids(header_xml_bytes, "소항목명")
            italic_charpr_ids = _detect_italic_charpr_ids(header_xml_bytes)
            bold_charpr_ids = _detect_bold_charpr_ids(header_xml_bytes)

        # 스타일 이름표(예: "항목명")로 못 찾았거나 실제 본문 사용과 어긋날 수
        # 있으므로, 표준 항목명 어휘가 실제로 어떤 스타일을 쓰는지 본문에서
        # 직접 확인해 우선시한다 (문서마다 스타일 이름 표기가 제각각이기 때문).
        content_based_ids = _detect_header_style_ids_by_content(section_bytes)
        if content_based_ids:
            header_style_ids = content_based_ids
        elif not header_style_ids:
            header_style_ids = DEFAULT_HEADER_STYLE_IDS

    return parse_hwpx_bytes_sections(
        section_bytes, header_style_ids, subheader_style_ids, italic_charpr_ids, bold_charpr_ids
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
