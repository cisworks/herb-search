// 대한민국약전 검색(/kp) 화면. 생약 검색(app.js)과 같은 틀(왼쪽 검색 패널, 오른쪽 설명창, 끌어서 너비 조절, 글자 크기)을 쓴다.
const KP = '/kp';
const drugInput = document.getElementById('drugInput');
const drugToggle = document.getElementById('drugToggle');
const drugDropdown = document.getElementById('drugDropdown');
const drugResultsBox = document.getElementById('drugResults');
const refInput = document.getElementById('refInput');
const refToggle = document.getElementById('refToggle');
const refDropdown = document.getElementById('refDropdown');
const refResultsBox = document.getElementById('refResults');
const textInput = document.getElementById('textInput');
const textResultsBox = document.getElementById('textResults');
const quickBox = document.getElementById('quickBox');
const detailBox = document.getElementById('detail');
const detailPlaceholder = document.getElementById('detailPlaceholder');
const leftPanelEl = document.querySelector('.left-panel');

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}

// ---- 왼쪽 패널 너비 / 접기 / 설명창 너비 (생약 검색과 같은 동작) ------------------------------------------
function measureTextWidth(text, font) {
  const canvas = measureTextWidth._canvas || (measureTextWidth._canvas = document.createElement('canvas'));
  const ctx = canvas.getContext('2d');
  ctx.font = font;
  return ctx.measureText(text).width;
}

// 처음 너비: 검색칸 안내 문구가 잘리지 않고 다 보이는 만큼(가장 긴 안내 문구 기준)
function computeInitialLeftWidth(leftPanel) {
  let inputNeeded = 0;
  [drugInput, refInput, textInput].forEach(inp => {
    const st = getComputedStyle(inp);
    const font = `${st.fontWeight} ${st.fontSize} ${st.fontFamily}`;
    const w = measureTextWidth(inp.placeholder, font) * (parseFloat(st.fontSize) - 1.33) / parseFloat(st.fontSize);  // 안내 문구는 1pt 작게
    inputNeeded = Math.max(inputNeeded, Math.ceil(w + parseFloat(st.paddingLeft) + parseFloat(st.paddingRight) + parseFloat(st.borderLeftWidth) + parseFloat(st.borderRightWidth) + 4));
  });
  const tabBar = leftPanel.querySelector('.tab-bar');
  // 탭 버튼은 칸이 모자라면 줄어들 수 있어서(scrollWidth 로는 모른다) 탭 이름이 한 줄에 다 들어가는 폭을 직접 잰다
  let tabsNeeded = 0;
  if (tabBar) {
    const btns = [...tabBar.querySelectorAll('.tab-btn')];
    btns.forEach(b => {
      const st = getComputedStyle(b);
      tabsNeeded += measureTextWidth(b.textContent.trim(), `${st.fontWeight} ${st.fontSize} ${st.fontFamily}`)
        + parseFloat(st.paddingLeft) + parseFloat(st.paddingRight) + parseFloat(st.borderLeftWidth) + parseFloat(st.borderRightWidth) + 2;
    });
    tabsNeeded += 6 * btns.length + 16;  // 버튼 사이 간격 + 접기 버튼(◀)
  }
  return Math.ceil(Math.max(inputNeeded, tabBar ? tabBar.scrollWidth : 0, tabsNeeded, 200));
}

(function initPanelResizer() {
  const panelResizer = document.getElementById('panelResizer');
  const wrapEl = document.querySelector('.wrap');
  if (!leftPanelEl || !panelResizer || !wrapEl) return;
  const baseLeftWidth = computeInitialLeftWidth(leftPanelEl);
  leftPanelEl.style.width = baseLeftWidth + 'px';
  let dragging = false, startX = 0, startWidth = 0;
  const clampWidth = (w) => {
    const min = baseLeftWidth / 2;
    return Math.min(Math.max(min, wrapEl.clientWidth * 0.6), Math.max(min, w));
  };
  panelResizer.addEventListener('mousedown', (e) => {
    dragging = true;
    startX = e.clientX;
    startWidth = leftPanelEl.getBoundingClientRect().width;
    panelResizer.classList.add('dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (dragging) leftPanelEl.style.width = clampWidth(startWidth + (e.clientX - startX)) + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    panelResizer.classList.remove('dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    fitAll();
  });
})();

(function initLeftPanelCollapse() {
  const panelResizer = document.getElementById('panelResizer');
  const collapseBtn = document.getElementById('leftPanelCollapseBtn');
  if (!leftPanelEl || !panelResizer || !collapseBtn) return;
  const COLLAPSED_WIDTH = 44 * 0.3;
  let savedWidth = null;
  let peekTimer = null;
  function openPeek() {
    if (!leftPanelEl.classList.contains('collapsed')) return;
    clearTimeout(peekTimer);
    leftPanelEl.style.setProperty('--peek-width', savedWidth || '400px');
    leftPanelEl.classList.add('peek');
  }
  function closePeek() {
    clearTimeout(peekTimer);
    leftPanelEl.classList.remove('peek');
    closeAllDropdowns();
  }
  collapseBtn.addEventListener('mouseenter', openPeek);
  leftPanelEl.addEventListener('mouseenter', () => clearTimeout(peekTimer));
  leftPanelEl.addEventListener('mouseleave', () => {
    if (leftPanelEl.classList.contains('peek')) peekTimer = setTimeout(closePeek, 120);
  });
  collapseBtn.addEventListener('click', () => {
    clearTimeout(peekTimer);
    leftPanelEl.classList.remove('peek');
    const collapsed = leftPanelEl.classList.toggle('collapsed');
    if (collapsed) {
      savedWidth = leftPanelEl.style.width;
      leftPanelEl.style.width = COLLAPSED_WIDTH + 'px';
      panelResizer.style.display = 'none';
      collapseBtn.textContent = '▶';
      collapseBtn.title = '왼쪽 화면 펼치기';
      collapseBtn.setAttribute('aria-label', '왼쪽 화면 펼치기');
    } else {
      if (savedWidth) leftPanelEl.style.width = savedWidth;
      panelResizer.style.display = '';
      collapseBtn.textContent = '◀';
      collapseBtn.title = '왼쪽 화면 접기';
      collapseBtn.setAttribute('aria-label', '왼쪽 화면 접기');
    }
    fitAll();
  });
})();

// 설명창(.detail)은 항상 오른쪽 화면 가운데에 두고, 왼쪽/오른쪽 모서리를 끌면 가운데를 기준으로 너비가 바뀐다.
// 모서리를 더블 클릭하면: 기본 너비(100%)이면 최대로, 아니면 100%로.
function positionDetailResizer() {
  const detailWrap = document.getElementById('detailWrap');
  const resizerLeft = document.getElementById('detailResizerLeft');
  const resizerRight = document.getElementById('detailResizerRight');
  if (!detailWrap || !resizerLeft || !resizerRight) return;
  const shown = detailBox.classList.contains('show');
  detailWrap.classList.toggle('show', shown);
  if (!shown) return;
  resizerLeft.style.left = detailBox.offsetLeft + 'px';
  resizerRight.style.left = (detailBox.offsetLeft + detailBox.offsetWidth) + 'px';
}

(function initDetailResizer() {
  const detailWrap = document.getElementById('detailWrap');
  const resizerLeft = document.getElementById('detailResizerLeft');
  const resizerRight = document.getElementById('detailResizerRight');
  if (!detailWrap || !resizerLeft || !resizerRight) return;
  let dragging = false, moved = false, centerX = 0, baseWidth = 900, dragSide = 'right', saved = null;
  const widthTip = document.createElement('div');
  widthTip.className = 'detail-width-tip';
  widthTip.setAttribute('aria-hidden', 'true');
  document.body.appendChild(widthTip);

  function showWidthTip(width, e) {
    widthTip.textContent = `${Math.round((width / baseWidth) * 100)}%`;
    widthTip.style.display = 'block';
    const w = widthTip.offsetWidth, h = widthTip.offsetHeight;
    const wantLeft = dragSide === 'left' ? e.clientX - 14 - w : e.clientX + 14;
    widthTip.style.left = Math.min(window.innerWidth - w - 4, Math.max(4, wantLeft)) + 'px';
    widthTip.style.top = Math.min(window.innerHeight - h - 4, Math.max(4, e.clientY - h - 12)) + 'px';
  }

  let hintTimer = null, hintShown = false, lastMouse = null;
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
      hintTimer = setTimeout(() => showHint(side), 250);
    });
    el.addEventListener('mousemove', (e) => {
      lastMouse = e;
      if (hintShown && !dragging) placeHint(side, e);
    });
    el.addEventListener('mouseleave', hideHint);
  }
  const clampWidth = (w) => {
    const min = 400;
    return Math.min(Math.max(min, detailWrap.getBoundingClientRect().width), Math.max(min, w));
  };
  function startDrag(resizerEl) {
    return (e) => {
      dragging = true;
      moved = false;
      const rect = detailBox.getBoundingClientRect();
      centerX = rect.left + rect.width / 2;
      saved = { width: detailBox.style.width, maxWidth: detailBox.style.maxWidth, marginLeft: detailBox.style.marginLeft, marginRight: detailBox.style.marginRight };
      detailBox.style.width = rect.width + 'px';
      detailBox.style.maxWidth = 'none';
      detailBox.style.marginLeft = 'auto';
      detailBox.style.marginRight = 'auto';
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
  let resetTipTimer = null;
  function resetWidth(e) {
    clearTimeout(hintTimer);
    hintShown = false;
    const wrapW = detailWrap.getBoundingClientRect().width;
    const curW = detailBox.getBoundingClientRect().width;
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
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    moved = true;
    const w = clampWidth(2 * Math.abs(e.clientX - centerX));
    detailBox.style.width = w + 'px';
    positionDetailResizer();
    showWidthTip(w, e);
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    widthTip.style.display = 'none';
    if (!moved && saved) {
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
  });
  window.addEventListener('resize', positionDetailResizer);
})();

