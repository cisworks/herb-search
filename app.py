# -*- coding: utf-8 -*-
"""
생약 검색 웹앱.

실행 방법:
    pip install -r requirements.txt
    python app.py
그 다음 브라우저에서 http://127.0.0.1:5000 접속.

이 폴더 안의 모든 *.hwpx 파일을 자동으로 찾아 파싱해서 데이터베이스로 쓴다.
(가자.hwpx 대신 실제 생약(한약)규격집 hwpx 파일로 교체하거나 여러 개를
 함께 넣어두면 그 내용이 자동으로 반영된다. 앱을 재시작하면 다시 읽어온다.)
"""

import json
import re
from pathlib import Path
from urllib.parse import quote

from flask import Flask, Response, abort, jsonify, render_template, request

from parse_hwpx import parse_hwpx
from parse_sensory_pdf import build_sensory_entries
from parse_case_pdf import parse_case_pdf
from parse_hwpx import fix_private_use_chars
from parse_test_methods import parse_test_methods, read_test_method_image

BASE_DIR = Path(__file__).resolve().parent

# 일반시험법 "35. 생약시험법"(3. 생약시험법.hwpx). 생약 상세 화면의 이물/중금속/잔류농약/
# 이산화황/곰팡이독소/건조감량/회분/산불용성회분 항목이 이 문서의 해당 항목을 보여 준다.
TEST_METHOD_FILE = "3. 생약시험법.hwpx"

app = Flask(__name__)

ENTRIES = []  # 공정서(hwpx)에서 파싱된 생약 목록 (앱 시작 시 1회 로드)
SENSORY_ENTRIES = []  # 관능검사해설서(pdf)에서 파싱된 생약 목록
CASE_ENTRIES = []  # 관능검사 사례집(pdf)에서 파싱된 부적합/적합 사례 목록


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _text_has_keyword(norm_text: str, raw_keyword: str) -> bool:
    """정규화된 본문(norm_text) 안에 raw_keyword가 있는지 찾는다. "가지"는
    "가지고"(조사가 아니라 "가지다"의 활용형, 예: "이 약을 가지고 ~")에
    흔히 우연히 포함되므로, 그 경우만 제외하고 찾는다."""
    kw = _normalize(raw_keyword)
    if kw == "가지":
        return bool(re.search(r"가지(?!고)", norm_text))
    return kw in norm_text


def _source_tag(filename: str) -> str:
    """파일명으로 출처를 구분해 배지를 붙인다: 약전 -> KP, 생약(한약)규격집 -> KHP."""
    if "약전" in filename:
        return "KP"
    if "생규" in filename or "생약규격" in filename:
        return "KHP"
    return ""


_REF_BRACKET_RE = re.compile(r"[「｢]([^」｣]+)[」｣]")


def _resolve_purity_references(entries):
    """
    "대한민국약전 「두충」의 순도시험 ~에 따른다." 처럼 items에 심어둔
    ref_name(예: "두충")을 실제 품목 id로 바꿔 ref_id로 채운다.
    """
    name_to_id = {}
    for e in entries:
        if e.get("name_only") and e["name_only"] not in name_to_id:
            name_to_id[e["name_only"]] = e["id"]

    def walk(items):
        for it in items:
            ref_name = it.get("ref_name")
            if ref_name and ref_name in name_to_id:
                it["ref_id"] = name_to_id[ref_name]
            walk(it.get("children") or [])

    # 확인시험/정량법에도 "「인삼」의 정량법에 따라 시험한다." 처럼 다른 생약을 「 」로 가리키는
    # 곳이 있다. 순도시험의 ref_id(문장 속 첫 「 」 하나)와 달리, 이 두 항목은 문장 안의
    # 「생약명」 중 실제 품목 이름과 일치하는 것을 모두 모아 refs({이름: id})로 달아 두면
    # 화면에서 그 이름마다 링크를 건다. 자기 자신을 가리키는 이름은 링크하지 않는다.
    def collect_refs(text, own_name):
        refs = {}
        for m in _REF_BRACKET_RE.finditer(text or ""):
            name = m.group(1).strip()
            if name != own_name and name in name_to_id:
                refs[name] = name_to_id[name]
        return refs

    def walk_refs(items, own_name):
        for it in items:
            refs = collect_refs(it.get("text", ""), own_name)
            if refs:
                it["refs"] = refs
            walk_refs(it.get("children") or [], own_name)

    for e in entries:
        for s in e["sections"]:
            if s.get("items"):
                walk(s["items"])
            ref_name = s.get("ref_name")
            if ref_name and ref_name in name_to_id:
                s["ref_id"] = name_to_id[ref_name]
            if s.get("label") in ("확인시험", "정량법"):
                refs = collect_refs(s.get("text", ""), e.get("name_only"))
                if refs:
                    s["refs"] = refs
                if s.get("items"):
                    walk_refs(s["items"], e.get("name_only"))


