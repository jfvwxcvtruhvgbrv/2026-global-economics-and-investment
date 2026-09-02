"""
archive/{날짜-세션}.json (스키마: global_picture + issues)을 읽어
Global Economic & Investment Intelligence 스타일 정적 HTML 사이트를
생성한다.

- 각 세션별 페이지: site/{date}-{session}.html
- index.html: 세션별 아카이브 목록 (site/index.html)

build_site()의 시그니처와 OUTPUT_DIR/ARCHIVE_DIR 상수는 다른 파일과
호환되도록 유지한다 — run_pipeline.py 등 다른 파일은 수정할 필요가
없다.
"""
import json
import glob
import os
from urllib.parse import quote

OUTPUT_DIR = "site"
ARCHIVE_DIR = "archive"

# ── 추세 상태 → 한글 라벨 / 색 / 게이지 단계 ─────────────────────────
TREND_LABEL = {
    "Accelerating": "가속 국면",
    "Developing":   "전개 중",
    "Emerging":     "신규 신호",
    "Structural":   "구조적 흐름",
    "Cooling":      "진정 국면",
}
TREND_COLOR = {
    "Accelerating": "#B43B2E",
    "Developing":   "#2C5C87",
    "Emerging":     "#A6790F",
    "Structural":   "#5A6270",
    "Cooling":      "#3F7A5D",
}
TREND_STEPS = {
    "Accelerating": 4,
    "Developing":   3,
    "Emerging":     2,
    "Structural":   2,
    "Cooling":      1,
}
DEFAULT_TREND_COLOR = "#5A6270"

# ── 다중 관점(perspectives)의 frame 이름 → 색 (키워드 매칭) ─────────
LENS_KEYWORDS = [
    ("macro",      "#1D6B60"),  # Macro View
    ("market",     "#8F6A17"),  # Markets View
    ("polic",      "#2C5C87"),  # Policy View
    ("corporate",  "#7E3040"),  # Corporate View
    ("geopolit",   "#454E5B"),  # Geopolitical View
]
DEFAULT_LENS_COLOR = "#4B515C"

# ── 자산군(asset_classes) → 색 (정확히 5개 표준 라벨과 매칭) ────────
ASSET_CLASS_ORDER = ["주식", "채권·금리", "원자재", "암호자산", "외환"]
ASSET_CLASS_COLOR = {
    "주식":     "#2C5C87",
    "채권·금리": "#5A6270",
    "원자재":   "#8F6A17",
    "암호자산": "#7E3040",
    "외환":     "#1D6B60",
}
DEFAULT_ASSET_COLOR = "#4B515C"
NO_SIGNAL_MARKERS = ("특별한", "신호 없음", "없음")

# ── 마켓 픽처 보드 컬럼 정의 ──────────────────────────────────────
BOARD_COLUMNS = [
    ("world_right_now",     "지금 시장에서",         "#12151C"),
    ("emerging_signals",    "새롭게 감지된 신호",     "#A6790F"),
    ("structural_trends",   "지속되는 구조적 흐름",   "#5A6270"),
    ("what_changed_today",  "직전 세션 대비 달라진 점", "#2C5C87"),
    ("what_to_watch_next",  "다음에 확인할 것",       "#B43B2E"),
]

INSIGHT_LABELS = [
    ("what_happened",   "무슨 일이"),
    ("why_it_matters",  "왜 중요한가"),
    ("what_is_changing","무엇이 달라지는가"),
    ("connection",      "연결점"),
    ("signal",          "시그널"),
    ("what_to_watch",   "지켜볼 것"),
]

WD = ["월", "화", "수", "목", "금", "토", "일"]
SESSION_LABEL = {"am": "오전", "pm": "오후"}


def _pretty_date(date_str: str) -> str:
    import datetime as dt
    try:
        d = dt.date.fromisoformat(date_str)
        return f"{d.year}년 {d.month}월 {d.day}일 {WD[d.weekday()]}요일"
    except Exception:
        return date_str


def _lens_color(frame: str) -> str:
    f = (frame or "").lower()
    for kw, color in LENS_KEYWORDS:
        if kw in f:
            return color
    return DEFAULT_LENS_COLOR


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── 렌더 함수들 ──────────────────────────────────────────────────────

def _asset_color(asset_class: str) -> str:
    return ASSET_CLASS_COLOR.get(asset_class, DEFAULT_ASSET_COLOR)


def _fmt_value(v) -> str:
    if v is None:
        return "-"
    av = abs(v)
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 10:
        return f"{v:,.1f}"
    return f"{v:,.4f}".rstrip("0").rstrip(".")


