"""
소스 설정 파일

법적 안전 원칙:
- RSS/공개 API로 제공되는 '제목 + 짧은 발췌(summary)'만 수집한다.
- 원문 본문 전체를 저장/재게시하지 않는다.
- 각 소스의 robots.txt / 이용약관을 준수한다 (특히 커뮤니티 사이트는
  요청 빈도를 낮게 유지하고, User-Agent를 명시한다).
- 최종 사이트에는 항상 원문 링크를 함께 노출한다 (출처 표시).

이 파일은 "전세계 경제·투자 인사이트" 버전의 소스 목록이다. 일부러
지역 종합 뉴스 피드(Al Jazeera, NHK World 등)도 함께 넣어뒀는데, 이는
그 지역에 별도의 경제 전문 RSS가 마땅치 않은 경우 종합 피드를 넣고
cluster_and_summarize.py의 1단계에서 "경제/투자와 무관한 항목"을
노이즈로 걸러내는 방식으로 커버리지 균형을 잡기 위함이다.

지역 커버리지와는 별개로 "자산군(asset class)" 커버리지도 신경 썼다:
주식/채권·금리/원자재/암호자산/외환 5개 축을 기준으로 최소한 하나
이상의 관련 피드가 있도록 NEWS_FEEDS 하단 "Global" 섹션에 자산군별
전문 소스를 모아뒀다. 실제 자산군 분류·균형 점검은
cluster_and_summarize.py의 2~3단계 프롬프트가 담당한다.
"""

# ── 뉴스 소스 (RSS 피드) ──────────────────────────────
# 각 소스는 (RSS 주소, 지역 태그) 튜플로 관리한다. 지역 태그는 글로벌
# 커버리지 균형을 점검하고, Claude에게 "이 기사가 어느 지역 소스인지"
# 명시적으로 알려주기 위한 메타데이터로만 쓰인다 (강제 균등 배분 아님).
#
# RSS 주소는 언론사 사정으로 종종 바뀐다. 아래 목록은 발행 시점 기준
# 확인된 것들이지만, 수집 실패 로그(fetch_sources.py의 [WARN])가 계속
# 늘어나는 소스가 있으면 해당 언론사의 최신 RSS 주소를 다시 확인해야
# 한다.
NEWS_FEEDS = {
    # North America — 거시/시장/통화정책
    "Reuters Business & Finance": ("https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best", "North America"),
    "CNBC Economy": ("https://www.cnbc.com/id/20910258/device/rss/rss.html", "North America"),
    "MarketWatch Top Stories": ("http://feeds.marketwatch.com/marketwatch/topstories/", "North America"),
    "Federal Reserve Press Releases": ("https://www.federalreserve.gov/feeds/press_all.xml", "North America"),
    # Europe
    "BBC Business": ("http://feeds.bbci.co.uk/news/business/rss.xml", "Europe"),
    "The Economist — Finance & Economics": ("https://www.economist.com/finance-and-economics/rss.xml", "Europe"),
    "ECB Press Releases": ("https://www.ecb.europa.eu/rss/press.html", "Europe"),
    # China / East Asia
    "SCMP Business (Hong Kong)": ("https://www.scmp.com/rss/92/feed", "China"),
    "NHK World Japan (종합, 경제 이슈 필터링)": ("https://www3.nhk.or.jp/nhkworld/en/news/all.xml", "Japan"),
    "Korea Herald Business": ("http://www.koreaherald.com/rss/020100000000.xml", "Korea"),
    # South Asia / Southeast Asia
    "Times of India Business": ("https://timesofindia.indiatimes.com/rssfeeds/1898055.cms", "India"),
    "Straits Times Business (Singapore)": ("https://www.straitstimes.com/news/business/rss.xml", "Southeast Asia"),
    # Middle East — 종합 피드 (에너지/지정학의 시장 영향은 1단계에서 필터링)
    "Al Jazeera": ("https://www.aljazeera.com/xml/rss/all.xml", "Middle East"),
    # Africa
    "AllAfrica": ("https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "Africa"),
    # Latin America
    "MercoPress": ("https://en.mercopress.com/rss/", "Latin America"),
    # Oceania
    "NZ Herald": ("https://www.nzherald.co.nz/arc/outboundfeeds/rss/", "Oceania"),

    # ── Global / 자산군별 전문 피드 ──────────────────────────
    # 지역이 아니라 "자산군(주식/채권·금리/원자재/암호자산/외환)"
    # 커버리지를 명시적으로 채우기 위한 소스들. 위의 지역별 종합
    # 경제 피드만으로는 원자재·암호자산·외환처럼 특정 자산군 뉴스가
    # 희석되기 쉬워서 별도로 추가했다.
    "Investing.com — Stock Market News": ("https://www.investing.com/rss/news_25.rss", "Global"),      # 주식
    "Investing.com — Market Overview": ("https://www.investing.com/rss/market_overview.rss", "Global"),  # 크로스에셋 분석
    "Investing.com — Economic Indicators": ("https://www.investing.com/rss/news_95.rss", "Global"),    # 채권/금리와 연관된 거시지표
    "Investing.com — Forex News": ("https://www.investing.com/rss/news_1.rss", "Global"),               # 외환
    "FXStreet (Forex & Commodities)": ("https://www.fxstreet.com/rss/news", "Global"),                   # 외환/원자재
    "Investing.com — Commodities & Futures": ("https://www.investing.com/rss/news_11.rss", "Global"),   # 원자재
    "Investing.com — Cryptocurrency News": ("https://www.investing.com/rss/news_301.rss", "Global"),    # 암호자산
    "CoinDesk (Crypto Markets)": ("https://www.coindesk.com/arc/outboundfeeds/rss/", "Global"),          # 암호자산
    "Cointelegraph (Crypto Markets)": ("https://cointelegraph.com/rss", "Global"),                       # 암호자산
    "OilPrice.com (Energy & Commodities)": ("https://oilprice.com/rss/main", "Global"),                  # 원자재(에너지)
}

# ── 커뮤니티 소스 (공개 JSON 엔드포인트) ─────────────────
# Reddit의 .json 엔드포인트는 로그인 없이 공개적으로 접근 가능하지만
# 이용약관상 과도한 자동 수집은 제한될 수 있으므로 요청 간격을 두고,
# 명확한 User-Agent를 지정해야 한다. 상업적 대량 이용 시 공식 API
# (OAuth) 사용을 권장한다.
COMMUNITY_SOURCES = {
    "Reddit r/economics": "https://www.reddit.com/r/economics/top.json?limit=25&t=day",
    "Reddit r/investing": "https://www.reddit.com/r/investing/top.json?limit=25&t=day",
    "Reddit r/StockMarket": "https://www.reddit.com/r/StockMarket/top.json?limit=25&t=day",
    "Reddit r/finance": "https://www.reddit.com/r/finance/top.json?limit=25&t=day",
}

REQUEST_USER_AGENT = "personal-archive-bot/0.1 (contact: your-email@example.com)"
REQUEST_DELAY_SECONDS = 2  # 사이트별 과도한 요청 방지

# Claude API 모델 (클러스터링/다중관점 요약에 사용)
CLAUDE_MODEL = "claude-sonnet-4-6"
