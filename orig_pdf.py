# -*- coding: utf-8 -*-
"""
'원문' 버튼용 PDF 자르기(생약 검색 app.py 와 약전 검색 kp_site.py 가 함께 쓴다).
한글에서 hwpx 를 1단 쓰기 PDF 로 바꾼 파일에서, 한 항목의 부분(시작 위치~끝 위치 앞)만 이어 붙인 새 PDF 를 만든다.
앞뒤 항목은 지우고, 쪽 위아래의 빈 곳도 뺀 내용 덩어리를 첫 쪽 맨 위에서부터 차례로 채운다(원본에서 몇 쪽에 걸쳐 있어도 한 쪽에 들어가는 분량이면 한 쪽).
"""
import re

ORIG_VER = 9  # 자르는 방식을 바꾸면 올린다(저장해 둔 옛 PDF 와 브라우저가 기억한 옛 PDF 를 쓰지 않는다)
ORIG_PAD = 2  # 내용 덩어리 위아래에 더 붙이는 여백(pt)
ORIG_MARGIN = 50  # 새로 만든 쪽의 위 여백(pt). 내용은 쪽 맨 위(이 여백)에서부터 채운다
ORIG_MARGIN_BOTTOM = 30  # 새로 만든 쪽의 아래 여백(pt)
ORIG_GAP = 6  # 원본에서 쪽이 달랐던 내용 덩어리 사이의 간격(pt)
ORIG_TOP = 95  # (약전 PDF) 본문 첫 줄이 y≈88 에서 시작한다. 쪽 맨 위(이 값 이하)에서 다음 항목이 시작하면 그 쪽은 통째로 다음 항목 것이다


def page_items(page, lo, hi):
    """한 쪽에서 세로 범위 [lo, hi] 와 겹치는 글 줄·그림·선의 (위, 아래) 목록(범위 안으로 잘라서). 지운 부분을 덮은 흰 사각형은 뺀다."""
    w = page.rect.width
    items = []

    def add(y0, y1):
        y0, y1 = max(y0, lo), min(y1, hi)
        if y1 - y0 > 0.2:
            items.append((y0, y1))

    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 0:
            for ln in b["lines"]:
                if "".join(sp["text"] for sp in ln["spans"]).strip():
                    add(ln["bbox"][1], ln["bbox"][3])
        else:
            add(b["bbox"][1], b["bbox"][3])
    try:
        for dr in page.get_drawings():  # 표의 선 같은 벡터 그림
            r = dr["rect"]
            if dr.get("fill") == (1.0, 1.0, 1.0) and r.width >= w - 2:
                continue
            add(r.y0, r.y1)
    except Exception:  # noqa: BLE001
        pass
    return items


def safe_cut(items, min_y, limit_y):
    """min_y~limit_y 사이에서 글 줄·그림·표 선을 가로지르지 않는 가장 아래 자리(없으면 None)."""
    spans = sorted((a + 1, b - 1) for a, b in items if b - a > 2)
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cands = sorted({a - 1 for a, _ in spans} | {b + 1 for _, b in spans})
    best = None
    for c in cands:
        if c > limit_y:
            break
        if c < min_y:
            continue
        if any(a < c < b for a, b in merged):
            continue
        best = c
    return best


def svg_clip_to_viewbox(svg_bytes):
    """SVG 안의 그림 요소 중 viewBox 밖으로 나간 것은 브라우저에서는 가려지지만 PDF 로 바꾸면 그대로 그려지므로, viewBox 로 잘라 둔다."""
    text = svg_bytes.decode("utf-8")
    m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)\s*"', text)
    tag_end = text.find(">", text.find("<svg"))
    close = text.rfind("</svg>")
    if not m or tag_end < 0 or close < 0:
        return svg_bytes
    x, y, w, h = m.groups()
    clip = f'<defs><clipPath id="vbclip"><rect x="{x}" y="{y}" width="{w}" height="{h}"/></clipPath></defs><g clip-path="url(#vbclip)">'
    return (text[: tag_end + 1] + clip + text[tag_end + 1 : close] + "</g>" + text[close:]).encode("utf-8")