def _sparkline_svg(values: list, width: int = 108, height: int = 30, color: str = "#2C5C87") -> str:
    """외부 차트 라이브러리 없이 순수 SVG로 그리는 작은 추세선.

    정적 HTML 파일 하나로 끝나야 하는 이 사이트의 특성상(별도 JS/CDN
    없이 GitHub Pages에 그대로 올라감), matplotlib이나 차트 JS 라이브러리
    대신 직접 폴리라인 좌표를 계산해서 인라인 SVG로 심는다.
    """
    if not values or len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or (abs(hi) or 1)
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i / (n - 1) * (width - 4) + 2
        y = height - 2 - (v - lo) / span * (height - 4)
        pts.append((x, y))
    points_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    last_x, last_y = pts[-1]
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'class="spark" preserveAspectRatio="none">'
        f'<polyline points="{points_attr}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.1" fill="{color}"/>'
        f'</svg>'
    )


def render_instrument(inst: dict, color: str, compact: bool = False) -> str:
    """실제 시세 한 종목(지수/금리/원자재/코인/환율)의 값+추세선 한 줄."""
    change = inst.get("change_pct")
    if change is None:
        change_html = ""
    else:
        up = change >= 0
        arrow = "▲" if up else "▼"
        change_color = "#3F7A5D" if up else "#B43B2E"
        change_html = f'<span class="chg" style="color:{change_color}">{arrow} {abs(change):.2f}%</span>'
    cls = "instrument compact" if compact else "instrument"
    return (
        f'<div class="{cls}">'
        f'<div class="inst-top"><span class="inst-label">{_esc(inst.get("label",""))}</span>{change_html}</div>'
        f'<div class="inst-bottom">{_sparkline_svg(inst.get("series", []), color=color)}'
        f'<span class="inst-val">{_esc(_fmt_value(inst.get("latest")))}</span></div>'
        f'</div>'
    )


def render_issue_charts(asset_classes: list, market_snapshot: dict) -> str:
    """이슈 카드에 딸린 자산군의 실제 시세를 1개씩만 짧게 보여준다."""
    if not asset_classes or not market_snapshot:
        return ""
    rows = []
    for ac in asset_classes[:2]:  # 너무 많아지지 않도록 최대 2개 자산군만
        insts = market_snapshot.get(ac) or []
        if insts:
            rows.append(render_instrument(insts[0], _asset_color(ac), compact=True))
    if not rows:
        return ""
    return f'<div class="issue-charts">{"".join(rows)}</div>'


FG_COLOR_STOPS = [
    (25, "#B43B2E"),   # Extreme Fear
    (45, "#C9822F"),   # Fear
    (55, "#A6790F"),   # Neutral
    (75, "#5B8A4A"),   # Greed
    (101, "#3F7A5D"),  # Extreme Greed
]


def _fg_color(value: int) -> str:
    for threshold, color in FG_COLOR_STOPS:
        if value <= threshold:
            return color
    return FG_COLOR_STOPS[-1][1]


def render_fear_greed(fg: dict | None) -> str:
    """비트코인/암호자산 공포·탐욕 지수를 작은 반원 게이지(SVG)로 그린다.

    market_data.py의 fetch_fear_greed()가 실패해서 fg가 None이면 그냥
    빈 문자열을 반환해 이 섹션이 조용히 빠지도록 한다.
    """
    if not fg or fg.get("value") is None:
        return ""
    value = max(0, min(100, fg["value"]))
    color = _fg_color(value)
    angle = (value / 100) * 180 - 90  # -90(공포) ~ +90(탐욕)
    import math
    rad = math.radians(angle)
    nx = 50 + 34 * math.sin(rad)
    ny = 50 - 34 * math.cos(rad)
    prev = fg.get("prev_value")
    diff_html = ""
    if prev is not None:
        diff = value - prev
        sign = "+" if diff > 0 else ""
        diff_color = "#3F7A5D" if diff > 0 else ("#B43B2E" if diff < 0 else DEFAULT_ASSET_COLOR)
        diff_html = f'<span style="color:{diff_color}">전일 {prev} ({sign}{diff})</span>'
    return f"""
    <div class="fg-gauge">
      <svg viewBox="0 0 100 58" width="100" height="58">
        <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="var(--rule)" stroke-width="7" stroke-linecap="round"/>
        <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="{color}" stroke-width="7"
          stroke-linecap="round" stroke-dasharray="{value*1.2566:.1f} 400" opacity=".85"/>
        <line x1="50" y1="50" x2="{nx:.1f}" y2="{ny:.1f}" stroke="var(--ink)" stroke-width="2" stroke-linecap="round"/>
        <circle cx="50" cy="50" r="2.6" fill="var(--ink)"/>
      </svg>
      <div class="fg-read"><b style="color:{color}">{value}</b><span>{_esc(fg.get('classification',''))}</span></div>
      <div class="fg-prev">{diff_html}</div>
    </div>"""


