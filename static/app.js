const searchInput = document.getElementById('searchInput');
// 검색(Enter, 목록에서 선택)을 마친 뒤 검색칸을 다시 누르면 검색어 전체를 블록 선택한다. 입력 중에는 false.
let searchDone = false;
let selectAllOnMouseUp = false;
const prepInput = document.getElementById('prepInput');
const prepToggle = document.getElementById('prepToggle');
const prepDropdown = document.getElementById('prepDropdown');
const prepResultsBox = document.getElementById('prepResults');
const comboToggle = document.getElementById('comboToggle');
const comboDropdown = document.getElementById('comboDropdown');
const originDropdown = document.getElementById('originDropdown');
let originTimer = null, originSeq = 0, originItems = [];  // 기원 추천 목록(아래 '기원 추천 목록' 참고)
const resultsBox = document.getElementById('results');
const detailBox = document.getElementById('detail');
const detailPlaceholder = document.getElementById('detailPlaceholder');

// 좌우 패널 사이의 핸들을 마우스로 끌어 왼쪽 패널 너비를 조절한다.
// 초기 너비는 검색창 안내 문구("생약명을 입력하거나 목록에서
// 선택하세요")가 잘리지 않고 다 보이는 만큼으로 고정해서 시작하고,
// 리사이저로는 그 초기 너비의 절반까지만 줄일 수 있게 한다.
function measureTextWidth(text, font) {
  const canvas = measureTextWidth._canvas || (measureTextWidth._canvas = document.createElement('canvas'));
  const ctx = canvas.getContext('2d');
  ctx.font = font;
  return ctx.measureText(text).width;
}

function computeInitialLeftWidth(leftPanel) {
  const inputStyle = getComputedStyle(searchInput);
  const font = `${inputStyle.fontWeight} ${inputStyle.fontSize} ${inputStyle.fontFamily}`;
  const textWidth = measureTextWidth(searchInput.placeholder, font);
  const horizontalPadding = parseFloat(inputStyle.paddingLeft) + parseFloat(inputStyle.paddingRight);
  const horizontalBorder = parseFloat(inputStyle.borderLeftWidth) + parseFloat(inputStyle.borderRightWidth);
  const slack = 4; // 안내 문구 끝이 테두리에 바짝 붙지 않도록 여유를 조금 둔다.
  const inputNeeded = Math.ceil(textWidth + horizontalPadding + horizontalBorder + slack);
  const tabBar = leftPanel.querySelector('.tab-bar');
  const tabBarNeeded = tabBar ? tabBar.scrollWidth : 0;
  return Math.max(inputNeeded, tabBarNeeded, 200);
}

