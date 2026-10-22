# -*- coding: utf-8 -*-
"""
관능검사해설서(2022).pdf 의 품목 페이지에서 사진과 그 아래 설명을 한 묶음씩 찾아낸다.

이 PDF 는 한 장의 사진이 폭 약 220pt, 높이 60~80pt 의 이미지 조각 여러 장을 세로로 이어 붙여 만든
것이고(조각 하나만 따로는 의미가 없다), 사진 바로 아래에 굵은 제목(예: "감초 G. uralensis")과 설명
글이 붙어 있다. PDF 에서 뽑은 글자는 쉼표/마침표가 따로 떨어져 나오는 등 순서가 어긋나서 글자로 다시
조립하면 틀리므로, 사진과 설명이 놓인 영역을 PDF 페이지에서 그대로 잘라 그림으로 보여 준다.

  - 사진 조각: 폭 80pt 이상, 높이 25pt 이상인 이미지(작은 아이콘 제외)
  - 같은 폭(좌우 끝이 8pt 이내)이고 세로로 맞닿은(3pt 이내) 조각은 한 사진으로 묶는다
  - 설명: 사진 바로 아래에서 같은 칸(가로로 겹치는) 글 블록을, 줄 간격이 12pt 이내로 이어지는 동안 모은다
    (페이지 아래 바닥글, 다음 사진 위에서 멈춘다)
  - 사진 이미지는 흰 여백이 넓고 옆 사진과 여백이 겹치기도 하므로, 그릴 때는 다른 묶음의 사진/글자를
    지운 임시 쪽에 그린 뒤 흰 여백을 잘라 낸다.
"""

import re

MIN_PHOTO_W = 80
MIN_PHOTO_H = 25
TILE_X_TOL = 8
TILE_GAP = 3
CAPTION_GAP = 12
FIRST_CAPTION_GAP = 40  # 사진 이미지 아래 흰 여백 때문에, 설명 첫 줄은 사진 아래끝에서 이만큼까지 떨어져 있을 수 있다
FOOTER_MARGIN = 60  # 페이지 아래 이만큼은 바닥글 영역
CLIP_DPI = 170
MIN_NATIVE_DPI = 72
RESIZE_TOLERANCE_PX = 1  # 사진 조각을 목표 크기와 이 픽셀 수 이하로만 다르면 늘리거나 줄이지 않는다
MAX_NATIVE_DPI = 1200  # 원본 해상도가 이보다 높은 조각은 없다(최대 약 1130dpi)
CROP_PAD = 4  # 흰 여백을 자른 뒤 남기는 여백(pt)
WHITE = 248   # 이 값 이상이면 흰색으로 본다(회색조 0~255)

_NOT_CAPTION = re.compile(r"[㉠-㉻❍※]")  # 쪽 아래 각주(㉠)나 본문 글머리는 사진 설명이 아니다


def _tiles(page):
    out = []
    for im in page.get_image_info(xrefs=True):
        x0, y0, x1, y1 = im["bbox"]
        if (x1 - x0) >= MIN_PHOTO_W and (y1 - y0) >= MIN_PHOTO_H:
            out.append(([x0, y0, x1, y1], im.get("xref", 0)))
    return sorted(out, key=lambda t: (t[0][1], t[0][0]))


def _group_tiles(tiles):
    """[(rect, xref)] -> [{"rect": [x0, y0, x1, y1], "xrefs": {...}}]"""
    groups = []
    for (x0, y0, x1, y1), xref in tiles:
        for g in groups:
            r = g["rect"]
            if abs(x0 - r[0]) <= TILE_X_TOL and abs(x1 - r[2]) <= TILE_X_TOL and r[1] <= y0 <= r[3] + TILE_GAP:
                r[0], r[2] = min(r[0], x0), max(r[2], x1)
                r[3] = max(r[3], y1)
                g["xrefs"].add(xref)
                break
        else:
            groups.append({"rect": [x0, y0, x1, y1], "xrefs": {xref}})
    return groups