def render_asset_pulse(pulse: list, market_snapshot: dict | None = None, fear_greed: dict | None = None) -> str:
    """자산군(주식/채권·금리/원자재/암호자산/외환) 5개 요약 스트립.

    market_snapshot이 있으면(=market_data.py가 API 키/네트워크 문제 없이
    실제 시세를 가져온 경우) 텍스트 요약 아래에 대표 종목의 실제 값과
    스파크라인을 최대 2개까지 덧붙인다. 데이터가 없으면(FRED_API_KEY
    미설정 등) 텍스트 요약만 표시되고 깨지지 않는다. fear_greed가 있으면
    "암호자산" 카드에 공포·탐욕 게이지를 덧붙인다.
    """
    if not pulse:
        return ""
    market_snapshot = market_snapshot or {}
    by_class = {p.get("asset_class"): p.get("summary", "") for p in pulse}
    cards = []
    for name in ASSET_CLASS_ORDER:
        summary = by_class.get(name)
        if summary is None:
            continue
        color = _asset_color(name)
        is_quiet = any(m in summary for m in NO_SIGNAL_MARKERS)
        quiet_cls = " quiet" if is_quiet else ""
        instruments = (market_snapshot.get(name) or [])[:2]
        instruments_html = "".join(render_instrument(i, color) for i in instruments)
        fg_html = render_fear_greed(fear_greed) if name == "암호자산" else ""
        cards.append(
            f'<div class="pulse-card{quiet_cls}" style="border-top-color:{color}">'
            f'<div class="pulse-h"><i style="background:{color}"></i>{_esc(name)}</div>'
            f'<p>{_esc(summary)}</p>{instruments_html}{fg_html}</div>'
        )
    if not cards:
        return ""
    return f'<div class="pulse-strip">{"".join(cards)}</div>'


def _tv_url(widget: str, cfg: dict) -> str:
    """TradingView 공식 무료 임베드 위젯 URL을 만든다 (API 키 불필요).

    config JSON을 URL 프래그먼트로 인코딩해 별도 스크립트 태그 없이
    순수 <iframe> src만으로 위젯을 심을 수 있게 한다 — 위젯 내부의
    호버/탭 전환 등 인터랙션은 tradingview.com이 자체 처리하므로
    우리 정적 사이트 쪽에는 추가 JS가 전혀 필요 없다.
    """
    payload = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
    return f"https://www.tradingview.com/embed-widget/{widget}/?locale=kr#{quote(payload)}"


# 사이트 팔레트(라이트 테마)에 맞춘 TradingView 위젯 공통 설정
TV_BASE_CFG = {
    "colorTheme": "light",
    "isTransparent": True,
    "locale": "kr",
}

# (자산군, TradingView 심볼, 한글 라벨) — index.html에만 노출되는
# 실시간 미니 시세 16종. 자산군 5개 축을 모두 커버한다. (기존 12종에서
# 나스닥100/다우존스/항셍/중국 ETF를 추가해 "주요 지수" 커버리지를
# 사용자가 참고로 준 옛 대시보드 수준까지 넓혔다.)
TV_MINI_SYMBOLS = [
    ("주식",      "FOREXCOM:SPXUSD", "S&P 500"),
    ("주식",      "FOREXCOM:NSXUSD", "나스닥 100"),
    ("주식",      "FOREXCOM:DJI",    "다우존스"),
    ("주식",      "INDEX:NKY",       "닛케이 225"),
    ("주식",      "CAPITALCOM:DE40", "DAX"),
    ("주식",      "TVC:HSI",         "항셍지수"),
    ("주식",      "NASDAQ:MCHI",     "중국 ETF"),
    ("주식",      "AMEX:EWY",        "MSCI 한국 ETF"),
    ("채권·금리", "TVC:US10Y",       "미 10년물 금리"),
    ("채권·금리", "AMEX:TLT",        "20년 국채 ETF"),
    ("원자재",    "TVC:GOLD",        "금"),
    ("원자재",    "TVC:USOIL",       "WTI 원유"),
    ("암호자산",  "BINANCE:BTCUSDT", "비트코인"),
    ("암호자산",  "BINANCE:ETHUSDT", "이더리움"),
    ("외환",      "FX:USDKRW",       "달러/원"),
    ("외환",      "TVC:DXY",         "달러 인덱스"),
]