def load_entries():
    # 3. 생약시험법.hwpx 는 생약 품목이 아니라 시험법 본문이라(아래 TEST_METHODS) 제외한다.
    hwpx_files = sorted(p for p in BASE_DIR.glob("*.hwpx") if p.name != TEST_METHOD_FILE)
    entries = []
    for f in hwpx_files:
        try:
            file_entries = parse_hwpx(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] {f} 파싱 실패: {exc}")
            continue
        tag = _source_tag(f.name)
        for e in file_entries:
            e["source_file"] = f.name
            e["source_tag"] = tag
        entries.extend(file_entries)
    for i, e in enumerate(entries):
        e["id"] = i
        e["_search_blob"] = _normalize(
            " ".join(
                filter(
                    None,
                    [
                        e.get("korean_name"),
                        e.get("name_only"),
                        e.get("hanja"),
                        e.get("english_name"),
                        e.get("latin_name"),
                    ],
                )
            )
        )
    _resolve_purity_references(entries)
    return entries, [f.name for f in hwpx_files]


def load_sensory_entries(start_id):
    """이 폴더의 *.pdf(관능검사해설서) 중 "사례집"이 파일명에 없는 것들을
    찾아 파싱한다("관능검사사례집.pdf"는 형식이 전혀 달라 load_case_entries
    가 따로 처리한다). id는 공정서 항목 다음부터 이어서 매겨, 두 목록을
    하나의 id 공간으로 조회할 수 있게 한다."""
    pdf_files = sorted(p for p in BASE_DIR.glob("*.pdf") if "사례집" not in p.name)
    entries = []
    for f in pdf_files:
        try:
            file_entries = build_sensory_entries(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] {f} 파싱 실패: {exc}")
            continue
        for e in file_entries:
            e["source_file"] = f.name
        entries.extend(file_entries)
    for i, e in enumerate(entries):
        e["id"] = start_id + i
        e["kind"] = "sensory"
        e["_search_blob"] = _normalize(
            " ".join(filter(None, [e.get("korean_name"), e.get("name_only"), e.get("hanja")]))
        )
    return entries, [f.name for f in pdf_files]


def load_case_entries():
    """이 폴더의 "...사례집....pdf"(관능검사 사례집)를 찾아 파싱한다.
    공정서/관능검사해설서와 달리 검색 결과에서 생약 하나당 여러 건(카테고리별)
    으로 나올 수 있어 ENTRY_BY_ID에는 넣지 않고, /api/search에서 herb_name으로
    직접 매칭한다."""
    case_files = sorted(BASE_DIR.glob("*사례집*.pdf"))
    entries = []
    for f in case_files:
        try:
            file_entries = parse_case_pdf(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] {f} 파싱 실패: {exc}")
            continue
        for e in file_entries:
            e["source_file"] = f.name
        entries.extend(file_entries)
    return entries, [f.name for f in case_files]


def load_test_methods():
    path = BASE_DIR / TEST_METHOD_FILE
    if not path.is_file():
        return {}
    try:
        return parse_test_methods(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] {path} 파싱 실패: {exc}")
        return {}


TEST_METHODS = load_test_methods()
ENTRIES, LOADED_FILES = load_entries()
SENSORY_ENTRIES, SENSORY_FILES = load_sensory_entries(start_id=len(ENTRIES))
CASE_ENTRIES, CASE_FILES = load_case_entries()


