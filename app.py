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

import re
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

from parse_hwpx import parse_hwpx
from parse_sensory_pdf import build_sensory_entries

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)

ENTRIES = []  # 공정서(hwpx)에서 파싱된 생약 목록 (앱 시작 시 1회 로드)
SENSORY_ENTRIES = []  # 관능검사해설서(pdf)에서 파싱된 생약 목록


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _source_tag(filename: str) -> str:
    """파일명으로 출처를 구분해 배지를 붙인다: 약전 -> KP, 생약(한약)규격집 -> KHP."""
    if "약전" in filename:
        return "KP"
    if "생규" in filename or "생약규격" in filename:
        return "KHP"
    return ""


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

    for e in entries:
        for s in e["sections"]:
            if s.get("items"):
                walk(s["items"])
            ref_name = s.get("ref_name")
            if ref_name and ref_name in name_to_id:
                s["ref_id"] = name_to_id[ref_name]


def load_entries():
    hwpx_files = sorted(BASE_DIR.glob("*.hwpx"))
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
    """이 폴더의 *.pdf(관능검사해설서)를 찾아 파싱한다. id는 공정서 항목
    다음부터 이어서 매겨, 두 목록을 하나의 id 공간으로 조회할 수 있게 한다."""
    pdf_files = sorted(BASE_DIR.glob("*.pdf"))
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


ENTRIES, LOADED_FILES = load_entries()
SENSORY_ENTRIES, SENSORY_FILES = load_sensory_entries(start_id=len(ENTRIES))
ENTRY_BY_ID = {e["id"]: e for e in ENTRIES + SENSORY_ENTRIES}


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


@app.route("/api/search")
def api_search():
    q = _normalize(request.args.get("q", ""))
    if not q:
        return jsonify({"official": [], "sensory": []})

    official = _search(ENTRIES, q)[:50]
    sensory = _search(SENSORY_ENTRIES, q)[:50]
    return jsonify(
        {
            "official": [summary(e) for e in official],
            "sensory": [summary(e) for e in sensory],
        }
    )


@app.route("/api/test_item_search")
def api_test_item_search():
    """시험항목(확인시험/순도시험 및 그 하위 항목인 이물·잔류농약·납·비소·
    수은·카드뮴·이산화황·벤조피렌·곰팡이독소 등)으로 공정서 품목을 찾는다.
    "확인시험"/"순도시험"은 그 이름의 항목(section)이 있는지로, 그 외에는
    순도시험 항목의 본문에 그 낱말이 나오는지로 찾는다."""
    item = (request.args.get("item") or "").strip()
    if not item:
        return jsonify([])

    matches = []
    if item in ("확인시험", "순도시험"):
        for e in ENTRIES:
            if any(s["label"] == item for s in e["sections"]):
                matches.append(e)
    else:
        needle = _normalize(item)
        for e in ENTRIES:
            for s in e["sections"]:
                if s["label"] == "순도시험" and needle in _normalize(s.get("text", "")):
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
            "name_only": e["name_only"],
            "hanja": e["hanja"],
            "english_name": e["english_name"],
            "latin_name": e["latin_name"],
            "definition": e["definition"],
            "definition_parts": e.get("definition_parts", []),
            "sections": e["sections"],
            "source_tag": e.get("source_tag", ""),
        }
    )


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
        out = fitz.open()
        try:
            out.insert_pdf(src, from_page=start, to_page=end)
            pdf_bytes = out.tobytes()
        finally:
            out.close()
    finally:
        src.close()
    return Response(pdf_bytes, mimetype="application/pdf")


if __name__ == "__main__":
    print(f"[생약검색] {len(LOADED_FILES)}개 hwpx 파일에서 {len(ENTRIES)}개 품목을 불러왔습니다: {LOADED_FILES}")
    print(f"[생약검색] {len(SENSORY_FILES)}개 pdf 파일에서 {len(SENSORY_ENTRIES)}개 품목을 불러왔습니다: {SENSORY_FILES}")
    app.run(debug=True)