def render_tv_mini_grid() -> str:
    """자산군별 실시간 미니 시세 위젯을 그리드로 배치한다.

    TradingView mini-symbol-overview 위젯을 그대로 iframe으로 심는다.
    실시간 갱신은 TradingView 쪽에서 처리하고, 이 정적 사이트는 URL만
    미리 계산해두면 된다 — 이 위젯들은 아카이브 페이지가 아니라
    index.html(지금 이 순간의 시세)에만 노출한다.
    """
    cells = []
    for asset_class, symbol, label in TV_MINI_SYMBOLS:
        cfg = dict(TV_BASE_CFG, symbol=symbol, dateRange="12M", width="100%", height="100%")
        src = _tv_url("mini-symbol-overview", cfg)
        color = _asset_color(asset_class)
        cells.append(
            f'<div class="tv-cell">'
            f'<div class="tv-cell-label" style="color:{color}">{_esc(label)}</div>'
            f'<iframe src="{src}" style="width:100%;height:110px;border:none" loading="lazy" '
            f'title="{_esc(label)}"></iframe></div>'
        )
    return f'<div class="tv-mini-grid">{"".join(cells)}</div>'


# "주요 지수 / 선물·원자재 / ETF" 탭(고정 market-overview 위젯)은
# 없앴다 — 이 세 자산군은 TradingView 무료 위젯 중 실시간 순위가
# 바뀌는 스크리너를 공식 지원하지 않아서, 확실히 동적인 것만
# (미니 그리드의 실시간 시세 16종 + 아래 외환/암호자산 스크리너)
# 남기기로 사용자와 합의했다.


def render_tv_forex_screener() -> str:
    """외환(통화) 실시간 스크리너 — TradingView 공식 무료 Screener 위젯.

    market-overview(고정 탭)와 달리 이 위젯은 실제로 TradingView가
    매 순간 살아있는 시세로 종목을 나열/정렬하는 위젯이라, 우리가
    "어떤 통화쌍을 보여줄지" 하드코딩하지 않아도 된다 — 사용자가
    요청한 "고정된 상품이 아니라 시장에 따라 동적으로" 조건을 외환은
    이 위젯으로 충족한다.
    """
    cfg = dict(
        TV_BASE_CFG,
        width="100%",
        height="100%",
        market="forex",
        showToolbar=True,
        defaultColumn="overview",
        defaultScreen="general",
    )
    src = _tv_url("screener", cfg)
    return (
        f'<div class="tv-screener">'
        f'<div class="tv-cell-label">외환·통화 (실시간, TradingView 자체 정렬)</div>'
        f'<iframe src="{src}" style="width:100%;height:400px;border:none" loading="lazy" '
        f'title="Forex Screener"></iframe></div>'
    )


def render_tv_crypto_heatmap() -> str:
    """암호자산 시가총액 히트맵 — TradingView 공식 무료 위젯.

    옛 개인 대시보드의 "가상자산" 화면(트리맵 + 코인 테이블)을
    참고했다. 히트맵은 TradingView의 공식 crypto-coins-heatmap
    위젯을 그대로 쓴다 — 색상/크기(시총) 계산 전부 TradingView가
    실시간으로 처리한다.
    """
    cfg = dict(
        TV_BASE_CFG,
        dataSource="Crypto",
        blockSize="market_cap_calc",
        blockColor="change",
        width="100%",
        height="100%",
        hasTopBar=True,
        isDataSetEnabled=True,
        isZoomEnabled=True,
        hasSymbolTooltip=True,
        isMonoSize=False,
    )
    src = _tv_url("crypto-coins-heatmap", cfg)
    return (
        f'<div class="tv-heatmap">'
        f'<iframe src="{src}" style="width:100%;height:100%;border:none" loading="lazy" '
        f'title="Crypto Heatmap"></iframe></div>'
    )


def render_tv_crypto_screener() -> str:
    """암호자산 실시간 순위 테이블 — TradingView 공식 무료 Screener 위젯.

    옛 대시보드의 코인 테이블(이름/가격/1h·24h·7d%/시총/거래량/차트)과
    같은 역할. 목록과 정렬 모두 TradingView가 실시간으로 계산한다.
    """
    cfg = dict(
        TV_BASE_CFG,
        width="100%",
        height="100%",
        market="crypto",
        showToolbar=True,
        defaultColumn="overview",
        defaultScreen="general",
    )
    src = _tv_url("screener", cfg)
    return (
        f'<div class="tv-screener">'
        f'<iframe src="{src}" style="width:100%;height:100%;border:none" loading="lazy" '
        f'title="Crypto Screener"></iframe></div>'
    )