// 왼쪽 패널은 따라다니는(sticky) 상태라 내용이 길면 패널 안에서 스크롤되게 높이를 제한한다.
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
function updateLeftPanelScrollGap() {
  leftPanelEl.classList.toggle('has-scroll', leftPanelEl.scrollHeight > leftPanelEl.clientHeight + 1);
}
window.addEventListener('resize', fitLeftPanelHeight);
window.addEventListener('scroll', fitLeftPanelHeight, { passive: true });
if (leftPanelEl && window.ResizeObserver) {
  const ro = new ResizeObserver(updateLeftPanelScrollGap);
  leftPanelEl.querySelectorAll('.tab-panel, .tab-bar').forEach(el => ro.observe(el));
}

function fitDetailPlaceholderHeight() {
  if (detailPlaceholder.classList.contains('hide')) return;
  const top = detailPlaceholder.getBoundingClientRect().top;
  detailPlaceholder.style.height = Math.max(200, window.innerHeight - top - 24) + 'px';
}
window.addEventListener('resize', fitDetailPlaceholderHeight);

// 의약품등 검색 결과 카드는 목록이 길면 화면 아래끝(창 아래 24px 위)까지 늘어난다(통칙·제제·시험법·정보 탭의 카드와 같다). 그 안에서 스크롤된다.
function fitDrugResultsHeight() {
  const g = drugResultsBox.querySelector('.result-group');
  if (!g || drugResultsBox.style.display === 'none') return;
  if (window.matchMedia('(max-width: 860px)').matches) {  // 모바일은 페이지 전체가 스크롤되므로 기본 높이를 쓴다
    g.style.maxHeight = '';
    return;
  }
  const top = g.getBoundingClientRect().top;
  if (top <= 0) return;  // 숨겨진 탭 등 위치를 잴 수 없을 때
  g.style.maxHeight = Math.max(160, window.innerHeight - top - 24) + 'px';
}
window.addEventListener('resize', fitDrugResultsHeight);

function fitAll() {
  fitDrugResultsHeight();
  if (typeof fitRefResultsHeight === 'function') fitRefResultsHeight();
  fitDetailPlaceholderHeight();
  fitDropdownHeights();
  fitLeftPanelHeight();
  positionDetailResizer();
}
fitLeftPanelHeight();
fitDetailPlaceholderHeight();

// ---- 목록(드롭다운) ------------------------------------------------------------------------------
let requestSeq = 0;
const listCache = {};
async function getList(tab) {
  if (!listCache[tab]) {
    const res = await fetch(`${KP}/api/list?tab=${tab}`);
    listCache[tab] = await res.json();
  }
  return listCache[tab];
}

// 오른쪽 설명창 이름 오른쪽의 파일 구분: 각조 제2부는 분류까지 "각조 제2부 · 5) 첨가제" 처럼 보인다. 글자 크기 버튼이 이 글자 끝에 이어진다.
function detailPartHtml(it) {
  if (it.kind === 'drug') {
    const text = it.doc === 4 && it.group ? `${it.part} · ${it.group}` : it.part;
    return `<span class="source-part">${escapeHtml(text)}</span>`;
  }
  return `<span class="source-part">${escapeHtml(it.cat)}</span>`;
}

function sourceBadge(it) {
  // "KP" 배지는 두지 않는다(약전 검색 화면은 전부 KP 라서 구분할 필요가 없다). 파일 구분 글자만 보인다.
  if (it.kind === 'drug') return `<span class="source-part">${escapeHtml(it.part)}</span>`;
  return `<span class="source-part">${escapeHtml(it.cat)}</span>`;
}