def photo_regions(page):
    """페이지 하나의 사진 묶음 목록. 각 항목은 {"rect": (x0, y0, x1, y1) 사진+아래 설명 영역,
    "photo": 사진 영역, "xrefs": 사진 이미지 번호들, "texts": 이 묶음에 딸린 글 블록 영역들}.
    읽는 순서(위에서 아래, 같은 줄이면 왼쪽부터)로 돌려준다."""
    groups = _group_tiles(_tiles(page))
    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    footer_y = page.rect.height - FOOTER_MARGIN
    for g in groups:
        gy1 = g["rect"][3]
        # 같은 칸 아래에 있는 다음 사진의 위쪽 = 설명의 끝
        g["limit"] = footer_y
        for o in groups:
            r, gr = o["rect"], g["rect"]
            if o is not g and r[1] >= gy1 - 1 and min(r[2], gr[2]) - max(r[0], gr[0]) > 0.5 * (gr[2] - gr[0]):
                g["limit"] = min(g["limit"], r[1])
        g["cands"] = []
    # 사진 이미지 여백이 옆 사진과 겹치는 쪽이 있어서, 글 블록 하나는 묶음 하나에만 준다.
    # 바로 위(세로로 가장 가까운) 사진 중에서 가로 중심이 가장 가까운 묶음을 고른다
    # (가운데나 오른쪽 아래에 엇갈려 놓인 사진의 설명이 윗줄 사진에 잘못 붙지 않게)
    for b in blocks:
        bx0, by0, bx1, by1 = b[:4]
        fits = []
        for g in groups:
            gx0, gy0, gx1, gy1 = g["rect"]
            overlap = min(bx1, gx1 + TILE_X_TOL) - max(bx0, gx0 - TILE_X_TOL)
            if by0 >= gy1 - 4 and by1 <= g["limit"] + 1 and overlap > 0.5 * (bx1 - bx0):
                fits.append((by0 - gy1, abs((bx0 + bx1) / 2 - (gx0 + gx1) / 2), g))
        if fits:
            near = min(f[0] for f in fits)
            best = min((f for f in fits if f[0] <= near + CAPTION_GAP), key=lambda f: f[1])
            best[2]["cands"].append((by0, by1, bx0, bx1, _NOT_CAPTION.match(b[4].strip()) is not None))
    assigned = {c[:4] for g in groups for c in g["cands"]}
    regions = []
    for g in groups:
        gx0, gy0, gx1, gy1 = g["rect"]
        cands = sorted(g["cands"])
        bottom, cx0, cx1 = gy1, gx0, gx1
        texts = []
        for by0, by1, bx0, bx1, note in cands:
            if by0 - bottom > (CAPTION_GAP if texts else FIRST_CAPTION_GAP) or (note and not texts):
                break
            bottom = max(bottom, by1)
            cx0, cx1 = min(cx0, bx0), max(cx1, bx1)
            texts.append((bx0, by0, bx1, by1))
        # 사진 위에 얹힌 글(사진 안 설명선 이름, 눈금 "1cm" 등)
        for b in blocks:
            if (
                gx0 <= (b[0] + b[2]) / 2 <= gx1
                and gy0 <= (b[1] + b[3]) / 2 <= gy1
                and (b[1], b[3], b[0], b[2]) not in assigned
            ):
                texts.append(tuple(b[:4]))
        x0 = min(gx0, cx0) - 2
        x1 = max(gx1, cx1) + 2
        regions.append(
            {
                "rect": (x0, gy0, x1, bottom + 2),
                "photo": (gx0, gy0, gx1, gy1),
                "xrefs": g["xrefs"],
                "texts": texts,
            }
        )
    # 읽는 순서: 위쪽 시작이 비슷한(30pt 이내) 사진끼리 한 줄로 보고 왼쪽부터
    regions.sort(key=lambda r: r["rect"][1])
    rows = []
    for r in regions:
        if rows and r["rect"][1] - rows[-1][0]["rect"][1] <= 30:
            rows[-1].append(r)
        else:
            rows.append([r])
    return [r for row in rows for r in sorted(row, key=lambda r: r["rect"][0])]


def entry_regions(doc, page_start, page_end):
    """품목의 모든 페이지에서 사진 묶음을 찾아 [(쪽 번호, 묶음 번호), ...] 로 돌려준다."""
    out = []
    for p in range(page_start, page_end + 1):
        out.extend((p, i) for i in range(len(photo_regions(doc[p]))))
    return out