def render_btc_dominance() -> str:
    """비트코인 도미넌스(전체 암호자산 시총 대비 BTC 비중) 미니 차트.

    옛 대시보드의 "BTC 도미넌스" 게이지를 값+추세선 형태로 대체했다.
    TradingView의 CRYPTOCAP:BTC.D 심볼은 순수 수치(비중 %)라
    매수/매도 신호 요소가 전혀 없다 — 아래 BTC 레인보우 밴드와 달리
    컴플라이언스 문제가 없어서 그대로 채택했다.
    """
    cfg = dict(TV_BASE_CFG, symbol="CRYPTOCAP:BTC.D", dateRange="12M", width="100%", height="100%")
    src = _tv_url("mini-symbol-overview", cfg)
    color = _asset_color("암호자산")
    return (
        f'<div class="tv-cell">'
        f'<div class="tv-cell-label" style="color:{color}">비트코인 도미넌스</div>'
        f'<iframe src="{src}" style="width:100%;height:110px;border:none" loading="lazy" '
        f'title="비트코인 도미넌스"></iframe></div>'
    )


def render_crypto_dashboard(fear_greed: dict | None = None) -> str:
    """암호자산 실시간 현황 섹션 (히트맵 + 순위 테이블 + 도미넌스 + 공포·탐욕).

    옛 개인 대시보드의 "가상자산" 화면을 참고해 만들었다. 다만 그
    화면에 있던 "BTC 레인보우 밴드"(Fire Sale!/BUY!/Hold!/SELL
    Seriously! 같은 매수·매도 레이블)는 이 사이트의 무권유 원칙과
    정면으로 충돌해서 의도적으로 제외했다 — 대신 매수/매도 해석이
    섞이지 않는 수치형 지표(도미넌스, 공포·탐욕 지수)만 담았다.
    fear_greed는 가장 최근 세션 아카이브의 값을 재사용한다(별도
    API 호출 없음).
    """
    fg_html = render_fear_greed(fear_greed) if fear_greed else ""
    fg_cell = (
        f'<div class="tv-cell fg-cell"><div class="tv-cell-label">비트코인 공포·탐욕 지수</div>{fg_html}</div>'
        if fg_html else ""
    )
    return f"""
    <div class="tv-crypto-dash">
      <div class="tv-crypto-grid">
        {render_tv_crypto_screener()}
        {render_tv_crypto_heatmap()}
      </div>
      <div class="tv-crypto-stats">
        {render_btc_dominance()}
        {fg_cell}
      </div>
    </div>"""


def render_asset_tags(asset_classes: list) -> str:
    if not asset_classes:
        return ""
    tags = "".join(
        f'<span class="tag" style="border-color:{_asset_color(a)};color:{_asset_color(a)}">{_esc(a)}</span>'
        for a in asset_classes
    )
    return f'<div class="tags">{tags}</div>'


def render_global_picture(global_picture: dict) -> str:
    if not global_picture:
        return ""
    cols = []
    for key, label, color in BOARD_COLUMNS:
        items = global_picture.get(key) or []
        if not items:
            continue
        lis = "".join(
            f'<li><i style="background:{color}"></i><span>{_esc(t)}</span></li>'
            for t in items
        )
        cols.append(
            f'<div class="board-col"><h3><span class="dot" style="background:{color}"></span>{label}</h3>'
            f'<ul>{lis}</ul></div>'
        )
    if not cols:
        return ""
    return f'<div class="board"><div class="board-grid">{"".join(cols)}</div></div>'


def render_insight(insight: dict) -> str:
    if not insight:
        return ""
    items = []
    for key, label in INSIGHT_LABELS:
        val = insight.get(key)
        if not val:
            continue
        items.append(
            f'<div class="field"><b>{label}</b><p>{_esc(val)}</p></div>'
        )
    if not items:
        return ""
    return f'<div class="fields">{"".join(items)}</div>'


def render_perspectives(perspectives: list) -> str:
    if not perspectives:
        return ""
    boxes = []
    for p in perspectives:
        frame = p.get("frame", "")
        color = _lens_color(frame)
        boxes.append(
            f'<div class="lens" style="border-top-color:{color}">'
            f'<div class="lh"><i style="background:{color}"></i>{_esc(frame)}</div>'
            f'<p>{_esc(p.get("summary",""))}</p></div>'
        )
    return f'<div class="lenses">{"".join(boxes)}</div>'


def render_sources(sources: list) -> str:
    if not sources:
        return ""
    links = "".join(
        f'<a class="src" href="{_esc(s.get("link","#"))}" target="_blank" rel="noopener">'
        f'{_esc(s.get("source",""))}<small>({_esc(s.get("region",""))})</small></a>'
        for s in sources
    )
    return f'<div class="sources"><span class="lbl">출처</span>{links}</div>'


