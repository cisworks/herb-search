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
MERGE_X_OVERLAP = 0.4  # 사진 묶음 둘이 가로로 좁은 쪽 폭의 이만큼 넘게 겹치고
MERGE_Y_OVERLAP = 0.5  # 세로로도 낮은 쪽 높이의 이만큼 넘게 겹치면 한 장의 사진이 둘로 쪼개진 것으로 보고 합친다
MERGE_STACK_GAP = 6  # 좌우 끝이 같게(MERGE_STACK_X_TOL 이내) 위아래로 이 pt 이하로만 떨어져 있어도 한 사진으로 본다(설명이 들어갈 수 없는 간격)
MERGE_STACK_X_TOL = 12
CAPTION_GAP = 12
FIRST_CAPTION_GAP = 40  # 사진 이미지 아래 흰 여백 때문에, 설명 첫 줄은 사진 아래끝에서 이만큼까지 떨어져 있을 수 있다
FOOTER_MARGIN = 60  # 페이지 아래 이만큼은 바닥글 영역
CLIP_DPI = 170
MIN_NATIVE_DPI = 72
RESIZE_TOLERANCE_PX = 1  # 사진 조각을 목표 크기와 이 픽셀 수 이하로만 다르면 늘리거나 줄이지 않는다
MAX_NATIVE_DPI = 1200  # 원본 해상도가 이보다 높은 조각은 없다(최대 약 1130dpi)
CROP_PAD = 4  # 흰 여백을 자른 뒤 남기는 여백(pt)
WORD_JOIN_GAP = 1.2  # 낱말 사이가 이 pt 이하로 붙어 있으면(쉼표, 마침표, 괄호 등) 한 덩어리 글로 본다
# 가로로 나란한 두 사진 아래 설명이 PDF 에서 한 줄로 이어진 예외: 그 줄을 after 글 자리에서 갈라 앞 절반은 첫째 사진,
# 뒤 절반은 둘째 사진 아래에 보인다. photos 는 두 사진 묶음의 왼쪽 위 위치(pt). 택사(705쪽, 0부터)는 한 줄
# "위쪽에는 드물게 줄기의 그루터기가 남아 있고, 아랫면에는 많은 뿌리자국이 작은 돌기를 이룬다."가 두 사진 아래로 이어진다.
# 진교(647쪽)는 "큰잎용담 전형: …모양이다." 와 "큰잎용담 절편: …있다." 두 설명이 한 줄 블록으로 합쳐져 첫째 사진 아래에 같이 나온다.
SPLIT_CAPTIONS = [
    {"page": 705, "photos": [(70, 422), (287, 414)], "after": "있고"},
    {"page": 647, "photos": [(73, 599), (303, 588)], "after": "모양이다."},
    # 진교(648쪽) "조경진교 뿌리" 설명 줄에 옆 사진(소진교 뿌리)의 설명이 이어져 있다
    {"page": 648, "photos": [(62, 445), (293, 432)], "after": "조경진교뿌리"},
    # 곽향(64쪽) 5·6번째와 7·8번째 사진: 두 사진의 설명이 한 줄로 이어져 있다("…털이 없다. 곽향 잎 아랫면 확대: …")
    {"page": 64, "photos": [(62, 467), (293, 467)], "after": "털이없다."},
    {"page": 64, "photos": [(62, 632), (293, 632)], "after": "윗면확대:털이많다."},
    # 금전초(106쪽) 3·4번째 사진: 3번째 설명 마지막 줄 끝에 4번째 사진의 제목 "금전초 잎 확대"가 같은 줄로 이어져 있다
    {"page": 106, "photos": [(40, 291), (298, 298)], "after": "이다."},
    # 갱미(22쪽) 1·2번째 사진: "갱미: 멥쌀. 반투명하다." 와 "나미 [위품]: 찹쌀. 불투명하다." 가 한 줄로 이어져 있다
    {"page": 22, "photos": [(79, 519), (316, 525)], "after": "반투명하다."},
    # 녹용(131쪽) 5·6번째: "녹용(마록, 중간부) 종단면 | 녹용(마록, 하부) 종단면" 이 5번째 아래 한 줄로 이어져 있다
    {"page": 131, "photos": [(73, 282), (313, 281)], "after": "종단면"},
    # 녹용(131쪽) 7·8번째: "녹용(마록, 하부) 종단면 | 녹용(마록, 측지부) 종단면" 이 8번째(오른쪽) 아래 한 줄로 이어져 있고,
    # 앞 절반이 왼쪽 7번째 사진의 설명이다
    {"page": 131, "photos": [(74, 476), (310, 477)], "after": "종단면"},
]
# 보이는 가로폭을 맞추는 예외: 사진 묶음 그림이 이 폭(pt)이 되도록 가운데에 흰 여백을 덧붙이고, ◀ 설명이 있으면 그 폭에
# 맞춰 줄바꿈한다. 진교(648쪽)의 3번째(큰잎용담 엽초)와 9번째(진범 뿌리) 사진을 2번째 사진(절편)의 폭에 맞춘다.
WIDTH_TO = [
    {"page": 648, "at": (104, 61), "width_pt": 229.2},
    {"page": 648, "at": (158, 602), "width_pt": 229.2},
    # 택란(704쪽) 2~6번째 사진을 1번째 사진의 폭에 맞춘다(4번째는 이미 같은 폭)
    {"page": 704, "at": (293, 60), "width_pt": 244.56},
    {"page": 704, "at": (62, 307), "width_pt": 244.56},
    {"page": 704, "at": (293, 298), "width_pt": 244.56},
    {"page": 704, "at": (104, 500), "width_pt": 244.56},
    {"page": 704, "at": (335, 499), "width_pt": 244.56},
]
WHITE = 248   # 이 값 이상이면 흰색으로 본다(회색조 0~255)

