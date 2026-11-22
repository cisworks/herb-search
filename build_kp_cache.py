# -*- coding: utf-8 -*-
"""
대한민국약전 검색(/kp)에 필요한 것을 미리 만들어 .parse_cache/ 에 저장한다.

  1) "[별표 N] ... (제2026-44호).hwpx" 6개 파일의 파싱 결과(약 10초)
  2) 본문에 나오는 모든 그림(구조식 등)의 화면용 변환본: BMP -> PNG, WMF/EMF -> SVG (수백 개, 1~2분)

이걸 해 두면 서버를 켠 뒤 처음 /kp 에 들어와도 기다리지 않고, 그림도 바로 뜬다. 안 해도 첫 접속 때와 그림을 처음 열 때 자동으로 만들어진다.
hwpx 파일이나 parse_kp.py 를 바꾸면 저장된 것이 자동으로 무효가 되므로, 그때는 이 스크립트를 한 번 다시 실행하면 된다.

사용법(이 폴더에서):
    python build_kp_cache.py

PythonAnywhere 에서는 git pull 한 뒤 콘솔에서 실행하고, 그다음 Web 탭에서 Reload 하면 된다(웹앱과 같은 파이썬으로):
    python3.10 build_kp_cache.py
"""
import re
import sys
import time
import zlib

import kp_site


def main():
    t0 = time.time()
    d = kp_site.get_data()
    print(f"[파싱] {len(d.entries)}개 항목 ({time.time() - t0:.1f}초)")
    for docno in sorted(kp_site.parse_kp.DOCS):
        n = sum(1 for e in d.entries if e["doc"] == docno)
        name = d.files.get(docno, "(파일 없음)")
        print(f"  {kp_site.DOC_LABEL[docno]}: {n}개  <- {name}")
    missing = [n for n in kp_site.parse_kp.DOCS if n not in d.files]
    if missing:
        print(f"[경고] 찾지 못한 파일: {', '.join(kp_site.parse_kp.DOCS[n][0] for n in missing)} ... .hwpx")

    # 본문에 나오는 그림(번호, 이름, 자르기)을 모아 모두 변환해 저장한다.
    refs = set()
    rx = re.compile(r'src="/kp/img/(\d)/([A-Za-z0-9_\-]+)(?:\?c=([0-9.,]+))?"')
    for e in d.entries:
        for m in rx.finditer(zlib.decompress(e["html_z"]).decode("utf-8")):
            refs.add((int(m.group(1)), m.group(2), m.group(3) or ""))
    refs = sorted(refs)
    t1 = time.time()
    done = bad = 0
    failed = []
    for i, (doc, name, crop) in enumerate(refs, 1):
        try:
            ok = kp_site.image_bytes(doc, name, crop) is not None
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  [실패] {doc}/{name}: {exc}")
        if ok:
            done += 1
        else:
            bad += 1
            failed.append(f"{doc}/{name}")
        if i % 100 == 0 or i == len(refs):
            print(f"  [그림] {i}/{len(refs)}  ({time.time() - t1:.0f}초 경과)", flush=True)
    print(f"[그림] {done}개 변환/확인 완료" + (f", {bad}개는 변환하지 못함: {', '.join(failed[:10])}" if bad else ""))
    try:
        files = list(kp_site.IMG_CACHE_DIR.glob("*"))
        print(f"그림 파일 {len(files)}개, 합계 {sum(f.stat().st_size for f in files) / 1048576:.0f}MB ({kp_site.IMG_CACHE_DIR})")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