(function initPanelResizer() {
  const leftPanel = document.querySelector('.left-panel');
  const panelResizer = document.getElementById('panelResizer');
  const wrapEl = document.querySelector('.wrap');
  if (!leftPanel || !panelResizer || !wrapEl) return;

  const baseLeftWidth = computeInitialLeftWidth(leftPanel);
  leftPanel.style.width = baseLeftWidth + 'px';
  syncLeftWidthVar();

  let dragging = false;
  let startX = 0;
  let startWidth = 0;

  function clampWidth(w) {
    const min = baseLeftWidth / 2;
    const max = Math.max(min, wrapEl.clientWidth * 0.6);
    return Math.min(max, Math.max(min, w));
  }

  function onMouseMove(e) {
    if (!dragging) return;
    leftPanel.style.width = clampWidth(startWidth + (e.clientX - startX)) + 'px';
    syncLeftWidthVar();
  }

  function onMouseUp() {
    if (!dragging) return;
    dragging = false;
    panelResizer.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    // 왼쪽 패널 너비가 바뀌면 줄바꿈 때문에 오른쪽 배경/목록창의 세로
    // 위치도 바뀔 수 있어, 기존 창 크기 변경 때 쓰던 높이 재계산 함수들을
    // 그대로 다시 불러 맞춘다.
    fitDetailPlaceholderHeight();
    fitComboDropdownHeight();
    fitTestItemDropdownHeight();
    fitTestResultsHeight();
    positionDetailResizer();
  }

  panelResizer.addEventListener('mousedown', (e) => {
    dragging = true;
    startX = e.clientX;
    startWidth = parseFloat(leftPanel.style.width) || leftPanel.getBoundingClientRect().width;  // 패널 상자는 스크롤 막대 자리(app.css)만큼 넓어서 getBoundingClientRect 대신 정한 너비를 쓴다
    panelResizer.classList.add('dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  });
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
})();

// 시험항목 검색 버튼 오른쪽의 ◀ 버튼을 누르면 왼쪽 화면 전체를 좁은
// 띠(원래 접힘 너비였던 44px의 30% = 약 13px)로 접고, 오른쪽 화면(배경
// 그림/상세 내용)이 남은 폭을 채운다. 다시 누르면 접기 전 너비로
// 되돌아온다.
(function initLeftPanelCollapse() {
  const leftPanel = document.querySelector('.left-panel');
  const panelResizer = document.getElementById('panelResizer');
  const collapseBtn = document.getElementById('leftPanelCollapseBtn');
  if (!leftPanel || !panelResizer || !collapseBtn) return;

  const COLLAPSED_WIDTH = 44 * 0.3;
  let savedWidth = null;

  // 접힌 상태에서 ▶ 에 마우스를 올리면 왼쪽 화면을 임시로 펼치고(peek), 마우스가 왼쪽 화면 밖으로 나가면 다시 접는다.
  let peekTimer = null;
  function openPeek() {
    if (!leftPanel.classList.contains('collapsed')) return;
    clearTimeout(peekTimer);
    leftPanel.style.setProperty('--peek-width', savedWidth || '400px');
    leftPanel.classList.add('peek');
  }
  function closePeek() {
    clearTimeout(peekTimer);
    leftPanel.classList.remove('peek');
    closeComboDropdown();
    closeTestItemDropdown();
    closePrepDropdown();
  }
  collapseBtn.addEventListener('mouseenter', openPeek);
  leftPanel.addEventListener('mouseenter', () => clearTimeout(peekTimer));
  leftPanel.addEventListener('mouseleave', () => {
    if (!leftPanel.classList.contains('peek')) return;
    peekTimer = setTimeout(closePeek, 120);  // 잠깐 경계를 스쳐도 바로 닫히지 않게
  });

  collapseBtn.addEventListener('click', () => {
    clearTimeout(peekTimer);
    leftPanel.classList.remove('peek');
    const collapsed = leftPanel.classList.toggle('collapsed');
    if (collapsed) {
      savedWidth = leftPanel.style.width;
      leftPanel.style.width = COLLAPSED_WIDTH + 'px';
      panelResizer.style.display = 'none';
      collapseBtn.textContent = '▶';
      collapseBtn.title = '왼쪽 화면 펼치기';
      collapseBtn.setAttribute('aria-label', '왼쪽 화면 펼치기');
    } else {
      if (savedWidth) { leftPanel.style.width = savedWidth; syncLeftWidthVar(); }
      panelResizer.style.display = '';
      collapseBtn.textContent = '◀';
      collapseBtn.title = '왼쪽 화면 접기';
      collapseBtn.setAttribute('aria-label', '왼쪽 화면 접기');
    }
    fitDetailPlaceholderHeight();
    fitComboDropdownHeight();
    fitTestItemDropdownHeight();
    fitTestResultsHeight();
    fitLeftPanelHeight();
    positionDetailResizer();
  });
})();

// 생약 내용을 보여주는 .detail 박스는 항상 오른쪽 화면(.detail-wrap)
// 가운데 정렬을 유지한다(margin:0 auto). 왼쪽/오른쪽 모서리 중 어느 쪽을
// 끌어도 고정된 중심점(centerX)을 기준으로 "중심에서 마우스까지 거리 ×
// 2"를 새 너비로 계산해 넣기만 하면, margin:auto가 알아서 다시 가운데로
// 맞춰준다 - 그래서 결과 너비는 항상 가운데 정렬된 채로 끝난다. 핸들
// 위치는 .detail의 실제 좌우 끝에 맞춰 positionDetailResizer가 다시
// 계산해 넣는다(패널 폭이 바뀌는 다른 조작들에서도 호출된다).
function positionDetailResizer() {
  const detailWrap = document.getElementById('detailWrap');
  const resizerLeft = document.getElementById('detailResizerLeft');
  const resizerRight = document.getElementById('detailResizerRight');
  if (!detailWrap || !resizerLeft || !resizerRight) return;
  // 관능검사해설서(PDF) 화면은 원래 전체 폭 고정이라 너비 조절 핸들이
  // 필요 없다.
  const shown = detailBox.classList.contains('show') && !detailBox.classList.contains('sensory-mode');
  detailWrap.classList.toggle('show', shown);
  if (!shown) return;
  resizerLeft.style.left = detailBox.offsetLeft + 'px';
  resizerRight.style.left = (detailBox.offsetLeft + detailBox.offsetWidth) + 'px';
}

(function initDetailResizer() {
  const detailWrap = document.getElementById('detailWrap');
  const resizerLeft = document.getElementById('detailResizerLeft');
  const resizerRight = document.getElementById('detailResizerRight');
  if (!detailWrap || !resizerLeft || !resizerRight || !detailBox) return;

  let dragging = false;
  let moved = false;
  let centerX = 0;
  let baseWidth = 900;  // 100%로 보는 기본 너비(끌기 시작할 때 계산)
  let dragSide = 'right';  // 지금 잡은 모서리('left' | 'right'): 퍼센트 풍선을 모서리 바깥쪽에 띄우는 데 쓴다
  let saved = null;  // 누르기 전 인라인 스타일(움직이지 않고 놓으면 원래대로 되돌린다)

  // 끄는 동안 마우스 옆에 기본 너비(100%) 대비 지금 너비를 퍼센트 숫자만 보여 주는 작은 풍선.
  // 눌렀을 때 "100%", 기본보다 넓히면 "120%"처럼 숫자가 커지고, 좁히면 "90%"처럼 숫자가 작아진다(+/- 부호 없이 숫자만).
  // 모서리에 마우스를 올리면 뜨는 안내("끌어서 너비 조절") 풍선과 비슷한 크기로 보이게 한다.
  const widthTip = document.createElement('div');
  widthTip.className = 'detail-width-tip';
  widthTip.setAttribute('aria-hidden', 'true');
  document.body.appendChild(widthTip);

  function showWidthTip(width, e) {
    const pct = Math.round((width / baseWidth) * 100);
    widthTip.textContent = `${pct}%`;
    widthTip.style.display = 'block';
    // 잡은 모서리 바깥쪽, 포인터 위에 띄운다: 왼쪽 모서리면 포인터의 왼쪽 위, 오른쪽 모서리면 포인터의 오른쪽 위.
    // 화면 밖으로 나가지 않게 한다
    const w = widthTip.offsetWidth, h = widthTip.offsetHeight;
    const wantLeft = dragSide === 'left' ? e.clientX - 14 - w : e.clientX + 14;
    const left = Math.min(window.innerWidth - w - 4, Math.max(4, wantLeft));
    const top = Math.min(window.innerHeight - h - 4, Math.max(4, e.clientY - h - 12));
    widthTip.style.left = left + 'px';
    widthTip.style.top = top + 'px';
  }

  // 모서리에 마우스를 올리면 "끌어서 너비 조절" 안내를 모서리 바깥쪽에 띄운다(왼쪽 모서리는 모서리 왼쪽, 오른쪽 모서리는 오른쪽).
  // 브라우저 기본 title 풍선은 위치를 정할 수 없어서 같은 모양의 풍선(widthTip)을 쓴다.
  let hintTimer = null;
  let hintShown = false;
  let lastMouse = null;

  function placeHint(side, e) {
    const w = widthTip.offsetWidth, h = widthTip.offsetHeight;
    const r = (side === 'left' ? resizerLeft : resizerRight).getBoundingClientRect();
    const edgeX = r.left + r.width / 2;
    const wantLeft = side === 'left' ? edgeX - 8 - w : edgeX + 8;
    widthTip.style.left = Math.min(window.innerWidth - w - 4, Math.max(4, wantLeft)) + 'px';
    widthTip.style.top = Math.min(window.innerHeight - h - 4, Math.max(4, e.clientY + 18)) + 'px';
  }

  function showHint(side) {
    if (dragging || !lastMouse) return;
    widthTip.textContent = '끌어서 너비 조절';
    widthTip.style.display = 'block';
    hintShown = true;
    placeHint(side, lastMouse);
  }

  function hideHint() {
    clearTimeout(hintTimer);
    if (hintShown && !dragging) widthTip.style.display = 'none';
    hintShown = false;
  }

  function bindHint(el, side) {
    el.addEventListener('mouseenter', (e) => {
      if (dragging) return;
      lastMouse = e;
      clearTimeout(hintTimer);
      hintTimer = setTimeout(() => showHint(side), 250);  // 기본 안내 풍선처럼 잠깐 머문 뒤에 뜬다
    });
    el.addEventListener('mousemove', (e) => {
      lastMouse = e;
      if (hintShown && !dragging) placeHint(side, e);
    });
    el.addEventListener('mouseleave', hideHint);
  }

  function clampWidth(w) {
    const min = 400;
    const max = Math.max(min, detailWrap.getBoundingClientRect().width);
    return Math.min(max, Math.max(min, w));
  }

  function onMouseMove(e) {
    if (!dragging) return;
    moved = true;
    const w = clampWidth(2 * Math.abs(e.clientX - centerX));
    detailBox.style.width = w + 'px';
    positionDetailResizer();
    showWidthTip(w, e);
  }

  function onMouseUp() {
    if (!dragging) return;
    dragging = false;
    widthTip.style.display = 'none';
    if (!moved && saved) {
      // 끌지 않고 놓았으면 누르기 전 상태로 되돌린다(폭이 고정값으로 남지 않게)
      detailBox.style.width = saved.width;
      detailBox.style.maxWidth = saved.maxWidth;
      detailBox.style.marginLeft = saved.marginLeft;
      detailBox.style.marginRight = saved.marginRight;
      positionDetailResizer();
    }
    resizerLeft.classList.remove('dragging');
    resizerRight.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  }

  function startDrag(resizerEl) {
    return (e) => {
      dragging = true;
      moved = false;
      const rect = detailBox.getBoundingClientRect();
      centerX = rect.left + rect.width / 2;
      saved = {
        width: detailBox.style.width,
        maxWidth: detailBox.style.maxWidth,
        marginLeft: detailBox.style.marginLeft,
        marginRight: detailBox.style.marginRight,
      };
      // max-width(900px)를 풀기 전에 지금 폭을 먼저 고정한다. 그러지 않으면 최대 폭에 걸려 있던 설명창이 오른쪽
      // 화면 전체 폭으로 순간 넓어졌다가 첫 마우스 이동에서 되돌아와 화면이 깜빡인다.
      detailBox.style.width = rect.width + 'px';
      detailBox.style.maxWidth = 'none';
      detailBox.style.marginLeft = 'auto';
      detailBox.style.marginRight = 'auto';
      // 100% = 기본 너비: CSS 의 max-width(900px) 와 오른쪽 화면 폭 중 작은 쪽(손으로 조절하기 전 설명창 너비)
      baseWidth = Math.min(900, detailWrap.getBoundingClientRect().width);
      dragSide = resizerEl === resizerLeft ? 'left' : 'right';
      hideHint();
      showWidthTip(rect.width, e);
      resizerEl.classList.add('dragging');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ew-resize';
      e.preventDefault();
    };
  }

  // 양쪽 모서리 더블 클릭: 가로폭이 100%(기본 폭)이면 오른쪽 화면 가득(최대)으로 넓히고, 100%가 아니거나 이미 최대 폭이면
  // 100%로 되돌린다(끌어서 넣어 둔 인라인 너비/여백을 지우면 CSS 기본값 max-width 900px, 가운데 정렬이 다시 적용된다).
  // 바뀐 폭은 "NNN%" 풍선으로 잠깐 보여 준다.
  let resetTipTimer = null;
  function resetWidth(e) {
    clearTimeout(hintTimer);
    hintShown = false;
    const wrapW = detailWrap.getBoundingClientRect().width;
    const curW = detailBox.getBoundingClientRect().width;
    // 지금 폭이 100%(기본 폭)일 때만 최대화하고, 100%가 아니거나 이미 최대 폭이면 100%로 되돌린다.
    const isDefault = Math.abs(curW - Math.min(900, wrapW)) <= 1;
    if (!isDefault) {
      detailBox.style.width = '';
      detailBox.style.maxWidth = '';
      detailBox.style.marginLeft = '';
      detailBox.style.marginRight = '';
    } else {
      detailBox.style.width = wrapW + 'px';
      detailBox.style.maxWidth = 'none';
      detailBox.style.marginLeft = 'auto';
      detailBox.style.marginRight = 'auto';
    }
    positionDetailResizer();
    dragSide = e.currentTarget === resizerLeft ? 'left' : 'right';
    baseWidth = Math.min(900, wrapW);
    showWidthTip(detailBox.getBoundingClientRect().width, e);
    clearTimeout(resetTipTimer);
    resetTipTimer = setTimeout(() => { if (!dragging) widthTip.style.display = 'none'; }, 800);
    e.preventDefault();
  }

  bindHint(resizerLeft, 'left');
  bindHint(resizerRight, 'right');
  resizerLeft.addEventListener('dblclick', resetWidth);
  resizerRight.addEventListener('dblclick', resetWidth);
  resizerLeft.addEventListener('mousedown', startDrag(resizerLeft));
  resizerRight.addEventListener('mousedown', startDrag(resizerRight));
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
  window.addEventListener('resize', positionDetailResizer);
})();

// 성상/확인시험/순도시험/정량법 등 각 섹션의 헤더(라벨 + 네모 안의 +/-
// 표시)를 클릭하면 그 섹션만 접고 펼친다. detailBox.innerHTML이 매번
// 통째로 새로 그려지므로, 개별 헤더마다 리스너를 다시 달지 않고 detailBox
// 하나에 위임해서 처리한다. 단, 순도시험 안의 특정 시험항목만 골라 보여주는
// "purity-toggle" 섹션은 접고 펼치는 대신 "해당 시험항목만" ↔ "순도시험
// 전체"를 번갈아 보여준다(purityToggleSectionHtml 참조).
detailBox.addEventListener('click', (e) => {
  if (e.target.closest('.tm-btn')) return;  // "생약시험법" 버튼은 접기/펴기가 아니다
  const header = e.target.closest('.section > .section-header');
  if (!header) return;
  const section = header.parentElement;
  const icon = header.querySelector('.toggle-icon');
  if (section.classList.contains('purity-toggle')) {
    const showingFull = section.dataset.state === 'full';
    const tpl = section.querySelector(showingFull ? '.narrowed-content-template' : '.full-content-template');
    const content = section.querySelector('.section-content');
    if (tpl && content) {
      content.innerHTML = tpl.innerHTML;
      section.dataset.state = showingFull ? 'narrowed' : 'full';
      if (icon) icon.textContent = showingFull ? '+' : '−';
    }
    return;
  }
  section.classList.toggle('collapsed');
  if (icon) icon.textContent = section.classList.contains('collapsed') ? '+' : '−';
});

// 왼쪽 패널은 화면에 붙어 따라다니는(sticky) 상태라, 내용이 길면 창 아래로 넘쳐
// 보이지 않게 된다. 패널의 현재 위치(top)에서 창 아래끝(24px 여백)까지만 높이를
// 허용하고 넘치는 내용은 패널 안에서 세로 스크롤되게 한다. 페이지를 스크롤하면
// 패널의 top이 바뀌므로(위쪽 24px에서 붙을 때까지) 스크롤/창 크기 변경마다 다시 계산한다.
const leftPanelEl = document.querySelector('.left-panel');
function fitLeftPanelHeight() {
  if (!leftPanelEl) return;
  if (window.matchMedia('(max-width: 860px)').matches || leftPanelEl.classList.contains('collapsed')) {
    leftPanelEl.style.maxHeight = '';
    return;
  }
  const top = Math.max(leftPanelEl.getBoundingClientRect().top, 24);
  leftPanelEl.style.maxHeight = Math.max(200, window.innerHeight - top - 24) + 'px';
  updateLeftPanelScrollGap();
}
// 스크롤바가 생겼을 때만 검색창/카드 오른쪽 끝과 스크롤바 사이에 5px 간격을 둔다
// (스크롤바가 없을 땐 원래 폭 그대로 쓴다).
function updateLeftPanelScrollGap() {
  const scrollable = leftPanelEl.scrollHeight > leftPanelEl.clientHeight + 1;
  leftPanelEl.classList.toggle('has-scroll', scrollable);
  syncLeftWidthVar();
}
// 패널이 스크롤될 때 막대 자리(12px)만큼 상자를 넓히는 app.css 규칙이 JS 가 정한 너비를 알도록 --lp-w 로 옮겨 둔다.
function syncLeftWidthVar() {
  const el = document.querySelector('.left-panel');
  if (el && el.style.width && el.style.getPropertyValue('--lp-w') !== el.style.width) {
    el.style.setProperty('--lp-w', el.style.width);
  }
}
window.addEventListener('resize', fitLeftPanelHeight);
window.addEventListener('scroll', fitLeftPanelHeight, { passive: true });
if (leftPanelEl && window.ResizeObserver) {
  const ro = new ResizeObserver(updateLeftPanelScrollGap);
  leftPanelEl.querySelectorAll('.tab-panel, .tab-bar').forEach(el => ro.observe(el));
}
fitLeftPanelHeight();

// 시작화면 오른쪽 배경 영역은 창 높이에 맞춰 아래끝까지 채운다(고정
// calc(100vh - Npx) 값은 헤더 줄 수가 바뀌면 실제 높이와 어긋나 아래에
//빈 공간이 남거나 반대로 넘칠 수 있어, 실제 위치를 측정해서 맞춘다).
function fitDetailPlaceholderHeight() {
  if (detailPlaceholder.classList.contains('hide')) return;
  const top = detailPlaceholder.getBoundingClientRect().top;
  const bottomGap = 24;
  const height = window.innerHeight - top - bottomGap;
  detailPlaceholder.style.height = Math.max(200, height) + 'px';
}
window.addEventListener('resize', fitDetailPlaceholderHeight);
fitDetailPlaceholderHeight();

let debounceTimer = null;
let officialListCache = null;
// 전체 목록 fetch(599건, 느림)와 타이핑에 의한 검색 fetch(적음, 빠름)가
// 겹치면 늦게 끝난 쪽이 화면을 덮어써 버릴 수 있다. 매 요청마다 번호를
// 매겨, 그 사이 더 최신 요청이 시작됐으면 응답이 와도 그리지 않도록 막는다.
let requestSeq = 0;

async function getOfficialList() {
  if (!officialListCache) {
    const res = await fetch('/api/list');
    officialListCache = await res.json();
  }
  return officialListCache;
}

// 콤보박스 전체 목록에는 국문명만 보여준다(이명, 영문명, 라틴 학명은 뺀다).
// korean_name에는 "갈화(葛花) 갈조화(葛條花), Pueraria Flower"처럼 이명이
// 이미 이어붙어 있는 경우가 있어, 이명이 빠진 name_primary를 우선 쓴다.
function comboRowHtml(it) {
  const primary = it.name_primary || it.korean_name;
  return `
    <div class="result-item" data-id="${it.id}">
      <span class="rname">${escapeHtml(primary)}${sourceBadge(it.source_tag, it.part)}${flagBadge(it.flag)}</span>
    </div>
  `;
}

// 화살표로 여는 전체 목록 팝업(comboDropdown)은 검색 결과(#results)와
// 완전히 분리된 레이어다. 그래서 팝업을 열고 닫아도 이미 표시된 검색
// 결과 카드는 절대 사라지지 않는다.
// 전체 목록이 펼쳐진 상태에서 검색칸에 검색어를 치면 목록이 닫히고, 검색 결과 카드가 보인다(생약제제 검색칸도 같다).
function dropdownListHtml(title, items) {
  return `<div class="group-label">${title} 전체 목록 (${items.length})</div>${items.map(comboRowHtml).join('')}`;
}

let comboItemsAll = null;  // 전체 목록

async function showAllOfficial() {
  const seq = ++requestSeq;
  const items = await getOfficialList();
  if (seq !== requestSeq) return;
  comboItemsAll = items;
  comboDropdown.innerHTML = dropdownListHtml('공정서', items);
  comboDropdown.scrollTop = 0;
}

// 목록 항목을 누르면 그 생약을 고른다
comboDropdown.addEventListener('click', (ev) => {
  const row = ev.target.closest('.result-item');
  if (!row || !comboItemsAll) return;
  const it = comboItemsAll.find(x => String(x.id) === row.dataset.id);
  if (it) selectComboItem(it);
});

function openComboDropdown() {
  closeOriginSuggest();
  showAllOfficial();
  comboDropdown.style.display = 'block';
  syncDropdownOpenClass();
  fitComboDropdownHeight();
}

function closeComboDropdown() {
  comboDropdown.style.display = 'none';
  syncDropdownOpenClass();
}

// 왼쪽 패널은 세로 스크롤(overflow:auto)이라, 검색창 아래로 펼쳐지는 목록 팝업(절대
// 위치)이 패널 자기 높이보다 크면 패널 안에서 잘려 안 보인다(초기 화면처럼 패널이
// 짧을 때). 팝업이 열려 있는 동안만 패널의 overflow를 풀어 팝업이 패널 밖으로
// 그대로 펼쳐지게 한다.
function syncDropdownOpenClass() {
  const open = comboDropdown.style.display === 'block' || testItemDropdown.style.display === 'block'
    || prepDropdown.style.display === 'block' || originDropdown.style.display === 'block';
  document.querySelector('.left-panel').classList.toggle('dropdown-open', open);
}

// 배경 그림(detailPlaceholder)의 아래끝은 항상 "창 아래끝 - 24px"에
// 온다(fitDetailPlaceholderHeight 참조: 높이 = innerHeight - top - 24 이므로
// top이 얼마든 아래끝은 innerHeight - 24로 고정된다). 팝업도 같은 24px
// 간격을 그대로 써서 같은 계산식으로 아래끝을 맞추면, DOM 위치를 직접
// 읽지 않고도 창 크기가 바뀔 때마다 배경 그림과 같은 높이로 따라온다.
function fitComboDropdownHeight() {
  if (comboDropdown.style.display !== 'block') return;
  const top = comboDropdown.getBoundingClientRect().top;
  const bottomGap = 24;
  const height = window.innerHeight - top - bottomGap;
  comboDropdown.style.maxHeight = Math.max(160, height) + 'px';
}

window.addEventListener('resize', fitComboDropdownHeight);

// 콤보박스에서 품목을 고르면: 드롭다운 목록을 닫고, 검색칸에 한글
// 생약명만(한자 없이) 채운 다음, 그 이름으로 실제 검색을 실행해
// 공정서/관능검사해설서 카드에 결과를 보여주고, 고른 품목의 상세를
// 오른쪽 화면에 표시한다(동명이품목이 있어도 고른 id를 그대로 보여주기
// 위해 검색 결과와 무관하게 마지막에 다시 확정한다).
async function selectComboItem(it) {
  closeComboDropdown();
  const nameOnly = it.name_only || it.name_primary || it.korean_name;
  searchInput.value = nameOnly;
  await doSearch(nameOnly);
  searchDone = true;
  showDetail(it.id);
}

comboToggle.addEventListener('click', () => {
  if (comboDropdown.style.display === 'block') {
    closeComboDropdown();
  } else {
    openComboDropdown();
  }
});

// ---- 탭 전환 ----------------------------------------------------------
const tabButtons = document.querySelectorAll('.tab-btn');
function activateTab(tabId) {
  tabButtons.forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === tabId));
  closeComboDropdown();
  closeOriginSuggest();
  closeTestItemDropdown();
  closePrepDropdown();
  if (tabId === 'testTab') fitTestResultsHeight();
  if (tabId === 'prepTab') fitPrepResultsHeight();
}
// 그 탭의 검색칸에 커서를 놓아 바로 입력할 수 있게 한다: 탭을 누를 때, 처음 열 때, 뒤로/앞으로 가기로 상태가 복원될 때.
// 시험항목 검색 칸은 직접 치는 칸이 아니라(읽기 전용, 목록에서 고름) 제외한다. preventScroll: 포커스가 화면을 움직여서
// 방금 열린 상세 화면의 스크롤 위치를 흐트러뜨리지 않게 한다.
function focusTabInput(tabId) {
  const box = { nameTab: searchInput, prepTab: prepInput }[tabId];
  if (box) box.focus({ preventScroll: true });
}
tabButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    activateTab(btn.dataset.tab);
    navSet({ tab: btn.dataset.tab });
    focusTabInput(btn.dataset.tab);
  });
});

