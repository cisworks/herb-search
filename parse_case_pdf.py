# -*- coding: utf-8 -*-
"""
"관능검사사례집" PDF(한약(생약) 관능검사 사례집)를 파싱해서 생약별 부적합/적합
사례 목록으로 나누는 모듈.

이 PDF는 4개 파트(PART1~4)로 나뉘어 있고, 파트마다 "부적합 OOO의 위품 XXX"
/ "부적합 OOO 약용부위, 이물 부적합품" 같은 제목으로 시작하는 새 페이지에서
각 생약의 사례(부적합 사례 + 적합품 설명, 일부는 유전자 분석법 추가 페이지)가
몇 쪽에 걸쳐 이어지는 구조다. 제목 줄만 찾아 그 사이 페이지 구간을 그 생약의
사례로 묶는다.

세로로 돌려 쓴 파트 라벨("part\n1\n기원\n부적합\n사례")이 PyMuPDF의 위치
기반 정렬 추출(sort=True)에서 가끔 제목 줄 맨 앞에 공백 없이 붙어 나오므로
("part부적합 후박의 위품..."), 제목 패턴은 줄 전체가 아니라 부분 일치
(search)로 찾는다. 같은 이유로 "PART2" 같은 구분 페이지 표시도 줄 중간에
섞여 나올 수 있어, 그 페이지는 짧은 줄 수(<=3)로만 식별해 직전 생약의
사례 범위에 끼어들지 않도록 잘라낸다.
"""

import re
from pathlib import Path

CATEGORY_LABELS = {
    1: "기원 부적합 사례",
    2: "약용부위, 이물 부적합 사례",
    3: "충해 및 곰팡이 발생 부적합 사례",
    4: "가공방법 부적합 사례",
}

_PART_RE = re.compile(r"PART\s*([1-4])\b")
_TITLE_RE = re.compile(
    r"부적합\s+(?P<name>\S+?)(?:의)?\s*"
    r"(?P<kind>위품|성상\s*부적합품|약용부위[,，]\s*이물\s*부적합품"
    r"|충해\s*및\s*곰팡이\s*부적합품?|가공방법\s*부적합품)"
)


def parse_case_pdf(path):
    """반환: [{"herb_name", "category"(1~4), "category_label", "kind",
    "page_start", "page_end"}, ...] (페이지 번호는 0부터, /api/sensory_pdf의
    start/end 파라미터와 바로 호환된다)."""
    import pymupdf as fitz

    doc = fitz.open(str(path))
    try:
        page_lines = [
            [ln.strip() for ln in page.get_text("text", sort=True).split("\n") if ln.strip()]
            for page in doc
        ]

        divider_pages = set()
        current_part = 1
        titles = []
        for i, lines in enumerate(page_lines):
            is_divider = False
            for ln in lines:
                pm = _PART_RE.search(ln.replace(" ", ""))
                if pm:
                    current_part = int(pm.group(1))
                    is_divider = True
            if i == 0 and any(ln == "PART" for ln in lines):
                # 표지(0쪽)는 "PART"와 "1"이 줄이 나뉘어 나오는 특수 케이스.
                current_part = 1
                is_divider = True
            if is_divider and len(lines) <= 3:
                divider_pages.add(i)
            for ln in lines:
                tm = _TITLE_RE.search(ln)
                if tm:
                    titles.append(
                        {
                            "page": i,
                            "category": current_part,
                            "herb_name": tm.group("name"),
                            "kind": tm.group("kind"),
                        }
                    )
                    break

        for idx, t in enumerate(titles):
            next_page = titles[idx + 1]["page"] if idx + 1 < len(titles) else len(doc)
            end = next_page - 1
            for dp in divider_pages:
                if t["page"] < dp <= end:
                    end = dp - 1
            t["page_start"] = t.pop("page")
            t["page_end"] = end
            t["category_label"] = CATEGORY_LABELS.get(t["category"], "")
    finally:
        doc.close()
    return titles


if __name__ == "__main__":
    import sys

    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not pdf_path:
        print("사용법: python parse_case_pdf.py <pdf 경로>")
        sys.exit(1)
    result = parse_case_pdf(pdf_path)
    print(f"{len(result)}개 사례 파싱됨")
    for e in result[:5]:
        print(e["herb_name"], "|", e["category_label"], "|", e["page_start"], "-", e["page_end"])
