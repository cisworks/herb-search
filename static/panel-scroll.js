// 왼쪽 검색 패널과 오른쪽 설명 패널(PC 배치): 마우스 포인터가 있는 패널에서 휠을 돌리면 그 패널의 화면만 스크롤된다.
// 오른쪽 설명 패널은 페이지 스크롤로 움직이고 왼쪽 패널은 제자리(sticky)라서, 설명 패널 위의 휠은 왼쪽을 건드리지 않는다.
// 반대로 왼쪽 패널(또는 그 안의 목록·카드) 위에서 휠을 돌릴 때, 안쪽 스크롤이 끝(맨 위/맨 아래)에 닿거나 스크롤할 것이 없으면
// 브라우저가 그 휠을 페이지로 넘겨(스크롤 연쇄) 오른쪽 설명 화면이 움직이는데, 그것을 막는다.
// 다만 페이지가 아래로 스크롤되어 상단 표시부(제목 줄)가 가려져 있을 때는, 왼쪽 목록 맨 위에서 휠을 계속 위로 굴리면 페이지를 위로 올려 상단 표시부가 보이게 한다.
// (폰·태블릿 세로 배치(폭 860px 이하)는 페이지 전체가 한 화면으로 스크롤되므로 건드리지 않는다.)
(function () {
  const left = document.querySelector('.left-panel');
  if (!left) return;
  const narrow = window.matchMedia('(max-width: 860px)');

  // 휠이 가리킨 자리에서 왼쪽 패널까지 올라가며, 그 방향으로 더 스크롤할 수 있는 안쪽 스크롤 영역이 있는지 본다
  function canScrollInside(target, dy) {
    for (let el = target; el && el !== document.documentElement; el = el.parentElement) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1) {
        const atTop = el.scrollTop <= 0;
        const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
        if ((dy < 0 && !atTop) || (dy > 0 && !atBottom)) return true;
      }
      if (el === left) break;
    }
    return false;
  }

  left.addEventListener('wheel', (ev) => {
    if (ev.defaultPrevented || ev.ctrlKey || ev.shiftKey || narrow.matches) return;  // Ctrl+휠(확대·축소)과 좁은 화면은 그대로
    const dy = ev.deltaY;
    if (!dy || Math.abs(ev.deltaX) > Math.abs(dy)) return;  // 가로 스크롤은 그대로
    if (canScrollInside(ev.target, dy)) return;  // 목록·카드가 그 방향으로 더 스크롤되면 그 안에서 스크롤된다
    ev.preventDefault();  // 더 스크롤할 곳이 없으면 오른쪽 화면으로 번지지 않게 막는다
    // 페이지 상단 표시부(제목 줄)가 위로 스크롤되어 가려져 있으면, 목록 맨 위에서 계속 휠을 위로 굴릴 때 페이지를 위로 올려 상단 표시부가 보이게 한다
    if (dy < 0 && window.scrollY > 0) window.scrollBy(0, dy);
  }, { passive: false });
})();