_NOT_CAPTION = re.compile(r"[㉠-㉻❍※]")  # 쪽 아래 각주(㉠)나 본문 글머리는 사진 설명이 아니다
SIDE_MARKS = "◀◁◄"  # 사진 오른쪽에 "◀ 설명"으로 놓인 설명의 머리표
LEFT_MARKS = "▶▷►"  # 사진 왼쪽에 "설명 ▶"로 놓인(오른쪽 맞춤) 설명의 머리표
SIDE_NEAR = 50 # 머리표가 사진 이미지(흰 여백 포함) 오른쪽 끝에서 이만큼까지 겹쳐 있어도 그 사진의 설명으로 본다
SIDE_NEAR_LEFT = 100  # 왼쪽 설명(▶)은 사진 이미지의 왼쪽 흰 여백이 넓은 경우(차전자)가 있어 이만큼까지 겹쳐 있어도 본다
SIDE_GAP = 6  # 사진(과 아래 설명) 아래에 옮겨 붙이는 오른쪽 설명 사이의 간격(pt)


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


def _merge_split_photos(groups):
    """한 장의 그림이 사진 둘로 쪼개져 있으면 한 묶음으로 합친다. 이미지 둘이 가로로 겹쳐 놓인 경우(견우자 흑축 외형·단면,
    상심자 꽃잎조각·수과, 인동 잎 윗면·아랫면)와 위아래로 5pt쯤만 떨어져 이어진 경우(견우자 미국나팔꽃 씨 외형·단면 등)다.
    이미지 가장자리의 흰 여백이 옆 사진과 겹치는 정도(40% 미만)는 나란히 놓인 다른 사진이므로 합치지 않는다."""
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                A, B = a["rect"], b["rect"]
                xo = min(A[2], B[2]) - max(A[0], B[0])
                yo = min(A[3], B[3]) - max(A[1], B[1])
                stacked = (
                    abs(A[0] - B[0]) <= MERGE_STACK_X_TOL and abs(A[2] - B[2]) <= MERGE_STACK_X_TOL
                    and (0 <= B[1] - A[3] <= MERGE_STACK_GAP or 0 <= A[1] - B[3] <= MERGE_STACK_GAP)
                )
                if stacked or (xo > MERGE_X_OVERLAP * min(A[2] - A[0], B[2] - B[0]) and yo > MERGE_Y_OVERLAP * min(A[3] - A[1], B[3] - B[1])):
                    a["rect"] = [min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3])]
                    a["xrefs"] |= b["xrefs"]
                    groups.remove(b)
                    merged = True
                    break
            if merged:
                break
    return groups


def _has_mark(text):
    return any(ch in text for ch in SIDE_MARKS + LEFT_MARKS)