def _intersects(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 1 and min(a[3], b[3]) - max(a[1], b[1]) > 1


def _content_rect(page, rect):
    """rect 안에서 흰 여백을 뺀 그림이 있는 영역(없으면 rect 그대로)."""
    import pymupdf as fitz

    pix = page.get_pixmap(clip=fitz.Rect(*rect), dpi=48, colorspace=fitz.csGRAY, alpha=False)
    w, h, s = pix.width, pix.height, pix.samples
    rows = [y for y in range(h) if min(s[y * w:(y + 1) * w]) < WHITE]
    cols = [x for x in range(w) if min(s[x::w]) < WHITE]
    if not rows or not cols:
        return fitz.Rect(*rect)
    sx, sy = (rect[2] - rect[0]) / w, (rect[3] - rect[1]) / h
    return fitz.Rect(
        rect[0] + cols[0] * sx - CROP_PAD,
        rect[1] + rows[0] * sy - CROP_PAD,
        rect[0] + (cols[-1] + 1) * sx + CROP_PAD,
        rect[1] + (rows[-1] + 1) * sy + CROP_PAD,
    ) & fitz.Rect(*rect)


def native_dpi(doc, page_no, index):
    """index 번째 사진 묶음 속 사진 조각들의 원본 해상도(dpi, 가로 픽셀 / 가로 인치)의 최댓값.
    이 dpi로 그리면 PDF 안의 사진 픽셀이 줄거나 늘지 않고 1:1로 나온다. 묶음이 없으면 None."""
    page = doc[page_no]
    regions = photo_regions(page)
    if not 0 <= index < len(regions):
        return None
    xrefs = regions[index]["xrefs"]
    best = 0.0
    for im in page.get_image_info(xrefs=True):
        x0, _, x1, _ = im["bbox"]
        if im.get("xref", 0) in xrefs and x1 > x0:
            best = max(best, im["width"] / ((x1 - x0) / 72))
    return max(MIN_NATIVE_DPI, min(round(best), MAX_NATIVE_DPI)) if best else None


def _tile_infos(page, xrefs):
    """묶음에 속한 사진 조각들: [{"xref", "bbox"(pt), "w", "h"(원본 픽셀)}] (위에서 아래 순)."""
    out = []
    for im in page.get_image_info(xrefs=True):
        x0, y0, x1, y1 = im["bbox"]
        if im.get("xref", 0) in xrefs and x1 > x0 and y1 > y0:
            out.append({"xref": im["xref"], "bbox": (x0, y0, x1, y1), "w": im["width"], "h": im["height"], "transform": im["transform"]})
    return sorted(out, key=lambda t: (t["bbox"][1], t["bbox"][0]))


def render_region_original(doc, page_no, index, layer="all", k=1):
    """사진 묶음을 PDF 안의 원본 JPEG 픽셀 그대로 붙여서 PNG 바이트로 만든다. None 이면 그 묶음이 없다.

    PDF 페이지를 다시 그리면(render_region) 사진이 한 번 더 보간되어 원본보다 흐려진다(원본 대비
    선명도 약 20~40% 감소). 여기서는 사진 조각의 원본 JPEG(extract_image)을 다시 그리지 않고 그대로
    정수 픽셀 위치에 붙이고, 사진 위에 얹힌 글자/선은 이미지를 모두 뺀 쪽을 같은 배율로 투명 배경에 그려
    그 위에 겹친다. 배율은 조각의 원본 해상도(픽셀/pt)이다. 지원하지 않는 형식(회전, 투명 마스크,
    RGB/회색조가 아닌 이미지)이 있으면 render_region(원본 dpi)으로 대신한다.

    layer: "all"(기본, 사진 + 글자/선), "photo"(사진만, 글자/선 없음), "text"(글자/선만, 투명 배경, 가로세로
    k배 해상도). photo 와 text 는 같은 자르기 영역을 쓰므로 text 를 1/k 크기로 photo 위에 겹치면 정확히
    맞는다. 확대해서 볼 때 글자/선을 PDF 벡터처럼 또렷하게 보이려고 쓴다. 이 두 층은 fallback 이 없어,
    원본 방식을 쓸 수 없으면 None 을 돌려준다(호출한 쪽에서 "all" 로 대신한다)."""
    import io

    import pymupdf as fitz
    from PIL import Image, ImageChops

    page = doc[page_no]
    regions = photo_regions(page)
    if not 0 <= index < len(regions):
        return None
    me = regions[index]
    tiles = _tile_infos(page, me["xrefs"])

    def fallback():
        if layer != "all":
            return None
        return render_region(doc, page_no, index, dpi=native_dpi(doc, page_no, index) or CLIP_DPI)

    if not tiles:
        return fallback()
    rect = fitz.Rect(*me["rect"])
    z = max(t["w"] / (t["bbox"][2] - t["bbox"][0]) for t in tiles)  # 픽셀 / pt
    loaded = []
    for t in tiles:
        a, b, c, d = t["transform"][:4]
        if abs(b) > 1e-3 or abs(c) > 1e-3 or a <= 0 or d <= 0:
            return fallback()
        info = doc.extract_image(t["xref"])
        if not info or info.get("smask") or info.get("colorspace") not in (1, 3):
            return fallback()
        try:
            img = Image.open(io.BytesIO(info["image"])).convert("RGB")
        except Exception:  # noqa: BLE001
            return fallback()
        loaded.append(img)

    # 투명 배경에 글자/선만 그린 겹침 층: 사진 조각 이미지를 모두 지운 임시 쪽을 쓴다
    tmp = fitz.open()
    try:
        tmp.insert_pdf(doc, from_page=page_no, to_page=page_no)
        tp = tmp[0]
        tregs = photo_regions(tp)
        tme = tregs[index]
        others = [r for r in tregs if r is not tme and _intersects(r["rect"], tme["rect"])]
        for r in tregs:
            for xref in r["xrefs"]:
                if xref:
                    tp.delete_image(xref)
        for o in others:
            for t in o["texts"]:
                tp.add_redact_annot(fitz.Rect(*t) + (-1, -1, 1, 1), fill=False)
        tp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        m = fitz.Matrix(z, 0, 0, z, -rect.x0 * z, -rect.y0 * z)
        pm = tp.get_pixmap(matrix=m, clip=rect, alpha=True)
        overlay = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGBA")
        overlay_k = None
        if layer == "text":
            mk = fitz.Matrix(z * k, 0, 0, z * k, -rect.x0 * z * k, -rect.y0 * z * k)
            pmk = tp.get_pixmap(matrix=mk, clip=rect, alpha=True)
            overlay_k = Image.open(io.BytesIO(pmk.tobytes("png"))).convert("RGBA")
    finally:
        tmp.close()

    canvas = Image.new("RGB", overlay.size, (255, 255, 255))
    prev_bottom = None  # 바로 위 조각의 아래 끝(픽셀): 맞닿은 조각 사이에 1픽셀 틈/겹침이 생기지 않게 맞춘다
    for t, img in zip(tiles, loaded):
        x0, y0, x1, y1 = t["bbox"]
        tw, th = round((x1 - x0) * z), round((y1 - y0) * z)
        # 목표 크기와 1픽셀 이하로만 다르면(반올림 차이) 늘리거나 줄이지 않고 원본 픽셀 그대로 붙인다.
        # 1픽셀 때문에 조각 전체를 다시 보간하면 그 조각이 눈에 띄게 흐려진다.
        if (tw, th) != img.size and (abs(tw - img.width) > RESIZE_TOLERANCE_PX or abs(th - img.height) > RESIZE_TOLERANCE_PX):
            img = img.resize((tw, th), Image.LANCZOS)
        px = round((x0 - rect.x0) * z)
        py_exact = (y0 - rect.y0) * z
        py = round(py_exact)
        if prev_bottom is not None and abs(py_exact - prev_bottom) <= 1.5:
            py = prev_bottom
        canvas.paste(img, (px, py))
        prev_bottom = py + img.size[1]
    photo_only = canvas.copy()
    canvas.paste(overlay, (0, 0), overlay)

    # 흰 여백 잘라내기(render_region의 _content_rect와 같은 기준: 밝기 WHITE 미만인 곳이 그림)
    nonwhite = canvas.convert("L").point(lambda v: 255 if v < WHITE else 0)
    box = nonwhite.getbbox()
    if box:
        pad = round(CROP_PAD * z)
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(canvas.width, box[2] + pad), min(canvas.height, box[3] + pad))
        canvas = canvas.crop(box)
    else:
        box = (0, 0, canvas.width, canvas.height)
    out = io.BytesIO()
    if layer == "photo":
        photo_only.crop(box).save(out, "PNG")
    elif layer == "text":
        w, h = box[2] - box[0], box[3] - box[1]
        text = Image.new("RGBA", (w * k, h * k), (255, 255, 255, 0))
        text.paste(overlay_k.crop((box[0] * k, box[1] * k, box[2] * k, box[3] * k)), (0, 0))
        text.save(out, "PNG")
    else:
        canvas.save(out, "PNG")
    return out.getvalue()


