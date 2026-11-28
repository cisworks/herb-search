# -*- coding: utf-8 -*-
"""
약전 검색(/kp) '원문' 버튼용 위치표(kp_orig_pos.json)를 만든다.
"[별표 N] ... (제2026-44호).pdf"(hwpx 를 한글에서 1단 쓰기로 고쳐 PDF 로 바꾼 것)에서 항목마다 제목이 놓인 자리(쪽, 세로 위치)를 찾아 적는다.
서버는 이 표로 항목의 시작~다음 항목 시작 사이만 보이게 해서 '원문' PDF 를 만든다(kp_site.py 의 /api/orig).

사용: python build_kp_orig.py        (PDF 를 새로 만들었거나 약전 파서가 바뀐 뒤 한 번)
"""
import json
import os
import re
import sys
import time

import pymupdf

import kp_site

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "kp_orig_pos.json")


def norm(s):
    return re.sub(r"\s+", "", s or "").lower()


def page_lines(page):
    """한 쪽의 줄들을 읽는 순서(위→아래, 같은 높이면 왼쪽→오른쪽)로: (위, 아래, 왼쪽 x, 오른쪽 x, 글자).
    한 줄이 "성 상" 라벨과 본문처럼 둘로 쪼개져 위 좌표가 조금 다를 수 있어서, 위 좌표가 2.5 이내인 줄은 한 줄로 보고 x 순서로 둔다."""
    raw = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            t = "".join(sp["text"] for sp in ln["spans"])
            if not t.strip():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            raw.append((y0, y1, x0, x1, t))
    # 쪽 번호("- 596 -")는 줄 목록에서 뺀다: 제목이 쪽 끝에서 다음 쪽으로 이어질 때 중간에 끼어 제목을 못 찾게 한다
    raw = [r for r in raw if not re.fullmatch(r"-\s*\d+\s*-", r[4].strip())]
    raw.sort(key=lambda r: (r[0], r[2]))
    out = []
    row = []
    for r in raw:
        if row and r[0] - row[0][0] > 2.5:
            out.extend(sorted(row, key=lambda q: q[2]))
            row = []
        row.append(r)
    out.extend(sorted(row, key=lambda q: q[2]))
    return out


def load_lines(pdf):
    doc = pymupdf.open(pdf)
    lines = []  # (쪽, 위, 아래, x0, x1, 글자, 공백뺀글자)
    for pno in range(len(doc)):
        for y0, y1, x0, x1, t in page_lines(doc[pno]):
            lines.append((pno, y0, y1, x0, x1, t, norm(t)))
    n_pages = len(doc)
    doc.close()
    return lines, n_pages


def find_from(lines, i0, nko, nen="", prefix=False, maxjoin=3):
    """lines[i0:] 에서 nko 와 같은 줄(같은 쪽에서 이어지는 최대 3줄)을 찾는다. nen 이 있으면 바로 뒤 줄에 영문명 앞부분이 있어야 한다."""
    n = len(lines)
    for i in range(i0, n):
        p = lines[i][0]
        if not nko.startswith(lines[i][6]) and not (prefix and lines[i][6].startswith(nko)):
            continue
        if prefix and lines[i][6].startswith(nko):
            return i, i
        joined = ""
        for k in range(maxjoin):
            j = i + k
            if j >= n:  # (제목이 쪽 끝에서 다음 쪽으로 이어질 수도 있어 쪽이 달라도 잇는다)
                break
            joined += lines[j][6]
            if joined == nko:
                if nen:
                    nxt = "".join(lines[m][6] for m in range(j + 1, min(j + 3, n)))
                    if nen[:8] not in nxt:
                        break
                return i, j
            if not nko.startswith(joined):
                break
    return None