// 별표 4(의약품각조 제2부) 품목의 분류("2) 생물학적제제 등" 등): 목록의 이름 아래 줄에 보인다. 별표 3 품목("의약품")은 분류줄이 없다.
// 이름 줄 안의 '각조 제2부' 배지 바로 뒤에 이어서 붙는다.
function groupLineHtml(it) {
  return it.kind === 'drug' && it.doc === 4 && it.group ? `<span class="rgrp">${escapeHtml(it.group)}</span>` : '';
}

// 목록/결과 한 줄: 한글명(+ 번호). 영문명은 목록에 보이지 않는다(상세 화면에서만 보인다)
function rowHtml(it, withBadge) {
  const en = '';
  if (it.kind === 'ref') {
    // 통칙·제제총칙·일반시험법·일반정보 항목: 번호는 따로 두고 이름(+영문명)은 한 덩어리로 흘러, 두 줄 이상이면 단어 단위로 줄바꿈되고
    // 둘째 줄부터 번호 뒤 첫 글자 위치에 맞춰 들여 쓴다(번호/이름 칸 구조는 CSS .ref-row)
    const no = it.no ? `<span class="rno">${escapeHtml(it.no)}.</span>` : '';
    // 본문이 없는 제목뿐인 항목(Ⅱ. 제제 각조)은 줄만 보이고 누를 수 없다(링크 없음)
    if (it.nolink) return `<div class="result-item ref-row no-link">${no}<span class="rtext"><span class="rname">${escapeHtml(it.ko)}</span>${en}</span></div>`;
    return `<div class="result-item ref-row" data-id="${it.id}">${no}<span class="rtext"><span class="rname">${escapeHtml(it.ko)}</span>${en}</span></div>`;
  }
  const no = '';
  return `<div class="result-item" data-id="${it.id}"><span class="rname">${no}${escapeHtml(it.ko)}${withBadge ? sourceBadge(it) : ''}${groupLineHtml(it)}</span></div>`;
}

// 전체 목록의 '분류별' 보기: 켜면 "의약품 각조 제1부"와 "의약품 각조 제2부"(그 안은 2) 생물학적제제 등 ... 6) 의약외품 분류별)만 + 로 접혀 보이고,
// + 를 누르면 그 아래 품목이 펼쳐진다. 선택은 이 브라우저에 기억해 둔다.
const CLS_KEY = 'kpClsMode';
function loadClsMode() { try { return localStorage.getItem(CLS_KEY) === '1'; } catch (e) { return false; } }
function saveClsMode(on) { try { localStorage.setItem(CLS_KEY, on ? '1' : '0'); } catch (e) { /* 저장 못 해도 이번 화면에서는 그대로 쓴다 */ } }
let clsMode = loadClsMode();
function clsNode(title, count, inner, open) {
  return `<div class="cls-node"><div class="cls-head" role="button" tabindex="0" aria-expanded="${open ? 'true' : 'false'}"><span class="cls-sign">${open ? '−' : '+'}</span><span class="cls-name">${escapeHtml(title)}</span><span class="cls-count">(${count})</span></div><div class="cls-body"${open ? '' : ' hidden'}>${inner}</div></div>`;
}
function groupedDrugHtml(items) {
  const part1 = items.filter(it => it.doc === 3);
  const part2 = items.filter(it => it.doc === 4);
  const groups = [];
  part2.forEach(it => {
    let g = groups.find(x => x.name === it.group);
    if (!g) groups.push(g = { name: it.group, items: [] });
    g.items.push(it);
  });
  groups.sort((a, b) => a.name.localeCompare(b.name, 'ko'));  // "2) ...", "3) ..." 번호 순
  const inner2 = groups.map(g => clsNode(g.name, g.items.length, g.items.map(it => rowHtml(it, false)).join(''), false)).join('');
  // 제2부는 분류별 보기를 켤 때마다 항상 펼쳐져 나온다(그 안의 2)~6) 분류는 접힌 채)
  return clsNode('의약품 각조 제1부', part1.length, part1.map(it => rowHtml(it, false)).join(''), false)
    + clsNode('의약품 각조 제2부', part2.length, inner2, true);
}

function dropdownHtml(tab, items) {
  if (tab === 'drug') {
    const label = `<div class="group-label"><span>의약품각조 전체 목록 (${items.length})</span><label class="cls-check"><input type="checkbox" id="clsCheck"${clsMode ? ' checked' : ''}> 분류별</label></div>`;
    return label + (clsMode ? groupedDrugHtml(items) : items.map(it => rowHtml(it, true)).join(''));
  }
  // 목록에는 문서(통칙/제제총칙/일반시험법/일반정보) 넷만 한 줄씩 나온다(+ 표시도, 하위 항목도 없다). 하나를 고르면 그 하위 항목들이 아래 카드에 나온다.
  const cats = [];
  items.forEach(it => {
    let c = cats.find(x => x.cat === it.cat);
    if (!c) cats.push(c = { cat: it.cat, doc: it.doc, n: 0 });
    c.n += 1;
  });
  return cats.map(c => `<div class="result-item" data-cat="${escapeHtml(c.cat)}"><span class="rname">${escapeHtml(c.cat)}</span><span class="ren">[별표 ${c.doc}] ${c.n}항목</span></div>`).join('');
}

// 통칙/제제총칙/일반시험법/일반정보 항목 줄들(제제총칙은 투여 경로별 소제목 포함). showCat: 문서 이름 구분줄을 넣을지
function refRowsHtml(items, showCat) {
  let html = '';
  let lastCat = '', lastGroup = '';
  items.forEach(it => {
    if (it.cat !== lastCat) {
      if (showCat) html += `<div class="case-category-label">${escapeHtml(it.cat)}</div>`;
      lastCat = it.cat;
      lastGroup = '';
    }
    // 투여 경로 묶음("1. 경구투여 하는 제제")은 그 자신이 첫 줄이라 소제목을 따로 달지 않는다
    const isGroupHead = it.no && /^\d+$/.test(it.no) && it.group && it.group.startsWith(it.no + '.');
    if (it.group && it.group !== lastGroup && !isGroupHead) {
      html += `<div class="case-subgroup-label">${escapeHtml(it.group)}</div>`;
    }
    lastGroup = it.group;
    html += rowHtml(it, false);
  });
  return html;
}

const combos = {
  drug: { tab: 'drug', input: drugInput, toggle: drugToggle, dropdown: drugDropdown, items: null, token: 0 },
  ref: { tab: 'ref', input: refInput, toggle: refToggle, dropdown: refDropdown, items: null, token: 0 },
};

