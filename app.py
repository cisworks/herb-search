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

import functools
import hashlib
import json
import os
import pickle
import re
import sys
import threading
from pathlib import Path
from urllib.parse import quote, urlencode

from flask import Flask, Response, abort, jsonify, render_template, request, url_for

import sensory_photos
from parse_hwpx import finalize_method_section, parse_hwpx, parse_method_hwpx
from parse_sensory_pdf import build_sensory_entries
from parse_case_pdf import parse_case_pdf
from parse_hwpx import fix_private_use_chars
from parse_test_methods import parse_test_methods, read_test_method_image

BASE_DIR = Path(__file__).resolve().parent

# 일반시험법 "35. 생약시험법"(3. 생약시험법.hwpx). 생약 상세 화면의 이물/중금속/잔류농약/
# 이산화황/곰팡이독소/건조감량/회분/산불용성회분 항목이 이 문서의 해당 항목을 보여 준다.
TEST_METHOD_FILE = "3. 생약시험법.hwpx"

# 생약제제(엑스, 과립, 환, 산 등) 품목집. 생약 품목(ENTRIES)과 섞이지 않게 따로 불러와 "생약제제 검색" 탭에서만 쓴다.
PREP_FILE_NAMES = (
    "4. 약전(각조 제2부 생약제제).hwpx",
    "5. 생규집(각조 제2부).hwpx",
    "6. 생규집(각조 제3부).hwpx",
)
PREP_ID_START = 100000  # 생약제제 품목 id 는 생약/관능검사해설서 id 와 겹치지 않게 큰 수부터 매긴다

# 생약제제에 적용하는 확인시험법·함량시험법(생약/생약제제 이름별). 품목집이 아니라 시험법 모음이라 load_entries 에서 빼고,
# "시험항목 검색"의 확인시험/정량법 목록과, 같은 이름의 공정서 생약 상세("확인시험 (생약제제에 적용)" 등)에 쓴다.
METHOD_FILE_NAME = "7. 생규집(확인, 함량).hwpx"
METHOD_ID_START = 200000


# ---- 원본 파일 파싱 결과 캐시 -------------------------------------------------------
# hwpx/pdf 를 서버를 켤 때마다 파싱하면 약 45초가 걸린다(관능검사해설서 pdf 약 27초, hwpx 약 14초). 파싱 결과를 .parse_cache/ 에
# pickle 로 저장해 두고, 원본 파일(크기·수정 시각)과 파서 코드(parse_*.py)가 그대로이면 그 파일을 읽어 쓴다(몇 초).
# 원본이나 파서가 바뀌면 키가 달라져 자동으로 다시 파싱하고, 같은 파일의 옛 캐시는 지운다. 캐시를 못 쓰거나 깨졌으면 그냥 파싱한다.
# 강제로 다시 파싱하려면 .parse_cache 폴더를 지우면 된다. 이 폴더는 git 에 올리지 않는다(.gitignore).
PARSE_CACHE_DIR = BASE_DIR / ".parse_cache"
_PARSER_FILES = ("parse_hwpx.py", "parse_sensory_pdf.py", "parse_case_pdf.py", "parse_test_methods.py")