def _find_side_captions(page, blocks, groups, footer_y):
    """사진 오른쪽에 "◀ 설명"으로, 또는 사진 왼쪽에 "설명 ▶"(오른쪽 맞춤)으로 놓인 설명을 찾아 그 사진 묶음(g["side"])에
    붙인다. 이어지는 설명 줄(같은 칸에서 줄 간격 CAPTION_GAP 이내)도 함께 모으고, 쓰인 글 블록 영역의 집합을 돌려준다.
    g["side"] = {"rect": 설명 전체 영역, "blocks": [글 블록 영역], "marks": [머리표(◀) 글자 영역]}"""
    used = set()
    for m in blocks:
        # PDF 글자 순서가 뒤섞여("편강황 ◀ 위품") 머리표가 글 블록 맨 앞에 안 나올 수 있어 블록 안에 있는지로 본다
        if not _has_mark(m[4]) or tuple(m[:4]) in used:
            continue
        mx0, my0, mx1, my1 = m[:4]
        left = any(ch in m[4] for ch in LEFT_MARKS)  # 설명이 사진 왼쪽에 있다(머리표 ▶, 오른쪽 맞춤)
        best = None
        for g in groups:
            gx0, gy0, gx1, gy1 = g["rect"]
            vo = min(my1, gy1) - max(my0, gy0)
            if g.get("side") or vo <= 0:
                continue
            if left:
                if gx1 <= mx1 or mx1 - gx0 > SIDE_NEAR_LEFT:
                    continue
                key = (-vo, abs(gx0 - mx1))
            else:
                if gx0 >= mx0 or gx1 - mx0 > SIDE_NEAR:
                    continue
                key = (-vo, abs(mx0 - gx1))
            if best is None or key < best[0]:
                best = (key, g)
        if best is None:
            continue
        rects = [tuple(m[:4])]
        bottom = my1
        for b in sorted(blocks, key=lambda b: b[1]):
            bx0, by0, bx1, by1 = b[:4]
            if (
                tuple(b[:4]) in used or tuple(b[:4]) == rects[0] or by0 < bottom - 3 or by0 - bottom > CAPTION_GAP
                or by1 > footer_y or _has_mark(b[4])
                or not (mx1 - 40 <= bx1 <= mx1 + 6 if left else mx0 - 6 <= bx0 <= mx0 + 40)
                or _NOT_CAPTION.match(b[4].strip())
            ):
                continue
            rects.append((bx0, by0, bx1, by1))
            bottom = max(bottom, by1)
        used.update(rects)
        union = (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))
        marks = [(r.x0, r.y0, r.x1, r.y1) for ch in SIDE_MARKS + LEFT_MARKS for r in page.search_for(ch, clip=fitz_rect(m[:4]))]
        best[1]["side"] = {"rect": union, "blocks": rects, "marks": marks, "left": left}
    return used


def fitz_rect(t):
    import pymupdf as fitz

    return fitz.Rect(*t)


def _split_captions(page, number, regions):
    """SPLIT_CAPTIONS 예외: 가로로 나란한 두 사진의 설명이 PDF 에서 한 줄로 이어져 있을 때, 그 줄을 지정한 글 자리에서
    갈라 앞 절반(쉼표 앞)은 첫째 사진, 뒤 절반은 둘째 사진 아래에 보이게 한다. 각 묶음의 "hide" 에 상대편 절반이 있는
    영역을 넣어 그릴 때 지우고, 둘째 묶음의 영역을 설명 줄까지 늘린다."""
    for s in SPLIT_CAPTIONS:
        if s["page"] != number:
            continue
        found = [next((r for r in regions if abs(r["photo"][0] - p[0]) < 3 and abs(r["photo"][1] - p[1]) < 3), None)
                 for p in s["photos"]]
        if None in found:
            continue
        a, b = found  # a: 왼쪽 사진, b: 오른쪽 사진
        # 긴 설명 줄이 어느 쪽 사진 아래에 붙어 있는지(보통 왼쪽이지만 오른쪽일 때도 있다: 녹용 8번째 사진)
        owner = wide = None
        for r in (a, b):
            w = max(r["texts"], key=lambda t: t[2] - t[0], default=None)
            if w is not None and w[2] - w[0] > r["photo"][2] - r["photo"][0]:
                owner, wide = r, w
                break
        if owner is None:
            continue
        chars = []
        for blk in page.get_text("rawdict", clip=fitz_rect((wide[0] - 1, wide[1] - 1, wide[2] + 1, wide[3] + 1)))["blocks"]:
            for ln in blk.get("lines", []):
                for sp in ln["spans"]:
                    # 바로 위/아래 줄 글자가 clip 경계에 걸려 섞이지 않게, 글자 중심이 설명 줄 안에 있는 것만 쓴다
                    chars += [(c["c"], c["bbox"]) for c in sp["chars"] if c["c"].strip()
                              and wide[0] - 1 <= (c["bbox"][0] + c["bbox"][2]) / 2 <= wide[2] + 1
                              and wide[1] <= (c["bbox"][1] + c["bbox"][3]) / 2 <= wide[3]]
        chars.sort(key=lambda c: c[1][0])
        text = "".join(c[0] for c in chars)
        at = text.find(s["after"])
        if at < 0:
            continue
        last = chars[at + len(s["after"]) - 1][1]
        nxt = next((c for c in chars[at + len(s["after"]):] if c[0] not in ",.;:"), None)
        if nxt is None:
            continue
        cut_a, start_b = last[2] + 0.3, nxt[1][0] - 0.3
        owner["texts"] = [t for t in owner["texts"] if t != wide]
        if owner is a:
            ax1 = max([a["photo"][2]] + [t[2] for t in a["texts"]]) + 2
            a["rect"] = (a["rect"][0], a["rect"][1], ax1, a["rect"][3])
            # 뒤 절반 설명이 둘째 사진보다 왼쪽에서 시작할 수 있다(갱미 "나미 [위품]")
            b["rect"] = (min(b["rect"][0], start_b - 2), b["rect"][1], max(b["rect"][2], wide[2] + 2), max(b["rect"][3], wide[3] + 2))
        else:
            # 긴 줄이 오른쪽 사진 아래에 붙어 있던 경우: 앞 절반이 왼쪽 사진, 뒤 절반이 오른쪽 사진의 설명이 된다.
            # 영역을 둘 다 설명 줄에 맞게 다시 잡는다(오른쪽 영역은 긴 줄 때문에 왼쪽으로 넓어져 있었다)
            a["rect"] = (min(a["rect"][0], wide[0] - 2), a["rect"][1],
                         max([a["photo"][2]] + [t[2] for t in a["texts"]]) + 2, max(a["rect"][3], wide[3] + 2))
            b["rect"] = (min([b["photo"][0], start_b] + [t[0] for t in b["texts"]]) - 2, b["rect"][1],
                         max([b["photo"][2], wide[2]] + [t[2] for t in b["texts"]]) + 2, max(b["rect"][3], wide[3] + 2))
        # 지울 영역은 설명 줄 높이에서 위아래 1.5pt 안쪽으로 잡는다(줄 간격이 붙은 바로 위 줄의 글자까지 지우지 않게)
        a["hide"].append((cut_a, wide[1] + 1.5, wide[2] + 2, wide[3] - 1.5))
        b["hide"].append((wide[0] - 2, wide[1] + 1.5, start_b, wide[3] - 1.5))