def render_region(doc, page_no, index, dpi=CLIP_DPI):
    """page_no 쪽의 index 번째 사진 묶음(사진 + 아래 설명)을 PNG 바이트로 그린다. None 이면 그 묶음이 없다."""
    import pymupdf as fitz

    regions = photo_regions(doc[page_no])
    if not 0 <= index < len(regions):
        return None
    me = regions[index]
    others = [r for r in regions if r is not me and _intersects(r["rect"], me["rect"])]
    page = doc[page_no]
    tmp = None
    try:
        if others:
            # 겹치는 다른 묶음의 사진과 글자를 지운 임시 쪽에 그린다
            tmp = fitz.open()
            tmp.insert_pdf(doc, from_page=page_no, to_page=page_no)
            tp = tmp[0]
            # 이미지 번호는 임시 문서에서 달라지므로 그쪽에서 다시 찾는다(순서는 같다)
            tregs = photo_regions(tp)
            tme = tregs[index]
            others = [r for r in tregs if r is not tme and _intersects(r["rect"], tme["rect"])]
            for o in others:
                for xref in o["xrefs"] - tme["xrefs"]:
                    if xref:
                        tp.delete_image(xref)
                for t in o["texts"]:
                    tp.add_redact_annot(fitz.Rect(*t) + (-1, -1, 1, 1), fill=False)
            tp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            page = tp
        clip = _content_rect(page, me["rect"])
        return page.get_pixmap(clip=clip, dpi=dpi).tobytes("png")
    finally:
        if tmp is not None:
            tmp.close()