function syncDropdownOpenClass() {
  const open = Object.values(combos).some(c => c.dropdown.style.display === 'block');
  leftPanelEl.classList.toggle('dropdown-open', open);
}
function fitDropdownHeights() {
  Object.values(combos).forEach(c => {
    if (c.dropdown.style.display !== 'block') return;
    const top = c.dropdown.getBoundingClientRect().top;
    c.dropdown.style.maxHeight = Math.max(160, window.innerHeight - top - 24) + 'px';
  });
}
window.addEventListener('resize', fitDropdownHeights);
function closeDropdown(c) {
  c.token += 1;  // 목록을 받아 오는 중에 닫으면 그 열기를 취소한다
  c.dropdown.style.display = 'none';
  syncDropdownOpenClass();
}
function closeAllDropdowns() { Object.values(combos).forEach(closeDropdown); }
async function openDropdown(c) {
  const token = ++c.token;
  const items = await getList(c.tab);
  if (token !== c.token) return;
  c.items = items;
  c.dropdown.innerHTML = dropdownHtml(c.tab, items);
  c.dropdown.scrollTop = 0;
  c.dropdown.style.display = 'block';
  syncDropdownOpenClass();
  fitDropdownHeights();
}

function toggleClsNode(head) {
  const open = head.getAttribute('aria-expanded') !== 'true';
  head.setAttribute('aria-expanded', open ? 'true' : 'false');
  head.querySelector('.cls-sign').textContent = open ? '−' : '+';
  head.nextElementSibling.hidden = !open;
}
combos.drug.dropdown.addEventListener('change', (ev) => {
  if (ev.target.id !== 'clsCheck') return;
  clsMode = ev.target.checked;
  saveClsMode(clsMode);
  const dd = combos.drug.dropdown;
  dd.innerHTML = dropdownHtml('drug', combos.drug.items);
  dd.scrollTop = 0;
  fitDropdownHeights();
});
combos.drug.dropdown.addEventListener('keydown', (ev) => {
  const head = ev.target.closest && ev.target.closest('.cls-head');
  if (head && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); toggleClsNode(head); }
});

Object.values(combos).forEach(c => {
  c.dropdown.addEventListener('click', (ev) => {
    const head = ev.target.closest('.cls-head');
    if (head) { toggleClsNode(head); return; }
    const row = ev.target.closest('.result-item');
    if (row && row.dataset.cat) {  // 문서 이름(통칙/제제총칙/일반시험법/일반정보)을 고르면 검색칸에 표시하고 카드에는 그 문서의 항목만 나온다
      closeDropdown(c);
      refSelectedId = null;
      selectRefCat(row.dataset.cat);
      return;
    }
    if (!row || !c.items) return;
    const it = c.items.find(x => String(x.id) === row.dataset.id);
    if (it) selectFromCombo(c, it);
  });
  c.toggle.addEventListener('click', () => {
    if (c.dropdown.style.display === 'block') closeDropdown(c);
    else openDropdown(c);
  });
});

// 검색(Enter, 목록에서 고름)을 마친 뒤 검색칸을 다시 누르면 검색어 전체를 블록 선택한다. 입력 중에는 false.
const searchDone = { drug: false, ref: false, text: false };

// 통칙·제제·시험법·정보 탭: 입력칸은 읽기 전용이고, 목록에서 항목을 고르면 그 항목이 속한 문서(통칙/제제총칙/일반시험법/일반정보)의
// 항목들이 아래 카드에 나오고(고른 항목 표시) 오른쪽에 내용이 열린다.
// 문서(통칙 등)를 고르면 검색칸에 그 문서 이름이, 카드에는 그 문서의 항목들이 나온다. 묶음 머리 항목("1. 경구투여 하는 제제")을 고르면
// 카드에는 그 묶음의 하위 항목만 나온다. 하위 항목이 없는 항목(일반시험법 한 개 등)을 고르면 같은 묶음(또는 문서)의 항목들이 나오고 고른 항목이 표시된다.
// 검색칸에는 "상위 항목"(문서 이름 또는 묶음 머리 항목)만 나온다. 카드에서 하위 항목을 눌러도 검색칸은 그대로이고, 카드는 다시 그려지지 않아
// 스크롤 위치가 유지되며 눌린 줄에만 선택 표시가 붙는다. 목록(▾)에서 하위 항목을 직접 고르면 그 상위 항목이 검색칸에 나오고 카드에서 그 줄이 보이게 한다.
let refParentKey = '';   // 'cat:문서이름' 또는 묶음 머리 항목의 id(문자열)
let refSelectedId = null;  // 마지막으로 연 항목(목록을 다시 열 때 그 자리를 펼쳐 두는 데 쓴다)
function refTitle(it) { return `${it.no && it.kind === 'ref' ? it.no + '. ' : ''}${it.ko}`; }
const refIsGroupHead = (it) => it.no && /^\d+$/.test(it.no) && it.group && it.group.startsWith(it.no + '.');
function refStateKey() { return refParentKey; }
// 하위 항목 카드는 목록이 길면 화면 아래끝(창 아래 24px 위)까지 늘어난다(생약 검색의 시험항목 검색 결과 카드와 같다). 그 안에서 스크롤된다.
function fitRefResultsHeight() {
  const g = refResultsBox.querySelector('.result-group');
  if (!g || refResultsBox.style.display === 'none') return;
  if (window.matchMedia('(max-width: 860px)').matches) {  // 모바일은 페이지 전체가 스크롤되므로 기본 높이를 쓴다
    g.style.maxHeight = '';
    return;
  }
  const top = g.getBoundingClientRect().top;
  g.style.maxHeight = Math.max(160, window.innerHeight - top - 24) + 'px';
}
window.addEventListener('resize', fitRefResultsHeight);
function showRefCard(titleHtml, itemsHtml, items) {
  refResultsBox.style.display = 'flex';
  refResultsBox.innerHTML = `<div class="result-group"><div class="group-label">${titleHtml}</div>${itemsHtml}</div>`;
  fitRefResultsHeight();
  refResultsBox.querySelectorAll('.result-item[data-id]').forEach(el => el.addEventListener('click', () => {
    const t = items.find(x => String(x.id) === el.dataset.id);
    if (t) { refSelectedId = t.id; showEntry(t.id); }  // 검색칸·카드는 그대로 두고 내용만 연다
  }));
  applyShownHighlight();
}
// 상위 항목 하나를 검색칸에 표시하고, 카드에 그 하위 항목들을 그린다
async function renderRefParent(key) {
  const all = await getList('ref');
  refParentKey = key;
  if (key.startsWith('cat:')) {
    const cat = key.slice(4);
    const same = all.filter(x => x.cat === cat);
    refInput.value = cat;
    showRefCard(`[별표 ${same[0] ? same[0].doc : ''}] ${escapeHtml(cat)} (${same.length})`, refRowsHtml(same, false), same);
  } else {
    const head = all.find(x => String(x.id) === key);
    if (!head) return;
    const kids = all.filter(x => x.cat === head.cat && x.group === head.group && x.id !== head.id);
    refInput.value = refTitle(head);
    showRefCard(`${escapeHtml(refTitle(head))} (${kids.length})`, kids.map(k => rowHtml(k, false)).join(''), kids);
  }
  setQuery('ref', key);
}
// 문서(통칙 등)를 고르면 카드에 그 항목들을 그리고, 첫 번째 하위 항목(누를 수 있는 첫 줄)을 자동으로 골라 오른쪽 설명창에 연다
async function selectRefCat(cat) {
  await renderRefParent('cat:' + cat);
  const first = (await getList('ref')).find(x => x.cat === cat && !x.nolink);
  if (!first) return;
  refSelectedId = first.id;
  await showEntry(first.id);  // 선택 표시는 showEntry 가 붙인다
}
// 항목 it 의 상위 항목: 하위 항목이 딸린 묶음 머리이면 자기 자신, 묶음 안의 항목이면 그 묶음 머리, 그 밖에는 문서 이름
async function refParentKeyFor(it) {
  const all = await getList('ref');
  if (refIsGroupHead(it) && all.some(x => x.cat === it.cat && x.group === it.group && x.id !== it.id)) return String(it.id);
  if (it.group) {
    const head = all.find(x => x.cat === it.cat && x.group === it.group && refIsGroupHead(x));
    if (head) return String(head.id);
  }
  return 'cat:' + it.cat;
}
// 목록(▾)에서 항목을 고른 경우: 그 항목의 상위 항목을 검색칸에 표시하고, 카드에서 고른 줄이 보이게 한다
async function renderRefSelection(it) {
  refSelectedId = it.id;
  await renderRefParent(await refParentKeyFor(it));
}
refInput.addEventListener('click', () => {
  if (refDropdown.style.display !== 'block') openDropdown(combos.ref);
});
// 목록 밖을 누르면 목록을 닫는다
document.addEventListener('click', (ev) => {
  if (!ev.target.closest('.search-box')) closeAllDropdowns();
});