def _fix_private_use(obj):
    """PDF 에서 읽은 글에 섞인 사용자 영역 한자(피마자의 蓖 등)를 원래 한자로 되돌린다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = fix_private_use_chars(v) if isinstance(v, str) else _fix_private_use(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            obj[i] = fix_private_use_chars(v) if isinstance(v, str) else _fix_private_use(v)
    return obj


_fix_private_use(SENSORY_ENTRIES)
_fix_private_use(CASE_ENTRIES)
ENTRY_BY_ID = {e["id"]: e for e in ENTRIES + SENSORY_ENTRIES}


# 국가생약정보(nifds.go.kr) 공정서 생약 상세 페이지 대응표.
# nifds_herb_map.json 은 "생약명 -> [[기원종, selectedDmstcOfcmNo, selectedMdntfNo], ...]"
# 형태다. 사이트가 스크립트 접속을 막고 있어(notAllowBrower) 프로그램으로 자동
# 수집하지 않고, 사이트 목록 페이지(list.do)를 브라우저로 열어 확인한 값을 담았다.


def _load_json_map(filename):
    path = BASE_DIR / filename
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


NIFDS_MAP = _load_json_map("nifds_herb_map.json")

# 상세 페이지의 "사진정보 > 약재" 탭 사진. 탭이 불러오는 사진 조각 페이지는
# 첫 사진이 두 번 나오고 꾸밀 수 없어서, 사진 목록만 가져와 /photos/<번호> 에서
# 직접 보여 준다. nifds_photo_map.json ("선택번호_기원번호" -> 사진 번호)과
# nifds_photo_list.json ("사진 번호" -> [사진묶음번호, 장수] 또는 [[묶음번호, 순번], ...])은
# 상세 페이지를 브라우저로 열어 확인한 값이다. 사진 자체는 국가생약정보 서버의 것을
# 그대로 불러온다.
NIFDS_PHOTO_MAP = _load_json_map("nifds_photo_map.json")
NIFDS_PHOTO_LIST = _load_json_map("nifds_photo_list.json")
NIFDS_PREVIEW_URL = "https://nifds.go.kr/nhmi/preview.do?flgrpNo={flgrp}&sn={sn}"
NIFDS_THUMB_URL = NIFDS_PREVIEW_URL + "&thumbnail=true&maxWidth=800&maxHeight=800"

# 국가생약정보 HPTLC/HPLC 조회(analscase/hptlc, analscase/hplc) 대응표:
# "생약명 -> [[기원종, 번호], ...]". 만든 방법은 위 nifds_herb_map.json 과 같다
# (각 목록 페이지를 브라우저로 열어 확인).
NIFDS_HPTLC_URL = "https://nifds.go.kr/nhmi/analscase/hptlc/view.do?selectedHptlcNo={no}"
NIFDS_HPTLC_MAP = _load_json_map("nifds_hptlc_map.json")
NIFDS_HPLC_URL = "https://nifds.go.kr/nhmi/analscase/hplc/view.do?selectedHplcNo={no}"
NIFDS_HPLC_MAP = _load_json_map("nifds_hplc_map.json")

# 국가생약정보 생약 상세 페이지의 "공정서 시험사례" 오른쪽 "미리보기"가 여는 PDF 뷰어
# 주소. PDF 번호(flgrpNo)가 생약마다 규칙 없이 달라서 nifds_exam_case_map.json
# ("생약명 -> flgrpNo")에 생약별로 모아 두었다. 기원종이 여러 개인 생약도 같은
# 파일을 쓴다. 만든 방법은 위 대응표들과 같다(상세 페이지를 브라우저로 열어 확인).
NIFDS_EXAM_CASE_URL = (
    "https://nifds.go.kr/nhmi/js/pdfjs/web/viewer.jsp"
    "?file=%2fnhmi%2fpreview.do%3fflgrpNo%3d{no}%26sn%3d1"
)
NIFDS_EXAM_CASE_MAP = _load_json_map("nifds_exam_case_map.json")

# 국가생약정보 "한약재 품질표준화 연구사업단 자료"(srcbk/crshm) 목록의 "미리보기"가
# 여는 PDF 뷰어 주소. 생약마다 PDF 번호(flgrpNo)가 달라 nifds_crshm_map.json
# ("생약명 -> flgrpNo")에 모아 두었다(목록 페이지를 브라우저로 열어 확인, 84개).
NIFDS_CRSHM_URL = (
    "https://nifds.go.kr/nhmi/js/pdfjs/web/viewer.jsp"
    "?file=%2fnhmi%2fdownload.do%3fflgrpNo%3d{no}%26sn%3d1"
)
NIFDS_CRSHM_MAP = _load_json_map("nifds_crshm_map.json")

# 국가생약정보 "생약 감별자료집"(analscase/dscrm) 목록의 "미리보기" PDF 뷰어 주소.
# nifds_dscrm_map.json ("생약명 -> flgrpNo", 50개)은 목록 페이지를 브라우저로 열어
# 확인한 값이다. 방풍/식방풍/해방풍처럼 한 PDF를 같이 쓰는 생약은 번호가 같다.
NIFDS_DSCRM_URL = (
    "https://nifds.go.kr/nhmi/js/pdfjs/web/viewer.jsp"
    "?file=%2fnhmi%2fdownload.do%3fflgrpNo%3d{no}%26sn%3d1"
)
NIFDS_DSCRM_MAP = _load_json_map("nifds_dscrm_map.json")

# 국가생약정보 생약 상세 페이지의 "표본정보" 탭(증거표본 목록)에서 첫 번째 행
# "증거표본번호" 링크가 여는 화면 주소. 탭 목록은 식물(기원종)별이라
# nifds_specimen_map.json ("선택번호_기원번호" -> [식물 번호, 첫 증거표본번호 또는 null])에
# 모아 두었다(상세 페이지와 그 탭 목록을 브라우저로 열어 확인).
NIFDS_SPECIMEN_URL = (
    "https://nifds.go.kr/nhmi/prslf/prslfspcmn/view.ajax"
    "?selectedPrslfspcmnNo={no}&selectedTaxon={taxon}"
)
NIFDS_SPECIMEN_MAP = _load_json_map("nifds_specimen_map.json")

# 국가생약정보 구성성분정보(analscase/hbdcirdntAnals)는 생약별 상세 페이지가 없고
# 화합물을 한 줄씩 나열한 목록이라, 목록을 생약명으로 검색한 결과(searchText)를
# 해당 생약의 페이지로 연다. 이 사이트 검색은 생약명 부분 일치라 "지황"으로 찾으면
# "생지황"/"숙지황" 행도 같이 나온다. nifds_ingredient_herbs.json 은 이 목록에
# 자료가 있는 생약명(한글 이름만)이다.
NIFDS_INGREDIENT_URL = "https://nifds.go.kr/nhmi/analscase/hbdcirdntAnals/list.do?searchText={q}"
NIFDS_INGREDIENT_HERBS = set(_load_json_map("nifds_ingredient_herbs.json"))


# "한약(생약) 중 유전자 기원 감별 정보자료집"(유전자기원감별정보집.pdf)은 글자 정보가
# 없는 그림 PDF라서, "4. 품목별 감별사례"의 품목별 시작/끝 쪽(0부터 세는 PDF 쪽
# 번호)을 목차를 보고 gene_case_pages.json("생약명 -> [시작, 끝]")에 정리해 두었다.
# 화면에서는 /api/sensory_pdf 로 이 구간만 잘라 보여 준다.
GENE_CASE_FILE = "유전자기원감별정보집.pdf"
GENE_CASE_PAGES = _load_json_map("gene_case_pages.json")


def _lookup_key(table, name_only):
    """"사프란 번홍화"처럼 이명이 같이 붙은 이름은 첫 단어("사프란")로 한 번 더 찾는다."""
    return name_only if name_only in table else (name_only.split() or [""])[0]


# ---- 동음 생약(이름은 같고 한자/기원이 다른 생약) -------------------------------------
# 국가생약정보 연결 정보는 거의 모두 "생약명"으로 찾기 때문에, 이름이 같은 생약이 둘 있으면
# 서로의 정보가 섞인다. 지금은 "진피"가 유일하다: 대한민국약전(KP)의 진피(陳皮, 귤나무 열매껍질)와
# 대한민국약전외한약(생약)규격집(KHP)의 진피(秦皮, 물푸레나무 껍질). 이런 생약은 한자(와 기원)로
# 구분해 아래 표에 항목별 연결 정보를 직접 적어 둔다(국가생약정보 사이트에서 각 상세 페이지,
# HPTLC/HPLC 조회, 성분정보 목록의 기원종 열을 열어 확인한 값).
#   origins   : nifds_herb_map.json 의 기원종(국명) - 사진정보/표본정보 연결에 쓸 기원종
#   hptlc/hplc: [[목록에 보일 이름(학명), 번호], ...] (없으면 빈 목록)
#   exam_case : 공정서 시험사례 PDF 번호      crshm: 품질표준화연구 PDF 번호(없으면 None)
#   ingredient: 성분정보 목록 검색어. 성분정보 목록은 생약명으로만 찾으면 두 진피가 한꺼번에
#               나오므로, 기원종 학명으로 검색해 해당 기원의 성분만 나오게 한다.
# 표에 없는 항목(생약감별자료집, 유전자감별사례 등)은 이 생약들에게는 연결하지 않는다.
HOMONYM_OVERRIDES = {
    ("진피", "陳皮"): {
        "origins": ["귤나무"],
        "hptlc": [["Citrus reticulata Blanco", 191], ["Citrus unshiu Markovich", 192]],
        "hplc": [["Citrus unshiu Markovich, Citrus reticulata Blanco", 21]],
        "exam_case": 1048,
        "crshm": 1297,
        "ingredient": "Citrus unshiu",
    },
    ("진피", "秦皮"): {
        "origins": ["물푸레나무"],
        "hptlc": [],
        "hplc": [],
        "exam_case": 1049,
        "crshm": None,
        "ingredient": "Fraxinus rhynchophylla",
    },
}


def _find_homonym_names():
    hanjas = {}
    for e in ENTRIES:
        hanjas.setdefault(e["name_only"], set()).add(e["hanja"])
    return {name for name, hs in hanjas.items() if len(hs) > 1}


HOMONYM_NAMES = _find_homonym_names()


def _homonym(name_only, hanja):
    """동음 생약이면 연결 정보 표(없으면 빈 표 - 아무 것도 연결하지 않음)를, 아니면 None 을 돌려준다."""
    if name_only not in HOMONYM_NAMES:
        return None
    return HOMONYM_OVERRIDES.get((name_only, hanja), {})


def _nifds_origins(name_only, hanja):
    """국가생약정보 상세 페이지 번호 목록 [(기원종, dmstc, mdntf), ...]. 동음 생약은 자기 기원만."""
    origins = NIFDS_MAP.get(_lookup_key(NIFDS_MAP, name_only), [])
    ov = _homonym(name_only, hanja)
    if ov is None:
        return origins
    return [o for o in origins if o[0] in ov.get("origins", [])]


def nifds_links(name_only, hanja=""):
    """생약명으로 국가생약정보 "사진정보" 주소들을 만든다. 상세 페이지의 사진정보
    탭은 주소로 바로 열 수 없어서, 약재 사진만 모아 보여 주는 우리 쪽 페이지
    (/photos/<번호>)를 연다. 약재 사진이 없는 생약은 빈 목록을 돌려줘서 버튼이
    나오지 않는다. 기원종이 여러 개여도 사진정보는 같으므로 기원종 구분 없이
    첫 번째 하나만 돌려준다."""
    for origin, dmstc, mdntf in _nifds_origins(name_only, hanja):
        drgnm = NIFDS_PHOTO_MAP.get(f"{dmstc}_{mdntf}")
        if str(drgnm) in NIFDS_PHOTO_LIST:
            return [{"origin": "", "url": f"/photos/{drgnm}"}]
    return []


def _analscase_links(table, url_template, name_only):
    return [
        {"origin": origin, "url": url_template.format(no=no)}
        for origin, no in table.get(_lookup_key(table, name_only), [])
    ]


def hptlc_links(name_only, hanja=""):
    """생약명으로 국가생약정보 HPTLC 조회 페이지 주소들을 만든다(HPTLC 자료가
    있는 생약만 나온다. 기원종이 여러 개면 여러 개)."""
    ov = _homonym(name_only, hanja)
    if ov is not None:
        return [{"origin": o, "url": NIFDS_HPTLC_URL.format(no=n)} for o, n in ov.get("hptlc", [])]
    return _analscase_links(NIFDS_HPTLC_MAP, NIFDS_HPTLC_URL, name_only)


def hplc_links(name_only, hanja=""):
    """생약명으로 국가생약정보 HPLC 조회 페이지 주소들을 만든다(HPLC 자료가
    있는 생약만 나온다)."""
    ov = _homonym(name_only, hanja)
    if ov is not None:
        return [{"origin": o, "url": NIFDS_HPLC_URL.format(no=n)} for o, n in ov.get("hplc", [])]
    return _analscase_links(NIFDS_HPLC_MAP, NIFDS_HPLC_URL, name_only)


def exam_case_links(name_only, hanja=""):
    """생약명으로 국가생약정보 "공정서 시험사례" PDF 미리보기 주소를 만든다
    (자료가 있는 생약만 나온다)."""
    ov = _homonym(name_only, hanja)
    if ov is not None:
        no = ov.get("exam_case")
    else:
        no = NIFDS_EXAM_CASE_MAP.get(_lookup_key(NIFDS_EXAM_CASE_MAP, name_only))
    if no is None:
        return []
    return [{"origin": "", "url": NIFDS_EXAM_CASE_URL.format(no=no)}]


def gene_case(name_only, hanja=""):
    """유전자 기원 감별 자료집에서 해당 생약의 사례 구간(없으면 None)."""
    if _homonym(name_only, hanja) is not None:
        return None  # 동음 생약은 자료집의 어느 쪽인지 구분할 수 없어 연결하지 않는다
    pages = GENE_CASE_PAGES.get(_lookup_key(GENE_CASE_PAGES, name_only))
    if pages is None or not (BASE_DIR / GENE_CASE_FILE).is_file():
        return None
    return {"source_file": GENE_CASE_FILE, "page_start": pages[0], "page_end": pages[1]}


def crshm_links(name_only, hanja=""):
    """생약명으로 "품질표준화 연구사업단 자료" PDF 미리보기 주소를 만든다(자료가
    있는 생약만 나온다)."""
    ov = _homonym(name_only, hanja)
    if ov is not None:
        no = ov.get("crshm")
    else:
        no = NIFDS_CRSHM_MAP.get(_lookup_key(NIFDS_CRSHM_MAP, name_only))
    if no is None:
        return []
    return [{"origin": "", "url": NIFDS_CRSHM_URL.format(no=no)}]


def dscrm_links(name_only, hanja=""):
    """생약명으로 "생약 감별자료집" PDF 미리보기 주소를 만든다(자료가 있는 생약만
    나온다)."""
    if _homonym(name_only, hanja) is not None:
        return []  # 동음 생약은 자료집의 어느 쪽인지 구분할 수 없어 연결하지 않는다
    no = NIFDS_DSCRM_MAP.get(_lookup_key(NIFDS_DSCRM_MAP, name_only))
    if no is None:
        return []
    return [{"origin": "", "url": NIFDS_DSCRM_URL.format(no=no)}]


def specimen_links(name_only, hanja=""):
    """생약명으로 "표본정보" 탭 첫 증거표본 화면 주소를 만든다. 기원종이 여러 개면
    표본이 있는 첫 번째 기원종의 것을 쓰고, 표본이 하나도 없는 생약은 빈 목록이다."""
    for _origin, dmstc, mdntf in _nifds_origins(name_only, hanja):
        taxon, no = NIFDS_SPECIMEN_MAP.get(f"{dmstc}_{mdntf}") or (None, None)
        if no:
            return [{"origin": "", "url": NIFDS_SPECIMEN_URL.format(no=no, taxon=taxon)}]
    return []


def ingredient_links(name_only, hanja=""):
    """생약명으로 국가생약정보 구성성분정보 페이지 주소를 만든다(자료가 있는
    생약만 나온다. 기원종과 상관없이 생약명 검색 결과 하나로 열린다). 동음 생약은
    생약명으로 찾으면 서로의 성분이 섞이므로 기원종 학명으로 검색한다."""
    ov = _homonym(name_only, hanja)
    if ov is not None:
        query = ov.get("ingredient")
        if not query:
            return []
        return [{"origin": "", "url": NIFDS_INGREDIENT_URL.format(q=quote(query))}]
    key = _lookup_key(NIFDS_INGREDIENT_HERBS, name_only)
    if key not in NIFDS_INGREDIENT_HERBS:
        return []
    return [{"origin": "", "url": NIFDS_INGREDIENT_URL.format(q=quote(key))}]


def summary(e):
    return {
        "id": e["id"],
        "kind": e.get("kind", "official"),
        "korean_name": e["korean_name"],
        "name_primary": e.get("name_primary", ""),
        "synonym_name": e.get("synonym_name", ""),
        "name_only": e["name_only"],
        "hanja": e["hanja"],
        "english_name": e.get("english_name", ""),
        "latin_name": e.get("latin_name", ""),
        "source_tag": e.get("source_tag", ""),
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        total_count=len(ENTRIES),
        loaded_files=LOADED_FILES,
        sensory_count=len(SENSORY_ENTRIES),
    )


@app.route("/api/list")
def api_list():
    items = sorted(ENTRIES, key=lambda e: e["korean_name"])
    return jsonify([summary(e) for e in items])


def _search(entries, q):
    exact, starts, contains = [], [], []
    for e in entries:
        blob = e["_search_blob"]
        name_norm = _normalize(e["name_only"])
        if q == name_norm or q == _normalize(e["korean_name"]):
            exact.append(e)
        elif name_norm.startswith(q) or _normalize(e.get("english_name", "")).startswith(q):
            starts.append(e)
        elif q in blob:
            contains.append(e)
    return exact + starts + contains


def _search_case_entries(q):
    """관능검사 사례집에서 herb_name에 q가 포함된 사례를 찾아 카테고리별로
    묶는다. 카테고리는 1~4 순서로, 그 안에서는 생약명 가나다순으로 정렬한다."""
    matches = [c for c in CASE_ENTRIES if q in _normalize(c["herb_name"])]
    matches.sort(key=lambda c: (c["category"], c["herb_name"]))
    grouped = []
    last_category = None
    for c in matches:
        if c["category"] != last_category:
            grouped.append({"category": c["category"], "label": c["category_label"], "items": []})
            last_category = c["category"]
        grouped[-1]["items"].append(
            {
                "herb_name": c["herb_name"],
                "source_file": c["source_file"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
            }
        )
    return grouped


@app.route("/api/search")
def api_search():
    q = _normalize(request.args.get("q", ""))
    if not q:
        return jsonify({"official": [], "sensory": [], "case": []})

    official = _search(ENTRIES, q)[:50]
    sensory = _search(SENSORY_ENTRIES, q)[:50]
    case = _search_case_entries(q)
    return jsonify(
        {
            "official": [summary(e) for e in official],
            "sensory": [summary(e) for e in sensory],
            "case": case,
        }
    )


# 이름이 같은 항목(section) 자체가 있는지로 찾는 시험항목
SECTION_TEST_ITEMS = ("확인시험", "순도시험", "정량법", "엑스함량", "정유함량")


@app.route("/api/test_item_search")
def api_test_item_search():
    """시험항목(확인시험/순도시험/정량법/엑스함량/정유함량 및 순도시험의 하위
    항목인 이물·변패·잔류농약·납·비소·수은·카드뮴·이산화황·벤조피렌·곰팡이독소
    등)으로 공정서 품목을 찾는다. SECTION_TEST_ITEMS 는 그 이름의
    항목(section)이 있는지로, 그 외에는 순도시험 항목의 본문에 그 낱말들
    중 하나라도 나오는지로 찾는다("이물시험"처럼 "이물" 뿐 아니라 "줄기"/
    "꽃대" 등 여러 낱말 중 하나만 있어도 해당하는 경우가 있어, item 파라미터는
    쉼표로 구분된 여러 낱말을 받을 수 있다)."""
    raw = (request.args.get("item") or "").strip()
    if not raw:
        return jsonify([])
    needles_raw = [w.strip() for w in raw.split(",") if w.strip()]
    if not needles_raw:
        return jsonify([])

    matches = []
    if len(needles_raw) == 1 and needles_raw[0] in SECTION_TEST_ITEMS:
        item = needles_raw[0]
        for e in ENTRIES:
            if any(s["label"] == item for s in e["sections"]):
                matches.append(e)
    else:
        for e in ENTRIES:
            for s in e["sections"]:
                if s["label"] == "순도시험":
                    norm_text = _normalize(s.get("text", ""))
                    if any(_text_has_keyword(norm_text, w) for w in needles_raw):
                        matches.append(e)
                        break
    matches.sort(key=lambda e: e["korean_name"])
    return jsonify([summary(e) for e in matches])


@app.route("/api/item/<int:item_id>")
def api_item(item_id):
    e = ENTRY_BY_ID.get(item_id)
    if e is None:
        return jsonify({"error": "not found"}), 404
    if e.get("kind") == "sensory":
        return jsonify(
            {
                "id": e["id"],
                "kind": "sensory",
                "korean_name": e["korean_name"],
                "name_only": e["name_only"],
                "hanja": e["hanja"],
                "source_tag": e.get("source_tag", ""),
                "html": e.get("html", ""),
                "source_file": e.get("source_file", ""),
                "page_start": e.get("page_start", 0),
                "page_end": e.get("page_end", 0),
            }
        )
    return jsonify(
        {
            "id": e["id"],
            "kind": "official",
            "korean_name": e["korean_name"],
            "name_primary": e.get("name_primary", ""),
            "synonym_name": e.get("synonym_name", ""),
            "synonym_html": e.get("synonym_html", ""),
            "name_only": e["name_only"],
            "hanja": e["hanja"],
            "english_name": e["english_name"],
            "latin_name": e["latin_name"],
            "definition": e["definition"],
            "definition_parts": e.get("definition_parts", []),
            "sections": e["sections"],
            "source_tag": e.get("source_tag", ""),
            "nifds_links": nifds_links(e["name_only"], e["hanja"]),
            "hptlc_links": hptlc_links(e["name_only"], e["hanja"]),
            "hplc_links": hplc_links(e["name_only"], e["hanja"]),
            "ingredient_links": ingredient_links(e["name_only"], e["hanja"]),
            "exam_case_links": exam_case_links(e["name_only"], e["hanja"]),
            "crshm_links": crshm_links(e["name_only"], e["hanja"]),
            "dscrm_links": dscrm_links(e["name_only"], e["hanja"]),
            "specimen_links": specimen_links(e["name_only"], e["hanja"]),
            "gene_case": gene_case(e["name_only"], e["hanja"]),
            "test_methods": sorted(TEST_METHODS),
        }
    )


@app.route("/api/test_method/<key>")
def api_test_method(key):
    """3. 생약시험법.hwpx 에서 뽑아 둔 항목 하나(제목과 계층 구조 HTML)."""
    data = TEST_METHODS.get(key)
    if data is None:
        abort(404)
    return jsonify({"key": key, **data})


@app.route("/api/test_method_image/<name>")
def api_test_method_image(name):
    """3. 생약시험법.hwpx 안의 그림(이산화황 장치 그림 등)."""
    if not re.fullmatch(r"\w+", name):
        abort(404)
    path = BASE_DIR / TEST_METHOD_FILE
    found = read_test_method_image(path, name) if path.is_file() else None
    if found is None:
        abort(404)
    data, mime = found
    return Response(data, mimetype=mime, headers={"Cache-Control": "public, max-age=86400"})


PHOTO_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin: 0; padding: 24px 12px; background: #f4f6f4; font-family: "Malgun Gothic", sans-serif; text-align: center; color: #555; }}
  .count {{ font-size: 0.8rem; margin-bottom: 8px; }}
  .photo {{ margin: 0 auto 36px; }}
  .photo img {{ max-width: 100%; height: auto; border-radius: 6px; box-shadow: 0 1px 6px rgba(0,0,0,.2); }}
</style></head><body>
<div class="count">※ Total : {total}</div>
{photos}
</body></html>"""