def cut_pdf(pdf_path, start, end, title, fixes=None, top_y=ORIG_TOP):
    """원본 PDF 에서 start~end(끝 위치 앞까지) 부분만 이어 붙인 새 PDF 바이트를 만든다.
    앞뒤 항목은 지우고, 쪽 위아래의 빈 곳도 뺀 내용 덩어리를 첫 쪽 맨 위에서부터 차례로 채운다. 원본에서 몇 쪽에 걸쳐 있어도 한 쪽에 들어가는 분량이면
    한 쪽으로 보이고, 넘치면 다음 쪽으로 이어진다(글 줄·그림·표를 가로지르지 않는 자리에서 나눈다). 글자·그림은 벡터 그대로다."""
    import pymupdf

    src = pymupdf.open(str(pdf_path))
    try:
        p0, y0 = start
        if end is None:
            p1, y1 = len(src) - 1, None
        else:
            p1, y1 = end
            if y1 <= top_y and p1 > p0:  # 다음 항목이 쪽 맨 위에서 시작하면 그 쪽은 통째로 다음 항목 것
                p1 -= 1
                y1 = None
        # 0) 다시 그릴 그림(fixes)이 있으면, 범위 안의 그림들을 문서에 나오는 차례로 모아 본문 그림 차례와 맞춘다(개수가 다르면 건드리지 않는다)
        redraw = {}  # 원본 쪽 -> [(fixes 번호, 그림 자리)]
        if fixes:
            placed = []
            for pg in range(p0, p1 + 1):
                for ii in src[pg].get_image_info():
                    r = pymupdf.Rect(ii["bbox"])
                    if pg == p0 and r.y0 < y0 - 2:
                        continue
                    if pg == p1 and y1 is not None and r.y0 >= y1 - 1.5:
                        continue
                    placed.append((pg, r))
            if len(placed) == len(fixes):
                for k, (pg, r) in enumerate(placed):
                    if fixes[k]:
                        redraw.setdefault(pg, []).append((k, r))
        # 1) 범위 밖(앞뒤 항목)을 지우고, 쪽마다 남은 내용의 세로 범위(덩어리)를 잡는다
        strips = []  # (원본 쪽, 위, 아래, 그 쪽의 줄·그림·선 목록)
        for pg in range(p0, p1 + 1):
            page = src[pg]
            w, h = page.rect.width, page.rect.height
            lo = max(y0 - 1.5, 0) if pg == p0 else 0  # 이 높이 위는 앞 항목 것
            hi = y1 - 1.5 if (pg == p1 and y1 is not None) else h  # 이 높이 아래는 다음 항목 것
            if lo > 0:
                page.add_redact_annot(pymupdf.Rect(0, 0, w, lo), fill=(1, 1, 1))
            if hi < h:
                page.add_redact_annot(pymupdf.Rect(0, hi, w, h), fill=(1, 1, 1))
            if lo > 0 or hi < h:
                page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
            for k, r in redraw.get(pg, []):  # 잘려 들어간 그림은 흰색으로 덮고 같은 자리에 온전한 그림을 다시 그린다(그림 비율은 그대로, 자리 안에 맞춘다)
                try:
                    svg_pdf = pymupdf.open("pdf", pymupdf.open(stream=svg_clip_to_viewbox(fixes[k]), filetype="svg").convert_to_pdf())
                    page.draw_rect(r + (-1.5, -1.5, 1.5, 1.5), color=None, fill=(1, 1, 1))  # 그림 가장자리 낱알이 삐져나오지 않게 사방 1.5pt 넓게 덮는다
                    page.show_pdf_page(r, svg_pdf, 0)
                except Exception as exc:  # noqa: BLE001  그림을 못 바꾸면 원래 그림 그대로 둔다
                    print(f"[알림] 원문 pdf 그림을 다시 그리지 못했습니다: {exc}")
            items = page_items(page, lo, hi)
            if not items:
                continue
            top = max(min(a for a, _ in items) - ORIG_PAD, lo)
            bottom = min(max(b for _, b in items) + ORIG_PAD, hi)
            if bottom - top > 1:
                strips.append((pg, top, bottom, items))
        # 2) 쪽 크기는 원본과 같고, 위아래 여백만 두고 덩어리들을 차례로 채운다
        w, h = src[p0].rect.width, src[p0].rect.height
        limit = h - ORIG_MARGIN_BOTTOM
        out = pymupdf.open()
        try:
            cur_page = out.new_page(width=w, height=h)
            y = ORIG_MARGIN
            for pg, top, bottom, items in strips:
                cur = top
                guard = 0
                while bottom - cur > 0.5 and guard < 200:
                    guard += 1
                    room = limit - y
                    if bottom - cur <= room:
                        end_y = bottom
                    else:
                        end_y = safe_cut(items, cur + 30, cur + room) if room > 30 else None
                        if end_y is None:
                            if y > ORIG_MARGIN + 1:  # 이 쪽에 이미 내용이 있으면 다음 쪽 처음에서 다시 시도한다
                                cur_page = out.new_page(width=w, height=h)
                                y = ORIG_MARGIN
                                continue
                            end_y = cur + room  # 한 쪽보다 큰 그림·표는 어쩔 수 없이 그 자리에서 나눈다
                    cur_page.show_pdf_page(pymupdf.Rect(0, y, w, y + (end_y - cur)), src, pg, clip=pymupdf.Rect(0, cur, w, end_y))
                    y += end_y - cur
                    cur = end_y
                    if bottom - cur > 0.5:  # 남았으면 다음 쪽으로
                        cur_page = out.new_page(width=w, height=h)
                        y = ORIG_MARGIN
                y += ORIG_GAP
                if y > limit - 30:
                    cur_page = out.new_page(width=w, height=h)
                    y = ORIG_MARGIN
            if len(out) > 1 and y <= ORIG_MARGIN + 1:  # 끝에 빈 쪽이 생겼으면 뺀다(마지막 쪽도 쪽 크기는 그대로 두고, 내용 아래는 빈 곳으로 남긴다)
                out.delete_page(len(out) - 1)
            out.set_metadata({"title": title})
            try:
                out.subset_fonts()
                return out.tobytes(garbage=3, deflate=True, deflate_fonts=True)
            except Exception as exc:  # noqa: BLE001  fonttools 가 없는 서버 등: 줄이지 않고 그대로
                print(f"[알림] 원문 pdf 글꼴 줄이기를 못 했습니다(그대로 보냅니다): {exc}")
                return out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
    finally:
        src.close()