async function selectFromCombo(c, it) {
  closeDropdown(c);
  if (c.tab === 'ref') {
    await renderRefSelection(it);
    await showEntry(it.id);
    const sel = refResultsBox.querySelector('.result-item.selected');  // 카드가 길면 고른 줄이 보이게(맨 위로 돌아가지 않는다)
    if (sel) sel.scrollIntoView({ block: 'nearest' });
    return;
  }
  c.input.value = it.ko;
  await runNameSearch(c.tab, it.ko, true);
  searchDone[c.tab] = true;
  showEntry(it.id);
}

// ---- 탭 ---------------------------------------------------------------------------------------
const tabButtons = document.querySelectorAll('.tab-btn');
function activateTab(tabId) {
  tabButtons.forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === tabId));
  closeAllDropdowns();
  refreshQuickBox();
  fitRefResultsHeight();
  fitDrugResultsHeight();
}
function focusTabInput(tabId) {
  const box = { drugTab: drugInput, refTab: refInput, textTab: textInput }[tabId];
  if (box) box.focus({ preventScroll: true });
}
tabButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    activateTab(btn.dataset.tab);
    navSet({ tab: btn.dataset.tab });
    focusTabInput(btn.dataset.tab);
  });
});

// ---- 브라우저 뒤로/앞으로 ------------------------------------------------------------------------
let navState = { tab: 'drugTab', q: { drug: '', ref: '', text: '' }, view: null };
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
function setQuery(tab, q) {
  navSet({ q: { ...navState.q, [tab]: q } }, 'replace');
}
function hideDetail() {
  detailBox.classList.remove('show');
  detailBox.innerHTML = '';
  detailPlaceholder.classList.remove('hide');
  fitDetailPlaceholderHeight();
  setShownKey('');
  positionDetailResizer();
}
const INPUTS = { drug: drugInput, ref: refInput, text: textInput };
async function applyNav(st) {
  navRestoring = true;
  try {
    activateTab(st.tab);
    for (const tab of ['drug', 'text']) {
      const q = (st.q && st.q[tab]) || '';
      if (INPUTS[tab].value.trim() === q) continue;
      INPUTS[tab].value = q;
      if (q) await (tab === 'text' ? runTextSearch(q) : runNameSearch(tab, q));
      else clearResults(tab);
    }
    const refQ = (st.q && st.q.ref) || '';
    if (refStateKey() !== refQ) {
      if (refQ) {
        await renderRefParent(refQ);
      } else {
        refSelectedId = null;
        refParentKey = '';
        refInput.value = '';
        clearResults('ref');
      }
    }
    if (!st.view) hideDetail();
    else await showEntry(st.view.id, { hl: st.view.hl || '', back: !!st.view.back, backName: st.view.backName || '' });
  } finally {
    navState = JSON.parse(JSON.stringify(st));
    navLast = JSON.stringify(navState);
    navRestoring = false;
  }
  focusTabInput(st.tab);
}
window.addEventListener('popstate', (ev) => {
  if (ev.state && ev.state.tab) applyNav(ev.state);
});

// ---- 검색 ---------------------------------------------------------------------------------------
const RESULT_BOX = { drug: drugResultsBox, ref: refResultsBox, text: textResultsBox };
function clearResults(tab) {
  RESULT_BOX[tab].style.display = 'none';
  RESULT_BOX[tab].innerHTML = '';
}

let shownKey = '';
function applyShownHighlight() {
  [drugResultsBox, refResultsBox, textResultsBox, quickBox].forEach(box => {
    box.querySelectorAll('.result-item').forEach(el => {
      el.classList.toggle('selected', !!shownKey && `id:${el.dataset.id}` === shownKey);
    });
  });
}
function setShownKey(key) {
  shownKey = key;
  applyShownHighlight();
}

function groupByCat(items) {
  const out = [];
  items.forEach(it => {
    let g = out.find(x => x.cat === it.cat);
    if (!g) out.push(g = { cat: it.cat, items: [] });
    g.items.push(it);
  });
  return out;
}

function renderNameResults(tab, items, q) {
  const box = RESULT_BOX[tab];
  box.style.display = 'flex';
  if (!items.length) {
    box.innerHTML = `<div class="empty-msg">"${escapeHtml(q)}"에 대한 검색 결과가 없습니다.</div>`;
    return;
  }
  if (tab === 'drug') {
    box.innerHTML = `<div class="result-group"><div class="group-label">의약품각조 (${items.length})</div>${items.map(it => rowHtml(it, true)).join('')}</div>`;
  } else {
    box.innerHTML = groupByCat(items).map(g => `<div class="result-group"><div class="group-label">${escapeHtml(g.cat)} (${g.items.length})</div>${g.items.map(it => rowHtml(it, false)).join('')}</div>`).join('');
  }
  box.querySelectorAll('.result-item').forEach(el => el.addEventListener('click', () => showEntry(Number(el.dataset.id))));
  applyShownHighlight();
  if (tab === 'drug') fitDrugResultsHeight();
}