@app.route("/photos/<int:no>")
def photos(no):
    """국가생약정보 "사진정보 > 약재" 사진을 가운데 정렬하고 사진마다 위아래 간격을
    둔 한 페이지로 보여 준다(사진에 링크는 걸지 않는다)."""
    spec = NIFDS_PHOTO_LIST.get(str(no))
    if spec is None:
        abort(404)
    if spec and isinstance(spec[0], list):
        pairs = spec
    else:
        pairs = [[spec[0], sn] for sn in range(1, spec[1] + 1)]
    items = "".join(
        '<div class="photo"><img src="{}" loading="lazy" alt="약재 사진 {}"></div>'.format(
            NIFDS_THUMB_URL.format(flgrp=f, sn=s), i
        )
        for i, (f, s) in enumerate(pairs, 1)
    )
    return PHOTO_PAGE.format(title="사진정보", total=len(pairs), photos=items)


_CASE_PAGE_NOISE_RE = re.compile(r"한약\(생약\)\s*관능검사\s*사례집|Ministry of Food and Drug Safety|\d+")


def _is_blank_case_page(page):
    """사례집 페이지에 머리글("한약(생약) 관능검사 사례집"), 바닥글(Ministry of Food and Drug
    Safety), 쪽번호 외에 아무 글자가 없으면 빈 페이지다."""
    return not _CASE_PAGE_NOISE_RE.sub("", page.get_text()).strip()