def photo_regions(page, number=None):
    """페이지 하나의 사진 묶음 목록. 각 항목은 {"rect": (x0, y0, x1, y1) 사진+아래 설명 영역,
    "photo": 사진 영역, "xrefs": 사진 이미지 번호들, "texts": 이 묶음에 딸린 글 블록 영역들}.
    읽는 순서(위에서 아래, 같은 줄이면 왼쪽부터)로 돌려준다."""
    groups = _merge_split_photos(_group_tiles(_tiles(page)))
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
    side_used = _find_side_captions(page, blocks, groups, footer_y)
    # 사진 이미지 여백이 옆 사진과 겹치는 쪽이 있어서, 글 블록 하나는 묶음 하나에만 준다.
    # 바로 위(세로로 가장 가까운) 사진 중에서 가로 중심이 가장 가까운 묶음을 고른다
    # (가운데나 오른쪽 아래에 엇갈려 놓인 사진의 설명이 윗줄 사진에 잘못 붙지 않게)
    for b in blocks:
        if tuple(b[:4]) in side_used:
            continue
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
    # 제목 줄이 사진보다 왼쪽으로 치우쳐 있으면(예: 택란 "택란 잎과 줄기") 가로로 사진 폭 안에 든 부분이 절반에 못 미쳐
    # 위에서 어느 묶음에도 못 들어간다. 이런 제목 줄이 같은 묶음 설명 첫 줄 바로 위에 왼쪽을 맞춰 놓여 있으면 그 묶음에 넣는다
    # (넣지 않으면 제목 맨 앞 글자가 잘린다)
    taken = {c[:4] for g in groups for c in g["cands"]} | side_used
    for b in blocks:
        bx0, by0, bx1, by1 = b[:4]
        if (by0, by1, bx0, bx1) in taken or (bx0, by0, bx1, by1) in side_used:
            continue
        for g in groups:
            gy1 = g["rect"][3]
            if by0 < gy1 - 4 or by1 > g["limit"] + 1:
                continue
            if any(0 <= c[0] - by1 <= CAPTION_GAP and abs(c[2] - bx0) <= TILE_X_TOL for c in g["cands"]):
                g["cands"].append((by0, by1, bx0, bx1, _NOT_CAPTION.match(b[4].strip()) is not None))
                taken.add((by0, by1, bx0, bx1))
                break
    assigned = {c[:4] for g in groups for c in g["cands"]} | side_used
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
                "side": g.get("side"),
                "hide": [],
            }
        )
    _split_captions(page, page.number if number is None else number, regions)
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