async function runNameSearch(tab, q, quiet) {
  const seq = ++requestSeq;
  const res = await fetch(`${KP}/api/search?tab=${tab}&q=${encodeURIComponent(q)}`);
  const data = await res.json();
  if (seq === requestSeq) {
    renderNameResults(tab, data.items, q);
    setQuery(tab, q);
    if (!quiet && data.items.length === 1 && !navRestoring) showEntry(data.items[0].id, { replace: true });
  }
  return data;
}

function textRowHtml(it) {
  return `
    <div class="result-item text-hit" data-id="${it.id}">
      <span class="rname">${it.no && it.kind === 'ref' ? `<span class="rno">${escapeHtml(it.no)}.</span>` : ''}${escapeHtml(it.ko)}${sourceBadge(it)}<span class="rcount">${it.count ? it.count + '곳' : '이름'}</span></span>
      ${it.snippet ? `<span class="rsnip">${it.snippet}</span>` : ''}
    </div>`;
}

// 본문 검색 결과: [별표 1] 통칙, [별표 2] 제제총칙, ... hwpx 파일 순서대로 카드를 나누고, 카드 안에서는 문서에 나오는 순서대로 보인다.
// 파일마다 처음 100개만 보이고 "더 보기"로 이어서 받는다.
function renderTextResults(data, q) {
  const box = textResultsBox;
  box.style.display = 'flex';
  const groups = data.groups || [];
  if (!groups.length) {
    box.innerHTML = q.replace(/\s+/g, '').length < 2
      ? `<div class="empty-msg">2글자 이상 입력하세요.</div>`
      : `<div class="empty-msg">"${escapeHtml(q)}"이(가) 들어 있는 항목이 없습니다.</div>`;
    return;
  }
  const total = groups.reduce((s, g) => s + g.total, 0);
  box.innerHTML = `<div class="text-summary">"${escapeHtml(q)}" 검색 결과 ${total}항목</div>` + groups.map(g => `
    <div class="result-group" data-doc="${g.doc}" data-shown="${g.items.length}" data-total="${g.total}">
      <div class="group-label">${escapeHtml(g.label)} (${g.total})</div>
      ${g.items.map(textRowHtml).join('')}
      ${g.total > g.items.length ? `<button type="button" class="text-more">더 보기 (남은 ${g.total - g.items.length}개)</button>` : ''}
    </div>`).join('');
  box.dataset.q = q;
  applyShownHighlight();
}
textResultsBox.addEventListener('click', async (ev) => {
  const q = textResultsBox.dataset.q || '';
  const more = ev.target.closest('.text-more');
  if (more) {
    const group = more.closest('.result-group');
    more.disabled = true;
    const shown = Number(group.dataset.shown);
    const res = await fetch(`${KP}/api/text?q=${encodeURIComponent(q)}&doc=${group.dataset.doc}&offset=${shown}`);
    const data = await res.json();
    more.insertAdjacentHTML('beforebegin', data.items.map(textRowHtml).join(''));
    group.dataset.shown = shown + data.items.length;
    const left = Number(group.dataset.total) - Number(group.dataset.shown);
    if (left > 0) { more.disabled = false; more.textContent = `더 보기 (남은 ${left}개)`; } else more.remove();
    applyShownHighlight();
    return;
  }
  const row = ev.target.closest('.result-item');
  if (row) showEntry(Number(row.dataset.id), { hl: q });
});
async function runTextSearch(q) {
  const seq = ++requestSeq;
  const res = await fetch(`${KP}/api/text?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  if (seq === requestSeq) {
    renderTextResults(data, q);
    setQuery('text', q);
  }
  return data;
}

// 검색칸: 입력 중에는 200ms 뒤 검색(본문 검색은 400ms), Enter 는 바로 검색해 첫 결과를 연다.
function dismissKeyboard(input) {
  if (window.matchMedia('(pointer: coarse)').matches) input.blur();
}
const timers = {};
function setupSearchInput(tab) {
  const input = INPUTS[tab];
  const c = combos[tab];
  let selectAllOnMouseUp = false, wasFocused = false;
  input.addEventListener('mousedown', () => {
    selectAllOnMouseUp = !!input.value && (searchDone[tab] || document.activeElement !== input);
    wasFocused = document.activeElement === input;
  });
  input.addEventListener('mouseup', (ev) => {
    if (!selectAllOnMouseUp) return;
    selectAllOnMouseUp = false;
    ev.preventDefault();
    input.select();
  });
  if (c) {
    input.addEventListener('click', () => {
      if (c.dropdown.style.display === 'block') closeDropdown(c);
      else if (wasFocused) openDropdown(c);
    });
  }
  input.addEventListener('input', () => {
    searchDone[tab] = false;
    clearTimeout(timers[tab]);
    if (c) closeDropdown(c);  // 검색어를 치면 펼쳐 둔 전체 목록은 닫고 검색 결과를 보여 준다
    const q = input.value.trim();
    if (!q) {
      clearResults(tab);
      setQuery(tab, '');
      refreshQuickBox();
      return;
    }
    if (tab === 'text' && q.replace(/\s+/g, '').length < 2) { clearResults(tab); return; }
    timers[tab] = setTimeout(() => (tab === 'text' ? runTextSearch(q) : runNameSearch(tab, q)), tab === 'text' ? 400 : 200);
    refreshQuickBox();
  });
  input.addEventListener('keydown', async (ev) => {
    if (ev.key !== 'Enter') return;
    ev.preventDefault();
    clearTimeout(timers[tab]);
    const q = input.value.trim();
    if (!q) return;
    if (c) closeDropdown(c);
    dismissKeyboard(input);
    if (tab === 'text') {
      const data = await runTextSearch(q);
      searchDone[tab] = true;
      const first = data.groups && data.groups[0] && data.groups[0].items[0];
      if (first) showEntry(first.id, { hl: q });
    } else {
      const data = await runNameSearch(tab, q, true);
      searchDone[tab] = true;
      if (data.items[0]) showEntry(data.items[0].id);
    }
  });
}
['drug', 'text'].forEach(setupSearchInput);  // 통칙·제제·시험법·정보 탭(ref)은 글자를 치지 않고 목록에서 고른다

// ---- 목록 방향키 이동 (생약 검색과 같다) ---------------------------------------------------------------
const pointerPos = { x: -1, y: -1 };
document.addEventListener('mousemove', (ev) => { pointerPos.x = ev.clientX; pointerPos.y = ev.clientY; });
function releaseToggleFocus() {
  const el = document.activeElement;
  if (el && el.classList && el.classList.contains('combo-toggle')) el.blur();
}
function setupDropdownKeys(c) {
  const { dropdown, input } = c;
  const rows = () => [...dropdown.querySelectorAll('.result-item')].filter(el => el.offsetParent !== null);  // 접혀 있는 줄은 건너뛴다
  const clearActive = () => dropdown.querySelectorAll('.result-item.kb-active').forEach(el => el.classList.remove('kb-active'));
  const setActive = (el) => {
    clearActive();
    el.classList.add('kb-active');
    el.scrollIntoView({ block: 'nearest' });
  };
  document.addEventListener('keydown', (ev) => {
    if (dropdown.style.display !== 'block') {
      if (ev.key === 'ArrowDown' && document.activeElement === input) {
        ev.preventDefault();
        openDropdown(c);
      }
      return;
    }
    if (ev.key === 'Escape') {
      ev.preventDefault();
      closeDropdown(c);
      return;
    }
    const list = rows();
    if (!list.length) return;
    const cur = list.findIndex(el => el.classList.contains('kb-active'));
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      const next = ev.key === 'ArrowDown' ? Math.min(cur + 1, list.length - 1) : Math.max(cur - 1, 0);
      setActive(list[cur < 0 && ev.key === 'ArrowUp' ? 0 : next]);
    } else if (ev.key === 'Enter' && cur >= 0) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      list[cur].click();
      releaseToggleFocus();
    }
  }, true);
  dropdown.addEventListener('click', releaseToggleFocus);
  dropdown.addEventListener('mousemove', (ev) => {
    if (ev.clientX === pointerPos.x && ev.clientY === pointerPos.y) return;
    const row = ev.target.closest('.result-item');
    if (row && !row.classList.contains('kb-active')) setActive(row);
  });
}
Object.values(combos).forEach(setupDropdownKeys);

// ---- 최근 본 항목 --------------------------------------------------------------------------------
const RECENT_KEY = 'kpRecent', RECENT_MAX = 15;
let recentMem = [];
function loadRecent() {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch (e) { return recentMem; }
}
function saveRecent(list) {
  recentMem = list;
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch (e) { /* 저장소를 못 쓰면 이 페이지가 열려 있는 동안만 기억 */ }
}
function recordRecent(it) {
  const snap = { id: it.id, ko: it.ko, en: it.en || '', no: it.no || '', kind: it.kind, part: it.part || '', cat: it.cat || '', doc: it.doc || 0, group: it.group || '' };
  saveRecent([snap, ...loadRecent().filter(x => x.id !== it.id)].slice(0, RECENT_MAX));
  refreshQuickBox();
}
function refreshQuickBox() {
  const list = loadRecent();
  const hasQuery = drugResultsBox.style.display !== 'none' && drugResultsBox.innerHTML;
  const onDrugTab = document.getElementById('drugTab').classList.contains('active');
  if (!list.length || !onDrugTab || hasQuery) {
    quickBox.hidden = true;
    quickBox.innerHTML = '';
    return;
  }
  quickBox.hidden = false;
  quickBox.innerHTML = `<div class="result-group"><div class="group-label"><span>최근 본 항목</span><button type="button" class="quick-clear">모두 지우기</button></div>${list.map(it => `
    <div class="result-item" data-id="${it.id}"><span class="rname">${it.no && it.kind === 'ref' ? `<span class="rno">${escapeHtml(it.no)}.</span>` : ''}${escapeHtml(it.ko)}${sourceBadge(it)}${groupLineHtml(it)}</span><button type="button" class="quick-del" title="목록에서 지우기" aria-label="목록에서 지우기"><svg viewBox="0 0 10 10" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M1.5 1.5 8.5 8.5M8.5 1.5 1.5 8.5"/></svg></button></div>`).join('')}</div>`;
  applyShownHighlight();
}
quickBox.addEventListener('click', (ev) => {
  if (ev.target.closest('.quick-clear')) {
    saveRecent([]);
    refreshQuickBox();
    return;
  }
  const row = ev.target.closest('.result-item');
  if (!row) return;
  if (ev.target.closest('.quick-del')) {
    saveRecent(loadRecent().filter(x => String(x.id) !== row.dataset.id));
    refreshQuickBox();
    return;
  }
  openRecent(row.dataset.id);
});
// 최근 본 항목은 번호(id)와 이름을 같이 저장해 둔다. 데이터 파일이나 파서가 바뀌어 번호가 달라졌으면 이름(과 문서)으로 다시 찾아서 연다.
async function openRecent(id) {
  const saved = loadRecent();
  const rec = saved.find(x => String(x.id) === String(id));
  if (!rec) return;
  const list = await getList(rec.kind === 'drug' ? 'drug' : 'ref');
  let it = list.find(x => x.id === rec.id && x.ko === rec.ko && (x.cat || '') === (rec.cat || ''));
  if (!it) it = list.find(x => x.ko === rec.ko && (x.cat || '') === (rec.cat || '') && (x.no || '') === (rec.no || ''));
  if (!it) {  // 더 이상 없는 항목은 목록에서 뺀다
    saveRecent(saved.filter(x => x !== rec));
    refreshQuickBox();
    return;
  }
  if (it.id !== rec.id) saveRecent(saved.map(x => (x === rec ? { ...x, id: it.id } : x)));
  showEntry(it.id);
}
new MutationObserver(refreshQuickBox).observe(drugResultsBox, { attributes: true, attributeFilter: ['style'] });

// ---- 설명창 ------------------------------------------------------------------------------------
const DETAIL_ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.2, 1.35, 1.5];
let detailZoomIdx = DETAIL_ZOOM_STEPS.indexOf(1);
try {
  const savedZoom = DETAIL_ZOOM_STEPS.indexOf(parseFloat(localStorage.getItem('detailZoom')));
  if (savedZoom >= 0) detailZoomIdx = savedZoom;
} catch (e) { /* 기본 크기 */ }
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
detailBox.addEventListener('dblclick', (e) => {
  if (!e.target.closest('.detail-zoom-pct')) return;
  const base = DETAIL_ZOOM_STEPS.indexOf(1);
  if (detailZoomIdx === base) return;
  detailZoomIdx = base;
  try { localStorage.setItem('detailZoom', '1'); } catch (err) { /* 저장 실패는 무시 */ }
  refreshDetailZoomUi();
  window.getSelection && window.getSelection().removeAllRanges();
});
detailBox.addEventListener('click', (e) => {
  const btn = e.target.closest('.detail-zoom-btn');
  if (!btn || btn.disabled) return;
  detailZoomIdx = Math.min(DETAIL_ZOOM_STEPS.length - 1, Math.max(0, detailZoomIdx + Number(btn.dataset.dir)));
  try { localStorage.setItem('detailZoom', String(DETAIL_ZOOM_STEPS[detailZoomIdx])); } catch (err) { /* 저장 실패는 무시 */ }
  refreshDetailZoomUi();
});

// 그림을 못 불러오면(원본 파일이 비어 있는 그림 등) 깨진 그림 아이콘 대신 안내 문구를 보인다.
detailBox.addEventListener('error', (ev) => {
  const img = ev.target;
  if (!img || img.tagName !== 'IMG' || !img.classList.contains('kp-img')) return;
  const note = document.createElement('span');
  note.className = 'kp-cap kp-missing';
  note.textContent = '[그림을 표시하지 못했습니다]';
  img.replaceWith(note);
}, true);

// 구간 머리(항목명 배지 + 네모 +/-)를 누르면 그 구간만 접고 편다.
detailBox.addEventListener('click', (e) => {
  const header = e.target.closest('.section > .section-header');
  if (!header) return;
  const section = header.parentElement;
  const icon = header.querySelector('.toggle-icon');
  section.classList.toggle('collapsed');
  if (icon) icon.textContent = section.classList.contains('collapsed') ? '+' : '−';
});
// '← 돌아가기': 링크를 누르기 전 항목으로 돌아간다(링크를 누를 때 history 에 쌓였으므로 브라우저 뒤로 가기와 같고, 앞으로 가기도 된다)
detailBox.addEventListener('click', (e) => {
  if (!e.target.closest('.detail-back-btn')) return;
  e.preventDefault();
  history.back();
});
// 본문 안의 일반시험법/품목 링크
detailBox.addEventListener('click', (e) => {
  const link = e.target.closest('.ref-link');
  if (!link) return;
  e.preventDefault();
  // 본문의 링크로 연 항목에는 이름 앞에 "돌아가기" 버튼이 붙는다(누르면 링크를 누르기 전 항목으로 돌아간다)
  showEntry(Number(link.dataset.id), { back: true, backName: detailBox.dataset.itemName || '' });
});

const entryCache = new Map();
function fetchEntry(id) {
  if (entryCache.has(id)) return entryCache.get(id);
  const p = fetch(`${KP}/api/entry/${id}`).then(res => (res.ok ? res.text() : null)).catch(() => null);
  entryCache.set(id, p);
  if (entryCache.size > 30) entryCache.delete(entryCache.keys().next().value);
  p.then(t => { if (t === null) entryCache.delete(id); });
  return p;
}
// 목록 줄에 마우스를 잠깐 올리면 그 항목을 미리 받아 둔다
let prefetchTimer = null;
document.addEventListener('mouseover', (ev) => {
  clearTimeout(prefetchTimer);
  const row = ev.target.closest ? ev.target.closest('.result-item[data-id]') : null;
  if (row) prefetchTimer = setTimeout(() => fetchEntry(Number(row.dataset.id)), 60);
});
document.addEventListener('touchstart', (ev) => {
  const row = ev.target.closest ? ev.target.closest('.result-item[data-id]') : null;
  if (row) fetchEntry(Number(row.dataset.id));
}, { passive: true });

// 본문 검색으로 열었을 때 검색어를 노란색으로 표시한다(공백이 달라도 찾는다).
function highlightIn(root, query) {
  const chars = [...query].filter(c => !/\s/.test(c));
  if (chars.length < 2) return null;
  const rx = new RegExp(chars.map(c => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s*'), 'gi');
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  let first = null;
  nodes.forEach(node => {
    const text = node.nodeValue;
    rx.lastIndex = 0;
    let m, last = 0, frag = null;
    while ((m = rx.exec(text)) && m[0]) {
      frag = frag || document.createDocumentFragment();
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      const mark = document.createElement('mark');
      mark.textContent = m[0];
      frag.appendChild(mark);
      first = first || mark;
      last = m.index + m[0].length;
    }
    if (frag) {
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      node.parentNode.replaceChild(frag, node);
    }
  });
  return first;
}

function showDetailBox() {
  detailBox.classList.add('show');
  detailPlaceholder.classList.add('hide');
  positionDetailResizer();
}

async function showEntry(id, opts = {}) {
  const text = await fetchEntry(id);
  if (text === null) return;
  const it = JSON.parse(text);
  setShownKey(`id:${it.id}`);
  // 링크로 연 경우(opts.back)에는 상태에도 기록해 두어, 뒤로/앞으로 가기로 이 화면이 복원돼도 '돌아가기' 버튼이 그대로 보이게 한다
  const back = opts.back ? { back: true, backName: opts.backName || '' } : {};
  navSet({ view: { id: it.id, hl: opts.hl || '', ...back } }, opts.replace ? 'replace' : 'push');
  const title = `${it.no && it.kind === 'ref' ? it.no + '. ' : ''}${it.ko}`;
  detailBox.dataset.itemId = it.id;
  detailBox.dataset.itemName = title;
  const backTitle = opts.backName ? `${escapeHtml(opts.backName)}(으)로 돌아가기` : '돌아가기';
  const backHtml = opts.back
    ? `<button type="button" class="detail-back-btn" title="${backTitle}" aria-label="${backTitle}"><svg viewBox="0 0 12 8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.2 4H10.8M4.8 .9 1.2 4 4.8 7.1"/></svg></button>`
    : '';
  detailBox.innerHTML = `
    <div class="detail-header">
      <div class="detail-title-row">
        <div class="detail-name-line">
          ${backHtml}
          <h2>${escapeHtml(title)}${detailPartHtml(it)}<span class="detail-zoom" role="group" aria-label="글자 크기"><button type="button" class="detail-zoom-btn" data-dir="-1" aria-label="글자 작게">가−</button><span class="detail-zoom-pct" aria-live="polite">100%</span><button type="button" class="detail-zoom-btn" data-dir="1" aria-label="글자 크게">가+</button></span></h2>
        </div>
      </div>
      ${it.en ? `<p class="en">${escapeHtml(it.en)}</p>` : ''}
    </div>
    <div class="kp-body">${it.html || '<p class="kp-p">(내용이 없습니다)</p>'}</div>
  `;
  refreshDetailZoomUi();
  showDetailBox();
  let target = null;
  if (opts.hl) target = highlightIn(detailBox.querySelector('.kp-body'), opts.hl);
  if (window.matchMedia('(max-width: 860px)').matches) {
    document.getElementById('detailWrap').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  if (target) setTimeout(() => target.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60);
  recordRecent(it);
}

window.addEventListener('load', () => { fitAll(); focusTabInput('drugTab'); });
refreshQuickBox();