// ---- 브라우저 뒤로/앞으로(Alt+←/→ 포함) ------------------------------------
// 화면은 한 페이지(SPA)라 그대로 두면 뒤로가기가 사이트를 벗어나 버린다. 탭, 검색어,
// 시험항목, 오른쪽에 보이는 내용(상세/PDF)을 navState 하나로 모아 두고, 사용자가 고른
// 동작(탭 전환, 시험항목 선택, 상세 열기)마다 history에 쌓는다. 검색어는 글자를 칠
// 때마다 바뀌므로 쌓지 않고 현재 항목만 갱신(replace)한다. 뒤로/앞으로를 누르면
// popstate에서 그 시점의 상태를 화면에 다시 그린다(복원 중에는 새로 쌓지 않는다).
let navState = { tab: 'nameTab', q: '', testIdx: -1, view: null };
let navLast = JSON.stringify(navState);
let navRestoring = false;
history.replaceState(JSON.parse(navLast), '');

function navSet(patch, mode = 'push') {
  Object.assign(navState, patch);
  if (navRestoring) return;
  const s = JSON.stringify(navState);
  if (s === navLast) return;
  navLast = s;
  if (mode === 'replace') history.replaceState(JSON.parse(s), '');
  else history.pushState(JSON.parse(s), '');
}

function hideDetail() {
  detailBox.classList.remove('show', 'sensory-mode');
  detailBox.innerHTML = '';
  detailPlaceholder.classList.remove('hide');
  fitDetailPlaceholderHeight();
  setShownKey('');
  positionDetailResizer();
}

async function applyNav(st) {
  navRestoring = true;
  try {
    activateTab(st.tab);
    if (searchInput.value.trim() !== st.q) {
      searchInput.value = st.q;
      if (st.q) {
        await doSearch(st.q);
      } else {
        resultsBox.style.display = 'none';
        resultsBox.innerHTML = '';
      }
    }
    if (st.testIdx !== navState.testIdx) {
      if (st.testIdx >= 0) {
        await selectTestItem(TEST_ITEMS[st.testIdx]);
      } else {
        testItemInput.value = '';
        testResultsBox.style.display = 'none';
        testResultsBox.innerHTML = '';
      }
    }
    if (!st.view) {
      hideDetail();
    } else if (st.view.type === 'item') {
      await showDetail(
        st.view.id,
        st.view.filterIdx >= 0 ? TEST_ITEMS[st.view.filterIdx] : undefined,
        st.view.backId ? { backId: st.view.backId, backName: st.view.backName } : undefined,
      );
    } else if (st.view.type === 'pdf') {
      setShownKey(st.view.key || '');
      showSensoryDetail(st.view.item);
    }
  } finally {
    navState = JSON.parse(JSON.stringify(st));
    navLast = JSON.stringify(navState);
    navRestoring = false;
  }
  focusTabInput(st.tab);  // 복원이 끝난 뒤(상세 화면 등을 다시 그린 다음) 현재 탭의 검색칸에 커서를 놓는다
}

window.addEventListener('popstate', (ev) => {
  if (ev.state && ev.state.tab) applyNav(ev.state);
});

focusTabInput(navState.tab);  // 처음 열 때도 현재 탭(기본: 생약 검색)의 검색칸에 커서를 놓는다

// ---- 시험항목 검색 탭 ---------------------------------------------------
// 확인시험과 순도시험의 하위 항목들을 드롭다운에서 골라, 그 항목이 있는
// 공정서 품목을 /api/test_item_search 로 찾아 공정서 카드에 표시한다.
// label은 목록에 보이는 이름, keywords는 실제 검색에 쓰는 낱말(들)이다
// (예: 목록엔 "이물시험"으로 보이지만, 원문 순도시험 항목에는 "이물" 외에도
// "줄기"/"꽃대"처럼 이물질의 구체적인 종류로 적혀 있는 경우가 있어, 그런
// 낱말들도 함께 검색어로 쓴다 - 이 중 하나라도 있으면 그 시험항목으로 본다).
const TEST_ITEMS = [
  { label: '확인시험', keywords: ['확인시험'] },
  { label: '정량법', keywords: ['정량법'] },
  { label: '이물시험', keywords: ['이물', '줄기', '꽃대', '꽃받침', '가지', '열매꼭지', '포엽', '엽초', '아코니툼속 식물', '유삼', '유사동물'] },
  { label: '변패, 산패', keywords: ['변패', '산패'] },
  { label: '잔류농약', keywords: ['잔류농약'] },
  { label: '납', keywords: ['납'] },
  { label: '비소', keywords: ['비소'] },
  { label: '수은', keywords: ['수은'] },
  { label: '카드뮴', keywords: ['카드뮴'] },
  { label: '이산화황', keywords: ['이산화황'] },
  { label: '벤조피렌', keywords: ['벤조피렌'] },
  { label: '곰팡이독소', keywords: ['곰팡이독소'] },
  { label: '유전자검사', keywords: ['유전자'] },
  { label: '정유함량', keywords: ['정유함량'] },
  { label: '엑스함량', keywords: ['엑스함량'] },
];

const testItemInput = document.getElementById('testItemInput');
const testItemToggle = document.getElementById('testItemToggle');
const testItemDropdown = document.getElementById('testItemDropdown');
const testResultsBox = document.getElementById('testResults');

// 시험항목 검색결과 카드에는 이명/영문명/학명 등은 빼고 국문명만 보여준다.
function testResultRowHtml(it) {
  const primary = it.name_primary || it.korean_name;
  return `
    <div class="result-item" data-id="${it.id}">
      <span class="rname">${escapeHtml(primary)}${sourceBadge(it.source_tag, it.part)}${flagBadge(it.flag)}</span>
    </div>
  `;
}

function openTestItemDropdown() {
  testItemDropdown.innerHTML = TEST_ITEMS.map((t, i) => `
    <div class="result-item" data-idx="${i}">
      <span class="rname">${escapeHtml(t.label)}</span>
    </div>
  `).join('');
  testItemDropdown.querySelectorAll('.result-item').forEach(el => {
    el.addEventListener('click', () => selectTestItem(TEST_ITEMS[Number(el.dataset.idx)]));
  });
  testItemDropdown.style.display = 'block';
  syncDropdownOpenClass();
  fitTestItemDropdownHeight();
}

function closeTestItemDropdown() {
  testItemDropdown.style.display = 'none';
  syncDropdownOpenClass();
}

// 시험항목 목록(13개)은 css의 기본 max-height(60vh)만 쓰면 창이 넓어도
// 그 비율에 갇혀 불필요하게 스크롤이 생길 수 있다. 생약 검색 탭의
// comboDropdown과 같은 "창 아래끝 - 24px" 식으로 실제 남은 공간을 다시
// 계산해 넣어, 공간이 넉넉하면(13개 목록 전체 높이보다 크면) 스크롤 없이
// 전체 목록이 그대로 다 보이게 한다.
function fitTestItemDropdownHeight() {
  if (testItemDropdown.style.display !== 'block') return;
  const top = testItemDropdown.getBoundingClientRect().top;
  const bottomGap = 24;
  const height = window.innerHeight - top - bottomGap;
  testItemDropdown.style.maxHeight = Math.max(160, height) + 'px';
}
window.addEventListener('resize', fitTestItemDropdownHeight);

async function selectTestItem(testItem) {
  closeTestItemDropdown();
  testItemInput.value = testItem.label;
  navSet({ testIdx: TEST_ITEMS.indexOf(testItem) });
  const res = await fetch('/api/test_item_search?item=' + encodeURIComponent(testItem.keywords.join(',')));
  const items = await res.json();
  testResultsBox.style.display = 'flex';
  // 확인시험/정량법은 생약제제에 적용하는 시험법(7. 생규집(확인, 함량))의 이름도 한 목록에 합쳐서 온다
  const merged = testItem.keywords.length === 1 && (testItem.keywords[0] === '확인시험' || testItem.keywords[0] === '정량법');
  const rows = items.length
    ? items.map(testResultRowHtml).join('')
    : `<div class="empty-msg">"${escapeHtml(testItem.label)}" 항목이 있는 품목이 없습니다.</div>`;
  testResultsBox.innerHTML = `
    <div class="result-group">
      <div class="group-label">${merged ? '공정서·생약제제' : '공정서'} (${items.length})</div>
      ${rows}
    </div>
  `;
  testResultsBox.querySelectorAll('.result-item').forEach(el => {
    el.addEventListener('click', () => showDetail(el.dataset.id, testItem));
  });
  applyShownHighlight();
  fitTestResultsHeight();
}

// ---- 생약제제 검색 탭 ----------------------------------------------------
// 생약 검색 탭과 같은 방식이다: 이름을 치면(200ms 뒤) 검색해서 아래에 목록을 보여 주고, 목록의 항목을 누르면 오른쪽에
// 상세를 보여 준다. ▾ 를 누르면 전체 목록(생약제제 hwpx 3개 파일의 품목)이 펼쳐진다.
let prepListCache = null;
let prepSeq = 0;
let prepTimer = null;
let prepDone = false;
let prepSelectAllOnMouseUp = false;

async function getPrepList() {
  if (!prepListCache) {
    const res = await fetch('/api/prep_list');
    prepListCache = await res.json();
  }
  return prepListCache;
}

let prepItemsAll = null;  // 생약제제 전체 목록
let prepOpenToken = 0;    // 목록을 받아 오는 동안 닫기/검색어 입력이 있으면 그 열기를 취소하려고

function closePrepDropdown() {
  prepOpenToken += 1;
  prepDropdown.style.display = 'none';
  syncDropdownOpenClass();
}

function fitPrepDropdownHeight() {
  if (prepDropdown.style.display !== 'block') return;
  const top = prepDropdown.getBoundingClientRect().top;
  prepDropdown.style.maxHeight = Math.max(160, window.innerHeight - top - 24) + 'px';
}
window.addEventListener('resize', fitPrepDropdownHeight);

// 검색 결과 카드는 창 아래끝 - 24px 까지 늘어나고 그 안에서 스크롤된다(시험항목 검색 탭과 같다).
function fitPrepResultsHeight() {
  const groups = [...prepResultsBox.querySelectorAll('.result-group')];
  if (!groups.length) return;
  const top = groups[0].getBoundingClientRect().top;
  // 카드가 둘이면(이름으로 찾은 것 + 제법에 든 것) 남은 높이를 나눠 쓰고, 카드 사이 간격(48px)을 뺀다
  const avail = window.innerHeight - top - 24 - 48 * (groups.length - 1);
  groups.forEach(g => { g.style.maxHeight = Math.max(160, avail / groups.length) + 'px'; });
}
window.addEventListener('resize', fitPrepResultsHeight);

async function openPrepDropdown() {
  const token = ++prepOpenToken;
  const items = await getPrepList();
  if (token !== prepOpenToken) return;  // 기다리는 사이에 닫았거나 검색어를 쳤다
  prepItemsAll = items;
  prepDropdown.innerHTML = dropdownListHtml('생약제제', items);
  prepDropdown.scrollTop = 0;
  prepDropdown.style.display = 'block';
  syncDropdownOpenClass();
  fitPrepDropdownHeight();
}

prepDropdown.addEventListener('click', (ev) => {
  const row = ev.target.closest('.result-item');
  if (!row || !prepItemsAll) return;
  const it = prepItemsAll.find(x => String(x.id) === row.dataset.id);
  if (it) selectPrepItem(it);
});

// data: { items: 이름으로 찾은 생약제제, recipe: 검색어(생약명)가 제법에 들어 있는 생약제제 }
function renderPrepResults(data, q) {
  prepResultsBox.style.display = 'flex';
  const items = data.items || [], recipe = data.recipe || [];
  // 이름으로 찾은 것이 없어도 제법에 들어 있는 생약제제가 있으면 그 카드만 보여 준다
  const nameCard = (items.length || !recipe.length) ? `
    <div class="result-group">
      <div class="group-label">생약제제 (${items.length})</div>
      ${items.length ? items.map(resultRowHtml).join('') : `<div class="empty-msg">"${escapeHtml(q)}"에 대한 검색 결과가 없습니다.</div>`}
    </div>` : '';
  const recipeCard = recipe.length ? `
    <div class="result-group recipe-group">
      <div class="group-label">제법에 "${escapeHtml(q)}" 포함 (${recipe.length})</div>
      ${recipe.map(resultRowHtml).join('')}
    </div>` : '';
  prepResultsBox.innerHTML = nameCard + recipeCard;
  prepResultsBox.querySelectorAll('.result-item').forEach(el => {
    el.addEventListener('click', () => showDetail(el.dataset.id));
  });
  applyShownHighlight();
  fitPrepResultsHeight();
}

async function doPrepSearch(q) {
  const seq = ++prepSeq;
  const res = await fetch('/api/prep_search?q=' + encodeURIComponent(q));
  const data = await res.json();
  if (seq === prepSeq) renderPrepResults(data, q);
  return data;
}

async function selectPrepItem(it) {
  closePrepDropdown();
  const name = it.name_primary || it.korean_name;
  prepInput.value = name;
  await doPrepSearch(name);
  prepDone = true;
  showDetail(it.id);
}

prepToggle.addEventListener('click', () => {
  if (prepDropdown.style.display === 'block') closePrepDropdown();
  else openPrepDropdown();
});

prepInput.addEventListener('input', () => {
  prepDone = false;
  clearTimeout(prepTimer);
  closePrepDropdown();  // 검색어를 치면 펼쳐 둔 전체 목록은 닫고 검색 결과를 보여 준다(목록을 받아 오는 중이었다면 그 열기도 취소)
  const q = prepInput.value.trim();
  if (!q) {
    prepResultsBox.style.display = 'none';
    prepResultsBox.innerHTML = '';
    return;
  }
  prepTimer = setTimeout(() => doPrepSearch(q), 200);
});

prepInput.addEventListener('keydown', async (ev) => {
  if (ev.key !== 'Enter') return;
  ev.preventDefault();
  clearTimeout(prepTimer);
  const q = prepInput.value.trim();
  if (!q) return;
  closePrepDropdown();  // 펼쳐 둔 전체 목록은 닫고 검색 결과를 보여 준다
  dismissKeyboard(prepInput);
  const data = await doPrepSearch(q);
  prepDone = true;
  if (data.items.length) showDetail(data.items[0].id);
});

// 생약 검색칸과 같다: 검색을 마친 뒤(또는 다른 곳에 있다가 들어올 때) 누르면 검색어 전체를 선택한다.
prepInput.addEventListener('mousedown', () => {
  prepSelectAllOnMouseUp = !!prepInput.value && (prepDone || document.activeElement !== prepInput);
});
prepInput.addEventListener('mouseup', (ev) => {
  if (!prepSelectAllOnMouseUp) return;
  prepSelectAllOnMouseUp = false;
  ev.preventDefault();
  prepInput.select();
});