def _redact_hidden(tp, me):
    """사진 본체를 그릴 때 숨길 글자(me["hide"]: 옆 사진 설명과 한 줄로 이어진 설명 중 상대편 절반)를 임시 쪽에서 지운다."""
    import pymupdf as fitz

    for r in me.get("hide") or []:
        tp.add_redact_annot(fitz.Rect(*r), fill=False)


def _redact_side(tp, side):
    """임시 쪽에서 오른쪽 설명(◀ 머리표 포함)의 글자를 지운다(사진 본체를 그릴 때 쓴다)."""
    import pymupdf as fitz

    for t in side["blocks"]:
        tp.add_redact_annot(fitz.Rect(*t) + (-1, -1, 1, 1), fill=False)


def _width_target(page_no, me):
    """WIDTH_TO 에 가로폭이 지정된 사진 묶음이면 그 폭(pt), 아니면 None."""
    return next((s["width_pt"] for s in WIDTH_TO if s["page"] == page_no
                 and abs(me["photo"][0] - s["at"][0]) < 3 and abs(me["photo"][1] - s["at"][1]) < 3), None)


def _pad_center(img, W1, off, m):
    """img(배율 m배)를 가로 W1(k=1 기준 픽셀) 폭의 그림 가운데(왼쪽 여백 off)에 놓는다. 폭이 같으면 그대로."""
    from PIL import Image

    if W1 * m == img.width:
        return img
    bg = (255, 255, 255, 0) if img.mode == "RGBA" else (255, 255, 255)
    out = Image.new(img.mode, (W1 * m, img.height), bg)
    out.paste(img, (off * m, 0))
    return out


def _block_words(words, rect):
    x0, y0, x1, y1 = rect
    return [t for t in words if x0 - 1 <= (t[0] + t[2]) / 2 <= x1 + 1 and y0 - 1 <= (t[1] + t[3]) / 2 <= y1 + 1]


def _split_units(words):
    """설명 블록의 낱말들을 줄(위에서 아래)별 "줄바꿈 단위"로 나눈다. 쉼표/마침표/괄호처럼 앞 낱말에 붙어 있는 글자는
    같은 단위다. 돌려주는 값: [{"x0","x1","y0","y1"(단위가 속한 줄의 위/아래)}] 읽는 순서."""
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines = []
    for w in words:
        cy = (w[1] + w[3]) / 2
        if lines and abs(cy - lines[-1]["cy"]) <= 0.5 * (lines[-1]["y1"] - lines[-1]["y0"]):
            lines[-1]["words"].append(w)
            lines[-1]["y0"], lines[-1]["y1"] = min(lines[-1]["y0"], w[1]), max(lines[-1]["y1"], w[3])
        else:
            lines.append({"cy": cy, "y0": w[1], "y1": w[3], "words": [w]})
    units = []
    for ln in lines:
        cur = None
        for w in sorted(ln["words"], key=lambda w: w[0]):
            if cur and w[0] - cur["x1"] <= WORD_JOIN_GAP:
                cur["x1"] = max(cur["x1"], w[2])
            else:
                cur = {"x0": w[0], "x1": w[2], "y0": ln["y0"], "y1": ln["y1"]}
                units.append(cur)
    return units


