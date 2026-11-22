# -*- coding: utf-8 -*-
"""
관능검사해설서 사진 정보를 미리 만들어 .parse_cache/ 에 저장한다.

설명창에서 생약을 처음 열 때, 그리고 "사진정보"를 처음 열 때 서버가 PDF 를 분석하고 사진을 그리느라 시간이 걸린다.
이 스크립트가 PDF 전체에 대해 아래를 한꺼번에 만들어 두면, 서버를 다시 켜도 그 대기가 없다.
  1) 품목별 사진 위치 표 (약 1분)
  2) 사진마다의 원본 해상도(dpi) 값
  3) 사진 그림 파일(목록에 보이는 원본 해상도 PNG) - 사진 한 장당 약 0.8초, 0.4~1.5MB. 전체 약 1,800장이면 수십 분, 1GB 이상

사용법(이 폴더에서):
    python build_photo_cache.py                전체(1~3)
    python build_photo_cache.py --regions-only 사진 위치 표만(1) - 예전 방식, 빠름
    python build_photo_cache.py --limit 50     사진 그림을 50장만 새로 만들고 멈춤(시험용)

이미 만들어 둔 것은 건너뛰므로, 중간에 멈췄다가 다시 실행하면 이어서 한다(그림 50장마다 중간 저장). 사진 PDF 나 sensory_photos.py 를
바꾸면 저장된 것이 자동으로 무효가 되므로, 서버를 켜기 전에 이 스크립트를 한 번 다시 실행하면 된다.

PythonAnywhere 에서는 git pull 한 뒤 콘솔에서 실행하고, 그다음 Web 탭에서 Reload 하면 된다. 오래 걸리니 콘솔 창을 닫아도 계속 돌게 하려면:
    nohup python3.10 build_photo_cache.py > build_photo_cache.log 2>&1 &
    tail -f build_photo_cache.log        (진행 상황 보기, 멈추려면 Ctrl+C - 작업은 계속 돈다)
"""
import sys
import time

import app  # 데이터를 불러온다(파싱 캐시가 있으면 몇 초). 서버는 켜지지 않는다.


def _fmt(sec):
    sec = int(sec)
    return f"{sec // 60}분 {sec % 60}초" if sec >= 60 else f"{sec}초"


def main(argv):
    started = time.time()
    last = [0.0]

    def progress_regions(n, total, name):
        now = time.time()
        if n == total or now - last[0] >= 5:
            last[0] = now
            print(f"  [사진 위치] {n}/{total}  {name}  ({_fmt(now - started)} 경과)", flush=True)

    if not (app.BASE_DIR / app.SENSORY_PHOTO_FILE).is_file():
        print(f"[경고] {app.SENSORY_PHOTO_FILE} 파일이 없어 만들 것이 없습니다.")
        return 1

    # 1) 품목별 사진 위치 표
    done, total = app.build_photo_regions_table(progress_regions)
    if done == 0:
        print(f"[사진 위치] 이미 모두 저장되어 있습니다({total}개 품목).")
    else:
        print(f"[사진 위치] {done}개 품목을 새로 계산해 저장했습니다(전체 {total}개).")
    if "--regions-only" in argv:
        print(f"저장 위치: {app._PHOTO_TABLE_FILE}")
        return 0

    # 2~3) 사진마다 원본 해상도 값과 그림 파일
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    t1 = time.time()
    last[0] = 0.0

    def progress_images(n, total, name):
        now = time.time()
        if n == total or now - last[0] >= 10:
            last[0] = now
            per = (now - t1) / n
            print(f"  [사진 그림] {n}/{total}  {name}  ({_fmt(now - t1)} 경과, 남은 시간 약 {_fmt(per * (total - n))})", flush=True)

    drawn, skipped, total = app.build_photo_images(progress_images, limit=limit)
    if drawn == 0:
        print(f"[사진 그림] 이미 모두 저장되어 있습니다(전체 {total}장).")
    else:
        print(f"[사진 그림] {drawn}장을 새로 만들었습니다(이미 있던 {skipped}장 제외, 전체 {total}장, {_fmt(time.time() - t1)}).")
        left = total - skipped - drawn
        if left > 0:
            print(f"  남은 {left}장은 같은 명령을 다시 실행하면 이어서 만듭니다.")
    try:
        files = list(app.PHOTO_IMAGE_CACHE_DIR.glob("*.png"))
        print(f"그림 파일 {len(files)}개, 합계 {sum(f.stat().st_size for f in files) / 1048576:.0f}MB ({app.PHOTO_IMAGE_CACHE_DIR})")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