// "text" 하나(예: "납  5 ppm 이하.")가 keywords 중 하나라도 담고 있는지
// 정규화해서 검사한다. 계층형 항목의 "자기 자신" 텍스트만 보고(자식 항목
// 내용은 제외) 판단해야, "1) 중금속"처럼 여러 하위 항목을 감싸는 상위
// 노드가 아니라 실제로 일치하는 "가) 납" 같은 가장 구체적인 노드를
// 찾아낼 수 있다.
function _normJs(s) {
  return (s || '').replace(/\s+/g, '').toLowerCase();
}

function _nodeOwnText(it) {
  if (it.bold !== undefined) return (it.bold || '') + (it.rest || '');
  return it.text || '';
}

// "가지"는 "가지고"(조사가 아니라 "가지다"의 활용형, 예: "~을 가지고
// ~")에 흔히 우연히 포함되므로, 그 경우만 제외하고 찾는다.
function _textHasKeyword(normText, rawKeyword) {
  const kw = _normJs(rawKeyword);
  if (kw === '가지') {
    return /가지(?!고)/.test(normText);
  }
  return normText.includes(kw);
}

function _nodeMatchesAny(it, keywords) {
  const t = _normJs(_nodeOwnText(it));
  return keywords.some(k => _textHasKeyword(t, k));
}

// 계층형 항목 목록에서 keywords(이물시험처럼 "이물"/"줄기"/"꽃대" 등 여러
// 낱말 중 하나라도 해당하면 되는 경우가 있어 배열로 받는다) 중 하나와
// 일치하는 노드를 찾되, 그 노드가 속한 바로 위 계층까지 함께 보여준다.
// 어떤 노드 자신이 일치하면 그 노드(와 하위 항목 전체)를 결과에 넣고 더
// 깊이 들어가지 않는다("4) 벤조피렌"처럼 그 자체가 일치하면 (제1법)/(제2법)
// 등 하위 구조를 그대로 통째로 보여준다). 자신은 일치하지 않지만 바로 아래
// 자식들 중 일부가 일치하면("1) 중금속"의 자식인 "가) 납"처럼), 부모
// 노드("중금속")는 계층(라벨)만 그대로 두고 그 자식은 실제로 일치하는
// 것만 남겨서("나) 비소"/"다) 수은"/"라) 카드뮴"은 빼고) 순도시험 > 중금속
// > 납 처럼 딱 그 항목까지만 계층적으로 보이게 한다. 그 조건에도 안 맞으면
// 더 깊은 하위 항목에서 계속 찾는다.
function findMatchingNodes(items, keywords) {
  let out = [];
  for (const it of (items || [])) {
    const children = it.children || [];
    if (_nodeMatchesAny(it, keywords)) {
      out.push(it);
      continue;
    }
    const matchedChildren = children.filter(c => _nodeMatchesAny(c, keywords));
    if (matchedChildren.length) {
      out.push(Object.assign({}, it, { children: matchedChildren }));
    } else if (children.length) {
      out = out.concat(findMatchingNodes(children, keywords));
    }
  }
  return out;
}

// 시험항목 검색 결과 카드는 생약 검색 탭의 comboDropdown과 같은 방식
// (창 아래끝 - 24px)으로 창 크기에 맞춰 세로로 늘어나고, 그 안에서 카드
// 자체가 스크롤되게 한다.
function fitTestResultsHeight() {
  const groups = [...testResultsBox.querySelectorAll('.result-group')];
  if (!groups.length) return;
  // 확인시험/정량법은 "공정서"와 "생약제제에 적용" 두 카드가 위아래로 놓이므로 남은 높이를 나눠 쓴다(카드 사이 간격 48px).
  const top = groups[0].getBoundingClientRect().top;
  const bottomGap = 24;
  const rowGap = 48;
  const height = (window.innerHeight - top - bottomGap - rowGap * (groups.length - 1)) / groups.length;
  groups.forEach(g => { g.style.maxHeight = Math.max(160, height) + 'px'; });
}

window.addEventListener('resize', fitTestResultsHeight);

testItemToggle.addEventListener('click', () => {
  if (testItemDropdown.style.display === 'block') {
    closeTestItemDropdown();
  } else {
    openTestItemDropdown();
  }
});

testItemInput.addEventListener('click', () => {
  if (testItemDropdown.style.display !== 'block') openTestItemDropdown();
});

document.addEventListener('click', (ev) => {
  if (!ev.target.closest('#nameTab .search-box')) { closeComboDropdown(); closeOriginSuggest(); }
  if (!ev.target.closest('#testTab .search-box')) closeTestItemDropdown();
  if (!ev.target.closest('#prepTab .search-box')) closePrepDropdown();
});

// ---- 목록(드롭다운) 방향키 이동 -------------------------------------------
// 목록이 열려 있을 때 위/아래 방향키로 마우스를 올렸을 때와 같은 옅은 색 반전(kb-active)을 옮기고, Enter 를 누르면
// 반전된 항목을 눌렀을 때와 똑같이 선택(검색)한다. 마우스를 움직여 다른 항목에 올리면 반전도 그 항목으로 옮겨 간다.
// 마지막으로 본 마우스 좌표(문서 전체). 드롭다운의 mousemove 처리가 먼저 돌고 그 다음에 갱신되므로 "직전 좌표"가 된다.
const pointerPos = { x: -1, y: -1 };
document.addEventListener('mousemove', (ev) => { pointerPos.x = ev.clientX; pointerPos.y = ev.clientY; });

// 터치 화면(모바일)에서 검색칸에서 Enter 로 검색하면 화면 키보드가 내려가도록 입력칸의 포커스를 푼다.
function dismissKeyboard(input) {
  if (window.matchMedia('(pointer: coarse)').matches) input.blur();
}

// 항목을 고르면 목록을 열었던 ▾ 버튼이 선택(포커스) 표시로 남지 않게 한다.
function releaseToggleFocus() {
  const el = document.activeElement;
  if (el && el.classList && el.classList.contains('combo-toggle')) el.blur();
}

// PageDown/PageUp: 열린 목록을 한 화면만큼 넘긴다. 반전된 항목이 있으면 화면 안의 같은 높이 항목으로, 없으면 맨 위(아래) 항목으로 옮긴다.
function pageScrollDropdown(dropdown, list, cur, dir, setActive) {
  const box = dropdown.getBoundingClientRect();
  const step = Math.max(dropdown.clientHeight - 40, 40);
  const offset = cur >= 0 ? list[cur].getBoundingClientRect().top - box.top : 0;
  const before = dropdown.scrollTop;
  dropdown.scrollTop = before + dir * step;
  const moved = dropdown.scrollTop !== before;
  let idx;
  if (!moved) {
    idx = dir > 0 ? list.length - 1 : 0;  // 이미 끝이면 끝 항목으로
  } else {
    const target = box.top + (cur >= 0 ? offset : (dir > 0 ? 0 : dropdown.clientHeight - 30));
    idx = list.findIndex(el => el.getBoundingClientRect().bottom > target + 1);
    if (idx < 0) idx = list.length - 1;
    if (dir < 0 && cur < 0) idx = Math.max(list.findIndex(el => el.getBoundingClientRect().top >= box.top), 0);
  }
  setActive(list[idx]);
}

function setupDropdownKeys(dropdown, input, openFn, closeFn) {
  const rows = () => [...dropdown.querySelectorAll('.result-item')];
  const clearActive = () => dropdown.querySelectorAll('.result-item.kb-active').forEach(el => el.classList.remove('kb-active'));
  const setActive = (el) => {
    clearActive();
    el.classList.add('kb-active');
    el.scrollIntoView({ block: 'nearest' });
  };
  // 목록을 ▾ 버튼으로 열면 포커스가 버튼에 있으므로, 입력칸이 아니라 문서 전체에서 키를 받는다(열려 있는 목록에만 적용).
  document.addEventListener('keydown', (ev) => {
    if (dropdown.style.display !== 'block') {
      // 입력칸에 커서가 있을 때 아래 방향키를 누르면 목록을 연다
      if (ev.key === 'ArrowDown' && document.activeElement === input && originDropdown.style.display !== 'block') {
        ev.preventDefault();
        openFn();
      }
      return;
    }
    if (ev.key === 'Escape') {  // 목록이 열려 있을 때 Esc 로 닫는다
      ev.preventDefault();
      closeFn();
      return;
    }
    const list = rows();
    if (!list.length) return;
    const cur = list.findIndex(el => el.classList.contains('kb-active'));
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const next = ev.key === 'ArrowDown' ? Math.min(cur + 1, list.length - 1) : Math.max(cur - 1, 0);
      setActive(list[cur < 0 && ev.key === 'ArrowUp' ? 0 : next]);
    } else if (ev.key === 'PageDown' || ev.key === 'PageUp') {
      // 한 화면(목록 높이)만큼 스크롤하고, 반전 표시도 화면 안의 같은 위치 항목으로 옮긴다
      ev.preventDefault();
      pageScrollDropdown(dropdown, list, cur, ev.key === 'PageDown' ? 1 : -1, setActive);
    } else if (ev.key === 'Enter' && cur >= 0) {
      ev.preventDefault();
      ev.stopImmediatePropagation();  // 입력칸의 기본 Enter 검색보다 먼저 처리한다
      list[cur].click();
      releaseToggleFocus();
    }
  }, true);
  dropdown.addEventListener('click', releaseToggleFocus);  // 마우스로 항목을 골랐을 때도 ▾ 버튼의 포커스 표시를 푼다
  dropdown.addEventListener('mousemove', (ev) => {
    // 목록이 열리거나 방향키로 스크롤될 때 브라우저가 보내는 가짜 mousemove(좌표가 직전과 같음)는 무시한다
    if (ev.clientX === pointerPos.x && ev.clientY === pointerPos.y) return;
    const row = ev.target.closest('.result-item');
    if (row && !row.classList.contains('kb-active')) setActive(row);
  });
}
setupDropdownKeys(comboDropdown, searchInput, openComboDropdown, closeComboDropdown);
setupDropdownKeys(originDropdown, searchInput, () => {}, closeOriginSuggest);  // 기원 추천 목록: 열려 있을 때만 방향키·Enter·Esc 가 듣는다
setupDropdownKeys(testItemDropdown, testItemInput, openTestItemDropdown, closeTestItemDropdown);
setupDropdownKeys(prepDropdown, prepInput, openPrepDropdown, closePrepDropdown);

detailBox.addEventListener('click', (ev) => {
  // '← 돌아가기': 생약제제 제법 표의 생약명 링크로 열린 생약 화면에서, 링크를 누르기 전 화면(그 생약제제)으로 돌아간다.
  // 링크를 누를 때 history 에 쌓였으므로 브라우저 뒤로 가기와 같다(그래서 앞으로 가기도 된다).
  if (ev.target.closest('.detail-back-btn')) {
    ev.preventDefault();
    history.back();
    return;
  }
  const fam = ev.target.closest('.fam-link');
  if (fam) {
    ev.preventDefault();
    openFamily(fam.dataset.fam, fam);
    return;
  }
  const link = ev.target.closest('.ref-link');
  if (!link) return;
  ev.preventDefault();
  if (link.closest('.recipe-table') && detailBox.dataset.itemId) {
    showDetail(link.dataset.id, undefined, { backId: Number(detailBox.dataset.itemId), backName: detailBox.dataset.itemName });
  } else {
    showDetail(link.dataset.id);
  }
});

// 검색칸을 누르면(검색을 마친 뒤이거나, 다른 곳에 있다가 들어올 때) 검색어 전체를 선택한다. 입력 중에 누르면 평소처럼
// 누른 자리에 커서만 놓는다. mouseup 에서 막지 않으면 브라우저가 선택을 풀고 커서를 놓아 버린다.
searchInput.addEventListener('mousedown', () => {
  selectAllOnMouseUp = !!searchInput.value && (searchDone || document.activeElement !== searchInput);
});
searchInput.addEventListener('mouseup', (ev) => {
  if (!selectAllOnMouseUp) return;
  selectAllOnMouseUp = false;
  ev.preventDefault();
  searchInput.select();
});

// 전체 목록이 펼쳐진 상태에서 검색칸을 누르면 목록을 닫는다(생약제제 검색칸도 같다).
// 반대로 목록이 닫혀 있을 때는, 이미 커서가 있는 입력칸을 한 번 더 누르면 목록을 연다(처음 눌러 커서를 놓을 때는 열지 않는다).
let searchWasFocused = false, prepWasFocused = false;
searchInput.addEventListener('mousedown', () => { searchWasFocused = document.activeElement === searchInput; });
prepInput.addEventListener('mousedown', () => { prepWasFocused = document.activeElement === prepInput; });
searchInput.addEventListener('click', () => {
  if (comboDropdown.style.display === 'block') closeComboDropdown();
  else if (searchWasFocused) openComboDropdown();
});
prepInput.addEventListener('click', () => {
  if (prepDropdown.style.display === 'block') closePrepDropdown();
  else if (prepWasFocused) openPrepDropdown();
});

searchInput.addEventListener('input', () => {
  searchDone = false;
  clearTimeout(debounceTimer);
  closeComboDropdown();  // 검색어를 치면 펼쳐 둔 전체 목록은 닫고 검색 결과를 보여 준다
  closeOriginSuggest();
  const q = searchInput.value.trim();
  if (!q) {
    resultsBox.style.display = 'none';
    resultsBox.innerHTML = '';
    navSet({ q: '' }, 'replace');
    return;
  }
  debounceTimer = setTimeout(() => doSearch(q), 200);
  if (q.replace(/\s+/g, '').length >= 2) originTimer = setTimeout(() => suggestOrigin(q), 250);
});