def locate_doc(docno, ents, pdf):
    lines, n_pages = load_lines(pdf)
    # 일반시험법 등은 앞쪽에 "1. 감마선 측정법 ..." 목차 쪽이 있다: 번호 줄이 많은 맨 앞 쪽들은 건너뛴다
    cnt = {}
    for ln in lines:
        if re.match(r"^\d+\.\s", ln[5]):
            cnt[ln[0]] = cnt.get(ln[0], 0) + 1
    toc_end = 0
    while cnt.get(toc_end + 1, 0) >= 25:
        toc_end += 1
    cur = next((i for i, ln in enumerate(lines) if ln[0] > toc_end), 0) if toc_end else 0

    keys = kp_site.entry_keys(ents)  # 서버가 항목을 찾을 때 쓰는 키와 같다
    pos = {}  # 항목 번호 -> (쪽, 단, 위)
    line_of = {}
    for idx, e in enumerate(ents):
        ko = e["ko"]
        no = e.get("no") or ""
        prefix = False
        nen = ""
        if docno in (3, 4):
            cands = [norm(ko)]
            nen = norm(e.get("en") or "")
        elif docno == 5 and re.fullmatch(r"92-\d", no):  # 92-1 표준품 ... 는 본문에서 "1) 표 준 품" 처럼 쓰인다
            cands = [norm(f"{no[3:]}){ko}")]
        elif docno == 5 and no == "92":  # 92. 표준품, 시약·시액, ... (제목이 길어 개요 이름과 다르다)
            cands = [norm("92.표준품")]
            prefix = True
        else:
            cands = [norm(f"{no}. {ko}"), norm(f"{no}. {ko} {e.get('en') or ''}")] if no else []
            cands.append(norm(ko))
        hit = None
        for nko in cands:
            hit = find_from(lines, cur, nko, nen, prefix)
            if hit:
                break
        if not hit:
            continue
        i, j = hit
        pos[idx] = (lines[i][0], round(lines[i][1], 1))
        line_of[idx] = i
        cur = j + 1
        if docno == 5 and no == "92":  # 개요 아래 "1) 표준품 ... 8) 멸균법" 차례 줄들은 제목이 아니다: 그 뒤부터 찾는다
            h8 = find_from(lines, cur, norm("8)멸균법및무균조작법"))
            if h8:
                cur = h8[1] + 1

    # 의약품각조 제2부: "2) 생물학적제제 등" 같은 분류 제목 줄은 앞 항목의 끝이다
    marks = []
    if docno == 4:
        first_of_group = {}
        for idx, e in enumerate(ents):
            if idx in line_of and e.get("group") and e["group"] not in first_of_group:
                first_of_group[e["group"]] = idx
        for g, idx in first_of_group.items():
            L = line_of[idx]
            for k in range(L - 1, max(L - 12, -1), -1):
                if lines[k][6] == norm(g):
                    marks.append((lines[k][0], round(lines[k][1], 1)))
                    break
    return keys, pos, marks, n_pages


def main():
    d = kp_site.get_data()
    out = {"version": 2, "docs": {}}
    for docno in sorted(kp_site.parse_kp.DOCS):
        head = kp_site.parse_kp.DOCS[docno][0]
        pdf = next((os.path.join(kp_site.KP_DIR, f) for f in sorted(os.listdir(kp_site.KP_DIR)) if f.startswith(head) and f.lower().endswith(".pdf")), None)
        if pdf is None:
            print(f"[경고] 별표 {docno} PDF 가 없습니다.")
            continue
        ents = [e for e in d.entries if e["doc"] == docno]
        t0 = time.time()
        keys, pos, marks, n_pages = locate_doc(docno, ents, pdf)
        out["docs"][str(docno)] = {
            "pdf": os.path.basename(pdf),
            "pages": n_pages,
            "entries": [[keys[i], *pos[i]] for i in sorted(pos)],
            "marks": [list(m) for m in marks],
        }
        miss = [ents[i]["ko"] for i in range(len(ents)) if i not in pos]
        print(f"별표 {docno}: 항목 {len(ents)}개 중 {len(pos)}개 위치 찾음, 못 찾음 {len(miss)}개 ({time.time() - t0:.1f}초)" + (f" 예: {miss[:5]}" if miss else ""))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("저장:", OUT)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