def render_issue(issue: dict, case_id: str, market_snapshot: dict | None = None) -> str:
    trend = issue.get("trend_status", "")
    label = TREND_LABEL.get(trend, trend or "이슈")
    color = TREND_COLOR.get(trend, DEFAULT_TREND_COLOR)
    steps = TREND_STEPS.get(trend, 2)
    gauge = "".join(
        f'<i style="background:{color}"></i>' if i < steps else '<i></i>'
        for i in range(4)
    )
    asset_classes = issue.get('asset_classes', [])
    charts_html = render_issue_charts(asset_classes, market_snapshot or {})
    return f"""
    <div class="case">
      <div class="case-head">
        <div>
          <div class="case-id">{case_id}</div>
          <div class="case-title">{_esc(issue.get('headline',''))}</div>
          {render_asset_tags(asset_classes)}
        </div>
        <div class="velocity">
          <div class="label" style="color:{color}">{label}</div>
          <div class="gauge">{gauge}</div>
        </div>
      </div>
      {charts_html}
      {render_insight(issue.get('insight', {}))}
      {render_perspectives(issue.get('perspectives', []))}
      {render_sources(issue.get('sources', []))}
    </div>"""


# ── 페이지 템플릿 (서체: Pretendard 하나로 통일, 좌우 여백 5vw) ──────