// ---- 기원 추천 목록 ----
// 검색칸에 2글자 이상 치면, 이름으로 검색되는 생약(검색 결과 카드)과 별도로 '기원' 문장(예: "콩과", "귤나무", "뿌리")에
// 그 말이 들어 있는 생약을 서버에서 찾아 검색칸 아래 추천 목록으로 보여 준다. 항목을 고르면 그 생약을 연다.
function closeOriginSuggest() {
  originSeq++;
  clearTimeout(originTimer);
  originItems = [];
  originDropdown.style.display = 'none';
  originDropdown.innerHTML = '';
  syncDropdownOpenClass();
}
function originRowHtml(it, idx) {
  const primary = it.name_primary || it.korean_name;
  const sn = it.snippet;
  const snip = sn ? `<span class="origin-snip">${escapeHtml(sn[0])}<mark>${escapeHtml(sn[1])}</mark>${escapeHtml(sn[2])}</span>` : '';
  return `
    <div class="result-item origin-item" data-idx="${idx}" data-id="${it.id}">
      <span class="rname">${escapeHtml(primary)}${sourceBadge(it.source_tag, it.part)}${flagBadge(it.flag)}</span>
      ${snip}
    </div>
  `;
}
async function suggestOrigin(q) {
  const seq = ++originSeq;
  let data;
  try {
    data = await (await fetch('/api/origin_search?q=' + encodeURIComponent(q))).json();
  } catch (e) { return; }
  // 그새 검색어가 바뀌었거나 다른 목록이 열렸거나 검색을 마쳤으면 버린다
  if (seq !== originSeq || searchInput.value.trim() !== q || searchDone || comboDropdown.style.display === 'block') return;
  if (!data.items.length) { closeOriginSuggest(); return; }
  originItems = data.items;
  const more = data.total > data.items.length ? ` · 상위 ${data.items.length}개` : '';
  originDropdown.innerHTML = `<div class="group-label">기원에 "${escapeHtml(q)}" 포함 (${data.total})${more}</div>`
    + data.items.map(originRowHtml).join('');
  originDropdown.scrollTop = 0;
  originDropdown.style.display = 'block';
  syncDropdownOpenClass();
  originDropdown.style.maxHeight = Math.max(160, window.innerHeight - originDropdown.getBoundingClientRect().top - 24) + 'px';
}
originDropdown.addEventListener('click', (ev) => {
  const row = ev.target.closest('.origin-item');
  if (!row) return;
  const it = originItems[Number(row.dataset.idx)];
  closeOriginSuggest();
  if (it) selectComboItem(it);
});

searchInput.addEventListener('keydown', async (ev) => {
  if (ev.key !== 'Enter') return;
  ev.preventDefault();
  clearTimeout(debounceTimer);
  const q = searchInput.value.trim();
  if (!q) return;
  closeComboDropdown();  // 펼쳐 둔 전체 목록은 닫고 검색 결과를 보여 준다
  closeOriginSuggest();
  dismissKeyboard(searchInput);
  const [data, origin] = await Promise.all([doSearch(q), fetchOriginAll(q)]);
  searchDone = true;
  addOriginGroup(origin, q);
  const first = data.official[0] || data.sensory[0];
  if (first) {
    showDetail(first.id);
  }
});

// Enter 로 검색하면 검색 결과 카드(공정서 다음)에 '기원에 검색어가 들어 있는 생약' 카드를 한 장 더 보여 준다(입력 중
// 추천 목록과 달리 전부). 이름으로 찾은 생약은 위 공정서 카드에 있으므로 이 카드에서는 빠진다.
async function fetchOriginAll(q) {
  if (q.replace(/\s+/g, '').length < 2) return null;
  try {
    return await (await fetch('/api/origin_search?limit=all&q=' + encodeURIComponent(q))).json();
  } catch (e) { return null; }
}
function addOriginGroup(origin, q) {
  if (!origin || !origin.items.length || searchInput.value.trim() !== q || resultsBox.style.display === 'none') return;
  const group = document.createElement('div');
  group.className = 'result-group origin-group';
  group.innerHTML = `<div class="group-label">기원에 "${escapeHtml(q)}" 포함 (${origin.total})</div>`
    + origin.items.map(originRowHtml).join('');
  group.querySelectorAll('.origin-item').forEach(el => el.addEventListener('click', () => showDetail(el.dataset.id)));
  const official = resultsBox.querySelector('.result-group');
  if (official) official.after(group);
  else resultsBox.innerHTML = '', resultsBox.appendChild(group);  // 이름으로는 하나도 안 나온 경우(안내 문구를 대체)
  applyShownHighlight();
}

// 지금 오른쪽 화면에 보이는 항목의 검색 결과 행에 selected 표시를 한다. 공정서/
// 관능검사해설서 행은 data-id, 관능검사 사례집 행은 파일+시작쪽으로 같은 항목인지
// 판단한다. 검색을 다시 해서 결과 카드가 새로 그려져도 같은 항목이 있으면 다시 표시한다.
let shownKey = '';
let prevShownKey = '';  // 바로 앞에 보이던 항목(터치 기기에서 PDF 로 넘어갈 때 화면을 그대로 두려고 되돌리는 데 쓴다)
function applyShownHighlight() {
  [resultsBox, testResultsBox, prepResultsBox].forEach(box => {
    box.querySelectorAll('.result-item').forEach(el => {
      const key = el.classList.contains('case-item')
        ? `case:${el.dataset.file}:${el.dataset.start}`
        : (el.dataset.id !== undefined ? `id:${el.dataset.id}` : '');
      el.classList.toggle('selected', !!shownKey && key === shownKey);
    });
  });
}
function setShownKey(key) {
  prevShownKey = shownKey;
  shownKey = key;
  applyShownHighlight();
}

async function doSearch(q) {
  const seq = ++requestSeq;
  const res = await fetch('/api/search?q=' + encodeURIComponent(q));
  const data = await res.json();
  if (seq === requestSeq) {
    renderResults(data, q);
    navSet({ q }, 'replace');
  }
  return data;
}

// 없어진 즐겨찾기/최근 본 생약 기능이 이 브라우저에 남겨 둔 기록을 지운다.
try { localStorage.removeItem('herbFavorites'); localStorage.removeItem('herbRecent'); } catch (e) { /* 저장소를 못 쓰면 지울 것도 없다 */ }

// 검색 결과 카드에는 이명, 영문명, 라틴 학명은 빼고 국문명만 보여준다.
function resultRowHtml(it) {
  const primary = it.name_primary || it.korean_name;
  return `
    <div class="result-item" data-id="${it.id}">
      <span class="rname">${escapeHtml(primary)}${sourceBadge(it.source_tag, it.part)}${flagBadge(it.flag)}</span>
    </div>
  `;
}

// 관능검사 사례집 카드는 카테고리(1~4)별로 묶여서 온다(/api/search의
// case 필드). 카테고리 라벨 아래에 그 카테고리에서 검색된 생약명을
// 나열하고, 클릭하면 showSensoryDetail을 그대로 재사용해 PDF 해당
// 페이지 구간을 오른쪽 화면에 보여준다(공정서/관능검사해설서의 일반
// result-item과 섞이지 않도록 case-item 클래스로 구분한다).
function caseGroupHtml(group) {
  const rows = group.items.map(it => `
    <div class="result-item case-item" data-file="${escapeHtml(it.source_file)}" data-start="${it.page_start}" data-end="${it.page_end}" data-name="${escapeHtml(it.herb_name)}">
      <span class="rname">${escapeHtml(it.herb_name)}</span>
    </div>
  `).join('');
  return `
    <div class="case-category-label">${escapeHtml(group.label)}</div>
    ${rows}
  `;
}

function selectCaseItem(data) {
  setShownKey(`case:${data.file}:${data.start}`);
  showSensoryDetail({
    source_file: data.file,
    page_start: Number(data.start),
    page_end: Number(data.end),
    korean_name: `${data.name} - 관능검사 사례집`,
  });
}

function renderResults(data, q) {
  resultsBox.style.display = 'flex';
  const official = data.official || [];
  const sensory = data.sensory || [];
  const caseGroups = data.case || [];
  if (official.length === 0 && sensory.length === 0 && caseGroups.length === 0) {
    resultsBox.innerHTML = `<div class="empty-msg">"${escapeHtml(q)}"에 대한 검색 결과가 없습니다.</div>`;
    return;
  }
  const officialRows = official.length
    ? official.map(resultRowHtml).join('')
    : `<div class="empty-msg">검색 결과가 없습니다.</div>`;
  const sensoryRows = sensory.length
    ? sensory.map(resultRowHtml).join('')
    : `<div class="empty-msg left">검색 결과가 없습니다.</div>`;
  const caseRows = caseGroups.length
    ? caseGroups.map(caseGroupHtml).join('')
    : `<div class="empty-msg left">검색 결과가 없습니다.</div>`;
  resultsBox.innerHTML = `
    <div class="result-group">
      <div class="group-label">공정서</div>
      ${officialRows}
    </div>
    <div class="result-group">
      <div class="group-label">관능검사 해설서</div>
      ${sensoryRows}
    </div>
    <div class="result-group">
      <div class="group-label">관능검사 사례집</div>
      ${caseRows}
    </div>
  `;
  resultsBox.querySelectorAll('.result-item:not(.case-item)').forEach(el => {
    el.addEventListener('click', () => showDetail(el.dataset.id));
  });
  resultsBox.querySelectorAll('.case-item').forEach(el => {
    el.addEventListener('click', () => selectCaseItem(el.dataset));
  });

  if (official.length === 1 && sensory.length === 0 && !navRestoring) {
    showDetail(official[0].id, undefined, { replace: true });
  }
  applyShownHighlight();
}

// 섹션 하나를 label(초록 배지) + 그 오른쪽에 분리된 네모 안의 +/- 표시로
// 렌더링한다. expanded가 false면 처음부터 접힌 상태(collapsed)로 그려지고,
// 헤더를 클릭하면 펼치고 접을 수 있다(클릭 처리는 detailBox의 위임 리스너
// 참조).
function sectionHtml(label, bodyHtml, expanded, note = '') {
  return `
    <div class="section${expanded ? '' : ' collapsed'}">
      <div class="section-header">
        <div class="label">${escapeHtml(label)}</div>
        <span class="toggle-icon">${expanded ? '−' : '+'}</span>
        ${note ? `<span class="section-note">${escapeHtml(note)}</span>` : ''}
        ${tmButton(TM_SECTION_KEYS[label])}
      </div>
      <div class="section-content">${bodyHtml}</div>
    </div>
  `;
}

// 섹션 하나를 항상 통째로(하위 항목 전부) 보여줄 때 쓰는 기본 렌더링.
function fullSectionHtml(sec, expanded = true) {
  const body = sec.items && sec.items.length ? renderItemListIn(sec.label, sec.items) : `<div class="body">${renderSectionBody(sec)}</div>`;
  return sectionHtml(sec.label, body, expanded, sec.note || '');
}

// ---- 생약시험법(3. 생약시험법.hwpx) 연결 ----
// 순도시험의 이물(마우스를 올리면 팝업), 중금속/잔류농약/이산화황(옆의 "생약시험법"
// 버튼), 곰팡이독소(단어 링크)와 건조감량/회분/산불용성회분(제목 옆 버튼)이
// 생약시험법 문서의 해당 항목을 보여 준다. 서버에서 해당 항목을 뽑아 두지 못한
// 경우(문서가 없을 때 등)에는 버튼/링크를 만들지 않는다.
const TM_SECTION_KEYS = {
  '건조감량': 'loss', '회분': 'ash', '산불용성회분': 'acid_ash', '엑스함량': 'extract', '정유함량': 'oil',
};
const TM_PARENT = {
  ibmul: '순도시험', heavy: '순도시험', pesticide: '순도시험', so2: '순도시험', mycotoxin: '순도시험',
};
let tmAvailable = new Set();   // 지금 보이는 생약 응답의 test_methods
let renderSectionLabel = '';   // renderItemText가 지금 어느 섹션을 그리는 중인지

function tmButton(key) {
  if (!key || !tmAvailable.has(key)) return '';
  return `<button type="button" class="tm-btn" data-tm="${key}" title="생약시험법의 해당 시험법 보기">생약시험법</button>`;
}

function renderItemListIn(sectionLabel, items) {
  renderSectionLabel = sectionLabel;
  try {
    return renderItemList(items);
  } finally {
    renderSectionLabel = '';
  }
}

// 항목 맨 앞 이름 끝의 "이물"(그 밖의 이물 포함)을 링크로 바꾼다. 누르면 생약시험법의
// 순도시험 가. 이물 항목이 창으로 열린다.
const TM_IBMUL_RE = /^((?:[^<>:：]*?\s)?)이물(?=\s*[:：]|\s{2}|\s*$)/;
function tmLinkIbmul(html) {
  if (!tmAvailable.has('ibmul')) return html;
  return html.replace(TM_IBMUL_RE, (m, pre) => `${pre}<a href="#" class="tm-link" data-tm="ibmul" title="생약시험법 이물 시험법 보기">이물</a>`);
}

// filterItem이 있으면(시험항목 검색에서 넘어온 경우) 성상/확인시험/순도시험/
// 정량법 등 모든 항목을 그대로 다 보여주되, 그 시험항목에 해당하는 섹션만
// 펼친 상태로, 나머지는 접힌 상태로 시작한다. "확인시험"/"정량법"/"순도시험"
// 처럼 항목명 자체가 keyword면 그 섹션 전체를 펼치고, 그 외(납/이산화황/
// 유전자검사 등)는 순도시험 계층 안에서 실제로 일치하는 하위 항목만 찾아
// 그 부분만 펼쳐서 보여준다(나머지 순도시험 항목은 제외). 일치하는 게 없으면
// (예상 밖의 경우를 대비해) 전체를 다 펼쳐서 보여준다.
function buildSectionsHtml(item, filterItem) {
  if (!filterItem) {
    return item.sections.map(sec => fullSectionHtml(sec, true)).join('');
  }
  if (filterItem.keywords.length === 1 && ['확인시험', '정량법', '순도시험', '엑스함량', '정유함량'].includes(filterItem.keywords[0])) {
    const targetLabel = filterItem.keywords[0];
    const found = item.sections.some(s => s.label === targetLabel);
    if (!found) return item.sections.map(sec => fullSectionHtml(sec, true)).join('');
    return item.sections.map(sec => fullSectionHtml(sec, sec.label === targetLabel)).join('');
  }
  const puritySec = item.sections.find(s => s.label === '순도시험');
  if (puritySec && puritySec.items && puritySec.items.length) {
    const matched = findMatchingNodes(puritySec.items, filterItem.keywords);
    if (matched.length) {
      return item.sections.map(sec => {
        if (sec === puritySec) {
          return purityToggleSectionHtml(sec.label, renderItemListIn(sec.label, matched), renderItemListIn(sec.label, sec.items));
        }
        return fullSectionHtml(sec, false);
      }).join('');
    }
  }
  return item.sections.map(sec => fullSectionHtml(sec, true)).join('');
}