def _reflow_image(tp, rects, avail_pt, scale, k=1):
    """설명 블록들(rects)의 글을 폭 avail_pt 안에서 다시 줄바꿈한 RGBA 그림(배율 scale*k 픽셀/pt, 투명 배경)을 돌려준다.
    PDF 안의 글자를 다시 배치할 수 없어서, 낱말 덩어리(단위)마다 PDF 에서 그대로 그린 조각을 새 줄에 이어 붙인다.
    놓을 위치와 크기는 k=1 배율의 정수 픽셀로 정해 k배 한다(k가 달라도 "그림 크기/k"가 같게). 글이 없으면 None."""
    import io

    import pymupdf as fitz
    from PIL import Image

    clip = fitz.Rect(min(r[0] for r in rects) - 3, min(r[1] for r in rects) - 3,
                     max(r[2] for r in rects) + 3, max(r[3] for r in rects) + 3)
    allw = tp.get_text("words", clip=clip)
    words = []
    for r in rects:
        words += _block_words(allw, r)
    units = _split_units(words)
    if not units:
        return None
    gaps = sorted(b["x0"] - a["x1"] for a, b in zip(units, units[1:])
                  if abs(a["y0"] - b["y0"]) < 3 and WORD_JOIN_GAP < b["x0"] - a["x1"] < 12)
    gap = gaps[len(gaps) // 2] if gaps else 3.5
    pitches = sorted({round(b["y0"] - a["y0"], 1) for a, b in zip(units, units[1:]) if b["y0"] - a["y0"] > 4})
    line_h = max(u["y1"] - u["y0"] for u in units)
    pitch = pitches[0] if pitches else line_h * 1.08
    rows, cur, cur_w = [], [], 0.0
    for u in units:
        uw = u["x1"] - u["x0"]
        add = uw if not cur else gap + uw
        if cur and cur_w + add > avail_pt:
            rows.append(cur)
            cur, cur_w = [u], uw
        else:
            cur.append(u)
            cur_w += add
    rows.append(cur)
    sx = scale * k
    pm = tp.get_pixmap(matrix=fitz.Matrix(sx, sx), clip=clip, alpha=True)
    src = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGBA")
    w1 = max(round(sum(u["x1"] - u["x0"] for u in r) * scale + gap * scale * (len(r) - 1)) for r in rows) + 2
    h1 = round(pitch * scale * (len(rows) - 1) + line_h * scale) + 3
    canvas = Image.new("RGBA", (w1 * k, h1 * k), (255, 255, 255, 0))
    for ri, row in enumerate(rows):
        x = 0.0
        for u in row:
            top = round((u["y0"] - 0.5 - clip.y0) * sx)
            box = (round((u["x0"] - 0.5 - clip.x0) * sx), top, round((u["x1"] + 0.5 - clip.x0) * sx), top + round((line_h + 1) * sx))
            piece = src.crop((max(0, box[0]), max(0, box[1]), min(src.width, box[2]), min(src.height, box[3])))
            canvas.paste(piece, (round(x) * k, round(ri * pitch * scale) * k))
            x += (u["x1"] - u["x0"]) * scale + gap * scale
    return canvas


def _side_image(doc, page_no, index, scale, k=1, reflow_pt=None):
    """사진 오른쪽에 있던 "◀ 설명"을 ◀ 없이 그려서(배율 scale*k 픽셀/pt) 흰 여백을 자른 RGBA 그림으로 돌려준다.
    사진은 모두 지우고 글자/선만 그리며 배경은 투명하다. 자르는 위치는 k의 배수 픽셀에 맞춰서, 이 그림을
    k로 나누면 배율 scale 그림이 크기가 딱 맞게 나온다. reflow_pt 가 있으면 첫 줄(제목)은 그대로 두고 그 아래 설명
    글을 폭 reflow_pt(pt) 안에서 다시 줄바꿈한다. 없으면 None."""
    import io

    import pymupdf as fitz
    from PIL import Image

    me = photo_regions(doc[page_no])[index]
    side = me.get("side")
    if not side:
        return None

    def plain(tp):
        # 사진 왼쪽에 오른쪽 맞춤으로 놓였던 설명(▶)은 줄마다 왼쪽 끝을 맞춰서 붙인다
        if side.get("left"):
            return _alpha_cropped_left(tp, side["blocks"], scale, k)
        return _alpha_cropped(tp, side["rect"], scale, k)

    tmp = fitz.open()
    try:
        tmp.insert_pdf(doc, from_page=page_no, to_page=page_no)
        tp = tmp[0]
        for xref in {im[0] for im in tp.get_images(full=True)}:
            tp.delete_image(xref)
        for mk in side["marks"]:
            tp.add_redact_annot(fitz.Rect(*mk) + (-0.5, -0.5, 0.5, 0.5), fill=False)
        tp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        if reflow_pt and len(side["blocks"]) > 1:
            # 첫 줄(제목, 예: "진범 뿌리 [위품]")은 그대로, 그 아래 설명 글만 폭 reflow_pt 에 맞춰 다시 줄바꿈한다
            head = _alpha_cropped(tp, side["blocks"][0], scale, k)
            body = _reflow_image(tp, side["blocks"][1:], reflow_pt - 2, scale, k)
            if head is None or body is None:
                return plain(tp)
            gap = round(-2 * scale) * k  # 제목 아래 흰 여백과 겹쳐서 원본 줄 간격에 가깝게
            out = Image.new("RGBA", (max(head.width, body.width), head.height + gap + body.height), (255, 255, 255, 0))
            out.paste(head, (0, 0))
            out.alpha_composite(body, (0, head.height + gap))
            return out
        return plain(tp)
    finally:
        tmp.close()


def _alpha_cropped(tp, rect, scale, k=1):
    """임시 쪽 tp 의 rect 영역을 투명 배경으로 그려(배율 scale*k) 글자가 있는 부분만 CROP_PAD 여백을 두고 자른 RGBA
    그림. 자르는 위치는 k=1 배율로 정하고 k배 해서 쓴다(k가 달라도 "그림 크기/k"가 같아서 사진층(k=1)과 글자층(k>1)이
    1픽셀도 어긋나지 않는다)."""
    import io

    import pymupdf as fitz
    from PIL import Image

    rect = fitz.Rect(*rect) + (-2, -2, 2, 2)
    pm1 = tp.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=True)
    img1 = Image.open(io.BytesIO(pm1.tobytes("png"))).convert("RGBA")
    box = img1.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return None
    pad = round(CROP_PAD * scale)
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(img1.width, box[2] + pad), min(img1.height, box[3] + pad)
    if k == 1:
        img = img1
    else:
        pm = tp.get_pixmap(matrix=fitz.Matrix(scale * k, scale * k), clip=rect, alpha=True)
        img = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGBA")
    out = Image.new("RGBA", ((x1 - x0) * k, (y1 - y0) * k), (255, 255, 255, 0))
    out.paste(img.crop((x0 * k, y0 * k, min(x1 * k, img.width), min(y1 * k, img.height))), (0, 0))
    return out


