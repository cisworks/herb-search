# -*- coding: utf-8 -*-
"""
관능검사해설서 사진 위치 표를 미리 만들어 .parse_cache/ 에 저장한다.

설명창에서 생약을 처음 열 때마다 서버가 PDF 에서 그 생약의 사진 위치를 분석하느라 약 0.2초가 걸린다(app.py 의
_sensory_photo_regions). 이 스크립트로 전체 품목을 한꺼번에 계산해 저장해 두면, 서버를 다시 켜도 그 대기가 없다.

사용법(이 폴더에서, 전체 약 1~2분):
    python build_photo_cache.py

다시 실행해도 이미 계산된 품목은 건너뛰므로 금방 끝난다. 사진 PDF(관능검사해설서)나 sensory_photos.py 를 바꿨을 때는
저장된 표가 자동으로 무효가 되므로, 서버를 켜기 전에 이 스크립트를 한 번 다시 실행하면 된다.
PythonAnywhere 에서는 git pull 한 뒤 콘솔에서 이 스크립트를 실행하고, 그다음 Web 탭에서 Reload 하면 된다.
"""
import sys
import time

import app  # 데이터를 불러온다(파싱 캐시가 있으면 몇 초). 서버는 켜지지 않는다.


def main():
    started = time.time()
    last = [0.0]

    def progress(n, total, name):
        now = time.time()
        if n == total or now - last[0] >= 5:
            last[0] = now
            print(f"  {n}/{total}  {name}  ({now - started:.0f}초 경과)", flush=True)

    if not (app.BASE_DIR / app.SENSORY_PHOTO_FILE).is_file():
        print(f"[경고] {app.SENSORY_PHOTO_FILE} 파일이 없어 만들 것이 없습니다.")
        return 1
    done, total = app.build_photo_regions_table(progress)
    if done == 0:
        print(f"이미 모두 저장되어 있습니다({total}개 품목). 할 일이 없습니다.")
    else:
        print(f"완료: {done}개 품목을 새로 계산해 저장했습니다(전체 {total}개, {time.time() - started:.0f}초).")
    print(f"저장 위치: {app._PHOTO_TABLE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