// 순도시험 안의 특정 시험항목(납/비소/이산화황 등)만 걸러서 보여주는
// 섹션은 일반 섹션과 달리 접었다 펼쳤다 하는 게 아니라, 헤더를 클릭할
// 때마다 "해당 시험항목만" ↔ "순도시험 전체"를 번갈아 보여준다. 두
// 내용을 각각 <template>에 미리 담아두면(템플릿 안 내용은 렌더링되지
// 않고 그대로 보관됨) 클릭 시 innerHTML만 바꿔치기하면 된다(클릭 처리는
// detailBox의 위임 리스너 참조).
function purityToggleSectionHtml(label, narrowedBody, fullBody) {
  return `
    <div class="section purity-toggle" data-state="narrowed">
      <div class="section-header">
        <div class="label">${escapeHtml(label)}</div>
        <span class="toggle-icon">+</span>
      </div>
      <div class="section-content">${narrowedBody}</div>
      <template class="narrowed-content-template">${narrowedBody}</template>
      <template class="full-content-template">${fullBody}</template>
    </div>
  `;
}

// 설명창 글자 크기: '가−' '가+' 버튼으로 헤더 아래 내용의 글자 크기(배율)를 단계별로 조절한다. 고른 단계는 다음에 열 때도 유지한다.
const DETAIL_ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.2, 1.35, 1.5];
let detailZoomIdx = DETAIL_ZOOM_STEPS.indexOf(1);
try {
  const saved = DETAIL_ZOOM_STEPS.indexOf(parseFloat(localStorage.getItem('detailZoom')));
  if (saved >= 0) detailZoomIdx = saved;
} catch (e) { /* 저장소를 못 쓰면 기본 크기 */ }

function refreshDetailZoomUi() {
  const z = DETAIL_ZOOM_STEPS[detailZoomIdx];
  detailBox.style.setProperty('--detail-zoom', z);
  detailBox.querySelectorAll('.detail-zoom-pct').forEach(s => { s.textContent = Math.round(z * 100) + '%'; s.title = z === 1 ? '' : '더블 클릭하면 100%로'; });
  detailBox.querySelectorAll('.detail-zoom-btn').forEach(b => {
    const dir = Number(b.dataset.dir);
    b.disabled = (dir < 0 && detailZoomIdx === 0) || (dir > 0 && detailZoomIdx === DETAIL_ZOOM_STEPS.length - 1);
    b.title = `${dir < 0 ? '글자 작게' : '글자 크게'} (현재 ${Math.round(z * 100)}%)`;
  });
}
// 글자 크기 표시부(%)를 더블 클릭하면 100%가 아닐 때 기본 크기(100%)로 되돌린다
detailBox.addEventListener('dblclick', (e) => {
  if (!e.target.closest('.detail-zoom-pct')) return;
  const base = DETAIL_ZOOM_STEPS.indexOf(1);
  if (detailZoomIdx === base) return;
  detailZoomIdx = base;
  try { localStorage.setItem('detailZoom', '1'); } catch (err) { /* 저장 실패는 무시 */ }
  refreshDetailZoomUi();
  window.getSelection && window.getSelection().removeAllRanges();  // 더블 클릭으로 글자가 선택되어 파랗게 보이는 것을 막는다
});
detailBox.addEventListener('click', (e) => {
  const btn = e.target.closest('.detail-zoom-btn');
  if (!btn || btn.disabled) return;
  detailZoomIdx = Math.min(DETAIL_ZOOM_STEPS.length - 1, Math.max(0, detailZoomIdx + Number(btn.dataset.dir)));
  try { localStorage.setItem('detailZoom', String(DETAIL_ZOOM_STEPS[detailZoomIdx])); } catch (err) { /* 저장 실패는 무시 */ }
  refreshDetailZoomUi();
});
refreshDetailZoomUi();

// ---- 설명 내용 미리 받기 ----
// 목록의 생약 줄에 마우스를 올리거나(잠깐 머물 때) 손가락을 대는 순간 그 생약의 설명 내용을 미리 받아 둔다. 눌렀을 때는 이미
// 받아져 있어 바로 뜨고, 서버와의 왕복 시간(PythonAnywhere 에서는 특히 큼)이 가려진다. 한 번 받은 것은 5분 동안 다시 받지 않는다.
// 글자(JSON 문자열)로 보관하고 쓸 때마다 새로 해석한다(showDetail 이 받은 객체를 고치기 때문에 같은 객체를 돌려쓰면 안 된다).
const ITEM_PREFETCH_TTL = 5 * 60 * 1000, ITEM_PREFETCH_MAX = 40;
const itemPrefetch = new Map();  // id -> { t: 받기 시작한 시각, p: Promise<문자열 | null> }
function fetchItemText(id) {
  const key = String(id);
  const hit = itemPrefetch.get(key);
  if (hit && Date.now() - hit.t < ITEM_PREFETCH_TTL) return hit.p;
  const p = fetch('/api/item/' + key).then(res => (res.ok ? res.text() : null)).catch(() => null);
  const entry = { t: Date.now(), p };
  itemPrefetch.delete(key);
  itemPrefetch.set(key, entry);
  if (itemPrefetch.size > ITEM_PREFETCH_MAX) itemPrefetch.delete(itemPrefetch.keys().next().value);
  p.then(text => { if (text === null && itemPrefetch.get(key) === entry) itemPrefetch.delete(key); });  // 실패는 보관하지 않는다
  return p;
}
// "사진정보" 버튼에 마우스를 올리거나 손가락을 대면, 새 창이 열리기 전에 사진 화면을 한 번 불러 서버가 사진 정보를 준비하게 하고
// 목록 맨 위 사진 두 장을 미리 받아 브라우저 저장소(하루 보관)에 넣어 둔다. 눌러서 열리는 새 창은 같은 주소의 그림을 거기서 바로 쓴다.
// 사진을 그리는 데는 서버가 1초 가까이 쓰므로 두 장으로 제한하고, 같은 버튼은 한 번만 한다.
const PHOTO_BTN = 'a.link-btn[href^="/photo_info"]';
const photoWarmed = new Set();
function warmPhotoInfo(href) {
  if (photoWarmed.has(href)) return;
  photoWarmed.add(href);
  fetch(href).then(res => (res.ok ? res.text() : '')).then(html => {
    const re = /src="(\/sensory_photos\/[^"]+)"/g;
    let m, n = 0;
    while ((m = re.exec(html)) && n < 2) {
      const img = new Image();
      img.src = m[1].replace(/&amp;/g, '&');
      n += 1;
    }
  }).catch(() => {});
}
const PREFETCH_ROW = '.result-item[data-id], .fam-item[data-id]';
let prefetchTimer = null;
document.addEventListener('mouseover', (ev) => {
  clearTimeout(prefetchTimer);
  const t = ev.target.closest ? ev.target : null;
  if (!t) return;
  const row = t.closest(PREFETCH_ROW);
  if (row) { prefetchTimer = setTimeout(() => fetchItemText(row.dataset.id), 60); return; }  // 줄 위를 스쳐 지나가기만 할 때는 받지 않는다
  const btn = t.closest(PHOTO_BTN);
  if (btn) prefetchTimer = setTimeout(() => warmPhotoInfo(btn.getAttribute('href')), 60);
});
document.addEventListener('touchstart', (ev) => {
  const t = ev.target.closest ? ev.target : null;
  if (!t) return;
  const row = t.closest(PREFETCH_ROW);
  if (row) { fetchItemText(row.dataset.id); return; }
  const btn = t.closest(PHOTO_BTN);
  if (btn) warmPhotoInfo(btn.getAttribute('href'));
}, { passive: true });

async function showDetail(id, filterItem, opts = {}) {
  const text = await fetchItemText(id);
  if (text === null) return;
  const item = JSON.parse(text);
  setShownKey(`id:${item.id}`);

  if (item.kind === 'sensory') {
    showSensoryDetail(item, opts);
    return;
  }
  // 생약제제 제법 표의 생약명 링크로 연 경우(opts.backId = 그 생약제제 id)에는 상태에도 기록해 두어, 뒤로/앞으로 가기로 이 화면이
  // 복원돼도 '← 돌아가기' 버튼이 그대로 보이게 한다.
  const back = opts.backId ? { backId: opts.backId, backName: opts.backName || '' } : {};
  navSet(
    { view: { type: 'item', id: item.id, filterIdx: filterItem ? TEST_ITEMS.indexOf(filterItem) : -1, ...back } },
    opts.replace ? 'replace' : 'push',
  );

  tmAvailable = new Set(item.test_methods || []);
  // "확인시험 (생약제제에 적용)", "정량법 (생규집 생약시험법)" 항목은 시험항목 검색에서 확인시험/정량법으로 열었을 때만 보여 준다
  // (생약 검색 결과나 다른 시험항목으로 열었을 때는 숨긴다).
  const showMethodSections = filterItem && filterItem.keywords.length === 1 && ['확인시험', '정량법'].includes(filterItem.keywords[0]);
  if (item.kind === 'official' && !showMethodSections) item.sections = item.sections.filter(sec => !sec.note);
  const sectionsHtml = buildSectionsHtml(item, filterItem);

  const definitionHtml = (item.definition_parts && item.definition_parts.length)
    ? item.definition_parts.map(p => `<p class="${p.small ? 'quantitative' : ''}">${familyLinkify(p.html)}</p>`).join('')
    : (item.definition ? `<p>${familyLinkify(escapeHtml(item.definition))}</p>` : '');

  detailBox.classList.remove('sensory-mode');
  // 이 화면이 어떤 항목인지 기억해 둔다: 생약제제 제법 표의 생약명 링크를 누를 때 "돌아갈 곳"(이 항목)으로 쓴다
  detailBox.dataset.itemId = item.id;
  detailBox.dataset.itemName = item.name_only || item.korean_name || '';
  const backTitle = opts.backName ? `${escapeHtml(opts.backName)}(으)로 돌아가기` : '돌아가기';
  const backHtml = opts.backId
    ? `<button type="button" class="detail-back-btn" title="${backTitle}" aria-label="${backTitle}"><svg viewBox="0 0 12 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.2 4H10.8M4.8 .9 1.2 4 4.8 7.1"/></svg></button>`
    : '';
  detailBox.innerHTML = `
    <div class="detail-header">
      <div class="detail-title-row">
        <div class="detail-name-line">
          ${backHtml}
          <h2>${renderName(item)}${sourceBadge(item.source_tag, item.part)}${flagBadge(item.flag)}</h2>
          ${item.orig ? `<a class="detail-orig-btn" href="/api/herb_orig/${item.id}?v=${item.orig}#page=1" target="_blank" rel="noopener" title="공정서 원문 PDF에서 이 부분만 보기">원문</a>` : ''}
          <span class="detail-name-gap" aria-hidden="true"></span>
          <div class="detail-zoom" role="group" aria-label="글자 크기">
            <button type="button" class="detail-zoom-btn" data-dir="-1" aria-label="글자 작게">가−</button>
            <span class="detail-zoom-pct" aria-live="polite">100%</span>
            <button type="button" class="detail-zoom-btn" data-dir="1" aria-label="글자 크게">가+</button>
          </div>
        </div>
        <div class="detail-title-actions">
          ${linkButtonHtml('사진정보', item.photo_links, '국가생약정보 사진(왼쪽)과 관능검사해설서 사진(오른쪽) 보기')}
          ${linkButtonHtml('적응증', item.indication_links, '국가생약정보의 채취 및 가공, 적응증(효능/효과), 용법용량, 저장 보기')}
          ${linkButtonHtml('확인시험사례', item.exam_case_links)}
          ${linkButtonHtml('HPTLC', item.hptlc_links)}
          ${linkButtonHtml('HPLC', item.hplc_links)}
          ${linkButtonHtml('성분정보', item.ingredient_links)}
          ${item.gene_case ? '<button type="button" class="link-btn link-btn-long gene-case-btn" title="유전자 기원 감별 정보자료집의 해당 생약 사례 보기">유전자감별사례</button>' : ''}
          ${linkButtonHtml('생약감별자료집', item.dscrm_links)}
          ${linkButtonHtml('표본정보', item.specimen_links)}
          ${linkButtonHtml('품질표준화연구', item.crshm_links)}
        </div>
      </div>
      <p class="en">${escapeHtml(item.english_name || '')}</p>
      <p class="latin">${escapeHtml(item.latin_name || '')}</p>
    </div>
    ${definitionHtml ? `<div class="definition">${definitionHtml}</div>` : ''}
    ${sectionsHtml}
  `;
  refreshDetailZoomUi();
  detailBox.querySelectorAll('.link-menu > .link-btn').forEach(btn => {
    btn.addEventListener('click', ev => {
      ev.stopPropagation();
      const menu = btn.parentElement;
      const willOpen = !menu.classList.contains('open');
      closeLinkMenus();
      if (willOpen) {
        menu.classList.add('open');
        btn.setAttribute('aria-expanded', 'true');
      }
    });
  });
  // "유전자감별사례": 유전자 기원 감별 정보자료집 4장 품목별 감별사례 중 이 생약
  // 구간만 오른쪽 화면에 PDF로 보여 준다(관능검사 사례집과 같은 방식).
  const geneBtn = detailBox.querySelector('.gene-case-btn');
  if (geneBtn) {
    geneBtn.addEventListener('click', () => showSensoryDetail({
      ...item.gene_case,
      korean_name: `${item.name_only} - 유전자 기원 감별 사례`,
    }));
  }
  _showDetailBox();
  if (typeof famRefreshCurrent === 'function') famRefreshCurrent();
}

// 국가생약정보(nifds.go.kr)의 해당 생약 페이지를 새 창으로 여는 버튼("사진정보",
// "HPTLC"). 그 사이트는 다른 사이트 화면 안에 끼워 넣는 것(iframe)을 막고 있어
// 새 창으로 연다. "사진정보"는 생약 상세 페이지이고, 그 안의 "사진정보" 탭은
// 주소만으로는 바로 열리지 않아서(탭 전환이 페이지 안 클릭으로만 동작) 열린
// 페이지에서 그 탭을 한 번 눌러야 한다. "HPTLC"는 해당 생약의 HPTLC 조회
// 페이지가 바로 열린다. 기원종이 여러 개인 생약은 기원종마다 페이지가 따로 있어,
// 버튼을 누르면 기원종을 고르는 목록을 보여준다.
function linkButtonHtml(label, links, title = '국가생약정보 사이트에서 열기') {
  if (!links || !links.length) return '';
  const attrs = 'target="_blank" rel="noopener noreferrer"';
  const long = label.length >= 7 ? ' link-btn-long' : '';  // 모바일에서 글자 크기를 키우지 않는 7글자 라벨
  if (links.length === 1) {
    return `<a class="link-btn${long}" href="${escapeHtml(links[0].url)}" ${attrs} title="${escapeHtml(title)}">${escapeHtml(label)}</a>`;
  }
  // 기원종 이름이 비었거나("-" 포함) 같은 이름이 여러 번 나오면 번호를 붙여 구분한다.
  const names = links.map((l, i) => (l.origin && l.origin !== '-' ? l.origin : `기원종 ${i + 1}`));
  const items = links.map((l, i) => {
    const dup = names.filter(n => n === names[i]).length > 1;
    return `<a href="${escapeHtml(l.url)}" ${attrs}>${escapeHtml(dup ? `${names[i]} (${i + 1})` : names[i])}</a>`;
  }).join('');
  return `
    <div class="link-menu">
      <button type="button" class="link-btn${long}" aria-haspopup="true" aria-expanded="false" title="기원종을 고르면 국가생약정보 사이트가 열립니다">${escapeHtml(label)} ▾</button>
      <div class="link-menu-list">${items}</div>
    </div>
  `;
}

