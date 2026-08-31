"""
실제 시세/지표 데이터를 가져와 차트(스파크라인)로 보여주기 위한 모듈.

뉴스 텍스트 요약만으로는 "추세"가 눈에 잘 안 들어오기 때문에, 자산군별
대표 지표의 실제 시계열 값을 최소한으로 가져와 사이트에 작은 추세선
(스파크라인)과 함께 보여준다. 소스가 여러 개인데 하나가 실패해도
나머지는 계속 진행되도록 각 시리즈를 개별적으로 try/except 한다
(fetch_sources.py와 동일한 철학).

사용하는 소스 (모두 공식 공개 API):
- FRED (세인트루이스 연은, Federal Reserve Economic Data): 주식지수
  (S&P500/나스닥/다우), 국채금리, 원자재 스팟가격. 무료 API 키가
  필요하다 — https://fredaccount.stlouisfed.org 에서 가입 후 API 키
  페이지에서 즉시 발급받을 수 있다 (완전 무료). 환경변수
  FRED_API_KEY가 없으면 이 소스에 해당하는 카드는 조용히 건너뛴다
  (사이트 자체는 정상 생성됨).
- Kraken 공개 시장데이터 API: 암호자산(BTC/ETH). 키 불필요.
- Frankfurter API (ECB 환율 기반 공개 API): 외환. 키 불필요.

이 모듈은 generate_site.py의 자산군(asset_classes) 라벨과 동일한
5개 표준 라벨을 사용한다: 주식 / 채권·금리 / 원자재 / 암호자산 / 외환.
"""
import os
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
REQUEST_TIMEOUT = 10
LOOKBACK_DAYS = 90   # 소스에서 가져오는 기간
CHART_POINTS = 60    # 스파크라인에 실제로 쓰는 최근 포인트 수

# (표시 이름, 자산군, FRED series_id)
FRED_SERIES = [
    ("S&P 500", "주식", "SP500"),
    ("나스닥종합", "주식", "NASDAQCOM"),
    ("다우존스", "주식", "DJIA"),
    ("미국 10년물 국채금리", "채권·금리", "DGS10"),
    ("미국 2년물 국채금리", "채권·금리", "DGS2"),
    ("연방기금금리", "채권·금리", "FEDFUNDS"),
    ("WTI 원유", "원자재", "DCOILWTICO"),
    ("브렌트유", "원자재", "DCOILBRENTEU"),
]

# (표시 이름, 자산군, Kraken pair 코드)
KRAKEN_PAIRS = [
    ("비트코인 (BTC/USD)", "암호자산", "XBTUSD"),
    ("이더리움 (ETH/USD)", "암호자산", "ETHUSD"),
]

# (표시 이름, 자산군, Frankfurter 통화 코드, 기준 통화)
FX_PAIRS = [
    ("원/달러", "외환", "KRW", "USD"),
    ("달러/엔", "외환", "JPY", "USD"),
    ("유로/달러", "외환", "USD", "EUR"),
]


def _pct_change(values):
    if not values or len(values) < 2 or values[0] == 0:
        return None
    return round((values[-1] - values[0]) / values[0] * 100, 2)


def _fetch_fred_series(series_id: str):
    if not FRED_API_KEY:
        return None
    end = dt.date.today()
    start = end - dt.timedelta(days=LOOKBACK_DAYS)
    resp = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    obs = resp.json().get("observations", [])
    return [float(o["value"]) for o in obs if o.get("value") not in (".", None, "")]


def _fetch_kraken_series(pair: str):
    resp = requests.get(
        "https://api.kraken.com/0/public/OHLC",
        params={"pair": pair, "interval": 1440},  # 1440분 = 1일봉
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    result = data.get("result", {})
    result_key = next((k for k in result if k != "last"), None)
    if not result_key:
        return None
    candles = result[result_key][-LOOKBACK_DAYS:]
    return [float(c[4]) for c in candles]  # index 4 = close


def _fetch_fx_series(symbol: str, base: str):
    end = dt.date.today()
    start = end - dt.timedelta(days=LOOKBACK_DAYS)
    resp = requests.get(
        f"https://api.frankfurter.dev/v1/{start.isoformat()}..{end.isoformat()}",
        params={"base": base, "symbols": symbol},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rates = resp.json().get("rates", {})
    dates = sorted(rates.keys())
    return [rates[d][symbol] for d in dates if symbol in rates[d]]


def fetch_fear_greed():
    """비트코인/암호자산 공포·탐욕 지수 (alternative.me, 무료 공개 API, 키 불필요).

    실패하면 None을 반환한다 — 호출부는 None이면 그 섹션을 건너뛴다.
    """
    try:
        resp = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": 2},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        today = data[0]
        prev = data[1] if len(data) > 1 else None
        return {
            "value": int(today["value"]),
            "classification": today.get("value_classification", ""),
            "prev_value": int(prev["value"]) if prev else None,
        }
    except Exception as e:
        print(f"[WARN] Fear & Greed 지수 수집 실패: {e}")
        return None


def fetch_market_snapshot() -> dict:
    """자산군별 실제 시세 시계열 스냅샷을 만든다.

    반환 형식: {"주식": [{"label":..., "series":[...], "latest":...,
    "change_pct":...}, ...], "채권·금리": [...], ...}
    개별 소스가 실패해도 [WARN]만 남기고 계속 진행한다.
    """
    snapshot: dict[str, list] = {}

    def _add(label, asset_class, series):
        if not series or len(series) < 2:
            return
        trimmed = series[-CHART_POINTS:]
        snapshot.setdefault(asset_class, []).append(
            {
                "label": label,
                "series": trimmed,
                "latest": trimmed[-1],
                "change_pct": _pct_change(trimmed),
            }
        )

    jobs = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for label, asset_class, series_id in FRED_SERIES:
            jobs.append((executor.submit(_fetch_fred_series, series_id), label, asset_class, "FRED", series_id))
        for label, asset_class, pair in KRAKEN_PAIRS:
            jobs.append((executor.submit(_fetch_kraken_series, pair), label, asset_class, "Kraken", pair))
        for label, asset_class, symbol, base in FX_PAIRS:
            jobs.append((executor.submit(_fetch_fx_series, symbol, base), label, asset_class, "Frankfurter", symbol))

        for future, label, asset_class, source_name, ident in jobs:
            try:
                series = future.result()
                _add(label, asset_class, series)
            except Exception as e:
                print(f"[WARN] {source_name} {ident} 수집 실패: {e}")

    return snapshot


if __name__ == "__main__":
    import json

    print(json.dumps({
        "market_snapshot": fetch_market_snapshot(),
        "fear_greed": fetch_fear_greed(),
    }, ensure_ascii=False, indent=2))
