# -*- coding: utf-8 -*-
"""
대한민국약전 검색(/kp): "[별표 N] ... (제2026-44호).hwpx" 6개 파일을 읽어 약전 항목을 검색·열람하게 해 주는 화면과 API.
생약 검색(app.py)에 블루프린트로 붙는다(app.register_blueprint(kp)). 주소가 /kp 로 시작한다.

- 데이터는 처음 /kp 에 들어올 때(또는 서버를 켠 직후 백그라운드에서) 읽는다. 파싱 결과는 .parse_cache 에 저장되어 다음부터는 몇 초 안에 읽힌다.
  미리 만들어 두려면 `python build_kp_cache.py` (그림 변환까지 한 번에).
- 항목 본문은 압축한 HTML 로 들고 있다가 열 때 풀어서 보낸다(메모리를 아끼려고).
"""

import hashlib
import io
import os
import pickle
import re
import sys
import threading
import zipfile
import zlib
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, render_template, request

import parse_kp

BASE_DIR = Path(__file__).resolve().parent
KP_DIR = BASE_DIR / "약전검색"  # 약전 검색에 쓰는 원본 파일([별표 1] ~ [별표 6] 의 hwpx 와 같은 이름의 PDF 12개)을 두는 폴더
CACHE_DIR = BASE_DIR / ".parse_cache"
IMG_CACHE_DIR = CACHE_DIR / "kp_img"
IMG_PIPE_VER = 3  # 그림 변환 방식을 바꾸면 올린다(저장된 변환 그림이 새로 만들어진다)

kp = Blueprint("kp", __name__, url_prefix="/kp")

DOC_LABEL = {n: v[1] for n, v in parse_kp.DOCS.items()}


def _normalize(s):
    return re.sub(r"\s+", "", s or "").lower()