function closeLinkMenus() {
  document.querySelectorAll('.link-menu.open').forEach(m => {
    m.classList.remove('open');
    m.querySelector('.link-btn').setAttribute('aria-expanded', 'false');
  });
}

document.addEventListener('click', closeLinkMenus);

function showSensoryDetail(item, opts = {}) {
  // 아이폰·아이패드 같은 터치 기기: 사파리는 iframe 안의 PDF를 첫 쪽만 보여 주므로, 오른쪽 설명 화면은 건드리지 않고 같은 탭에서
  // 바로 사파리의 PDF 보기로 넘어간다. 화면 상태(기록)도 바꾸지 않아서, 사파리 뒤로 가기로 돌아오면 넘어가기 전 설명 화면 그대로다.
  // 새 창(window.open)은 목록을 누른 뒤 내용을 받아 오는 사이에 사용자 동작으로 인정되지 않아 막힐 수 있어 같은 탭에서 이동한다.
  // 뒤로 가기·새로고침으로 화면을 복원하는 중(navRestoring)에는 이동하지 않는다.
  if (!navRestoring && window.matchMedia('(pointer: coarse), (hover: none)').matches) {
    setShownKey(prevShownKey);  // 목록의 선택 표시도 설명 화면에 남아 있는 항목 그대로 둔다
    const pdfUrl = `/api/sensory_pdf?file=${encodeURIComponent(item.source_file)}&start=${item.page_start}&end=${item.page_end}#page=1`;
    showPdfLoading();
    location.href = pdfUrl;
    return;
  }
  navSet(
    {
      view: {
        type: 'pdf',
        key: shownKey,
        item: {
          source_file: item.source_file,
          page_start: item.page_start,
          page_end: item.page_end,
          korean_name: item.korean_name,
        },
      },
    },
    opts.replace ? 'replace' : 'push',
  );
  // 끝에 붙인 "#page=1"은 PDF 뷰어 공통 오픈 파라미터로, 브라우저 내장
  // 뷰어가 같은 PDF를 이전에 열었을 때의 스크롤 위치를 기억해뒀다가
  // 복원하는 것을 막고 항상 문서 맨 위(1쪽)부터 보이게 한다.
  const pdfUrl = `/api/sensory_pdf?file=${encodeURIComponent(item.source_file)}&start=${item.page_start}&end=${item.page_end}#page=1`;
  // 생약 내용(.detail) 쪽에서 좌우 모서리 드래그로 width/max-width/margin에
  // 인라인 스타일이 남아있을 수 있다. 관능검사해설서 화면은 원래부터 항상
  // 전체 폭을 꽉 채우는 고정 레이아웃(.sensory-mode)이라 그 설정과
  // 무관해야 하므로, 전환할 때마다 인라인 스타일을 지워 원래 설정대로
  // 돌아가게 한다.
  detailBox.style.width = '';
  detailBox.style.maxWidth = '';
  detailBox.style.marginLeft = '';
  detailBox.style.marginRight = '';
  detailBox.classList.add('sensory-mode');
  detailBox.innerHTML = `
    <a class="pdf-open-link" href="${pdfUrl}" target="_blank" rel="noopener">PDF 새 창으로 열기</a>
    <iframe class="sensory-frame" src="${pdfUrl}" title="${escapeHtml(item.korean_name)} 원본 페이지"></iframe>
  `;
  _showDetailBox();
}

// 터치 기기에서 PDF 로 넘어가는 동안(서버가 해당 쪽을 잘라 보내는 데 시간이 걸린다) 보여 주는 "여는 중" 안내.
// 사파리 뒤로 가기로 돌아오면(페이지 캐시 복원) 지운다.
function showPdfLoading() {
  let box = document.getElementById('pdfLoading');
  if (!box) {
    box = document.createElement('div');
    box.id = 'pdfLoading';
    box.className = 'pdf-loading';
    box.setAttribute('role', 'status');
    box.textContent = 'PDF를 여는 중입니다…';
    document.body.appendChild(box);
  }
  box.hidden = false;
}
window.addEventListener('pageshow', () => {
  const box = document.getElementById('pdfLoading');
  if (box) box.hidden = true;
});

function _showDetailBox() {
  detailBox.classList.add('show');
  detailPlaceholder.classList.add('hide');
  if (window.matchMedia('(max-width: 860px)').matches) {
    // 모바일(세로 배치)에서는 내용이 검색칸/목록 아래에 있으므로 내용이 시작되는 곳으로 스크롤한다
    document.getElementById('detailWrap').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  positionDetailResizer();
}

// html 인자는 이미 이스케이프(및 <sub>/<table> 표시 처리)까지 끝난 문자열이어야 한다.
function linkifyRef(html, refId) {
  const m = html.match(/([「｢])([^」｣]+)([」｣])/);
  if (!m) return html;
  const before = html.slice(0, m.index);
  const [full, openBracket, name, closeBracket] = m;
  const after = html.slice(m.index + full.length);
  return `${before}${openBracket}<a href="#" class="ref-link" data-id="${refId}">${name}</a>${closeBracket}${after}`;
}

// 확인시험/정량법의 「생약명」: 서버가 문장 안의 「 」 중 실제 품목 이름과 일치하는 것을
// refs({이름: id})로 내려 주면, 그 이름마다 링크를 걸어 누르면 그 생약을 보여 준다.
function linkifyRefs(html, refs) {
  if (!refs) return html;
  return html.replace(/([「｢])([^」｣<]+)([」｣])/g, (m, open, name, close) => {
    const id = refs[name.trim()];
    return id === undefined ? m : `${open}<a href="#" class="ref-link" data-id="${id}">${name}</a>${close}`;
  });
}

function renderSectionBody(sec) {
  let html = sec.html !== undefined ? sec.html : escapeHtml(sec.text);
  html = linkifyRefs(html, sec.refs);
  return (sec.ref_id !== undefined && sec.ref_id !== null) ? linkifyRef(html, sec.ref_id) : html;
}

function renderItemText(it, depth = 0) {
  const refId = (it.ref_id !== undefined && it.ref_id !== null) ? it.ref_id : null;
  const purity = renderSectionLabel === '순도시험';
  if (it.bold !== undefined) {
    const boldHtml = linkifyRefs(it.bold_html !== undefined ? it.bold_html : escapeHtml(it.bold), it.refs);
    const restHtml = linkifyRefs(it.rest_html !== undefined ? it.rest_html : escapeHtml(it.rest), it.refs);
    let boldInner = it.bold ? (refId ? linkifyRef(boldHtml, refId) : boldHtml) : '';
    let afterBold = '';
    let atEnd = '';
    if (purity && it.bold) {
      const name = it.bold.trim().replace(/\s*[:：]$/, '');
      // "생약시험법" 버튼은 최상위 항목(1) 중금속)에만 붙인다. 그 아래 하위 항목(나) 중금속)에는 붙이지 않는다.
      if (depth === 0 && name === '곰팡이독소') {
        afterBold = tmButton('mycotoxin');
      } else if (depth === 0 && name === '중금속') {
        afterBold = tmButton('heavy');
      } else if (depth === 0 && name === '잔류농약') {
        afterBold = tmButton('pesticide');
      } else if (depth === 0 && name === '이산화황') {
        afterBold = tmButton('so2');
      } else if (!['곰팡이독소', '중금속', '잔류농약', '이산화황'].includes(name)) {
        boldInner = tmLinkIbmul(boldInner);
      }
    }
    const boldPart = it.bold ? `<strong>${boldInner}</strong>` : '';
    const restPart = it.rest ? ` ${refId ? linkifyRef(restHtml, refId) : restHtml}` : '';
    return `${boldPart}${afterBold}${restPart}${atEnd}`;
  }
  let textHtml = linkifyRefs(it.text_html !== undefined ? it.text_html : escapeHtml(it.text), it.refs);
  if (purity) textHtml = tmLinkIbmul(textHtml);
  return refId ? linkifyRef(textHtml, refId) : textHtml;
}

function renderItemList(items, depth = 0) {
  const lis = items.map(it => `
    <li>
      ${it.marker ? `<span class="item-marker">${it.marker_html !== undefined ? it.marker_html : escapeHtml(it.marker)}</span>` : ''}
      <span class="item-text">${renderItemText(it, depth)}</span>
      ${it.children && it.children.length ? renderItemList(it.children, depth + 1) : ''}
    </li>
  `).join('');
  return `<ol class="item-list">${lis}</ol>`;
}

// 기원 문장의 "콩과 Leguminosae"에서 한글 과 이름("콩과")을 누를 수 있는 링크로 바꾼다(서버의 FAMILY_RE 와 같은 규칙).
function familyLinkify(html) {
  return html.replace(/([가-힣]+과)(\s+[A-Z][a-z]+(?:aceae|idae|ae))/g,
    (m, fam, latin) => `<span class="fam-link" role="button" tabindex="0" data-fam="${fam}" title="같은 과 생약 목록 보기">${fam}</span>${latin}`);
}

function renderName(it) {
  const primary = it.name_primary || it.korean_name;
  const synonym = it.synonym_name || '';
  // 이명에 화학식("Na2SO4․10H2O")이 있으면 서버가 아래첨자를 적용해 둔 synonym_html 을 쓴다.
  const synonymHtml = it.synonym_html || escapeHtml(synonym);
  return `${escapeHtml(primary)}${synonym ? ` <span class="synonym">${synonymHtml}</span>` : ''}`;
}

function sourceBadge(tag, part) {
  if (!tag) return '';
  return `<span class="source-badge">${escapeHtml(tag)}</span>` + (part ? `<span class="source-part">${escapeHtml(part)}</span>` : '');
}

// 식약공용/독성주의 표시(식약공용독성주의.xlsx 기준, 서버가 flag 로 내려 준다).
function flagBadge(flag) {
  if (flag === '식약공용') return '<span class="flag-badge flag-common">식약공용</span>';
  if (flag === '독성주의') return '<span class="flag-badge flag-toxic">독성주의</span>';
  return '';
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ---- 생약시험법 항목 보기 ----
// "생약시험법" 버튼과 이물/곰팡이독소 링크를 누르면 해당 항목을 창으로 띄운다.
// 항목 내용은 처음 쓸 때 서버에서 한 번만 받아 와 보관한다.
const tmCache = new Map();
const tmModal = document.getElementById('tmModal');
const tmTitle = document.getElementById('tmTitle');
const tmBody = document.getElementById('tmBody');

async function loadTestMethod(key) {
  if (!tmCache.has(key)) {
    try {
      const res = await fetch('/api/test_method/' + encodeURIComponent(key));
      if (!res.ok) return null;
      tmCache.set(key, await res.json());
    } catch (err) {
      return null;
    }
  }
  return tmCache.get(key);
}

function tmFullTitle(key, data) {
  const parent = TM_PARENT[key];
  return ['생약시험법', parent, data.title].filter(Boolean).join(' › ');
}

async function openTestMethod(key) {
  const data = await loadTestMethod(key);
  if (!data) return;
  tmTitle.textContent = tmFullTitle(key, data);
  tmBody.innerHTML = `<div class="tm-content">${data.html}</div>`;  // 내용 래퍼: 끝에서 더 쓸 때 출렁이는 효과를 이 래퍼에 준다
  tmBody.scrollTop = 0;
  tmSetPull(0, false);
  setTmOffset(0, 0);  // 지난번에 옮겼더라도 열 때는 가운데에서 시작
  tmModal.hidden = false;
  document.documentElement.classList.add('tm-open');
}

function closeTestMethod() {
  tmModal.hidden = true;
  tmBody.innerHTML = '';
  document.documentElement.classList.remove('tm-open');
}

// 모바일: 생약시험법 창 안쪽에서만 스크롤되게 한다. 창 바깥이나 제목줄을 문지르는 터치, 그리고 창 내용이
// 맨 위/맨 아래에 닿은 뒤에도 계속 미는 터치는 막아서 뒤 화면으로 스크롤이 넘어가지 않게 한다.
// 스크롤이 끝(맨 위/맨 아래)에 닿은 뒤에도 계속 쓸면 내용이 손가락을 따라 조금(저항을 받으며) 밀려났다가,
// 손을 떼면 출렁이며 제자리로 돌아온다(고무줄 효과).
let tmTouch = { x: 0, y: 0 };
let tmRaw = 0;  // 끝에서 더 민 손가락 이동량 합(위로 끌려가면 음수, 아래로 끌려가면 양수). 화면에는 저항을 적용한 값으로 보인다.
const TM_PULL_MAX = 70;  // 내용이 밀려나는 최대 거리(px)

function tmSetPull(px, animate) {
  const c = tmBody.firstElementChild;
  tmRaw = px === 0 ? 0 : tmRaw;
  if (!c) return;
  c.style.transition = animate ? 'transform 0.45s cubic-bezier(0.25, 1.5, 0.5, 1)' : 'none';  // 1보다 큰 값으로 살짝 지나쳤다 돌아온다
  c.style.transform = px ? `translateY(${px.toFixed(1)}px)` : '';
}

function tmPullShown(raw) {
  return Math.sign(raw) * TM_PULL_MAX * (1 - Math.exp(-Math.abs(raw) / (TM_PULL_MAX * 1.6)));
}

tmModal.addEventListener('touchstart', (ev) => {
  if (ev.touches.length !== 1) return;
  tmTouch = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
  tmRaw = 0;
  tmSetPull(0, false);
}, { passive: true });
tmModal.addEventListener('touchmove', (ev) => {
  if (ev.touches.length !== 1) return;
  const t = ev.touches[0];
  const dx = t.clientX - tmTouch.x, dy = t.clientY - tmTouch.y;
  tmTouch = { x: t.clientX, y: t.clientY };
  if (!ev.target.closest('#tmBody')) { ev.preventDefault(); return; }
  if (tmRaw === 0 && Math.abs(dy) <= Math.abs(dx)) return;  // 표를 옆으로 미는 터치는 그대로 둔다
  const atTop = tmBody.scrollTop <= 0;
  const atBottom = tmBody.scrollTop + tmBody.clientHeight >= tmBody.scrollHeight - 1;
  if (tmRaw !== 0) {
    // 이미 끝에서 밀려난 상태: 손가락을 되돌리면 같이 되돌아오고, 제자리에 오면 그때부터 평소처럼 스크롤된다
    ev.preventDefault();
    const next = tmRaw + dy;
    tmRaw = (tmRaw > 0 && next <= 0) || (tmRaw < 0 && next >= 0) ? 0 : next;
    tmSetPull(tmPullShown(tmRaw), false);
  } else if ((dy > 0 && atTop) || (dy < 0 && atBottom)) {
    ev.preventDefault();
    tmRaw = dy;
    tmSetPull(tmPullShown(tmRaw), false);
  }
}, { passive: false });
['touchend', 'touchcancel'].forEach(type => {
  tmModal.addEventListener(type, () => {
    if (tmRaw !== 0 || (tmBody.firstElementChild && tmBody.firstElementChild.style.transform)) {
      tmRaw = 0;
      tmSetPull(0, true);
    }
  }, { passive: true });
});

// 제목줄을 마우스(또는 터치)로 끌어 창을 옮긴다. 창 위치는 transform 으로 옮기고, 창이 화면
// 밖으로 완전히 나가 제목줄을 잡을 수 없게 되지 않도록 화면 안에 가둔다. 새로 열 때마다
// 가운데로 되돌린다.
const tmDialog = tmModal.querySelector('.tm-dialog');
const tmDialogHead = tmModal.querySelector('.tm-dialog-head');
let tmOffset = { x: 0, y: 0 };
let tmDrag = null;

function setTmOffset(x, y) {
  tmOffset = { x, y };
  tmDialog.style.transform = `translate(${x}px, ${y}px)`;
}

tmDialogHead.addEventListener('pointerdown', (ev) => {
  if (ev.target.closest('button') || (ev.pointerType === 'mouse' && ev.button !== 0)) return;
  const r = tmDialog.getBoundingClientRect();
  tmDrag = {
    startX: ev.clientX, startY: ev.clientY, baseX: tmOffset.x, baseY: tmOffset.y,
    // 창이 움직일 수 있는 범위: 창의 일부(40px)와 제목줄이 항상 화면 안에 남도록
    minDx: -(r.left + r.width) + 40, maxDx: window.innerWidth - r.left - 40,
    minDy: -r.top, maxDy: window.innerHeight - r.top - 40,
  };
  tmDialogHead.setPointerCapture(ev.pointerId);
  ev.preventDefault();
});
tmDialogHead.addEventListener('pointermove', (ev) => {
  if (!tmDrag) return;
  const dx = Math.min(Math.max(ev.clientX - tmDrag.startX, tmDrag.minDx), tmDrag.maxDx);
  const dy = Math.min(Math.max(ev.clientY - tmDrag.startY, tmDrag.minDy), tmDrag.maxDy);
  setTmOffset(tmDrag.baseX + dx, tmDrag.baseY + dy);
});
['pointerup', 'pointercancel'].forEach(type => {
  tmDialogHead.addEventListener(type, () => { tmDrag = null; });
});


detailBox.addEventListener('click', (ev) => {
  const opener = ev.target.closest('.tm-btn, .tm-link');
  if (opener) {
    ev.preventDefault();
    openTestMethod(opener.dataset.tm);
  }
});
// 닫기 버튼(×)이나 Esc 로 닫힌다. 창 바깥을 눌러도 닫히지 않는다.
document.getElementById('tmClose').addEventListener('click', closeTestMethod);
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !tmModal.hidden) closeTestMethod();
});