BASE_CSS = """
:root{
  --paper:#E9EBEA; --card:#F7F8F6;
  --ink:#12151C; --ink-soft:#4B515C; --rule:#C8CCC4;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);
  font-family:"Pretendard",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;
  font-size:19px;line-height:1.65;word-break:keep-all;-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{padding:0 5vw}
.back{display:inline-block;margin:26px 0 6px;font-size:16px;color:var(--ink-soft);
  text-decoration:none;border-bottom:1px solid var(--rule)}
.back:hover{border-color:var(--ink);color:var(--ink)}
.headline{font-weight:800;font-size:clamp(32px,5vw,52px);letter-spacing:-.01em;margin:10px 0 6px}
.metarow{display:flex;gap:20px;flex-wrap:wrap;margin:14px 0 30px;font-size:16px;color:var(--ink-soft)}
.metarow .m{display:flex;align-items:center;gap:7px}
.dot{width:9px;height:9px;border-radius:50%;flex:none}

.pulse-strip{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--rule);
  border:1px solid var(--rule);margin:0 0 28px}
.pulse-card{background:var(--card);padding:14px 16px 16px;border-top:4px solid transparent}
.pulse-card.quiet{opacity:.55}
.pulse-h{display:flex;align-items:center;gap:7px;font-size:14px;font-weight:700;margin-bottom:6px}
.pulse-h i{width:9px;height:9px;border-radius:50%;flex:none}
.pulse-card p{font-size:14.5px;color:var(--ink-soft);line-height:1.5}
@media (max-width:1100px){.pulse-strip{grid-template-columns:repeat(2,1fr)}}
@media (max-width:600px){.pulse-strip{grid-template-columns:1fr}}

.fg-gauge{margin-top:12px;padding-top:10px;border-top:1px solid var(--rule);
  display:flex;align-items:center;gap:10px}
.fg-gauge svg{flex:none}
.fg-read{display:flex;flex-direction:column;gap:1px;font-size:12.5px}
.fg-read b{font-size:19px;line-height:1}
.fg-prev{margin-left:auto;font-size:11.5px;color:var(--ink-soft);align-self:flex-end}

.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.tag{font-size:12.5px;font-weight:700;letter-spacing:.02em;padding:2px 9px;border:1px solid;border-radius:999px}

.instrument{margin-top:12px;padding-top:10px;border-top:1px solid var(--rule)}
.instrument:first-of-type{margin-top:12px}
.inst-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px;margin-bottom:5px}
.inst-label{color:var(--ink-soft)}
.chg{font-weight:700;font-size:12.5px;white-space:nowrap}
.inst-bottom{display:flex;align-items:center;justify-content:space-between;gap:10px}
.inst-val{font-size:13.5px;font-weight:700;white-space:nowrap}
.spark{flex:none;display:block}

.issue-charts{display:flex;gap:28px;flex-wrap:wrap;padding:16px 22px;border-bottom:1px solid var(--rule);background:var(--paper)}
.issue-charts .instrument.compact{margin:0;padding:0;border-top:none;min-width:170px}

.board{margin:0 0 40px}
.board-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--rule);border:1px solid var(--rule)}
.board-col{background:var(--card);padding:18px 18px 20px}
.board-col h3{font-size:14px;letter-spacing:.06em;color:var(--ink-soft);margin-bottom:12px;
  display:flex;align-items:center;gap:7px;font-weight:700}
.board-col ul{list-style:none}
.board-col li{display:flex;gap:8px;padding:8px 0;border-top:1px solid var(--rule);font-size:15.5px;line-height:1.5}
.board-col li:first-child{border-top:none}
.board-col li i{width:6px;height:6px;border-radius:50%;flex:none;margin-top:7px}
@media (max-width:1100px){.board-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:600px){.board-grid{grid-template-columns:1fr}}

.section-title{font-weight:800;font-size:23px;margin:36px 0 14px}

.case{background:var(--card);border:1px solid var(--rule);margin-bottom:24px}
.case-head{padding:20px 22px;border-bottom:1px solid var(--rule);
  display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.case-id{font-size:13px;color:var(--ink-soft);letter-spacing:.05em;margin-bottom:8px}
.case-title{font-weight:800;font-size:24px;line-height:1.4;max-width:760px}
.velocity{flex:none;display:flex;flex-direction:column;align-items:flex-end;gap:6px}
.velocity .label{font-size:14px;font-weight:700;letter-spacing:.03em}
.gauge{display:flex;gap:3px}
.gauge i{width:18px;height:6px;border-radius:1px;background:var(--rule)}

.fields{display:grid;grid-template-columns:repeat(2,1fr)}
.field{padding:18px 22px;border-right:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.field:nth-child(2n){border-right:none}
.field b{display:block;font-size:13px;letter-spacing:.04em;color:#8F6A17;margin-bottom:7px;font-weight:700}
.field p{font-size:17px;color:var(--ink-soft);line-height:1.55}
@media (max-width:640px){.fields{grid-template-columns:1fr}.field{border-right:none}}

.lenses{display:flex;flex-wrap:wrap;border-bottom:1px solid var(--rule)}
.lens{flex:1 1 260px;padding:15px 22px;border-right:1px solid var(--rule);border-top:4px solid transparent}
.lens:last-child{border-right:none}
.lens .lh{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:700;margin-bottom:6px}
.lens .lh i{width:9px;height:9px;border-radius:50%}
.lens p{font-size:16px;color:var(--ink-soft);line-height:1.55}
@media (max-width:820px){.lenses{flex-direction:column}.lens{border-right:none;border-bottom:1px solid var(--rule)}}

.sources{padding:14px 22px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.sources .lbl{font-size:13px;letter-spacing:.04em;color:var(--ink-soft)}
.src{font-size:15px;text-decoration:none;border-bottom:1px solid var(--rule)}
.src:hover{border-color:var(--ink)}
.src small{color:var(--ink-soft);margin-left:3px}
.foot{padding:24px 0 60px;font-size:13px;color:var(--ink-soft)}
.foot .disclaimer{margin-top:6px;color:var(--ink-soft)}

.tv-mini-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0 28px}
.tv-cell{background:var(--card);border:1px solid var(--rule);padding:10px 12px 4px}
.tv-cell-label{font-size:13px;font-weight:700;margin-bottom:4px}
.tv-screener{background:var(--card);border:1px solid var(--rule);margin:0 0 20px;padding:6px}
.tv-badge{font-size:12px;font-weight:400;color:var(--ink-soft);letter-spacing:.02em;margin-left:10px}
@media (max-width:900px){.tv-mini-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:480px){.tv-mini-grid{grid-template-columns:1fr}}

.tv-crypto-dash{margin:0 0 44px}
.tv-crypto-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.tv-crypto-grid .tv-screener,.tv-crypto-grid .tv-heatmap{margin:0;height:440px}
.tv-heatmap{background:var(--card);border:1px solid var(--rule);padding:6px}
.tv-crypto-stats{display:flex;gap:12px;flex-wrap:wrap}
.tv-crypto-stats .tv-cell{flex:1 1 220px;max-width:280px}
.fg-cell .fg-gauge{margin-top:0;padding-top:0;border-top:none}
@media (max-width:1000px){.tv-crypto-grid{grid-template-columns:1fr}.tv-crypto-grid .tv-screener,.tv-crypto-grid .tv-heatmap{height:360px}}
@media (max-width:640px){.tv-crypto-stats .tv-cell{flex:1 1 100%;max-width:none}}

.log-row{display:grid;grid-template-columns:minmax(150px,auto) 1fr auto;gap:20px;align-items:center;
  padding:24px 0;border-top:1px solid var(--rule);text-decoration:none}
.log-row:last-child{border-bottom:1px solid var(--rule)}
.rdate{font-weight:700;font-size:19px}
.rteaser{font-size:18px;color:var(--ink-soft);display:-webkit-box;-webkit-line-clamp:1;
  -webkit-box-orient:vertical;overflow:hidden}
.rarrow{font-size:15px;color:var(--ink-soft)}
.log-row:hover .rteaser{color:var(--ink)}
@media (max-width:600px){.log-row{grid-template-columns:1fr}.rarrow{display:none}}
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{date} — Global Economic & Investment Intelligence</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="index.html">&larr; 전체 아카이브로</a>
  <div class="headline">{pretty_date}</div>
  <div class="metarow">{metarow}</div>
  {asset_pulse_html}
  {global_picture_html}
  <div class="section-title">이번 세션의 이슈</div>
  {issues_html}
  <div class="foot">
    Global Economic &amp; Investment Intelligence · 자동 수집·분석 브리핑 · 판단 전 원문 확인 요망
    <div class="disclaimer">이 페이지는 투자 자문이 아닙니다. 특정 종목·자산의 매수/매도를 권유하지 않으며, 모든 투자 판단과 그 결과는 이용자 본인에게 있습니다.</div>
  </div>
</div>
</body>
</html>
"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Global Economic & Investment Intelligence — 아카이브</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="headline" style="margin-top:60px">GLOBAL ECONOMIC &amp; INVESTMENT INTELLIGENCE</div>
  <div class="section-title" style="margin-top:18px">실시간 마켓 위젯<span class="tv-badge">Powered by TradingView</span></div>
  {tv_mini_grid_html}
  {tv_forex_screener_html}
  <div class="section-title">가상자산 실시간 현황</div>
  {tv_crypto_dashboard_html}
  <div class="section-title">세션별 아카이브</div>
  <div style="margin:0 0 90px">{rows}</div>
</div>
</body>
</html>
"""