# ---- 파싱 결과 캐시 -----------------------------------------------------------------------
def _signature():
    h = hashlib.sha1()
    h.update(f"py{sys.version_info[:2]}".encode())
    for name in ("parse_kp.py", "parse_hwpx.py"):
        try:
            h.update((BASE_DIR / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()


# 캐시 이름에 들어가는 코드 값은 이 모듈을 불러올 때(웹앱이 시작해 parse_kp.py 를 메모리에 올리는 순간) 한 번만 정하고 계속 쓴다.
# 데이터를 읽을 때마다 디스크의 파일을 다시 읽어 정하면, git pull 뒤 Reload 전에 /kp 를 처음 연 경우 "옛 코드로 파싱한 결과"가 "새 코드의
# 이름"으로 저장돼 Reload 후에도 옛 결과가 쓰인다. 시작 때 정해 두면 옛 코드는 옛 이름으로만 저장해 새 코드와 섞이지 않는다.
_SIG = _signature()


_FIRST_HANGUL_RE = re.compile(r"[가-힣]")


def _drug_sort_key(e):
    """의약품등 검색칸 목록의 가나다 순서. 이름 앞의 "(", "-", 숫자, "%", 영문 같은 글자는 건너뛰고, 제일 먼저 나오는 한글부터의 글자로 정렬한다
    ("(-)에버나모닌" -> "에버나모닌", "10% 염화나트륨 주사액" -> "염화나트륨 주사액", "2-데옥시-... 주사액" -> "데옥시-... 주사액").
    한글이 하나도 없는 이름은 맨 뒤에 둔다. 같으면 원래 이름, 영문명 순."""
    m = _FIRST_HANGUL_RE.search(e["ko"])
    return (0, e["ko"][m.start():]) if m else (1, e["ko"]), e["ko"], e["en"]


def hwpx_path(docno):
    head = parse_kp.DOCS[docno][0]
    for p in sorted(KP_DIR.glob("*.hwpx")):
        if p.name.startswith(head):
            return p
    return None


def _parse_cached(path, docno, sig):
    st = path.stat()
    key = hashlib.sha1(f"{st.st_size}|{st.st_mtime_ns}|{sig}".encode()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"kp{docno}-{key}.pkl"
    if cache_file.is_file():
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except Exception:  # noqa: BLE001  깨진 캐시는 무시하고 다시 파싱한다
            pass
    ents = parse_kp.parse_doc(path, docno)
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(ents, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, cache_file)
        for old in CACHE_DIR.glob(f"kp{docno}-*.pkl"):
            if old != cache_file:
                old.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[알림] 약전 파싱 캐시를 저장하지 못했습니다({path.name}): {exc}")
    return ents


# ---- 데이터 ------------------------------------------------------------------------------
class _Data:
    pass


_lock = threading.Lock()
_data = None


def get_data():
    """약전 데이터를 (처음 한 번) 읽어 돌려준다."""
    global _data
    if _data is not None:
        return _data
    with _lock:
        if _data is not None:
            return _data
        d = _Data()
        d.entries = []
        d.files = {}
        sig = _SIG
        for docno in sorted(parse_kp.DOCS):
            path = hwpx_path(docno)
            if path is None:
                print(f"[경고] 약전 파일이 없습니다: {parse_kp.DOCS[docno][0]} ... .hwpx")
                continue
            try:
                ents = _parse_cached(path, docno, sig)
            except Exception as exc:  # noqa: BLE001
                print(f"[경고] {path.name} 파싱 실패: {exc}")
                continue
            d.files[docno] = path.name
            for e in ents:
                e["id"] = len(d.entries)
                e["cat"] = DOC_LABEL[docno]
                e["kind"] = parse_kp.DOCS[docno][2]
                e["n_ko"] = _normalize(e["ko"])
                e["n_en"] = _normalize(e["en"])
                e["n_head"] = _normalize(e.get("head", ""))
                # 별표 4(의약품각조 제2부) 품목은 분류 제목("2) 생물학적제제 등", "5) 첨가제" ...)으로도 찾는다. 별표 3 품목의 "의약품"은 제외
                e["n_group"] = _normalize(e.get("group", "")) if e["doc"] == 4 else ""
                d.entries.append(e)  # n_text(본문 검색용 정규화 본문)는 본문 검색을 처음 쓸 때 만든다(ensure_text_index)
        d.text_ready = False
        d.by_id = d.entries
        d.drug = [e for e in d.entries if e["kind"] == "drug"]
        d.ref = [e for e in d.entries if e["kind"] == "ref"]
        d.drug_sorted = sorted(d.drug, key=_drug_sort_key)
        # 본문 안에서 링크를 걸 일반시험법/일반정보 이름(긴 이름 먼저)
        targets = {}
        for e in d.ref:
            if e["doc"] in (5, 6) and len(e["n_ko"]) >= 4 and "개요" not in e["ko"]:
                targets.setdefault(e["n_ko"], e["id"])
        d.link_ids = targets
        if targets:
            names = sorted(targets, key=len, reverse=True)
            # 원문 이름의 글자 사이 공백을 건너뛰고 찾는다("박층 크로마토그래프법")
            d.link_re = re.compile("|".join(r"\s*".join(re.escape(c) for c in n) for n in names), re.IGNORECASE)
        else:
            d.link_re = None
        d.drug_by_name = {}
        for e in d.drug:
            d.drug_by_name.setdefault(_normalize(re.sub(r"\(.*?\)", "", e["ko"])), e["id"])
        _data = d
        return d


_text_lock = threading.Lock()


def ensure_text_index(d):
    """본문 검색에 쓰는 정규화 본문(공백 제거·소문자, 약 500만 글자)을 처음 한 번만 만든다. 서버를 켤 때나 약전 첫 화면을 열 때는 만들지 않는다."""
    if d.text_ready:
        return
    with _text_lock:
        if d.text_ready:
            return
        for e in d.entries:
            e["n_text"] = _normalize(e["text"])
        d.text_ready = True


_WORD_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9\-]{2,}")
# 낱말 끝의 조사·어미("시험액으로", "시험한다" ...)를 떼어 같은 말로 센다. 모호한 한 글자(은·는·이·가·의·도 ...)는 건드리지 않는다("흡광도"가 "흡광"이 되지 않게)
_JOSA_RE = re.compile(r"^([가-힣]{2,}?)(?:에서는|으로써|으로서|으로|에서|에는|이며|으며|이다|이고|이면|한다|하여|하는|하고|하기|하며|되는|된다|할|한|된|를|을|에)$")
_JOSA1 = ("로부터", "보다", "부터", "까지", "이라", "은", "는", "이", "가", "의", "도", "로", "과", "와", "만")


def ensure_word_index(d):
    """본문 검색칸 추천 문구용 낱말 색인: 본문에 나오는 한글(2글자 이상)·영문 낱말을 세어 정렬해 둔다. 처음 쓸 때 한 번만 만든다."""
    if getattr(d, "words", None) is not None:
        return
    with _text_lock:
        if getattr(d, "words", None) is not None:
            return
        counts = {}
        for e in d.entries:
            if e.get("nolink"):
                continue
            for w in _WORD_RE.findall(e["text"]):
                w = w.lower()
                m = _JOSA_RE.match(w)
                if m:
                    w = m.group(1)
                counts[w] = counts.get(w, 0) + 1
        # 모호한 조사(은·는·이·가·의·도 ...)는, 그것을 뗀 낱말이 본문에 그만큼 이상 나올 때만 떼어 합친다("시험은"→"시험", 그러나 "흡광도"는 그대로)
        for w, c in list(counts.items()):
            for suf in _JOSA1:
                if w.endswith(suf) and len(w) - len(suf) >= 2:
                    stem = w[: -len(suf)]
                    if counts.get(stem, 0) >= c:
                        counts[stem] += c
                        del counts[w]
                        break
        d.word_count = counts
        d.words = sorted(counts)


@kp.route("/api/suggest")
def api_suggest():
    """본문 검색칸 추천 문구: 입력한 글자로 시작하는 본문 낱말을 많이 나오는 순으로 돌려준다(최대 8개)."""
    import bisect

    d = get_data()
    q = _normalize(request.args.get("q", ""))
    if len(q) < 2:
        return jsonify({"words": []})
    ensure_word_index(d)
    words, counts = d.words, d.word_count
    i = bisect.bisect_left(words, q)
    found = []
    while i < len(words) and words[i].startswith(q) and len(found) < 5000:
        if words[i] != q:
            found.append(words[i])
        i += 1
    found.sort(key=lambda w: (-counts[w], len(w), w))
    return jsonify({"words": [{"w": w, "n": counts[w]} for w in found[:8]]})


def warm():
    """서버를 켠 직후 백그라운드에서 데이터를 읽어 둔다(첫 접속자가 기다리지 않게)."""
    try:
        get_data()
    except Exception as exc:  # noqa: BLE001
        print(f"[경고] 약전 데이터를 미리 읽지 못했습니다: {exc}")


def summary(e):
    return {
        "id": e["id"],
        "ko": e["ko"],
        "en": e["en"],
        "no": e["no"],
        "doc": e["doc"],
        "cat": e["cat"],
        "group": e["group"],
        "part": e["part"],
        "kind": e["kind"],
        "nolink": bool(e.get("nolink")),
    }


# ---- 화면 / API ---------------------------------------------------------------------------
@kp.route("/")
def index():
    d = get_data()
    return render_template(
        "kp.html",
        drug_count=len(d.drug),
        ref_count=len(d.ref),
        loaded=sorted(d.files),
        missing=[n for n in parse_kp.DOCS if n not in d.files],
    )


def _json(payload, max_age=0):
    """큰 목록 응답: 브라우저가 매번 ETag 로 '안 바뀜(304)'만 확인한다. 항목 번호(id)가 데이터 파일/파서가 바뀔 때마다 달라질 수 있어서,
    목록을 몇 분씩 그냥 쓰면 이름과 본문이 어긋난다(옛 목록의 번호로 새 항목을 연다). 그래서 기본은 캐시하지 않고 확인만 한다."""
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = f"public, max-age={max_age}" if max_age else "no-cache"
    resp.add_etag()
    return resp.make_conditional(request)


@kp.route("/api/list")
def api_list():
    d = get_data()
    tab = request.args.get("tab", "drug")
    items = d.drug_sorted if tab == "drug" else d.ref
    return _json([summary(e) for e in items])


def _name_search(entries, q, limit=100):
    exact, starts, name_in, head_in, group_in = [], [], [], [], []
    for e in entries:
        if e.get("nolink"):
            continue
        nk, ne = e["n_ko"], e["n_en"]
        no = e["no"]
        if q == nk or q == ne or (no and q == _normalize(f"{no}.{e['ko']}")):
            exact.append(e)
        elif nk.startswith(q) or ne.startswith(q):
            starts.append(e)
        elif q in nk or q in ne:
            name_in.append(e)
        elif q in e["n_head"]:
            head_in.append(e)
        elif q in e.get("n_group", ""):
            group_in.append(e)
    # 분류 제목으로 찾은 품목은 한 분류가 통째로 나오도록(예: 첨가제 174개) 개수 제한 없이 이름순으로 뒤에 붙인다
    group_in.sort(key=lambda e: (e["ko"], e["en"]))
    return (exact + starts + name_in + head_in)[:limit] + group_in


@kp.route("/api/search")
def api_search():
    d = get_data()
    q = _normalize(request.args.get("q", ""))
    tab = request.args.get("tab", "drug")
    if not q:
        return jsonify({"items": []})
    items = _name_search(d.drug if tab == "drug" else d.ref, q)
    if tab == "drug":  # 의약품등 검색 결과는 전체 목록과 같이, 이름 맨 앞의 한글(괄호·숫자·영문은 건너뜀) 가나다 순으로 보여 준다
        items = sorted(items, key=_drug_sort_key)
    return jsonify({"items": [summary(e) for e in items]})


def _snippet(e, q_raw, q_norm, width=46):
    """본문에서 검색어 주변 글을 잘라 <mark> 로 강조한 HTML 로 돌려준다(공백이 달라도 찾는다)."""
    chars = [c for c in q_raw if not c.isspace()]
    if not chars:
        return ""
    rx = re.compile(r"\s*".join(re.escape(c) for c in chars), re.IGNORECASE)
    text = e["text"]
    m = rx.search(text)
    if not m:
        return ""
    s, t = max(0, m.start() - width), min(len(text), m.end() + width)
    esc = lambda x: x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    pre = "…" if s > 0 else ""
    post = "…" if t < len(text) else ""
    return f"{pre}{esc(text[s:m.start()])}<mark>{esc(m.group(0))}</mark>{esc(text[m.end():t])}{post}".replace("\n", " ")


TEXT_PAGE = 100  # 본문 검색 결과를 한 번에 보내는 개수(파일별)


@kp.route("/api/text")
def api_text():
    """본문 검색: 모든 약전 항목의 본문에서 검색어를 찾는다. 결과는 [별표 1], [별표 2], ... 파일 순서로 묶고, 파일 안에서는 문서에 나오는
    순서대로 보낸다. 파일마다 처음 TEXT_PAGE 개만 보내고, doc/offset 을 주면 그 파일의 다음 결과를 이어서 보낸다."""
    d = get_data()
    q_raw = request.args.get("q", "").strip()
    q = _normalize(q_raw)
    if len(q) < 2:
        return jsonify({"groups": []})
    ensure_text_index(d)
    only_doc = request.args.get("doc", type=int)
    offset = max(0, request.args.get("offset", 0, type=int))
    by_doc = {}
    for e in d.entries:  # d.entries 는 파일 순서, 파일 안에서는 문서 순서다
        if (only_doc is not None and e["doc"] != only_doc) or e.get("nolink"):
            continue
        c = e["n_text"].count(q)
        if c or q in e["n_ko"] or q in e["n_en"]:
            by_doc.setdefault(e["doc"], []).append((e["id"], c))

    def build(doc, hits, start):
        items = []
        for eid, c in hits[start : start + TEXT_PAGE]:
            e = d.entries[eid]
            s = summary(e)
            s["count"] = c
            s["snippet"] = _snippet(e, q_raw, q)
            items.append(s)
        return items

    if only_doc is not None:
        return jsonify({"doc": only_doc, "items": build(only_doc, by_doc.get(only_doc, []), offset)})
    groups = []
    for doc in sorted(by_doc):
        hits = by_doc[doc]
        groups.append(
            {
                "doc": doc,
                "label": f"{parse_kp.DOCS[doc][0]} {DOC_LABEL[doc]}",
                "total": len(hits),
                "items": build(doc, hits, 0),
            }
        )
    return jsonify({"groups": groups})


def _linkify(d, html_text, self_id):
    """본문 안의 일반시험법/일반정보 이름과 「품목명」에 눌러서 그 항목으로 가는 링크를 단다(태그 안쪽은 건드리지 않는다)."""
    if d.link_re is None:
        return html_text
    parts = re.split(r"(<[^>]*>)", html_text)
    depth_a = 0
    for i, part in enumerate(parts):
        if part.startswith("<"):
            low = part[:3].lower()
            if low == "<a ":
                depth_a += 1
            elif part.lower().startswith("</a"):
                depth_a = max(0, depth_a - 1)
            continue
        if depth_a or not part.strip():
            continue

        def repl(m):
            tid = d.link_ids.get(_normalize(m.group(0)))
            if tid is None or tid == self_id:
                return m.group(0)
            return f'<a href="#" class="ref-link" data-id="{tid}">{m.group(0)}</a>'

        part = d.link_re.sub(repl, part)

        def drug_repl(m):
            tid = d.drug_by_name.get(_normalize(m.group(2)))
            if tid is None or tid == self_id:
                return m.group(0)
            return f'{m.group(1)}<a href="#" class="ref-link" data-id="{tid}">{m.group(2)}</a>{m.group(3)}'

        parts[i] = re.sub(r"([「｢])([^」｣<>]{1,40})([」｣])", drug_repl, part)
    return "".join(parts)


def _link_words(html_text, words):
    """본문 글(태그·기존 링크 안쪽 제외)에서 앞뒤가 한글이 아닌 낱말 words({낱말: 항목 번호})에 링크를 단다. 이름이 세 글자라 일반 링크(_linkify)에서
    빠진 "표준품", "표준액"을 92번(개요) 항목 안에서만 걸 때 쓴다."""
    # 뒤에는 한글이 바로 이어지지 않거나, 조사("표준품은", "표준액을" ...)만 이어질 때 건다
    rx = re.compile("(?<![가-힣])(" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + ")(?=(?:은|는|이|가|을|를|의|와|과|도|에|으로|로)(?![가-힣])|(?![가-힣]))")
    parts = re.split(r"(<[^>]*>)", html_text)
    depth_a = 0
    for i, part in enumerate(parts):
        if part.startswith("<"):
            if part[:3].lower() == "<a ":
                depth_a += 1
            elif part.lower().startswith("</a"):
                depth_a = max(0, depth_a - 1)
            continue
        if depth_a or not part.strip():
            continue
        parts[i] = rx.sub(lambda m: f'<a href="#" class="ref-link" data-id="{words[m.group(1)]}">{m.group(1)}</a>', part)
    return "".join(parts)


@kp.route("/api/entry/<int:entry_id>")
def api_entry(entry_id):
    d = get_data()
    if not (0 <= entry_id < len(d.entries)):
        abort(404)
    e = d.entries[entry_id]
    if e.get("nolink"):  # 제목뿐인 항목은 열 내용이 없다
        abort(404)
    body = zlib.decompress(e["html_z"]).decode("utf-8")
    body = _linkify(d, body, e["id"])
    if e["doc"] == 5 and e["no"] == "92":  # 92. 표준품, 시약·시액 등 (개요): 하위 항목 92-1 표준품, 92-4 표준액으로도 링크를 건다
        words = {x["ko"]: x["id"] for x in d.ref if x["doc"] == 5 and x["no"] in ("92-1", "92-4")}
        if words:
            body = _link_words(body, words)
    out = summary(e)
    out["html"] = body
    # '원문' 버튼을 보일지(원문 PDF 와 위치표가 있는 항목만): 0 이면 없음, 아니면 자르는 방식 번호(주소 끝에 붙여, 방식이 바뀌면 브라우저가 옛 PDF 를 다시 쓰지 않게 한다)
    out["orig"] = ORIG_VER if orig_range(d, e) is not None else 0
    resp = jsonify(out)
    # 데이터 파일이나 파서가 바뀌면 바로 새 내용이 보이도록, 매번 서버에 "안 바뀜(304)"만 확인한다(ETag)
    resp.headers["Cache-Control"] = "private, no-cache"
    resp.add_etag()
    return resp.make_conditional(request)


# ---- 그림 ---------------------------------------------------------------------------------
def _convert_image(data, ext, crop):
    """BinData 그림 -> (바이트, MIME). 못 바꾸면 None."""
    if ext in ("wmf", "emf"):
        try:
            import kp_wmf

            svg = kp_wmf.to_svg(data, ext)
        except Exception as exc:  # noqa: BLE001
            print(f"[알림] {ext} 그림 변환 실패: {exc}")
            svg = None
        return (svg.encode("utf-8"), "image/svg+xml") if svg else None
    if ext not in ("png", "jpg", "jpeg", "gif", "bmp"):
        return None
    if ext in ("png", "jpg", "jpeg", "gif") and not crop:
        return data, {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}[ext]
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.load()
    if crop:
        l, t, r, b = crop
        w, h = im.size
        box = (int(l * w), int(t * h), max(int(l * w) + 1, int(r * w)), max(int(t * h) + 1, int(b * h)))
        im = im.crop(box)
    if im.mode not in ("RGB", "RGBA", "L"):
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.width > 1600:
        im.thumbnail((1600, 4000))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue(), "image/png"


def image_bytes(doc, name, crop_str=""):
    """한 그림을 (바이트, MIME)으로. 변환한 결과는 .parse_cache/kp_img 에 저장해 다음부터 바로 쓴다."""
    crop = None
    if crop_str:
        try:
            crop = tuple(max(0.0, min(1.0, float(x))) for x in crop_str.split(","))
            if len(crop) != 4:
                crop = None
        except ValueError:
            crop = None
    key = f"{doc}-{name}-{(crop_str or 'full').replace(',', '_')}-v{IMG_PIPE_VER}"
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg"), ("gif", "image/gif"), ("svg", "image/svg+xml")):
        f = IMG_CACHE_DIR / f"{key}.{ext}"
        if f.is_file():
            return f.read_bytes(), mime
    path = hwpx_path(doc)
    if path is None:
        return None
    with zipfile.ZipFile(path) as z:
        member = next((n for n in z.namelist() if n.startswith(f"BinData/{name}.")), None)
        if member is None:
            return None
        data = z.read(member)
    ext = member.rsplit(".", 1)[-1].lower()
    res = _convert_image(data, ext, crop)
    if res is None:
        return None
    out, mime = res
    try:
        IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/svg+xml": "svg"}[mime]
        (IMG_CACHE_DIR / f"{key}.{suffix}").write_bytes(out)
    except OSError:
        pass
    return out, mime


# ---- 원문(PDF) ----------------------------------------------------------------------------
# "[별표 N] ... (제2026-44호).pdf"(hwpx 를 한글에서 PDF 로 바꾼 것, 이 폴더에 둔다)에서 한 항목의 부분만 보이게 한 PDF 를 만들어 준다.
# 항목 제목이 놓인 자리(쪽, 세로 위치)는 build_kp_orig.py 가 kp_orig_pos.json 에 적어 둔다(PDF 는 1단 쓰기). 한 항목은 자기 제목 자리부터
# 다음 항목(또는 분류 제목) 자리 앞까지이고, 그 범위 밖의 글은 지운다(흰색으로 덮고 글자 제거).
ORIG_POS_FILE = BASE_DIR / "kp_orig_pos.json"
ORIG_CACHE_DIR = CACHE_DIR / "kp_orig"
from orig_pdf import ORIG_TOP, ORIG_VER, cut_pdf as _cut_orig_pdf, svg_clip_to_viewbox as _svg_clip_to_viewbox  # PDF 자르기는 orig_pdf.py (생약 검색과 함께 쓴다)


def entry_keys(ents):
    """항목마다 ('번호|이름', 같은 키가 또 나오면 #번호) 키. build_kp_orig.py 가 위치표를 만들 때와 같은 방식이다."""
    seen = {}
    keys = []
    for e in ents:
        k = f"{e.get('no') or ''}|{e['ko']}"
        seen[k] = seen.get(k, 0) + 1
        keys.append(k if seen[k] == 1 else f"{k}#{seen[k]}")
    return keys


_orig_lock = threading.Lock()
_orig = None


def _orig_index(d):
    """{문서번호: {pdf: 경로, start: {항목 id: 위치}, order: [모든 위치(정렬)]}}. 위치표나 PDF 가 없는 문서는 빠진다."""
    global _orig
    if _orig is not None:
        return _orig
    with _orig_lock:
        if _orig is not None:
            return _orig
        idx = {}
        try:
            import json

            data = json.loads(ORIG_POS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"docs": {}}
        for docno_s, info in data.get("docs", {}).items():
            docno = int(docno_s)
            pdf = KP_DIR / info["pdf"]
            if not pdf.is_file():
                continue
            try:
                import pymupdf

                with pymupdf.open(str(pdf)) as doc:
                    if len(doc) != info["pages"]:  # PDF 를 다시 만들어 쪽 수가 달라졌으면(위치표가 낡았다) 쓰지 않는다
                        print(f"[알림] 별표 {docno} PDF 쪽 수가 위치표와 다릅니다. build_kp_orig.py 를 다시 실행하세요.")
                        continue
            except Exception as exc:  # noqa: BLE001
                print(f"[알림] 별표 {docno} PDF 를 열지 못했습니다: {exc}")
                continue
            ents = [e for e in d.entries if e["doc"] == docno]
            by_key = {k: (e["id"]) for k, e in zip(entry_keys(ents), ents)}
            start = {}
            for key, page, y in info["entries"]:
                if key in by_key:
                    start[by_key[key]] = (page, y)
            order = sorted(set(start.values()) | {tuple(m) for m in info.get("marks", [])})
            idx[docno] = {"pdf": pdf, "start": start, "order": order, "pages": info["pages"]}
        _orig = idx
        return idx


def orig_range(d, e):
    """항목 e 의 원문 범위 (시작 위치, 끝 위치 또는 None, 문서 정보). 원문을 보여 줄 수 없으면 None."""
    info = _orig_index(d).get(e["doc"])
    if info is None or e["id"] not in info["start"]:
        return None
    s = info["start"][e["id"]]
    end = next((p for p in info["order"] if p > s), None)
    return s, end, info


def _orig_image_fixes(e):
    """항목 본문의 그림(<img>)을 차례로 훑어, WMF/EMF 그림은 웹 화면에서 쓰는 SVG 바이트를, 그 밖의 그림은 None 을 담은 목록.
    한글이 WMF/EMF 그림을 PDF 로 바꿀 때 그림이 잘려서 들어가는 경우가 있어(예: 슈도에페드린염산염의 화학식), 원문 PDF 의 그 자리에 SVG 를 다시 그린다."""
    import html as _html

    body = zlib.decompress(e["html_z"]).decode("utf-8")
    fixes = []
    for m in re.finditer(r"<img\b[^>]*>", body):
        tag = m.group(0)
        ext = re.search(r'data-ext="(\w+)"', tag)
        src = re.search(r'src="/kp/img/(\d+)/([A-Za-z0-9_\-]+)(?:\?c=([^"]*))?"', tag)
        fix = None
        if ext and ext.group(1) in ("wmf", "emf") and src:
            res = image_bytes(int(src.group(1)), src.group(2), _html.unescape(src.group(3) or ""))
            if res and res[1] == "image/svg+xml":
                fix = res[0]
        fixes.append(fix)
    return fixes if any(fixes) else None


@kp.route("/api/orig/<int:entry_id>")
def api_orig(entry_id):
    """'원문' 버튼: 그 항목 부분만 보이는 약전 원문 PDF."""
    d = get_data()
    if not (0 <= entry_id < len(d.entries)):
        abort(404)
    e = d.entries[entry_id]
    rng = orig_range(d, e)
    if rng is None or e.get("nolink"):
        abort(404)
    start, end, info = rng
    st = info["pdf"].stat()
    fixes = _orig_image_fixes(e)
    fix_sig = hashlib.sha1(b"|".join(f or b"-" for f in fixes)).hexdigest()[:8] if fixes else ""
    key = hashlib.sha1(f"{ORIG_VER}|{info['pdf'].name}|{st.st_size}|{st.st_mtime_ns}|{start}|{end}|{fix_sig}".encode()).hexdigest()[:24]
    # 브라우저는 저장해 두되 열 때마다 서버에 "안 바뀜(304)"만 확인한다(PDF 나 자르는 방식이 바뀌면 바로 새것이 보인다)
    headers = {"Cache-Control": "no-cache", "Content-Disposition": "inline"}

    def respond(data):
        resp = Response(data, mimetype="application/pdf", headers=headers)
        resp.set_etag(key)
        return resp.make_conditional(request)

    cache_file = ORIG_CACHE_DIR / f"{key}.pdf"
    if cache_file.is_file():
        try:
            return respond(cache_file.read_bytes())
        except OSError:
            pass
    title = f"{(e['no'] + '. ') if e.get('no') and e['kind'] == 'ref' else ''}{e['ko']} (대한민국약전 원문)"
    data = _cut_orig_pdf(info["pdf"], start, end, title, fixes)
    try:  # 저장 실패(읽기 전용 폴더 등)는 캐시 없이 계속한다
        ORIG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ORIG_CACHE_DIR / f"{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, cache_file)
    except OSError as exc:
        print(f"[알림] 원문 pdf 를 저장하지 못했습니다: {exc}")
    return respond(data)


@kp.route("/img/<int:doc>/<name>")
def api_image(doc, name):
    if doc not in parse_kp.DOCS or not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name):
        abort(404)
    res = image_bytes(doc, name, request.args.get("c", ""))
    if res is None:
        abort(404)
    data, mime = res
    return Response(data, mimetype=mime, headers={"Cache-Control": "public, max-age=86400"})