def _parser_signature():
    h = hashlib.sha1()
    h.update(f"py{sys.version_info[:2]}".encode())
    for name in _PARSER_FILES:
        try:
            h.update((BASE_DIR / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()


_PARSER_SIG = _parser_signature()


def _cached_parse(label, path, func, *args, **kwargs):
    """func(path, *args, **kwargs) 의 결과를 캐시해서 돌려준다. label 은 같은 파일을 다른 방식으로 파싱하는 경우(예: prep=True)를 구분한다.
    돌려주는 값은 호출마다 새로 만들어진 객체라(캐시를 읽거나 방금 파싱한 것) 호출한 쪽이 마음대로 고쳐도 캐시에는 영향이 없다."""
    try:
        st = path.stat()
        prefix = hashlib.sha1(f"{path.name}|{label}".encode()).hexdigest()[:10]
        key = hashlib.sha1(f"{st.st_size}|{st.st_mtime_ns}|{_PARSER_SIG}".encode()).hexdigest()[:16]
        cache_file = PARSE_CACHE_DIR / f"{prefix}-{key}.pkl"
    except OSError:
        return func(path, *args, **kwargs)
    if cache_file.is_file():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:  # noqa: BLE001  깨진 캐시는 무시하고 다시 파싱한다
            pass
    result = func(path, *args, **kwargs)
    try:
        PARSE_CACHE_DIR.mkdir(exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cache_file)
        for old in PARSE_CACHE_DIR.glob(f"{prefix}-*.pkl"):  # 같은 파일의 옛 버전 캐시 정리
            if old != cache_file:
                old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001  저장 실패(읽기 전용 폴더 등)는 캐시 없이 계속
        print(f"[알림] 파싱 캐시를 저장하지 못했습니다({path.name}): {exc}")
    return result


def _warn_missing(label, name):
    """서버를 켤 때 필요한 원본 파일(hwpx/pdf)이 폴더에 없으면 콘솔에 알린다. hwpx/pdf 는 .gitignore 에 있어 git 으로
    받아지지 않으므로, 새 컴퓨터에서 서버를 켰을 때 목록이 비는 이유를 바로 알 수 있게 한다.
    예: [경고] 생약제제 파일이 없습니다: 4. 약전(각조 제2부 생약제제).hwpx"""
    print(f"[경고] {label} 파일이 없습니다: {name}")

app = Flask(__name__)

# ---- 응답 압축(gzip) -------------------------------------------------------
# 첫 화면(약 124KB)과 전체 목록 JSON(생약 252KB, 생약제제 204KB)은 글자만 있어 gzip 으로 1/4~1/15 로 줄어든다.
# 외부 패키지(flask-compress) 없이 표준 라이브러리만 쓴다. 이미 압축된 그림/PDF 는 건드리지 않고, 작은 응답도 그대로 둔다.
import gzip

_GZIP_TYPES = ("text/html", "application/json", "text/css", "application/javascript", "text/javascript", "image/svg+xml")


@app.after_request
def _gzip_response(resp):
    if (
        resp.status_code != 200
        or resp.direct_passthrough
        or "Content-Encoding" in resp.headers
        or "gzip" not in request.headers.get("Accept-Encoding", "")
        or not resp.mimetype.startswith(_GZIP_TYPES)
    ):
        return resp
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp


def _cached_json(payload, max_age=600):
    """바뀌지 않는 큰 목록 응답: 브라우저가 잠시(기본 10분) 다시 받지 않고, 그 뒤에는 ETag 로 '안 바뀜(304)'만 확인한다.
    원본 파일을 바꾸고 서버를 다시 켜면 내용이 달라져 ETag 도 바뀌므로 새 목록이 곧 반영된다."""
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = f"public, max-age={max_age}"
    resp.add_etag()
    return resp.make_conditional(request)

@app.context_processor
def _asset_helpers():
    """템플릿에서 {{ asset('app.css') }} 로 static 파일 주소를 얻는다. 파일 수정 시각을 ?v= 로 붙여서, 서버에서 git pull 로 파일이
    바뀌면 주소가 달라져 브라우저가 옛 CSS/JS 캐시를 쓰지 않고 새 파일을 받는다."""
    def asset(filename):
        try:
            v = int((Path(app.static_folder) / filename).stat().st_mtime)
        except OSError:
            v = 0
        return url_for("static", filename=filename, v=v)
    return {"asset": asset}


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
    # "[별표 N] ... (제2026-44호).hwpx" 는 대한민국약전 전체(통칙·각조·시험법) 문서로 약전 검색(/kp)에서만 쓰므로 제외한다.
    hwpx_files = sorted(
        p
        for p in BASE_DIR.glob("*.hwpx")
        if p.name not in (TEST_METHOD_FILE, METHOD_FILE_NAME) and p.name not in PREP_FILE_NAMES and not p.name.startswith("[별표")
    )
    entries = []
    if not hwpx_files:
        _warn_missing("공정서(생약)", "*.hwpx (예: 1. 약전(각조 제2부).hwpx, 2. 생규집(각조 제1부).hwpx)")
    for f in hwpx_files:
        try:
            file_entries = _cached_parse("hwpx", f, parse_hwpx)
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
    if not pdf_files:
        _warn_missing("관능검사해설서", "*.pdf (예: 관능검사해설서(2022).pdf)")
    for f in pdf_files:
        try:
            file_entries = _cached_parse("sensory", f, build_sensory_entries)
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


_PREP_IMG_RE = re.compile(r"\[\[IMG:(image\d+)\]\]")


def _inject_prep_images(obj, file_idx):
    """생약제제 본문 속 그림 자리 표시("[[IMG:image3]]")를 실제 그림 태그로 바꾼다(html 필드). html 이 아닌 글(text)에서는 지운다."""
    img_tag = lambda m: (  # noqa: E731
        f'<img class="prep-fig" src="/api/prep_image/{file_idx}/{m.group(1)}" alt="크로마토그램" loading="lazy">'
    )
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                if "[[IMG:" in v:
                    obj[k] = _PREP_IMG_RE.sub(img_tag, v) if k.endswith("html") else _PREP_IMG_RE.sub("", v)
            else:
                _inject_prep_images(v, file_idx)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                if "[[IMG:" in v:
                    obj[i] = _PREP_IMG_RE.sub("", v)
            else:
                _inject_prep_images(v, file_idx)


def _mark_recipe_tables(obj):
    """제법의 "생약명 | 분량" 표에 recipe-table 클래스를 붙여 화면에서 첫 행을 제목 행처럼 꾸미지 않게 한다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = v.replace('<table class="orig-table">', '<table class="orig-table recipe-table">')
            else:
                _mark_recipe_tables(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = v.replace('<table class="orig-table">', '<table class="orig-table recipe-table">')
            else:
                _mark_recipe_tables(v)


def _prep_base_name(name):
    """"우황청심원 액 (변방)" -> "우황청심원 액" (끝의 괄호 부분을 뗀 이름)"""
    return re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()


_RECIPE_TABLE_RE = re.compile(r'(<table class="orig-table recipe-table">)(.*?)(</table>)', re.S)
_RECIPE_SEP = ",，、․·ㆍ•∙･"  # 한 칸에 여러 생약을 나열할 때 쓰는 구분자(쉼표, 가운뎃점 여러 모양)
_RECIPE_SPLIT_RE = re.compile(r"(\s*[" + re.escape(_RECIPE_SEP) + r"]\s*|\s+및\s+)")


def _segment_herb_names(compact, name_to_id):
    """줄바꿈이 사라져 이름이 붙은 글자("산수유우슬")를 알려진 생약 이름 둘 이상으로 정확히 나눈다. 안 나뉘면 None."""
    n = len(compact)
    best = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for j in range(i + 2, min(n, i + 8) + 1):
            if compact[i:j] in name_to_id:
                cand = best[i] + [(i, j)]
                if best[j] is None or len(cand) < len(best[j]):
                    best[j] = cand
    return best[n] if best[n] and len(best[n]) >= 2 else None


def _ref_link(text, herb_id):
    compact = re.sub(r"\s+", "", text)
    return f'<a href="#" class="ref-link" data-id="{herb_id}" title="{compact} 생약 설명 보기">{text}</a>'


def _link_herb_piece(piece, name_to_id):
    """제법 표 첫 칸에서 쉼표 등으로 나눈 이름 하나(글자 사이 공백이 있을 수 있다: "숙 지 황")에 공정서 생약 링크를 건다."""
    m = re.match(r"(\s*)(.*?)(\s*)$", piece, re.S)
    lead, core, trail = m.group(1), m.group(2), m.group(3)
    chars = [(c, k) for k, c in enumerate(core) if not c.isspace()]
    compact = "".join(c for c, _ in chars)
    if not compact:
        return piece
    if compact in name_to_id:
        return lead + _ref_link(core, name_to_id[compact]) + trail
    seg = _segment_herb_names(compact, name_to_id)
    if seg is None:
        return piece
    out, pos = [], 0
    for i, j in seg:
        start, end = chars[i][1], chars[j - 1][1] + 1
        out.append(core[pos:start])
        out.append(_ref_link(core[start:end], name_to_id[compact[i:j]]))
        pos = end
    out.append(core[pos:])
    return lead + "".join(out) + trail


def _link_recipe_cell(match, name_to_id):
    head, content, tail = match.group(1), match.group(2), match.group(3)
    chunks = re.split(r"(<br\s*/?>)", content)  # 줄바꿈(<br>)은 그대로 두고 앞뒤 글을 따로 처리한다
    if any("<" in c for c in chunks[::2]):  # 첨자 등 다른 표시가 섞인 칸은 건드리지 않는다
        return match.group(0)
    for k in range(0, len(chunks), 2):
        parts = _RECIPE_SPLIT_RE.split(chunks[k])
        for i in range(0, len(parts), 2):  # 짝수 번째가 이름, 홀수 번째가 구분자
            parts[i] = _link_herb_piece(parts[i], name_to_id)
        chunks[k] = "".join(parts)
    return head + "".join(chunks) + tail


_HANGUL_WORD_RE = re.compile(r"[가-힣]+")


def _link_amount_text(text, name_to_id):
    """분량 칸의 글에서 생약 이름에 링크를 건다. "(형개로서 0.88 g)"는 "형개"만, "(또는 건강 0.33 g)"는 "건강"만 건다.
    낱말이 생약 이름이거나 "이름+로서/으로서"일 때만 걸고, "구아이아콜로서" 같은 생약이 아닌 성분은 그대로 둔다."""
    def repl(m):
        w = m.group(0)
        if w in name_to_id:
            return _ref_link(w, name_to_id[w])
        for suf in ("으로서", "로서"):
            if w.endswith(suf) and w[: -len(suf)] in name_to_id:
                base = w[: -len(suf)]
                return _ref_link(base, name_to_id[base]) + suf
        return w

    return _HANGUL_WORD_RE.sub(repl, text)


def _link_amount_cell(match, name_to_id):
    head, content, tail = match.group(1), match.group(2), match.group(3)
    chunks = re.split(r"(<br\s*/?>)", content)
    if any("<" in c for c in chunks[::2]):
        return match.group(0)
    for k in range(0, len(chunks), 2):
        chunks[k] = _link_amount_text(chunks[k], name_to_id)
    return head + "".join(chunks) + tail


def _link_recipe_table(inner, name_to_id):
    """표 한 개의 행마다 첫 칸은 생약명 목록(_link_recipe_cell), 나머지 칸은 분량 글(_link_amount_cell)로 링크를 건다."""
    def row(mr):
        idx = [0]

        def cell(mc):
            i = idx[0]
            idx[0] += 1
            return _link_recipe_cell(mc, name_to_id) if i == 0 else _link_amount_cell(mc, name_to_id)

        return mr.group(1) + re.sub(r"(<td[^>]*>)(.*?)(</td>)", cell, mr.group(2), flags=re.S) + mr.group(3)

    return re.sub(r"(<tr>)(.*?)(</tr>)", row, inner, flags=re.S)


def _link_recipe_herbs(obj, name_to_id):
    """생약제제 제법의 "생약명 | 분량" 표(recipe-table)에서 생약 이름에 공정서 생약 상세로 가는 링크(ref-link)를 건다:
    첫 칸의 생약명 목록, 그리고 "형개엑스산 | (형개로서 0.88 g)"처럼 분량 칸에 들어 있는 생약 이름.
    정제수, 전체량, "감초엑스산" 같은 단미엑스, "구아이아콜"처럼 공정서 생약 이름이 아닌 것은 그대로 둔다."""
    def link_str(s):
        if '<table class="orig-table recipe-table">' not in s:
            return s
        return _RECIPE_TABLE_RE.sub(lambda t: t.group(1) + _link_recipe_table(t.group(2), name_to_id) + t.group(3), s)

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = link_str(v)
            else:
                _link_recipe_herbs(v, name_to_id)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = link_str(v)
            else:
                _link_recipe_herbs(v, name_to_id)


def load_prep_entries(start_id):
    """생약제제 hwpx(PREP_FILE_NAMES)를 파싱한다. 문서마다 품목 제목 아래에 영문명 줄이 오는 형식이라 parse_hwpx(prep=True)
    로 읽는다. 본문의 그림(크로마토그램)은 /api/prep_image 로 보여 준다."""
    entries, files = [], []
    for fi, name in enumerate(PREP_FILE_NAMES):
        f = BASE_DIR / name
        if not f.is_file():
            _warn_missing("생약제제", name)
            continue
        try:
            file_entries = _cached_parse("prep", f, parse_hwpx, prep=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[경고] {f} 파싱 실패: {exc}")
            continue
        tag = _source_tag(name)
        part = (re.search(r"각조 제\d부", name) or [""])[0]  # 파일명의 "각조 제2부", "각조 제3부"
        prev_name = ""
        for e in file_entries:
            e["source_file"] = name
            e["source_tag"] = tag
            e["part"] = part
            # 우황청심원처럼 제목이 "(변방, 사향대체물질 함유)"로 시작하면 앞 품목 이름에 이어 붙인다
            if e["korean_name"].startswith("(") and prev_name:
                base = _prep_base_name(prev_name)
                e["korean_name"] = base + e["korean_name"]
                e["name_primary"] = base + e["name_primary"]
                e["name_only"], e["hanja"] = base, e["korean_name"][len(base):].strip("()")
            else:
                prev_name = e["korean_name"]
            _inject_prep_images(e, fi)
            for sec in e["sections"]:
                if sec["label"] in ("제법", "분량"):
                    _mark_recipe_tables(sec)
        entries.extend(file_entries)
        files.append(name)
    for i, e in enumerate(entries):
        e["id"] = start_id + i
        e["kind"] = "prep"
        e["_search_blob"] = _normalize(
            " ".join(filter(None, [e.get("korean_name"), e.get("name_only"), e.get("english_name"), e.get("latin_name")]))
        )
    return entries, files


def load_prep_methods(start_id):
    """"7. 생규집(확인, 함량).hwpx" 를 읽어 (목록용 항목들, 생약 상세에 붙일 섹션 표)를 돌려준다.
    항목은 생약제제 품목처럼 /api/item 으로 열리고(kind="prep"), 섹션 표는 {종류: {(이름, 한자): [섹션,...]}} 이다."""
    path = BASE_DIR / METHOD_FILE_NAME
    entries = []
    by_herb = {"confirm": {}, "quant": {}}
    if not path.is_file():
        _warn_missing("생약제제 확인시험·함량시험", METHOD_FILE_NAME)
        return entries, by_herb
    try:
        data = _cached_parse("method", path, parse_method_hwpx)
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] {path} 파싱 실패: {exc}")
        return entries, by_herb
    labels = {"confirm": "확인시험", "quant": "정량법", "simul": "정량법"}
    for key in ("confirm", "quant", "simul"):
        for m in data[key]:
            section = finalize_method_section(key, m["text"], labels[key])
            # 항목명 배지 오른쪽에 따로 보여 준다
            section["note"] = "(생규집 생약시험법)" if key != "confirm" else "(생약제제에 적용)"
            display = m["name"].replace("․", "·").replace("‧", "·")
            entries.append(
                {
                    "kind": "prep",
                    "method_kind": "quant" if key == "simul" else key,
                    "names": m["names"],  # "건강․생강" -> ["건강", "생강"], "진피(陳皮)" 는 한자 포함
                    "korean_name": display,
                    "name_primary": display,
                    "name_only": display,
                    "hanja": "",
                    "english_name": "",
                    "latin_name": "",
                    "synonym_name": "",
                    "synonym_html": "",
                    "definition": "",
                    "definition_parts": [],
                    "sections": [section],
                    "source_tag": "KHP",
                    "part": "",
                    "_search_blob": _normalize(display),
                }
            )
            for part in m["names"]:
                name, hanja = _split_method_name(part)
                by_herb["quant" if key == "simul" else key].setdefault((name, hanja), []).append(section)
    for i, e in enumerate(entries):
        e["id"] = start_id + i
    return entries, by_herb


def _split_method_name(part):
    """"진피(陳皮)" -> ("진피", "陳皮"), "갈근" -> ("갈근", "")"""
    hm = re.match(r"^(.+?)\(([^()]+)\)$", part)
    return (hm.group(1), hm.group(2)) if hm else (part, "")


def prep_method_sections(name_only, hanja):
    """공정서 생약(name_only, hanja)과 이름이 같은 "생약제제에 적용" 시험법 섹션들을 {종류: [섹션,...]} 로 돌려준다.
    파일의 이름에 한자가 붙어 있으면(진피(陳皮)) 한자까지 같아야 한다."""
    found = {}
    for kind, table in PREP_METHOD_BY_HERB.items():
        secs = list(table.get((name_only, ""), [])) + (list(table.get((name_only, hanja), [])) if hanja else [])
        if secs:
            found[kind] = secs
    return found


def load_case_entries():
    """이 폴더의 "...사례집....pdf"(관능검사 사례집)를 찾아 파싱한다.
    공정서/관능검사해설서와 달리 검색 결과에서 생약 하나당 여러 건(카테고리별)
    으로 나올 수 있어 ENTRY_BY_ID에는 넣지 않고, /api/search에서 herb_name으로
    직접 매칭한다."""
    case_files = sorted(BASE_DIR.glob("*사례집*.pdf"))
    entries = []
    if not case_files:
        _warn_missing("관능검사 사례집", "*사례집*.pdf (예: 관능검사사례집.pdf)")
    for f in case_files:
        try:
            file_entries = _cached_parse("case", f, parse_case_pdf)
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
        _warn_missing("생약시험법", TEST_METHOD_FILE)
        return {}
    try:
        return _cached_parse("testmethods", path, parse_test_methods)
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] {path} 파싱 실패: {exc}")
        return {}


TEST_METHODS = load_test_methods()
ENTRIES, LOADED_FILES = load_entries()
SENSORY_ENTRIES, SENSORY_FILES = load_sensory_entries(start_id=len(ENTRIES))
CASE_ENTRIES, CASE_FILES = load_case_entries()
PREP_ENTRIES, PREP_FILES = load_prep_entries(PREP_ID_START)
# 제법 표의 생약명 -> 공정서 생약 id (이름이 같은 것이 여럿이면 먼저 나온 것). 제법 표의 생약명에 상세로 가는 링크를 건다.
_HERB_NAME_TO_ID = {}
for _e in ENTRIES:
    if _e.get("name_only") and _e["name_only"] not in _HERB_NAME_TO_ID:
        _HERB_NAME_TO_ID[_e["name_only"]] = _e["id"]
_link_recipe_herbs(PREP_ENTRIES, _HERB_NAME_TO_ID)


# 생약제제 제법 표에 들어 있는 생약 이름 모음(정규화한 이름). 생약제제 검색칸에 생약명을 치면 이름이 제법에 있는
# 생약제제를 찾는 데 쓴다. 링크가 걸린 이름과 표 첫 칸의 이름(공정서에 없는 생약 포함)을 모은다.
_ANCHOR_TEXT_RE = re.compile(r'<a href="#" class="ref-link"[^>]*>(.*?)</a>', re.S)


def _recipe_herb_tokens(obj, toks):
    if isinstance(obj, dict):
        for v in obj.values():
            _recipe_herb_tokens(v, toks)
    elif isinstance(obj, list):
        for v in obj:
            _recipe_herb_tokens(v, toks)
    elif isinstance(obj, str) and 'recipe-table' in obj:
        for t in _RECIPE_TABLE_RE.finditer(obj):
            inner = t.group(2)
            for a in _ANCHOR_TEXT_RE.findall(inner):
                toks.add(_normalize(re.sub(r"<[^>]+>", "", a)))
            for tr in re.findall(r"<tr>(.*?)</tr>", inner, re.S):
                m = re.search(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if not m:
                    continue
                cell = re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", ",", m.group(1)))
                for piece in _RECIPE_SPLIT_RE.split(cell)[::2]:
                    if _normalize(piece):
                        toks.add(_normalize(piece))


for _e in PREP_ENTRIES:
    _e["_recipe_herbs"] = set()
    _recipe_herb_tokens(_e.get("sections", []), _e["_recipe_herbs"])
PREP_METHOD_ENTRIES, PREP_METHOD_BY_HERB = load_prep_methods(METHOD_ID_START)


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
ENTRY_BY_ID = {e["id"]: e for e in ENTRIES + SENSORY_ENTRIES + PREP_ENTRIES + PREP_METHOD_ENTRIES}


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
# 첫 사진이 두 번 나오고 꾸밀 수 없어서, 사진 목록만 가져와 /photos/<번호>, /photo_info 에서
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


# 식약공용독성주의.xlsx 의 "생약명 -> 식약공용/독성주의"(herb_flags.json). 공정서 생약에만 표시한다.
HERB_FLAGS = _load_json_map("herb_flags.json")


def herb_flag(e):
    if e.get("kind", "official") != "official":
        return ""
    name = e["name_only"]
    return HERB_FLAGS.get(name) or HERB_FLAGS.get((name.split() or [""])[0], "")  # "사프란 번홍화"는 첫 단어로 찾는다


# ---- 과(科) 목록 -----------------------------------------------------------------------
# 공정서 생약의 기원 문장("이 약은 칡 Pueraria lobata Ohwi (콩과 Leguminosae)의 뿌리로서 ...")에서 "콩과 Leguminosae"처럼
# 한글 과 이름 + 라틴 과 이름 짝을 찾아, 과 이름별로 생약 목록을 만든다. 설명창의 기원 문장에서 과 이름을 누르면 같은 과의
# 생약 목록이 뜬다. 기원 문장에 과가 둘 이상(예: 대청엽)이면 그 과마다 목록에 들어간다.
FAMILY_RE = re.compile(r"([가-힣]+과)\s+([A-Z][a-z]+(?:aceae|idae|ae))")
FAMILY_INDEX = {}
for _e in ENTRIES:
    if _e.get("kind", "official") != "official":
        continue
    for _fam in {f for f, _latin in FAMILY_RE.findall(_e.get("definition", ""))}:
        FAMILY_INDEX.setdefault(_fam, []).append(_e)
for _lst in FAMILY_INDEX.values():
    _lst.sort(key=lambda x: x["korean_name"])
# 팝업 제목줄에서 고를 수 있는 전체 과 목록 [[과 이름, 생약 수], ...] (가나다순)
FAMILY_LIST = [[f, len(v)] for f, v in sorted(FAMILY_INDEX.items())]


@app.route("/api/family/<name>")
def api_family(name):
    """같은 과(예: 콩과)에 속한 공정서 생약 목록(가나다순)과, 고를 수 있는 전체 과 목록."""
    return jsonify({
        "family": name,
        "items": [summary(e) for e in FAMILY_INDEX.get(name, [])],
        "families": FAMILY_LIST,
    })


def summary(e):
    return {
        "flag": herb_flag(e),
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
        "part": e.get("part", ""),
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        total_count=len(ENTRIES),
        loaded_files=LOADED_FILES,
        sensory_count=len(SENSORY_ENTRIES),
    )


@app.route("/api/prep_list")
def api_prep_list():
    items = sorted(PREP_ENTRIES, key=lambda e: e["korean_name"])
    return _cached_json([summary(e) for e in items])


@app.route("/api/prep_search")
def api_prep_search():
    q = _normalize(request.args.get("q", ""))
    if not q:
        return jsonify({"items": [], "recipe": []})
    by_name = _search(PREP_ENTRIES, q)[:100]
    # 검색어가 생약 이름이면 그 생약이 제법에 들어 있는 생약제제(이름으로 이미 찾은 것은 뺀다)
    named = {e["id"] for e in by_name}
    recipe = []
    if len(q) >= 2:
        recipe = [e for e in PREP_ENTRIES if q in e["_recipe_herbs"] and e["id"] not in named]
        recipe.sort(key=lambda e: e["korean_name"])
    return jsonify({"items": [summary(e) for e in by_name], "recipe": [summary(e) for e in recipe]})


@functools.lru_cache(maxsize=64)
def _prep_image_png(file_idx, name):
    """생약제제 hwpx 안의 그림(BMP 등)을 웹에 맞게 PNG 로 바꿔 돌려준다. 없으면 None."""
    import io
    import zipfile

    from PIL import Image

    if not 0 <= file_idx < len(PREP_FILE_NAMES):
        return None
    path = BASE_DIR / PREP_FILE_NAMES[file_idx]
    if not path.is_file():
        return None
    with zipfile.ZipFile(path) as zf:
        member = next((n for n in zf.namelist() if re.fullmatch(rf"BinData/{name}\.\w+", n, re.I)), None)
        if member is None:
            return None
        img = Image.open(io.BytesIO(zf.read(member)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


@app.route("/api/prep_image/<int:file_idx>/<name>")
def api_prep_image(file_idx, name):
    if not re.fullmatch(r"image\d+", name):
        abort(404)
    png = _prep_image_png(file_idx, name)
    if png is None:
        abort(404)
    return Response(png, mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.route("/api/list")
def api_list():
    items = sorted(ENTRIES, key=lambda e: e["korean_name"])
    return _cached_json([summary(e) for e in items])


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


_ORIGIN_NORM = {}  # 항목 id -> 공백을 뺀 소문자 기원 문장(처음 쓸 때 만든다)
ORIGIN_SUGGEST_MAX = 30


@app.route("/api/origin_search")
def api_origin_search():
    """검색어가 생약의 '기원' 문장(예: "이 약은 칡 Pueraria lobata Ohwi (콩과 Leguminosae)의 뿌리로서 ...")에
    들어 있는 공정서 생약을 찾아 추천 목록으로 돌려준다. 생약명·한자·영명·학명으로 이미 검색되는 생약은
    검색 결과 카드에 나오므로 뺀다. 각 항목에는 일치한 부분 둘레의 기원 문장 조각(snippet: 앞/일치/뒤)을 붙인다."""
    raw = (request.args.get("q") or "").strip()
    q = _normalize(raw)
    if len(q) < 2:
        return jsonify({"q": raw, "total": 0, "items": []})
    if not _ORIGIN_NORM:
        for e in ENTRIES:
            _ORIGIN_NORM[e["id"]] = _normalize(e.get("definition", ""))
    found = [e for e in ENTRIES if q in _ORIGIN_NORM.get(e["id"], "") and q not in e["_search_blob"]]
    found.sort(key=lambda e: e["korean_name"])
    # 공백이 있어도 일치하도록 글자 사이에 공백 허용 패턴으로 원문에서 일치 위치를 찾는다
    pat = re.compile(r"\s*".join(re.escape(ch) for ch in q), re.IGNORECASE)
    items = []
    # 입력 중 추천 목록은 상위 일부만, Enter 로 여는 전체 목록은 limit=all 로 전부 받는다
    shown = found if request.args.get("limit") == "all" else found[:ORIGIN_SUGGEST_MAX]
    for e in shown:
        text = e.get("definition", "")
        m = pat.search(text)
        item = summary(e)
        if m:
            s, t = m.start(), m.end()
            item["snippet"] = [
                ("…" if s > 14 else "") + text[max(0, s - 14):s].lstrip(),
                text[s:t],
                text[t:t + 14].rstrip() + ("…" if t + 14 < len(text) else ""),
            ]
        items.append(item)
    return jsonify({"q": raw, "total": len(found), "items": items})


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
PREP_METHOD_KIND_BY_ITEM = {"확인시험": "confirm", "정량법": "quant"}  # 생약제제 적용 시험법 종류(7. 생규집(확인, 함량))


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
    # 확인시험/정량법은 생약제제에 적용하는 시험법(7. 생규집(확인, 함량))의 이름도 한 목록에 합친다.
    # 공정서 생약과 이름이 같은 것은 공정서 항목으로 통일하고, 공정서에 없는 이름만 따로 보탠다.
    if len(needles_raw) == 1 and needles_raw[0] in PREP_METHOD_KIND_BY_ITEM:
        kind = PREP_METHOD_KIND_BY_ITEM[needles_raw[0]]
        listed = {(e["name_only"], e["hanja"]) for e in matches}
        listed_names = {n for n, _ in listed}
        for pe in PREP_METHOD_ENTRIES:
            if pe["method_kind"] != kind:
                continue
            covered = bool(pe["names"]) and all(
                _split_method_name(p) in listed if _split_method_name(p)[1] else p in listed_names for p in pe["names"]
            )
            if not covered:
                matches.append(pe)
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
    if e.get("kind") == "prep":
        # 생약제제: 공정서 생약과 같은 모양으로 보여 주되, 국가생약정보 연결 버튼과 생약시험법 연결은 없다
        return jsonify(
            {
                "id": e["id"],
                "kind": "prep",
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
                "part": e.get("part", ""),
                "test_methods": [],
            }
        )
    sections = list(e["sections"])
    # 이름이 같은 "생약제제에 적용" 시험법이 있으면 확인시험/정량법 바로 아래에 새 항목으로 붙인다
    for kind, base_label in (("confirm", "확인시험"), ("quant", "정량법")):
        extra = prep_method_sections(e["name_only"], e["hanja"]).get(kind)
        if not extra:
            continue
        pos = next((i for i, sec in enumerate(sections) if sec["label"] == base_label), None)
        if pos is not None:
            at = pos + 1
        elif kind == "confirm":  # 공정서에는 없고 생약제제 파일에만 있는 확인시험(육종용 등)은 성상 다음에 둔다
            at = next((i + 1 for i, sec in enumerate(sections) if sec["label"] == "성상"), 0)
        else:
            at = len(sections)
        sections[at:at] = extra
    return jsonify(
        {
            "id": e["id"],
            "kind": "official",
            "flag": herb_flag(e),
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
            "sections": sections,
            "source_tag": e.get("source_tag", ""),
            "photo_links": photo_info_links(e["name_only"], e["hanja"]),
            "indication_links": indication_links(e["name_only"], e["hanja"]),
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


# ---- 사진(해설서) -------------------------------------------------------------------
# 관능검사해설서(2022).pdf 의 품목 페이지에 실린 사진을, 사진 아래 설명과 함께 한 페이지에 모아 보여 준다.
# 사진/설명 위치 찾기와 그리기는 sensory_photos.py 참고(설명은 글자가 아니라 PDF 에서 잘라낸 그림).
SENSORY_PHOTO_FILE = "관능검사해설서(2022).pdf"
# 사진 그림을 만드는 방식을 바꿀 때 올린다(브라우저가 하루 동안 저장해 둔 예전 그림 대신 새로 받게 한다).
SENSORY_PHOTO_VER = 32

def _sensory_entry_for(name_only, hanja):
    """공정서 품목에 해당하는 관능검사해설서 품목. 한자까지 같으면 그것을, 한자 표기만 다른 경우
    (猪/豬 등)는 이름이 같은 품목이 하나뿐일 때 그것을 쓴다(동음 생약은 한자가 같을 때만)."""
    sensory = [e for e in SENSORY_ENTRIES if e.get("source_file") == SENSORY_PHOTO_FILE]
    for e in sensory:
        if e["name_only"] == name_only and e["hanja"] == hanja:
            return e
    if name_only in HOMONYM_NAMES:
        return None
    key = (name_only.split() or [""])[0]
    same = [e for e in sensory if e["name_only"] == key]
    return same[0] if len(same) == 1 else None


# 사진 위치 분석은 PDF 를 열어 이미지를 살펴야 해서 품목 하나에 약 0.2초(최대 0.5초)가 걸린다. 설명창의 "사진정보" 버튼을 보여 줄지
# 정하려고 생약을 처음 열 때마다 이 분석이 돌므로, 결과 표 {(시작쪽, 끝쪽): ((쪽, 묶음 번호), ...)} 를 .parse_cache/ 에 저장해 두고 쓴다.
# 표는 build_photo_cache.py 로 미리 만들어 둘 수 있고(전체 약 1~2분), 없는 품목은 요청이 올 때 계산해서 표에 보태 저장한다.
# 사진 PDF(크기·수정 시각)나 sensory_photos.py 가 바뀌면 표 파일 이름이 달라져 자동으로 새로 만든다.
_PHOTO_TABLE = None
_PHOTO_TABLE_FILE = None


def _photo_table_file():
    pdf = BASE_DIR / SENSORY_PHOTO_FILE
    try:
        st = pdf.stat()
        h = hashlib.sha1()
        h.update(f"{st.st_size}|{st.st_mtime_ns}|py{sys.version_info[:2]}".encode())
        h.update((BASE_DIR / "sensory_photos.py").read_bytes())
        return PARSE_CACHE_DIR / f"photo_regions-{h.hexdigest()[:16]}.pkl"
    except OSError:
        return None


def _photo_table():
    """디스크에 저장된 사진 위치 표(없거나 깨졌으면 빈 표)를 한 번 읽어 메모리에 둔다."""
    global _PHOTO_TABLE, _PHOTO_TABLE_FILE
    if _PHOTO_TABLE is None:
        _PHOTO_TABLE_FILE = _photo_table_file()
        _PHOTO_TABLE = {}
        if _PHOTO_TABLE_FILE is not None and _PHOTO_TABLE_FILE.is_file():
            try:
                with open(_PHOTO_TABLE_FILE, "rb") as f:
                    loaded = pickle.load(f)
                if isinstance(loaded, dict):
                    _PHOTO_TABLE = loaded
            except Exception:  # noqa: BLE001  깨진 표는 버리고 다시 만든다
                pass
    return _PHOTO_TABLE


def _save_photo_table():
    if _PHOTO_TABLE_FILE is None:
        return
    try:
        PARSE_CACHE_DIR.mkdir(exist_ok=True)
        tmp = _PHOTO_TABLE_FILE.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(_PHOTO_TABLE, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _PHOTO_TABLE_FILE)
        for old in PARSE_CACHE_DIR.glob("photo_regions-*.pkl"):  # 옛 버전 표 정리
            if old != _PHOTO_TABLE_FILE:
                old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001  저장 실패는 메모리 표만 쓰고 계속
        print(f"[알림] 사진 위치 표를 저장하지 못했습니다: {exc}")


def build_photo_regions_table(progress=None):
    """관능검사해설서 사진이 있는 모든 품목의 사진 위치를 계산해 표에 채우고 저장한다(build_photo_cache.py 가 쓴다).
    PDF 를 한 번만 열어 쓰므로 품목마다 여는 것보다 빠르다. 이미 표에 있는 품목은 건너뛴다. (계산한 품목 수, 전체 품목 수)를 돌려준다."""
    import pymupdf as fitz

    table = _photo_table()
    todo = [e for e in SENSORY_ENTRIES if e.get("source_file") == SENSORY_PHOTO_FILE]
    pending = [e for e in todo if (int(e["page_start"]), int(e["page_end"])) not in table]
    if pending:
        doc = fitz.open(str(BASE_DIR / SENSORY_PHOTO_FILE))
        try:
            for n, e in enumerate(pending, 1):
                ps, pe = int(e["page_start"]), int(e["page_end"])
                table[(ps, pe)] = tuple(sensory_photos.entry_regions(doc, ps, pe))
                if n % 100 == 0:
                    _save_photo_table()  # 중간에 멈춰도(시간 제한 등) 지금까지 계산한 것은 남도록 100개마다 저장
                if progress:
                    progress(n, len(pending), e["korean_name"])
        finally:
            doc.close()
        _save_photo_table()
    return len(pending), len(todo)


@functools.lru_cache(maxsize=None)
def _sensory_photo_regions(entry_id):
    """품목의 [(쪽, 묶음 번호)] 목록. 저장된 표에 있으면 그것을 쓰고, 없으면 PDF 를 분석해 표에 보탠다."""
    e = ENTRY_BY_ID[entry_id]
    ps, pe = int(e["page_start"]), int(e["page_end"])
    table = _photo_table()
    if (ps, pe) in table:
        return table[(ps, pe)]
    import pymupdf as fitz

    doc = fitz.open(str(BASE_DIR / SENSORY_PHOTO_FILE))
    try:
        regions = tuple(sensory_photos.entry_regions(doc, ps, pe))
    finally:
        doc.close()
    table[(ps, pe)] = regions
    _save_photo_table()
    return regions


def _sensory_native_dpis(entry_id):
    """품목 사진들의 PDF 원본 해상도 {(쪽, 묶음 번호): dpi}. 사진마다 약 0.13초가 걸려서 사진 위치 표(_photo_table)에 같이 저장해 둔다
    (키는 ("dpi", 쪽, 묶음 번호)). 표에 없는 것만 PDF 를 한 번 열어 계산해 보태 저장한다."""
    regions = _sensory_photo_regions(entry_id)
    table = _photo_table()
    missing = [(p, i) for p, i in regions if ("dpi", p, i) not in table]
    if missing:
        import pymupdf as fitz

        doc = fitz.open(str(BASE_DIR / SENSORY_PHOTO_FILE))
        try:
            for p, i in missing:
                table[("dpi", p, i)] = sensory_photos.native_dpi(doc, p, i)
        finally:
            doc.close()
        _save_photo_table()
    return {(p, i): table[("dpi", p, i)] for p, i in regions}


def _sensory_photo_entry_id(name_only, hanja):
    """사진이 있는 관능검사해설서 품목의 id (없으면 None)."""
    if not (BASE_DIR / SENSORY_PHOTO_FILE).is_file():
        return None
    e = _sensory_entry_for(name_only, hanja)
    return e["id"] if e is not None and _sensory_photo_regions(e["id"]) else None


def _nifds_photo_no(name_only, hanja):
    """국가생약정보 "사진정보" 사진 번호(/photos/<번호>). 사진이 없으면 None."""
    for origin, dmstc, mdntf in _nifds_origins(name_only, hanja):
        drgnm = NIFDS_PHOTO_MAP.get(f"{dmstc}_{mdntf}")
        if str(drgnm) in NIFDS_PHOTO_LIST:
            return drgnm
    return None


def photo_info_links(name_only, hanja=""):
    """"사진정보" 버튼: 국가생약정보 사진(왼쪽)과 관능검사해설서 사진(오른쪽)을 한 화면에 보여 주는
    /photo_info 주소. 둘 다 없으면 빈 목록(버튼이 나오지 않는다)."""
    nifds = _nifds_photo_no(name_only, hanja)
    sensory = _sensory_photo_entry_id(name_only, hanja)
    if nifds is None and sensory is None:
        return []
    params = {}
    if nifds is not None:
        params["n"] = nifds
    if sensory is not None:
        params["s"] = sensory
    return [{"origin": "", "url": "/photo_info?" + urlencode(params)}]


@app.route("/photo_info")
def photo_info():
    """왼쪽: 국가생약정보 사진정보(약재 사진), 오른쪽: 관능검사해설서(2022) 사진과 설명. 한쪽만
    있으면 그쪽만 보여 준다. 사진을 누르면 화질을 유지한 채 확대해서 볼 수 있다."""
    columns = []
    nifds = request.args.get("n", type=int)
    if nifds is not None:
        spec = NIFDS_PHOTO_LIST.get(str(nifds))
        if spec is None:
            abort(404)
        pairs = spec if spec and isinstance(spec[0], list) else [[spec[0], sn] for sn in range(1, spec[1] + 1)]
        columns.append(
            {
                "title": "국가생약정보 사진정보",
                "photos": [
                    {"src": NIFDS_THUMB_URL.format(flgrp=f, sn=s), "hi": NIFDS_PREVIEW_URL.format(flgrp=f, sn=s)}
                    for f, s in pairs
                ],
            }
        )
    sensory = request.args.get("s", type=int)
    if sensory is not None:
        e = ENTRY_BY_ID.get(sensory)
        if e is None or e.get("source_file") != SENSORY_PHOTO_FILE:
            abort(404)
        native = _sensory_native_dpis(sensory)
        columns.append(
            {
                "title": "관능검사해설서 사진정보",
                "photos": [
                    {
                        # 목록과 확대 화면 모두 PDF 원본 해상도 그림을 쓴다(같은 주소라 한 번만 받는다).
                        "src": f"/sensory_photos/{sensory}/{p}/{i}.png?dpi=native&v={SENSORY_PHOTO_VER}",
                        "hi": f"/sensory_photos/{sensory}/{p}/{i}.png?dpi=native&v={SENSORY_PHOTO_VER}",
                        # 목록에서 보이는 크기는 예전(170dpi 그림) 그대로 두려고, 원본 해상도(dpi)를 알려 준다.
                        "dpi": native[(p, i)],
                        # 확대 화면용 두 층: 사진만 + 글자/선만(4배 해상도)
                        "photo": f"/sensory_photos/{sensory}/{p}/{i}.png?dpi=native&layer=photo&v={SENSORY_PHOTO_VER}",
                        "text": f"/sensory_photos/{sensory}/{p}/{i}.png?dpi=native&layer=text&k=4&v={SENSORY_PHOTO_VER}",
                    }
                    for p, i in _sensory_photo_regions(sensory)
                ],
                "shown_dpi": sensory_photos.CLIP_DPI,
            }
        )
    if not columns:
        abort(404)
    return render_template("photo_info.html", columns=columns)


# ---- 적응증 -------------------------------------------------------------------------
# 국가생약정보 공정서 생약 상세 페이지(hbdc/ofcmhbdc/view.do)의 "채취 및 가공 / 적응증(효능/효과) /
# 용법용량 / 저장" 항목. 같은 생약이라도 기원종마다 내용이 달라서 기원종("선택번호_기원번호")별로
# nifds_indication.json 에 [{t: 내용, r: "Ref. ChP 2015.", rt: 문헌 이름}, ...] 목록으로 정리해 두었다
# (브라우저로 사이트를 열어 읽은 값).
NIFDS_INDICATION = _load_json_map("nifds_indication.json")
INDICATION_SECTIONS = [("h", "채취 및 가공"), ("i", "적응증(효능/효과)"), ("d", "용법용량"), ("s", "저장")]


def _indication_keys(name_only, hanja):
    """생약의 기원종별 자료 키 [(기원종, "선택번호_기원번호")] (자료가 있는 것만)."""
    return [
        (origin, f"{dmstc}_{mdntf}")
        for origin, dmstc, mdntf in _nifds_origins(name_only, hanja)
        if NIFDS_INDICATION.get(f"{dmstc}_{mdntf}")
    ]


def indication_links(name_only, hanja=""):
    keys = _indication_keys(name_only, hanja)
    if not keys:
        return []
    return [{"origin": "", "url": "/indication?" + urlencode({"k": ",".join(k for _, k in keys), "t": name_only})}]


@app.route("/indication")
def indication():
    """기원종별 채취 및 가공 / 적응증 / 용법용량 / 저장. 내용이 같은 기원종은 한 묶음으로 보여 준다."""
    keys = [k for k in request.args.get("k", "").split(",") if k in NIFDS_INDICATION]
    if not keys:
        abort(404)
    names = {}
    for origin, dmstc, mdntf in sum(NIFDS_MAP.values(), []):
        names[f"{dmstc}_{mdntf}"] = origin
    groups = []
    for k in keys:
        data = NIFDS_INDICATION[k]
        for g in groups:
            if g["data"] == data:
                g["origins"].append(names.get(k, ""))
                break
        else:
            groups.append({"data": data, "origins": [names.get(k, "")]})
    for g in groups:
        g["origins"] = [o for o in g["origins"] if o]
        g["sections"] = [{"label": label, "entries": g["data"].get(key, [])} for key, label in INDICATION_SECTIONS]
    return render_template("indication.html", groups=groups, title=request.args.get("t", "")[:40])


# 사진 그림 파일 캐시: 사진 한 장을 PDF 에서 그리는 데 0.6~1.4초가 걸리고 PNG 가 0.4~1.5MB 라서, 목록에 보이는 그림(원본 해상도 전체 층)은
# 한 번 그린 뒤 .parse_cache/photos/ 에 저장해 두고 다음부터는 그 파일을 보낸다. 확대 화면용 두 층(photo/text)은 드물게 쓰여 저장하지 않는다.
# build_photo_cache.py 로 PDF 전체 사진을 미리 그려 둘 수 있고, 안 해 둔 사진은 처음 볼 때 그려서 보탠다.
# 저장 공간 상한: 기본은 없음(None). 공간이 모자라면 바이트 수를 넣으면(예: 500 * 1024 * 1024) 합계가 그것을 넘을 때 오래 안 본 것부터
# 상한의 80% 가 될 때까지 지운다. 상한이 없을 때는 지우는 일이 없다(서명이 달라진 옛 파일만 정리).
# 파일 이름은 (사진 PDF 크기·수정 시각 + sensory_photos.py + SENSORY_PHOTO_VER) 서명과 (쪽, 묶음 번호)로 정해서, PDF 나 그리는 코드가
# 바뀌면 옛 파일은 쓰이지 않고 정리된다. (쪽, 묶음 번호)는 품목 번호와 무관해서 데이터를 다시 만들어도 어긋나지 않는다.
PHOTO_IMAGE_CACHE_DIR = PARSE_CACHE_DIR / "photos"
PHOTO_IMAGE_CACHE_MAX = None
_PHOTO_IMAGE_SIG = None
_PHOTO_IMAGE_PRUNED = False  # 이 서버 실행에서 옛 서명 파일 정리를 이미 했는지


def _photo_image_sig():
    global _PHOTO_IMAGE_SIG
    if _PHOTO_IMAGE_SIG is None:
        h = hashlib.sha1()
        try:
            st = (BASE_DIR / SENSORY_PHOTO_FILE).stat()
            h.update(f"{st.st_size}|{st.st_mtime_ns}|v{SENSORY_PHOTO_VER}|py{sys.version_info[:2]}".encode())
            h.update((BASE_DIR / "sensory_photos.py").read_bytes())
        except OSError:
            h.update(b"nofile")
        _PHOTO_IMAGE_SIG = h.hexdigest()[:12]
    return _PHOTO_IMAGE_SIG


def _photo_image_cache_file(page_no, index):
    return PHOTO_IMAGE_CACHE_DIR / f"{_photo_image_sig()}-{page_no}-{index}.png"


def _photo_image_cache_read(page_no, index):
    f = _photo_image_cache_file(page_no, index)
    try:
        data = f.read_bytes()
    except OSError:
        return None
    if PHOTO_IMAGE_CACHE_MAX is not None:
        try:
            os.utime(f)  # 방금 본 사진으로 표시(상한이 있을 때 오래 안 본 것부터 지우기 위해)
        except OSError:
            pass
    return data


def _photo_image_cache_store(page_no, index, png):
    """그림 한 장을 저장한다(정리는 하지 않는다). 실패하면 예외가 난다."""
    PHOTO_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _photo_image_cache_file(page_no, index)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(png)
    os.replace(tmp, f)


def _photo_image_cache_prune():
    """서명이 다른 옛 파일을 지우고, 상한(PHOTO_IMAGE_CACHE_MAX)이 있으면 넘는 만큼 오래 안 본 것부터 지운다."""
    sig = _photo_image_sig()
    files = []
    for p in PHOTO_IMAGE_CACHE_DIR.iterdir():
        if p.suffix == ".tmp":
            continue
        if not p.name.startswith(sig + "-"):
            p.unlink(missing_ok=True)
            continue
        if PHOTO_IMAGE_CACHE_MAX is not None:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
    if PHOTO_IMAGE_CACHE_MAX is not None:
        total = sum(s for _, s, _ in files)
        if total > PHOTO_IMAGE_CACHE_MAX:
            for _, size, p in sorted(files):
                if total <= PHOTO_IMAGE_CACHE_MAX * 0.8:
                    break
                p.unlink(missing_ok=True)
                total -= size


def _photo_image_cache_write(page_no, index, png):
    global _PHOTO_IMAGE_PRUNED
    try:
        _photo_image_cache_store(page_no, index, png)
        # 상한이 없으면 폴더 정리는 서버가 켜진 뒤 처음 저장할 때 한 번만, 있으면 저장할 때마다 한다
        if PHOTO_IMAGE_CACHE_MAX is not None or not _PHOTO_IMAGE_PRUNED:
            _PHOTO_IMAGE_PRUNED = True
            _photo_image_cache_prune()
    except Exception as exc:  # noqa: BLE001  저장 실패는 캐시 없이 계속
        print(f"[알림] 사진 그림 캐시를 저장하지 못했습니다: {exc}")


def build_photo_images(progress=None, limit=None):
    """관능검사해설서 PDF 전체의 사진 그림(목록에 보이는 원본 해상도 전체 층)과 원본 해상도(dpi) 값을 미리 만들어 저장한다
    (build_photo_cache.py 가 쓴다). 이미 저장된 그림은 건너뛰므로 중간에 멈췄다가 다시 실행하면 이어서 한다.
    PDF 는 일정 개수마다 다시 열어 메모리가 불어나지 않게 한다. (새로 그린 수, 건너뛴 수, 전체 수)를 돌려준다."""
    import pymupdf as fitz

    table = _photo_table()
    pairs = []
    seen = set()
    for e in SENSORY_ENTRIES:
        if e.get("source_file") != SENSORY_PHOTO_FILE:
            continue
        for pi in table.get((int(e["page_start"]), int(e["page_end"])), ()):
            if pi not in seen:
                seen.add(pi)
                pairs.append(pi)
    pairs.sort()
    total = len(pairs)
    pending = [(p, i) for p, i in pairs if not _photo_image_cache_file(p, i).is_file() or ("dpi", p, i) not in table]
    skipped = total - len(pending)
    todo = pending[:limit] if limit is not None else pending
    done = 0
    doc = None
    try:
        for n, (p, i) in enumerate(todo, 1):
            if doc is None or n % 150 == 1:
                if doc is not None:
                    doc.close()
                doc = fitz.open(str(BASE_DIR / SENSORY_PHOTO_FILE))
            if ("dpi", p, i) not in table:
                table[("dpi", p, i)] = sensory_photos.native_dpi(doc, p, i)
            if not _photo_image_cache_file(p, i).is_file():
                png = sensory_photos.render_region_original(doc, p, i, layer="all", k=4)
                if png is not None:
                    _photo_image_cache_store(p, i, png)
            done += 1
            if n % 50 == 0:
                _save_photo_table()  # 중간에 멈춰도 dpi 값은 남도록
            if progress:
                progress(n, len(todo), f"{p}쪽 {i}번")
    finally:
        if doc is not None:
            doc.close()
        _save_photo_table()
    if not _PHOTO_IMAGE_PRUNED:
        try:
            _photo_image_cache_prune()
        except Exception:  # noqa: BLE001
            pass
    return done, skipped, total


@app.route("/sensory_photos/<int:entry_id>/<int:page_no>/<int:index>.png")
def sensory_photo_image(entry_id, page_no, index):
    import pymupdf as fitz

    e = ENTRY_BY_ID.get(entry_id)
    if e is None or e.get("source_file") != SENSORY_PHOTO_FILE or (page_no, index) not in _sensory_photo_regions(entry_id):
        abort(404)
    # 확대해서 볼 때는 ?dpi=native: 그 사진의 PDF 원본 해상도(대체로 380~430dpi)로 그려
    # 원본 픽셀이 그대로 나온다. 숫자를 주면 그 dpi(72~450)로 그린다.
    raw = request.args.get("dpi", "")
    layer = request.args.get("layer", "all")
    if raw == "native" and layer not in ("all", "photo", "text"):
        abort(400)
    headers = {"Cache-Control": "public, max-age=86400"}
    cacheable = raw == "native" and layer == "all"  # 목록에 보이는 그림만 서버에 저장한다
    if cacheable:
        cached = _photo_image_cache_read(page_no, index)
        if cached is not None:
            return Response(cached, mimetype="image/png", headers=headers)
    doc = fitz.open(str(BASE_DIR / SENSORY_PHOTO_FILE))
    try:
        if raw == "native":
            # PDF 안의 원본 JPEG 픽셀을 다시 그리지 않고 그대로 붙인다(렌더링보다 선명).
            # layer=photo(사진만) / layer=text&k=4(글자와 선만 4배 해상도, 투명 배경)는 확대 화면에서
            # 글자와 선을 PDF 벡터처럼 또렷하게 보이려고 겹쳐 쓰는 두 층이다.
            k = min(max(request.args.get("k", 4, type=int), 1), 6)
            png = sensory_photos.render_region_original(doc, page_no, index, layer=layer, k=k)
        else:
            dpi = min(max(int(raw) if raw.isdigit() else sensory_photos.CLIP_DPI, 72), 450)
            png = sensory_photos.render_region(doc, page_no, index, dpi=dpi)
    finally:
        doc.close()
    if png is None:
        abort(404)
    if cacheable:
        _photo_image_cache_write(page_no, index, png)
    return Response(png, mimetype="image/png", headers=headers)


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

    # 빠르게 열기 (1) 브라우저 캐시: 같은 구간을 다시 열 때 받지 않는다(원본 pdf 의 크기·수정 시각이 키에 들어 있어, 원본이 바뀌면 주소 그대로여도 새로 받는다).
    # (2) 서버 캐시: 한 번 잘라 만든 pdf 를 .parse_cache/pdfcut/ 에 저장해 두고 다시 쓴다. (3) 글꼴 줄이기: 잘라 낸 pdf 마다 붙던 한글 글꼴 전체(약 1MB)를
    # 그 구간에서 쓰는 글자만 남긴다(사진·글자 모양은 그대로, 벡터 유지). fonttools 가 없는 서버면 줄이지 않고 예전처럼 만든다.
    st = pdf_path.stat()
    prefix = hashlib.sha1(f"{filename}|{start}|{end}".encode()).hexdigest()[:12]
    key = prefix + "-" + hashlib.sha1(f"{_PDF_CUT_VERSION}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
    cache_headers = {"Cache-Control": "public, max-age=86400"}

    def respond(data):
        resp = Response(data, mimetype="application/pdf", headers=cache_headers)
        resp.set_etag(key)
        return resp.make_conditional(request)

    if key in request.if_none_match:  # 브라우저가 이미 같은 것을 갖고 있다
        resp = Response(status=304, headers=cache_headers)
        resp.set_etag(key)
        return resp
    cut_dir = PARSE_CACHE_DIR / "pdfcut"
    cache_file = cut_dir / f"{key}.pdf"
    if cache_file.is_file():
        try:
            return respond(cache_file.read_bytes())
        except OSError:
            pass

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
        pdf_bytes = _cut_pdf_bytes(fitz, src, pages, subset=True)
    finally:
        src.close()
    try:  # 저장 실패(읽기 전용 폴더 등)는 캐시 없이 계속한다
        cut_dir.mkdir(parents=True, exist_ok=True)
        tmp = cut_dir / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_bytes(pdf_bytes)
        os.replace(tmp, cache_file)
        for old in cut_dir.glob(f"{prefix}-*.pdf"):  # 같은 구간의 옛(원본이 바뀌기 전) 캐시 정리
            if old != cache_file:
                old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[알림] 잘라 낸 pdf 를 저장하지 못했습니다({filename} {start}-{end}): {exc}")
    return respond(pdf_bytes)


_PDF_CUT_VERSION = 1  # 잘라 내는 방식을 바꾸면 올려서 저장해 둔 옛 pdf 를 쓰지 않게 한다


def _cut_pdf_bytes(fitz, src, pages, subset=True):
    """src 에서 pages(0부터 센 쪽 번호 목록)만 잘라 pdf 바이트로 만든다. subset 이면 쓰는 글자만 남기고 압축한다(fonttools 가 없거나 실패하면
    줄이지 않은 pdf 를 돌려준다)."""
    for use_subset in ((True, False) if subset else (False,)):
        out = fitz.open()
        try:
            for p in pages:
                out.insert_pdf(src, from_page=p, to_page=p)
            if use_subset:
                try:
                    out.subset_fonts()
                    return out.tobytes(garbage=3, deflate=True, deflate_fonts=True)
                except Exception as exc:  # noqa: BLE001  fonttools 없음 등: 줄이지 않고 다시 만든다
                    print(f"[알림] pdf 글꼴 줄이기를 못 했습니다(그대로 보냅니다): {exc}")
                    continue
            return out.tobytes()
        finally:
            out.close()


# ---- 대한민국약전 검색(/kp) ------------------------------------------------------------------
# "[별표 N] ... (제2026-44호).hwpx" 6개 파일로 만드는 별도 화면(파란 테마). 약전 데이터는 /kp 에 처음 들어올 때 읽는다.
# 서버를 켤 때(생약 검색 첫 화면이 뜨는 동안)는 약전 데이터를 읽지 않는다 - 읽는 일이 생약 쪽 첫 응답과 CPU/메모리를 나눠 써서 느려지기 때문이다.
# 미리 읽어 두고 싶으면 환경변수 KP_WARM=1 (서버를 켠 직후 백그라운드에서 읽는다).
from kp_site import kp as _kp_blueprint, warm as _kp_warm

app.register_blueprint(_kp_blueprint)
if os.environ.get("KP_WARM") == "1" and (__name__ != "__main__" or os.environ.get("WERKZEUG_RUN_MAIN") is not None):
    import threading

    threading.Thread(target=_kp_warm, daemon=True).start()


if __name__ == "__main__":
    print(f"[생약검색] {len(LOADED_FILES)}개 hwpx 파일에서 {len(ENTRIES)}개 품목을 불러왔습니다: {LOADED_FILES}")
    print(f"[생약검색] {len(SENSORY_FILES)}개 pdf 파일에서 {len(SENSORY_ENTRIES)}개 품목을 불러왔습니다: {SENSORY_FILES}")
    print(f"[생약검색] {len(CASE_FILES)}개 pdf 파일에서 {len(CASE_ENTRIES)}개 사례를 불러왔습니다: {CASE_FILES}")
    print(f"[생약검색] {len(PREP_FILES)}개 hwpx 파일에서 {len(PREP_ENTRIES)}개 생약제제를 불러왔습니다: {PREP_FILES}")
    app.run(debug=True)
