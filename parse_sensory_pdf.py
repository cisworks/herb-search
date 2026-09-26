# -*- coding: utf-8 -*-
"""
"관능검사해설서" PDF(한약재 관능검사 해설서)를 파싱해서 생약별 텍스트로 나누는 모듈.

이 PDF는 품목마다 "KP 생약명(한자)" 또는 "KHP 생약명(한자)" 로 시작하는 새
페이지에서 시작해서, 감별 요점/참고사항/사진 설명이 몇 페이지에 걸쳐 이어지는
구조다(한자가 없는 품목도 있다, 예: "KP 겐티아나"). 페이지의 첫 줄이 이
"품목명 시작" 패턴인지만 보고 새 품목의 시작을 찾아내고, 다음 품목이
시작되기 전까지의 모든 페이지 텍스트를 그 품목의 내용으로 묶는다.

일반 텍스트가 아니라 PyMuPDF(fitz)의 위치 기반 정렬 추출(sort=True)을 쓴다 -
이 PDF는 한자를 원문 위에 겹쳐 쓰는 주석(注釋) 형태로 배치해서, 기본 추출
순서로는 "가자(訶子)"가 "가자(", "訶子", ")" 조각으로 흩어져 나온다.
"""

import re
import html
from pathlib import Path

# "KP 가자( 訶子 )", "KHP갱미( 粳米 )", "KP 겐티아나"(한자 없음) 처럼
# 태그가 이름보다 앞에 오고, 태그와 이름 사이/한자 괄호 안 공백이 문서마다
# 들쭉날쭉하다.
_HEADER_RE = re.compile(r"^(KP|KHP)\s*([가-힣][가-힣0-9]*)\s*(?:\(\s*([^()]*?)\s*\))?\s*$")
_FOOTER_RE = re.compile(r"^한약재\s*관능검사\s*해설서\s*[·.]\s*\d+\s*$")


def _clean_page_text(text: str) -> str:
    lines = [ln for ln in (text or "").split("\n") if not _FOOTER_RE.match(ln.strip())]
    # 페이지 앞뒤에 남는 빈 줄을 정리한다.
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def parse_sensory_pdf(path):
    """PDF를 파싱해서 [{"korean_name", "name_only", "hanja", "source_tag", "text"}...] 를 반환한다."""
    import pymupdf as fitz

    entries = []
    current = None
    doc = fitz.open(str(path))
    try:
        for page in doc:
            cleaned = _clean_page_text(page.get_text(sort=True))
            if not cleaned:
                continue
            lines = cleaned.split("\n", 1)
            first_line = lines[0].strip()
            rest = lines[1] if len(lines) > 1 else ""
            m = _HEADER_RE.match(first_line)
            if m:
                if current is not None:
                    entries.append(current)
                name_only = m.group(2).strip()
                hanja = (m.group(3) or "").strip()
                korean_name = f"{name_only}({hanja})" if hanja else name_only
                current = {
                    "korean_name": korean_name,
                    "name_only": name_only,
                    "hanja": hanja,
                    "source_tag": m.group(1),
                    "text": rest,
                    "page_start": page.number,
                    "page_end": page.number,
                }
            elif current is not None:
                current["text"] += "\n" + cleaned
                current["page_end"] = page.number
    finally:
        doc.close()
    if current is not None:
        entries.append(current)
    for e in entries:
        e["text"] = _collapse_blank_lines(e["text"])
    return entries


def _collapse_blank_lines(text: str) -> str:
    lines = text.split("\n")
    out = []
    for ln in lines:
        if ln.strip() == "" and out and out[-1] == "":
            continue
        out.append(ln.strip() if ln.strip() == "" else ln)
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _render_html(text: str) -> str:
    return "<br>".join(html.escape(ln) for ln in text.split("\n"))


def build_sensory_entries(path):
    entries = parse_sensory_pdf(path)
    for e in entries:
        e["html"] = _render_html(e["text"])
    return entries


if __name__ == "__main__":
    import sys

    pdf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not pdf_path:
        print("사용법: python parse_sensory_pdf.py <pdf 경로>")
        sys.exit(1)
    result = parse_sensory_pdf(pdf_path)
    print(f"{len(result)}개 품목 파싱됨")
    for e in result[:5]:
        print(e["korean_name"], "|", e["source_tag"], "|", len(e["text"]), "chars")