def _alpha_cropped_left(tp, blocks, scale, k=1):
    """_alpha_cropped 와 같되, 글 블록(줄)마다 따로 잘라 모든 줄의 왼쪽 끝이 같아지게 옮겨 붙인다(오른쪽 맞춤 설명을 왼쪽 맞춤으로).
    줄의 위치와 옮기는 폭은 k=1 배율의 정수 픽셀로 정하고 k배 한다(사진층/글자층이 어긋나지 않게)."""
    import io
    import math

    import pymupdf as fitz
    from PIL import Image

    rect = fitz.Rect(min(b[0] for b in blocks), min(b[1] for b in blocks), max(b[2] for b in blocks), max(b[3] for b in blocks)) + (-2, -2, 2, 2)
    left = min(b[0] for b in blocks)

    def build(kk):
        pm = tp.get_pixmap(matrix=fitz.Matrix(scale * kk, scale * kk), clip=rect, alpha=True)
        img = Image.open(io.BytesIO(pm.tobytes("png"))).convert("RGBA")
        out = Image.new("RGBA", img.size, (255, 255, 255, 0))
        for b in blocks:
            x0, x1 = int((b[0] - 1.5 - rect.x0) * scale), math.ceil((b[2] + 1.5 - rect.x0) * scale)
            y0, y1 = int((b[1] - 1 - rect.y0) * scale), math.ceil((b[3] + 1 - rect.y0) * scale)
            dx = max(0, x0 - round((b[0] - left) * scale))
            piece = img.crop((x0 * kk, y0 * kk, min(x1 * kk, img.width), min(y1 * kk, img.height)))
            out.alpha_composite(piece, (dx * kk, y0 * kk))
        return out

    img1 = build(1)
    box = img1.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return None
    pad = round(CROP_PAD * scale)
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(img1.width, box[2] + pad), min(img1.height, box[3] + pad)
    img = img1 if k == 1 else build(k)
    out = Image.new("RGBA", ((x1 - x0) * k, (y1 - y0) * k), (255, 255, 255, 0))
    out.paste(img.crop((x0 * k, y0 * k, min(x1 * k, img.width), min(y1 * k, img.height))), (0, 0))
    return out