def build_site():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    archive_files = sorted(glob.glob(f"{ARCHIVE_DIR}/*.json"), reverse=True)

    # index.html의 공포·탐욕 게이지는 별도 API를 다시 부르지 않고,
    # 가장 최근 세션 아카이브(archive_files[0], 최신순 정렬)의 값을
    # 재사용한다.
    latest_fear_greed = None

    log_rows = []
    for path in archive_files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        date = data["date"]
        # 세션 필드가 없는 예전 아카이브(하루 1회 시절)와도 호환되도록 처리
        session = data.get("session")
        slug = f"{date}-{session}" if session else date
        session_suffix = f" · {SESSION_LABEL.get(session, '')}" if session else ""

        issues = data.get("issues", [])

        rapid_n = sum(1 for i in issues if i.get("trend_status") == "Accelerating")
        metarow = (
            f'<div class="m"><span class="dot" style="background:{TREND_COLOR["Accelerating"]}"></span>'
            f'가속 국면 {rapid_n}건</div>'
            f'<div class="m">이슈 {len(issues)}건 수록</div>'
            f'<div class="m">{_pretty_date(date)}{session_suffix}</div>'
        )

        global_picture = data.get("global_picture", {})
        market_snapshot = data.get("market_snapshot", {})
        fear_greed = data.get("fear_greed")
        if latest_fear_greed is None and fear_greed:
            latest_fear_greed = fear_greed
        asset_pulse_html = render_asset_pulse(global_picture.get("asset_class_pulse", []), market_snapshot, fear_greed)
        global_picture_html = render_global_picture(global_picture)
        issues_html = "".join(
            render_issue(
                issue,
                f"GEI·{date.replace('-','')}{('-'+session.upper()) if session else ''}·{idx+1:02d}",
                market_snapshot,
            )
            for idx, issue in enumerate(issues)
        )

        page_html = PAGE_TEMPLATE.format(
            date=date,
            pretty_date=f"{date}{session_suffix} — 지금 세계 경제는",
            metarow=metarow,
            css=BASE_CSS,
            asset_pulse_html=asset_pulse_html,
            global_picture_html=global_picture_html,
            issues_html=issues_html,
        )
        with open(f"{OUTPUT_DIR}/{slug}.html", "w", encoding="utf-8") as f:
            f.write(page_html)

        teaser = issues[0].get("headline", "") if issues else ""
        log_rows.append(
            f'<a class="log-row" href="{slug}.html">'
            f'<div class="rdate">{_pretty_date(date)}{session_suffix}</div>'
            f'<div class="rteaser">{_esc(teaser)}</div>'
            f'<div class="rarrow">열기 →</div></a>'
        )

    # 실시간 마켓 위젯(TradingView)은 "지금 이 순간"의 시세이므로
    # 세션별 과거 아카이브 페이지가 아니라 index.html에만 넣는다.
    index_html = INDEX_TEMPLATE.format(
        css=BASE_CSS,
        rows="".join(log_rows),
        tv_mini_grid_html=render_tv_mini_grid(),
        tv_forex_screener_html=render_tv_forex_screener(),
        tv_crypto_dashboard_html=render_crypto_dashboard(latest_fear_greed),
    )
    with open(f"{OUTPUT_DIR}/index.html", "w", encoding="utf-8") as f:
        f.write(index_html)

    print(f"{len(archive_files)}개 페이지(세션 포함) 생성 완료 → {OUTPUT_DIR}/")


if __name__ == "__main__":
    build_site()