@app.route("/api/sensory_pdf")
def api_sensory_pdf():
    """관능검사해설서 pdf에서 해당 품목의 페이지 구간만 잘라 그대로(벡터 그대로)
    돌려준다. 화면에 <iframe>으로 띄우면 브라우저 내장 PDF 뷰어의 확대/축소가
    그 안에서만 적용되고, 이미지로 미리 그려서 보내는 것과 달리 아무리
    확대해도 원본과 같은 화질을 유지한다."""
    import pymupdf as fitz

    filename = request.args.get("file", "")
    start = request.args.get("start", type=int)
    end = request.args.get("end", type=int)
    if start is None or end is None or not filename:
        abort(400)
    pdf_path = (BASE_DIR / filename).resolve()
    if pdf_path.parent != BASE_DIR.resolve() or not pdf_path.is_file():
        abort(404)
    src = fitz.open(str(pdf_path))
    try:
        if start < 0 or end >= len(src) or start > end:
            abort(404)
        pages = list(range(start, end + 1))
        if "사례집" in filename:
            # 관능검사 사례집은 한 사례가 앞쪽 페이지 한 장이고 다음 장이 머리글/바닥글만 있는 빈 연결
            # 페이지(후박, 황련 등)인 경우가 있다. 그런 빈 페이지는 빼고 보낸다(전부 빈 경우만 그대로 둔다).
            content_pages = [p for p in pages if not _is_blank_case_page(src[p])]
            pages = content_pages or pages
        out = fitz.open()
        try:
            for p in pages:
                out.insert_pdf(src, from_page=p, to_page=p)
            pdf_bytes = out.tobytes()
        finally:
            out.close()
    finally:
        src.close()
    return Response(pdf_bytes, mimetype="application/pdf")


if __name__ == "__main__":
    print(f"[생약검색] {len(LOADED_FILES)}개 hwpx 파일에서 {len(ENTRIES)}개 품목을 불러왔습니다: {LOADED_FILES}")
    print(f"[생약검색] {len(SENSORY_FILES)}개 pdf 파일에서 {len(SENSORY_ENTRIES)}개 품목을 불러왔습니다: {SENSORY_FILES}")
    print(f"[생약검색] {len(CASE_FILES)}개 pdf 파일에서 {len(CASE_ENTRIES)}개 사례를 불러왔습니다: {CASE_FILES}")
    app.run(debug=True)