def _stack_side(main, side, scale, mode, k=1):
    """main 그림 아래에 side(_side_image 가 돌려준 RGBA, 배율 scale*k)를 붙인다. 간격은 SIDE_GAP.
    mode: "all"(main 은 RGB, 흰 배경에 겹침, 배율 scale) / "photo"(main 은 RGB, side 자리를 흰색으로 비움,
    배율 scale) / "text"(main 은 투명 배경 RGBA, 배율 scale*k, 투명 배경에 겹침)."""
    from PIL import Image

    gap = round(SIDE_GAP * scale) * (k if mode == "text" else 1)
    if mode != "text":
        side = side.resize((side.width // k, side.height // k), Image.LANCZOS)
    w, h = max(main.width, side.width), main.height + gap + side.height
    if mode == "text":
        out = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        out.paste(main, (0, 0))
        out.paste(side, (0, main.height + gap))
        return out
    out = Image.new("RGB", (w, h), (255, 255, 255))
    out.paste(main, (0, 0))
    if mode == "all":
        out.paste(side, (0, main.height + gap), side)
    return out


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
        tregs = photo_regions(tp, page_no)
        tme = tregs[index]
        others = [r for r in tregs if r is not tme and _intersects(r["rect"], tme["rect"])]
        for r in tregs:
            for xref in r["xrefs"]:
                if xref:
                    tp.delete_image(xref)
        for o in others:
            for t in o["texts"]:
                tp.add_redact_annot(fitz.Rect(*t) + (-1, -1, 1, 1), fill=False)
        if tme.get("side"):
            _redact_side(tp, tme["side"])
        _redact_hidden(tp, tme)
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
    for ti, (t, img) in enumerate(zip(tiles, loaded)):
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
        # 가로로 겹쳐 놓인 조각(한 그림이 둘로 쪼개진 경우)은 흰 여백이 앞 조각의 그림을 덮지 않게 어두운 쪽을 남긴다
        if any(min(o["bbox"][2], x1) - max(o["bbox"][0], x0) > 2 and min(o["bbox"][3], y1) - max(o["bbox"][1], y0) > 2 for o in tiles[:ti]):
            under = canvas.crop((px, py, px + img.width, py + img.height))
            img = ImageChops.darker(under, img)
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
    # 가로폭을 지정한 사진(WIDTH_TO)은 그 폭이 되게 가운데에 맞춰 흰 여백(글자층은 투명)을 덧붙인다
    target_pt = _width_target(page_no, me)
    w1 = box[2] - box[0]
    # 화면에 보이는 폭(가로 픽셀 x 보이는 dpi / 원본 dpi)이 2번째 사진과 같도록 원본 dpi(정수)로 계산한다
    W1 = max(w1, round(target_pt * (native_dpi(doc, page_no, index) or z * 72) / 72)) if target_pt else w1
    off = (W1 - w1) // 2
    # 사진 오른쪽에 "◀ 설명"으로 있던 설명은 ◀ 없이 사진(과 아래 설명) 아래에 붙인다. 가로폭을 지정한 사진이면
    # 그 폭에 맞춰 설명 글을 줄바꿈한다
    side = _side_image(doc, page_no, index, z, k, reflow_pt=target_pt) if me.get("side") else None
    out = io.BytesIO()
    if layer == "photo":
        img = _pad_center(photo_only.crop(box), W1, off, 1)
        if side:
            img = _stack_side(img, side, z, "photo", k)
        img.save(out, "PNG")
    elif layer == "text":
        w, h = box[2] - box[0], box[3] - box[1]
        text = Image.new("RGBA", (w * k, h * k), (255, 255, 255, 0))
        text.paste(overlay_k.crop((box[0] * k, box[1] * k, box[2] * k, box[3] * k)), (0, 0))
        text = _pad_center(text, W1, off, k)
        if side:
            text = _stack_side(text, side, z, "text", k)
        text.save(out, "PNG")
    else:
        canvas = _pad_center(canvas, W1, off, 1)
        if side:
            canvas = _stack_side(canvas, side, z, "all", k)
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
        if others or me.get("side") or me.get("hide"):
            # 겹치는 다른 묶음의 사진과 글자를 지운 임시 쪽에 그린다
            tmp = fitz.open()
            tmp.insert_pdf(doc, from_page=page_no, to_page=page_no)
            tp = tmp[0]
            # 이미지 번호는 임시 문서에서 달라지므로 그쪽에서 다시 찾는다(순서는 같다)
            tregs = photo_regions(tp, page_no)
            tme = tregs[index]
            others = [r for r in tregs if r is not tme and _intersects(r["rect"], tme["rect"])]
            for o in others:
                for xref in o["xrefs"] - tme["xrefs"]:
                    if xref:
                        tp.delete_image(xref)
                for t in o["texts"]:
                    tp.add_redact_annot(fitz.Rect(*t) + (-1, -1, 1, 1), fill=False)
            if tme.get("side"):
                _redact_side(tp, tme["side"])
            _redact_hidden(tp, tme)
            tp.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
            page = tp
        clip = _content_rect(page, me["rect"])
        png = page.get_pixmap(clip=clip, dpi=dpi).tobytes("png")
        target_pt = _width_target(page_no, me)
        if me.get("side") or target_pt:
            import io

            from PIL import Image

            z = dpi / 72
            main = Image.open(io.BytesIO(png)).convert("RGB")
            if target_pt:
                # 가로폭을 지정한 사진(WIDTH_TO)은 그 폭이 되게 가운데에 맞춰 흰 여백을 덧붙인다
                W1 = max(main.width, round(target_pt * dpi / 72))
                main = _pad_center(main, W1, (W1 - main.width) // 2, 1)
            # 사진 오른쪽에 "◀ 설명"으로 있던 설명은 ◀ 없이 사진(과 아래 설명) 아래에 붙인다
            side = _side_image(doc, page_no, index, z, reflow_pt=target_pt) if me.get("side") else None
            if side:
                main = _stack_side(main, side, z, "all")
            buf = io.BytesIO()
            main.save(buf, "PNG")
            png = buf.getvalue()
        return png
    finally:
        if tmp is not None:
            tmp.close()
