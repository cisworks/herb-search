# -*- coding: utf-8 -*-
"""
생약 검색 '원문' 버튼용 위치표(herb_orig_pos.json)를 만든다.
"1. 약전(각조 제2부).pdf" 처럼 hwpx 와 같은 이름의 PDF(한글에서 1단 쓰기로 바꾼 것)에서 생약·생약제제마다 제목이 놓인 자리(쪽, 세로 위치)를 찾아 적는다.
서버는 이 표로 항목의 시작~다음 항목 시작 사이만 보이게 해서 '원문' PDF 를 만든다(app.py 의 /api/herb_orig).

사용: python build_herb_orig.py        (PDF 를 새로 만들었거나 생약 파서가 바뀐 뒤 한 번)
"""
import json
import os
import re
import statistics
import sys
import time

import app
from build_kp_orig import find_from, load_lines, norm

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "herb_orig_pos.json")


def entry_keys(ents):
    """항목마다 (이름, 같은 이름이 또 나오면 #번호) 키. 서버(app.py)도 같은 방식으로 만든다."""
    return app.herb_entry_keys(ents)


def locate_file(ents, pdf):
    lines, n_pages = load_lines(pdf)
    keys = entry_keys(ents)
    pos = {}
    cur = 0
    for idx, e in enumerate(ents):
        ko = e["korean_name"]
        cands = [norm(ko)]
        if e.get("name_only") and e.get("hanja"):  # "백전(白前) 석람(石藍), 수약(嗽藥)" 처럼 이명이 붙은 이름은 앞 "이름(한자)"로도 찾는다
            cands.append(norm(f"{e['name_only']}({e['hanja']})"))
        if e.get("name_only") and not e.get("hanja"):
            cands.append(norm(e["name_only"]))
        first = (e.get("name_only") or "").split()  # 이름 뒤에 이명·구성 생약이 붙은 항목("사프란 번홍화", "정로환 크레오소오트․황련․진피")은 첫 낱말만 한 줄로 쓰인 제목으로도 찾는다
        if first:
            cands.append(norm(first[0]))
        cands = list(dict.fromkeys(cands))
        nen = norm(e.get("english_name") or "")
        hit = None
        for nko in cands:
            hit = find_from(lines, cur, nko, nen)
            if hit:
                break
        if not hit and nen:  # 영문명이 PDF 와 조금 다르면 영문명 확인 없이 다시
            for nko in cands:
                hit = find_from(lines, cur, nko, "")
                if hit:
                    break
        if not hit:
            continue
        i, j = hit
        pos[idx] = (lines[i][0], round(lines[i][1], 1))
        cur = j + 1
    # 쪽 맨 위 본문 첫 줄 높이(쪽마다 첫 줄 높이의 중앙값)에 조금 더한 값: 다음 항목이 이보다 위에서 시작하면 그 쪽은 통째로 다음 항목 것
    first_y = {}
    for ln in lines:
        first_y[ln[0]] = min(first_y.get(ln[0], 1e9), ln[1])
    top = round(statistics.median(first_y.values()) + 8, 1) if first_y else 95
    return keys, pos, n_pages, top


def main():
    groups = {}
    for e in list(app.ENTRIES) + list(app.PREP_ENTRIES):
        groups.setdefault(e["source_file"], []).append(e)
    out = {"version": 1, "files": {}}
    for fname, ents in groups.items():
        pdf = os.path.join(app.HERB_DIR, os.path.splitext(fname)[0] + ".pdf")
        if not os.path.isfile(pdf):
            print(f"[경고] {os.path.basename(pdf)} 가 없습니다.")
            continue
        t0 = time.time()
        keys, pos, n_pages, top = locate_file(ents, pdf)
        out["files"][fname] = {
            "pdf": os.path.basename(pdf),
            "pages": n_pages,
            "top": top,
            "entries": [[keys[i], *pos[i]] for i in sorted(pos)],
        }
        miss = [ents[i]["korean_name"] for i in range(len(ents)) if i not in pos]
        print(f"{fname}: 항목 {len(ents)}개 중 {len(pos)}개 위치 찾음, 못 찾음 {len(miss)}개 ({time.time() - t0:.1f}초)" + (f" 예: {miss[:6]}" if miss else ""))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("저장:", OUT)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