// ---- 같은 과 생약 목록 ----
// 기원 문장의 과 이름(예: "콩과")을 누르면 같은 과에 속한 공정서 생약 이름 목록이 팝업으로 뜬다. 목록에서 이름을 누르면 그 생약의
// 설명창으로 이동하고, 이동한 설명창에는 생약명 앞에 '돌아가기' 화살표가 붙어 지금 보던 생약으로 돌아올 수 있다.
const famModal = document.getElementById('famModal');
const famTitle = document.getElementById('famTitle');
const famMenu = document.getElementById('famMenu');
const famBody = document.getElementById('famBody');
let famFamilies = [];   // 고를 수 있는 전체 과 [[과 이름, 생약 수], ...]
let famCurName = '';    // 지금 목록에 나온 과

// 제목줄을 누르면 제목줄 바로 아래에 전체 과 목록이 펼쳐지고, 하나를 고르면 그 과의 생약 목록으로 바뀐다.
function openFamMenu() {
  famMenu.style.top = famDialogHead.offsetHeight + 'px';
  famMenu.hidden = false;
  famTitle.setAttribute('aria-expanded', 'true');
  const sel = famMenu.querySelector('.selected');
  if (sel) famMenu.scrollTop = Math.max(0, sel.offsetTop - famMenu.clientHeight / 2 + sel.offsetHeight / 2);  // 지금 과가 가운데쯤 보이게
}
function closeFamMenu() {
  famMenu.hidden = true;
  famTitle.setAttribute('aria-expanded', 'false');
}

// 처음 열릴 때 팝업을 눌린 과 이름(anchor) 오른쪽에, 제목줄 세로 가운데를 과 이름 높이에 맞춰 놓는다. 오른쪽에 자리가 없으면 왼쪽에,
// 양쪽 다 없으면(좁은 화면) 화면 안으로 가둔다. 아래로 넘치면 팝업 높이를 줄여(목록은 안에서 스크롤) 제목줄 높이를 유지하되,
// 너무 아래라서 목록이 240px 도 안 남으면 그만큼만 위로 올린다. 이후 과를 바꿔도 이 자리는 마우스로 옮기기 전까지 그대로다.
function placeFamilyNear(anchor) {
  setFamOffset(0, 0);
  famDialog.style.maxHeight = '';
  if (!anchor || !anchor.isConnected) return;
  const a = anchor.getClientRects()[0];
  if (!a) return;
  const r0 = famDialog.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight, gap = 8, m = 8, minH = 240;
  let left = a.right + gap;
  if (left + r0.width > vw - m) {
    const l2 = a.left - gap - r0.width;
    left = l2 >= m ? l2 : Math.max(m, vw - m - r0.width);
  }
  let top = a.top + a.height / 2 - famDialogHead.offsetHeight / 2;
  top = Math.max(m, Math.min(top, vh - m - minH));
  famDialog.style.maxHeight = (vh - m - top) + 'px';
  setFamOffset(left - r0.left, top - r0.top);
}

async function openFamily(name, anchor) {
  let data;
  try {
    const res = await fetch('/api/family/' + encodeURIComponent(name));
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    return;
  }
  const cur = Number(detailBox.dataset.itemId) || 0;
  // 제목줄: 지금 과를 "173. 콩과 생약 (29)"로 보여 주고, 누르면 펼쳐지는 과 목록(순번 + 과 이름 + 생약 수)을 채운다
  famFamilies = data.families || [[name, data.items.length]];
  famCurName = name;
  const fi = famFamilies.findIndex(([f]) => f === name);
  famTitle.textContent = `${fi + 1}. ${name} 생약 (${data.items.length})`;
  famMenu.innerHTML = famFamilies.map(([f, n], i) =>
    `<button type="button" class="fam-menu-item${f === name ? ' selected' : ''}" role="option" aria-selected="${f === name}" data-fam="${escapeHtml(f)}"><span class="fam-menu-no">${i + 1}.</span>${escapeHtml(f)} 생약 (${n})</button>`).join('');
  closeFamMenu();
  famBody.innerHTML = data.items.length
    ? `<ul class="fam-list">${data.items.map((it, i) =>
        `<li><button type="button" class="fam-item${it.id === cur ? ' current' : ''}" data-id="${it.id}"><span class="fam-no" style="min-width:${String(data.items.length).length + 1}ch">${i + 1}.</span>${renderName(it)}${sourceBadge(it.source_tag, it.part)}${flagBadge(it.flag)}</button></li>`
      ).join('')}</ul>`
    : '<p class="fam-empty">같은 과의 생약이 없습니다.</p>';
  famBody.scrollTop = 0;
  const fresh = famModal.hidden;
  famModal.hidden = false;
  if (fresh) placeFamilyNear(anchor);  // 새로 열 때만 자리를 잡는다(고정되어 이미 떠 있던 팝업은 옮겨 둔 자리 그대로)
  setFamPinned(famPinned);  // 뒤 화면 고정(fam-open) 여부를 고정 상태에 맞춘다
  // 지금 보고 있는 생약이 목록 아래쪽이면 보이게 한다. scrollIntoView 는 바깥 화면까지 움직여 제목줄이 밀릴 수 있어서
  // 목록(famBody) 안에서만 직접 스크롤한다.
  const curBtn = famBody.querySelector('.fam-item.current');
  if (curBtn) {
    const off = curBtn.getBoundingClientRect().top - famBody.getBoundingClientRect().top;
    famBody.scrollTop = Math.max(0, off - famBody.clientHeight / 2 + curBtn.offsetHeight / 2);
  }
}

function closeFamily() {
  closeFamMenu();
  famModal.hidden = true;
  famBody.innerHTML = '';
  setFamPinned(false);  // 닫으면 고정도 풀린다(다음에 열 때는 고정 안 된 상태)
  document.documentElement.classList.remove('fam-open');
}

// 핀(고정): 고정하면 팝업 바깥을 누르거나 목록에서 생약을 골라 설명창이 바뀌어도 팝업이 닫히지 않고, 팝업 바깥 화면도 그대로
// 쓸 수 있다(바깥 덮개가 입력을 막지 않는다). ×나 Esc 로는 닫힌다.
let famPinned = false;
const famPinBtn = document.getElementById('famPin');
function setFamPinned(on) {
  famPinned = on;
  famModal.classList.toggle('pinned', on);
  famPinBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  famPinBtn.setAttribute('aria-label', on ? '고정 풀기' : '고정');
  // 터치 화면에서 뒤 화면을 고정하는 것은 고정이 아닐 때, 팝업이 열려 있는 동안만이다
  document.documentElement.classList.toggle('fam-open', !on && !famModal.hidden);
}

// 설명창이 바뀌면(목록에서 골랐거나 다른 방법으로 열었을 때) 목록에서 지금 보고 있는 생약 표시를 옮긴다
function famRefreshCurrent() {
  if (famModal.hidden) return;
  const cur = Number(detailBox.dataset.itemId) || 0;
  famBody.querySelectorAll('.fam-item').forEach(b => b.classList.toggle('current', Number(b.dataset.id) === cur));
}

famBody.addEventListener('click', async (ev) => {
  const btn = ev.target.closest('.fam-item');
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const curId = Number(detailBox.dataset.itemId) || 0;
  const curName = detailBox.dataset.itemName || '';
  if (!famPinned) closeFamily();
  if (id === curId) return;  // 지금 보고 있는 생약이면 (고정이 아닐 때) 팝업만 닫는다
  await showDetail(id, undefined, curId ? { backId: curId, backName: curName } : undefined);
  famRefreshCurrent();
});
// 제목줄을 잡고 끌면 팝업을 옮긴다(생약시험법 창과 같은 방식: 제목줄이 화면 밖으로 나가지 않게 가둔다).
const famDialog = famModal.querySelector('.fam-dialog');
const famDialogHead = famModal.querySelector('.fam-dialog-head');
let famOffset = { x: 0, y: 0 };
let famDrag = null;
function setFamOffset(x, y) {
  famOffset = { x, y };
  famDialog.style.transform = x || y ? `translate(${x}px, ${y}px)` : '';
}
famDialogHead.addEventListener('pointerdown', (ev) => {
  if (ev.target.closest('button, select') || (ev.pointerType === 'mouse' && ev.button !== 0)) return;
  const r = famDialog.getBoundingClientRect();
  famDrag = {
    startX: ev.clientX, startY: ev.clientY, baseX: famOffset.x, baseY: famOffset.y,
    minDx: -(r.left + r.width) + 40, maxDx: window.innerWidth - r.left - 40,
    minDy: -r.top, maxDy: window.innerHeight - r.top - 40,
  };
  famDialogHead.setPointerCapture(ev.pointerId);
  ev.preventDefault();
});
famDialogHead.addEventListener('pointermove', (ev) => {
  if (!famDrag) return;
  const dx = Math.min(Math.max(ev.clientX - famDrag.startX, famDrag.minDx), famDrag.maxDx);
  const dy = Math.min(Math.max(ev.clientY - famDrag.startY, famDrag.minDy), famDrag.maxDy);
  setFamOffset(famDrag.baseX + dx, famDrag.baseY + dy);
});
['pointerup', 'pointercancel'].forEach(type => {
  famDialogHead.addEventListener(type, () => { famDrag = null; });
});
document.getElementById('famClose').addEventListener('click', closeFamily);
famModal.querySelector('.fam-backdrop').addEventListener('click', () => { if (!famPinned) closeFamily(); });
famPinBtn.addEventListener('click', () => setFamPinned(!famPinned));
famTitle.addEventListener('click', () => { if (famMenu.hidden) openFamMenu(); else closeFamMenu(); });
famMenu.addEventListener('click', (ev) => {
  const it = ev.target.closest('.fam-menu-item');
  if (!it) return;
  closeFamMenu();
  if (it.dataset.fam !== famCurName) openFamily(it.dataset.fam);  // 다른 과를 고르면 그 과의 생약 목록으로 바꾼다
});
// 과 목록이 펼쳐진 상태에서 목록·제목 바깥을 누르면 목록만 접는다(팝업은 그대로)
document.addEventListener('pointerdown', (ev) => {
  if (!famMenu.hidden && !ev.target.closest('#famMenu, #famTitle')) closeFamMenu();
}, true);
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !famModal.hidden) {
    if (!famMenu.hidden) closeFamMenu();  // 과 목록이 펼쳐져 있으면 목록만 접는다
    else closeFamily();
  }
});

// 처음 크기(왼쪽 칸 너비, 배경 그림 칸 높이)가 모두 정해졌으니 숨겨 둔 본문을 보인다(templates 의 head 스크립트 참조)
fitLeftPanelHeight();
fitDetailPlaceholderHeight();
document.documentElement.classList.remove('ui-pending');
