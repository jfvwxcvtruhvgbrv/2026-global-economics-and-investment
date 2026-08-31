"""
3단계 파이프라인 (Map → Chunk-Reduce → Final Funnel).

입력이 조금만 많아져도(120개 이상 클러스터) 2단계를 "전체를 한 번에
보는 단일 호출"로 처리하면 출력이 16000토큰 가까이 필요해지면서 생성
시간이 너무 길어지고, 중간 네트워크 장비가 "응답이 너무 안 온다"며
연결을 끊어버릴 수 있다 (타임아웃 숫자를 늘리는 것으로는 해결되지
않는 문제). 그래서 2단계를 다시 둘로 쪼갰다.

1단계 (MAP, 배치별):
    원본 아이템 → 배치별 raw event cluster (사실관계+출처). 이 단계에서
    이미 "경제/투자와 무관한 항목"을 1차로 걸러낸다.

2단계 (CHUNK-REDUCE, 덩어리별):
    raw cluster를 20개씩 묶어 덩어리 안에서만 중복 통합 + 가벼운
    후보 이슈로 압축한다. 아직 인사이트/관점/글로벌 픽처는 만들지
    않는다 (출력이 짧아서 빠르게 끝난다).

3단계 (FINAL FUNNEL, 전체 1회):
    모든 덩어리의 후보를 모아, 그중 정말 중요한 것만 최대 N개
    골라서 그 N개에 대해서만 인사이트·관점·트렌드·마켓 픽처를
    만든다. 최종 출력 개수를 프롬프트에서 명시적으로 제한하기
    때문에, 입력이 아무리 많아도 응답 크기와 생성 시간이 일정
    범위 안에 묶인다.

저작권 원칙(반드시 유지):
- 원문을 그대로 인용하지 않는다. 모든 요약은 재구성된 표현이어야 한다.
- 각 요약은 짧게(2~3문장). 항상 출처명 + 원문 링크를 함께 제공한다.

투자 자문 관련 원칙(반드시 유지):
- 이 파이프라인은 "무슨 일이 있었고 왜 중요한지"를 정리하는 뉴스
  인텔리전스이지, 매수/매도 추천이나 개인화된 투자 조언이 아니다.
  프롬프트 어디에도 특정 종목의 매수/매도를 권유하는 문구를 넣지
  않는다. 최종 사이트에도 "투자 조언이 아니다"라는 문구를 노출한다.
"""
import json
import os
import glob
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config import CLAUDE_MODEL

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# 여러 배치를 동시에 API 호출하는 동시 실행 수. 너무 높이면 API rate
# limit에 걸릴 수 있으므로 보수적으로 5로 둔다.
MAX_CONCURRENCY = 5


def _call_claude(system_prompt: str, user_content: str, max_tokens: int) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("환경변수 ANTHROPIC_API_KEY 가 설정되어 있지 않습니다.")

    resp = requests.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 응답이 max_tokens에 걸려 중간에 잘리면 JSON이 미완성 상태가
        # 된다. 이 경우 마지막으로 온전하게 닫힌 객체까지만 살려서
        # 부분 복구를 시도한다 (완전히 버리는 것보다 낫다).
        recovered = _try_recover_truncated_json(text)
        if recovered is not None:
            return recovered
        raise


def _try_recover_truncated_json(text: str):
    """잘린 JSON 문자열에서 최대한 온전한 부분만 복구한다.

    전략: 최상위 리스트(예: "raw_clusters"/"candidates"/"issues")의 원소들
    중 완전하게 닫힌 객체까지만 남기고, 뒤의 잘린 꼬리를 잘라낸 뒤
    괄호를 닫아 다시 파싱을 시도한다. 실패하면 None을 반환한다.
    """
    for key in ("raw_clusters", "candidates", "issues"):
        marker = f'"{key}"'
        if marker not in text:
            continue
        start = text.find("[", text.find(marker))
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        last_complete = None  # 마지막으로 온전히 닫힌 원소의 끝 위치
        for i in range(start + 1, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_complete = i
        if last_complete is None:
            continue
        candidate = text[: last_complete + 1] + "]}"
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


# ── 1단계 (MAP): 배치별 원재료 이벤트 클러스터 추출 ──────────────

STAGE1_SYSTEM_PROMPT = """\
당신은 뉴스/커뮤니티 원본 아이템에서 '경제·투자 관련 사건 단위'를
추출하는 리서치 어시스턴트입니다. 아직 최종 기사를 쓰는 단계가
아니라, 다음 단계의 에디터가 판단할 수 있도록 재료를 정리하는
단계입니다.

이 아카이브는 "전세계 경제·투자 인사이트"를 다룹니다. 입력에는
일반 종합 뉴스(정치/사회/재난 등)도 섞여 있을 수 있는데, 그 중
아래에 해당하는 것만 다룹니다:
- 거시경제 지표(성장률, 물가, 고용, 무역수지 등)
- 통화정책/중앙은행(금리, 양적완화·긴축, 환율 개입)
- 재정정책/정부 예산·부채·조세
- 기업 실적, M&A, 상장/상장폐지, 대규모 투자·구조조정
- 주식·채권·외환·원자재·암호자산 시장의 움직임과 그 원인
- 무역/공급망/관세/제재가 시장과 기업 실적에 미치는 영향
- 규제·정책 변화가 산업/자본시장에 미치는 영향
- 지정학·전쟁·재난이더라도 에너지·물류·금융시장에 뚜렷한 파급이
  있는 경우 (파급이 불분명하면 노이즈로 처리)

자산군(asset_classes) 태깅: 각 클러스터가 실제로 어느 자산군과
관련되는지 아래 5개 표준 라벨 중 해당하는 것을 모두 골라
asset_classes 배열에 넣으세요 (관련 없으면 빈 배열).
- "주식" (증시, 개별 종목·업종, 지수)
- "채권·금리" (국채/회사채, 중앙은행 금리, 수익률곡선)
- "원자재" (에너지, 금속, 농산물 등 커머디티)
- "암호자산" (비트코인 등 크립토·블록체인 산업)
- "외환" (환율, 통화 강세/약세, 외환보유고)
한 사건이 여러 자산군에 걸치면 여러 개를 넣어도 됩니다 (예: 유가
급등은 "원자재"와 "외환" 둘 다 해당할 수 있음).

작업:
1. 주어진 아이템들 중 같은 사건/이슈를 다루는 것들을 하나의
   raw cluster로 묶으세요 (제목이나 표현이 달라도 같은 사건이면 통합).
2. 각 클러스터에 대해 확인된 사실관계를 재구성된 표현으로 짧게
   정리하고, 관련된 모든 출처(언론사/커뮤니티명 + 링크 + 지역)를
   나열하세요.
3. 출처 간 해석/논조 차이가 있으면 어떤 출처가 어떤 입장인지
   짧게 남기세요 (없으면 생략 가능).
4. 위 경제/투자 카테고리에 해당하지 않는 클러스터(순수 정치·외교·
   연예·스포츠·범죄·생활정보 등 경제적 파급이 불분명한 것)는
   is_likely_noise를 true로 표시하세요. 단, 여러 국가/자산군에서
   유사한 패턴이 반복되며 시장에 영향을 줄 것으로 보이면 false로
   두고 그 패턴을 note에 적으세요.
5. 절대 원문을 그대로 옮기지 마세요 (직접 인용 금지).
6. 이 단계에서도, 이후 단계에서도 특정 종목/자산의 매수·매도를
   권유하는 표현은 절대 쓰지 마세요. "무슨 일이 있었는가"만
   정리합니다.
7. 반드시 아래 JSON 스키마로만 응답하세요. 다른 텍스트, 설명,
   코드펜스는 포함하지 마세요.

출력 스키마:
{
  "raw_clusters": [
    {
      "facts": "string (2~3문장, 재구성된 사실관계)",
      "topics": ["string", ...],
      "regions": ["string", ...],
      "asset_classes": ["string", ...],
      "is_likely_noise": true/false,
      "note": "string (선택, 패턴/맥락 메모)",
      "sources": [
        {"source": "string", "link": "string", "region": "string", "angle": "string (선택)"}
      ]
    }
  ]
}
"""


def _stage1_extract_raw_clusters(items: list[dict]) -> list[dict]:
    """배치 단위로 items를 raw event cluster로 압축한다 (병렬 처리)."""
    BATCH_SIZE = 15
    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]

    def _process_one(batch):
        user_content = (
            "다음은 오늘 수집된 뉴스/커뮤니티 원본 아이템입니다 (JSON).\n\n"
            + json.dumps(batch, ensure_ascii=False)
        )
        try:
            result = _call_claude(STAGE1_SYSTEM_PROMPT, user_content, max_tokens=6000)
            return result.get("raw_clusters", [])
        except (json.JSONDecodeError, requests.RequestException, RuntimeError) as e:
            print(f"[WARN] 1단계 배치 처리 실패, 건너뜀: {e}")
            return []

    all_clusters = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = [executor.submit(_process_one, b) for b in batches]
        for future in as_completed(futures):
            all_clusters.extend(future.result())
    return all_clusters


# ── 2단계 (CHUNK-REDUCE): 덩어리별 예비 통합 ─────────────────────

STAGE2_SYSTEM_PROMPT = """\
당신은 경제/투자 뉴스 클러스터를 예비 통합하는 에디터입니다. 아직
최종 결과물을 만드는 단계가 아니라, 다음 단계에서 전체를 놓고 최종
선별할 수 있도록 후보를 압축하는 단계입니다.

작업:
1. 주어진 raw cluster 중 같은 사건/발표/후속 보도를 다루는 것들을
   하나로 통합하세요 (표현이 달라도 같은 사건이면 통합).
2. 통합된 각 사건에 대해 짧은 headline_draft(제목 초안)와 facts(사실
   관계 2~3문장)를 재구성된 표현으로 작성하세요.
3. is_probably_significant를 판단하세요: 여러 국가/자산군/산업에
   영향을 미치거나, 통화정책·재정정책·기업실적·무역/공급망·원자재·
   환율·금리·자본시장 구조에 변화를 일으키는 사건이면 true, 경제적
   파급이 제한적이거나 단순 개별 사건이면 false로 표시하세요 (최종
   판단은 다음 단계에서 다시 하니 여기서는 대략적인 1차 필터만 하면
   됩니다).
4. 절대 원문을 그대로 인용하지 마세요.
5. 특정 종목/자산의 매수·매도를 권유하는 표현은 쓰지 마세요.
6. 반드시 아래 JSON 스키마로만, 간결하게 응답하세요. 다른 텍스트,
   설명, 코드펜스는 포함하지 마세요.

출력 스키마:
{
  "candidates": [
    {
      "headline_draft": "string",
      "facts": "string (2~3문장)",
      "regions": ["string", ...],
      "topics": ["string", ...],
      "asset_classes": ["string", ...],
      "is_probably_significant": true/false,
      "sources": [{"source": "string", "link": "string", "region": "string"}]
    }
  ]
}
"""


def _stage2_chunk_reduce(clusters: list[dict]) -> list[dict]:
    """raw cluster를 덩어리 단위로 예비 통합해 후보 리스트로 압축한다 (병렬)."""
    CHUNK_SIZE = 20
    chunks = [clusters[i : i + CHUNK_SIZE] for i in range(0, len(clusters), CHUNK_SIZE)]

    def _process_one(chunk):
        user_content = (
            "다음은 raw event cluster 목록입니다 (JSON). 같은 사건은 통합하고,\n"
            "간결한 후보 리스트로 압축하세요.\n\n"
            + json.dumps(chunk, ensure_ascii=False)
        )
        try:
            result = _call_claude(STAGE2_SYSTEM_PROMPT, user_content, max_tokens=8000)
            return result.get("candidates", [])
        except (json.JSONDecodeError, requests.RequestException, RuntimeError) as e:
            print(f"[WARN] 2단계 덩어리 처리 실패, 건너뜀: {e}")
            return []

    all_candidates = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = [executor.submit(_process_one, c) for c in chunks]
        for future in as_completed(futures):
            all_candidates.extend(future.result())
    return all_candidates


# ── 3단계 (FINAL FUNNEL): 최종 선별 + 인사이트 + 마켓 픽처 ──────

MAX_FINAL_ISSUES = 12

STAGE3_SYSTEM_PROMPT = f"""\
당신은 전세계 경제·시장 신호를 매일 두 차례(오전/오후) 종합해 "지금
세계 경제와 자본시장이 어느 방향으로 움직이고 있는가"를 설명하는
Global Economic & Investment Intelligence 에디터입니다. 이 페이지는
단순 경제 뉴스 모음이 아니라, 투자자·의사결정자가 5~10분만 읽어도
"지금 거시경제·정책·기업·시장이 이렇게 맞물려 돌아가는구나"를
이해하게 만드는 것이 목표입니다.

중요: 이 페이지는 투자 자문이 아닙니다. 특정 종목·자산의 매수/매도를
권유하거나 목표가를 제시하지 마세요. "무슨 일이 있었고, 왜 중요하며,
무엇이 달라지는지"를 설명하는 데 집중하세요.

입력으로 예비 통합된 후보 이슈 목록을 받습니다 (여러 덩어리에서
나왔기 때문에 같은 사건이 후보로 중복 존재할 수 있습니다). 그리고
(있다면) 직전 세션에 생성된 이슈 헤드라인 목록도 함께 받습니다.

작업 순서:

1. DEDUPLICATE: 후보 중 같은 사건이면 하나로 통합하세요. 같은
   사건이 여러 번 등장하는 것을 "중요도가 높다"는 신호로 오인하지
   마세요.

2. 최종적으로 딱 **최대 {MAX_FINAL_ISSUES}개**의 이슈만 선택하세요.
   Scale(영향 범위·자산군), Novelty(새로움), Acceleration(전개 속도),
   Structural Impact(구조 변화 가능성), Cross-asset Impact(자산군 간
   파급), Geographic Spread(확산 범위), Future Impact(향후 시장/정책
   영향)를 기준으로 가장 중요한 것만 남기세요. 경제적 파급이 불분명한
   가십·개별 사건·단순 정치 이슈는 제외하세요. {MAX_FINAL_ISSUES}개보다
   유의미한 이슈가 적으면 그보다 적게 반환해도 됩니다.

   자산군 커버리지 점검: 최종 선택이 지정학/거시정책 이슈로만
   쏠리지 않도록, 주식·채권·금리·원자재·암호자산·외환 중 그 세션에
   실제로 유의미한 뉴스가 있었던 자산군은 최대한 빠짐없이 issue로
   반영하세요. 단, 특정 자산군에 정말로 유의미한 뉴스가 없었다면
   억지로 이슈를 만들어내지 마세요 (강제 균등 배분이 아니라 "놓친
   자산군이 없는지"에 대한 점검입니다).

3. ISSUE 구성: 선택된 이슈마다 카테고리 제목(headline) 자체가 하나의
   헤드라인이어야 합니다. 고정 분류(거시/기업/시장 등)를 그대로 쓰지
   마세요.
   나쁜 예: "인플레이션 관련 동향"
   좋은 예: "연준 피벗 기대가 국채금리를 끌어내리며 신흥국으로 자금이
   재유입되고 있다"

4. INSIGHT 레이어: 각 issue마다 다음을 작성하세요.
   - what_happened: 확인된 사실 1~3문장
   - why_it_matters: 왜 중요한지 (어떤 자산군·산업·정책에 영향을
     주는지 포함)
   - what_is_changing: 기존과 비교해 무엇이 달라지는지
   - connection: 다른 국가/자산군/산업/정책과 연결되는 지점 (없으면
     생략)
   - signal: 이 사건이 더 큰 국면 전환의 초기 신호인지
   - what_to_watch: 앞으로 지켜볼 지표·이벤트·일정 (예: 다음 FOMC,
     실적 발표일, 경제지표 발표일 등 구체적일수록 좋음)

5. PERSPECTIVE: 기사 출처의 논조를 라벨링하지 말고, 하나의 사건을
   서로 다른 '분석 프레임'으로 해석하세요. 필요한 프레임만 선택:
   Macro View(거시/통화정책) / Markets View(가격·자금흐름) /
   Policy View(재정·규제/정책) / Corporate View(기업 실적·전략) /
   Geopolitical View(지정학이 경제에 미치는 영향). 프레임이 하나만
   의미 있으면 하나만 적어도 됩니다.

6. TREND 상태: 직전 세션 이슈 헤드라인 목록이 제공됐다면 비교해서
   trend_status를 Emerging(새로 등장)/Accelerating(빠르게 커짐)/
   Developing(지속 전개)/Structural(장기 구조 변화)/Cooling(관심 감소)
   중 하나로 표시하세요. 비교 대상이 없으면 Emerging으로 두세요.

7. ASSET CLASSES: 각 issue마다 관련된 자산군을 아래 5개 표준 라벨
   중 해당하는 것만 골라 asset_classes 배열에 넣으세요 (관련 없으면
   빈 배열, 여러 개 해당하면 여러 개 모두):
   "주식" / "채권·금리" / "원자재" / "암호자산" / "외환"

8. MARKET PICTURE: 선택된 이슈들을 종합해 다음을 작성하세요.

   8-1. asset_class_pulse: 아래 5개 자산군 각각에 대해 이번
   세션에서 가장 눈여겨볼 만한 흐름을 1~2문장으로 요약하세요
   ("asset_class"는 정확히 이 5개 라벨을 그대로 사용):
   "주식" / "채권·금리" / "원자재" / "암호자산" / "외환"
   그 자산군에 그 세션에서 특별히 유의미한 뉴스가 없었다면
   summary에 "이번 세션 특별한 신호 없음"처럼 짧게 표시하고,
   억지로 이슈를 지어내지 마세요. 5개 자산군을 모두 포함하세요
   (신호가 없어도 항목 자체는 유지).

   8-2. 다음 5개 리스트를 작성하세요 (각 항목은 한 문장, 헤드라인
   스타일):
   - world_right_now: 이번 세션 가장 중요한 경제/시장 변화 5~10개
   - emerging_signals: 아직 메인은 아니지만 앞으로 시장에 중요해질
     수 있는 것
   - structural_trends: 수주~수개월간 지속되는 거시/정책/산업 구조
     변화
   - what_changed_today: 직전 세션과 비교해 새롭게 달라진 것 (직전
     세션 목록이 없으면 빈 배열)
   - what_to_watch_next: 다음 세션~수주 내 확인해야 할 지표/이벤트
     (구체적인 일정이 있으면 명시)

9. 절대 원문을 그대로 인용하지 마세요. 모든 텍스트는 재구성된
   표현이어야 합니다.

10. 반드시 아래 JSON 스키마로만 응답하세요. 다른 텍스트, 설명,
    코드펜스는 포함하지 마세요.

출력 스키마:
{{
  "global_picture": {{
    "asset_class_pulse": [
      {{"asset_class": "주식", "summary": "string"}},
      {{"asset_class": "채권·금리", "summary": "string"}},
      {{"asset_class": "원자재", "summary": "string"}},
      {{"asset_class": "암호자산", "summary": "string"}},
      {{"asset_class": "외환", "summary": "string"}}
    ],
    "world_right_now": ["string", ...],
    "emerging_signals": ["string", ...],
    "structural_trends": ["string", ...],
    "what_changed_today": ["string", ...],
    "what_to_watch_next": ["string", ...]
  }},
  "issues": [
    {{
      "headline": "string (카테고리 제목=헤드라인)",
      "trend_status": "Emerging|Accelerating|Developing|Structural|Cooling",
      "asset_classes": ["string", ...],
      "insight": {{
        "what_happened": "string",
        "why_it_matters": "string",
        "what_is_changing": "string",
        "connection": "string (선택)",
        "signal": "string",
        "what_to_watch": "string"
      }},
      "perspectives": [
        {{"frame": "string (예: Macro View)", "summary": "string"}}
      ],
      "sources": [
        {{"source": "string", "link": "string", "region": "string"}}
      ]
    }}
  ]
}}
"""


def _stage3_final_funnel(candidates: list[dict], previous_headlines: list[str]) -> dict:
    user_content = (
        "다음은 예비 통합된 후보 이슈 목록입니다 (JSON):\n\n"
        + json.dumps(candidates, ensure_ascii=False)
        + "\n\n직전 세션에 생성된 이슈 헤드라인 목록입니다 (없으면 빈 배열):\n\n"
        + json.dumps(previous_headlines, ensure_ascii=False)
    )
    return _call_claude(STAGE3_SYSTEM_PROMPT, user_content, max_tokens=8000)


def _load_previous_headlines() -> list[str]:
    """가장 최근 아카이브 파일에서 직전 세션의 이슈 헤드라인만 추출."""
    files = sorted(glob.glob("archive/*.json"))
    if not files:
        return []
    try:
        with open(files[-1], encoding="utf-8") as f:
            prev = json.load(f)
        return [issue.get("headline", "") for issue in prev.get("issues", [])]
    except Exception as e:
        print(f"[WARN] 이전 아카이브 로드 실패: {e}")
        return []


def build_daily_archive(items: list[dict]) -> dict:
    """전체 파이프라인: 1단계(MAP) → 2단계(CHUNK-REDUCE) → 3단계(FINAL FUNNEL)."""
    print("    [1단계] 배치별 raw event cluster 추출 중...")
    raw_clusters = _stage1_extract_raw_clusters(items)

    significant_clusters = [c for c in raw_clusters if not c.get("is_likely_noise")]
    noise_count = len(raw_clusters) - len(significant_clusters)
    print(
        f"    → {len(raw_clusters)}개 raw cluster 추출 "
        f"({len(significant_clusters)}개 유의미, {noise_count}개 노이즈 제외)"
    )

    print("    [2단계] 덩어리별 예비 통합 중...")
    candidates = _stage2_chunk_reduce(significant_clusters)
    print(f"    → {len(candidates)}개 후보 이슈로 압축")

    previous_headlines = _load_previous_headlines()

    print(f"    [3단계] 최종 최대 {MAX_FINAL_ISSUES}개 선별 + 마켓 픽처 생성 중...")
    try:
        result = _stage3_final_funnel(candidates, previous_headlines)
    except (json.JSONDecodeError, requests.RequestException, RuntimeError) as e:
        print(f"[WARN] 3단계 처리 실패: {e}")
        result = {"global_picture": {}, "issues": []}

    from zoneinfo import ZoneInfo
    kst_now = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    return {
        "date": kst_now.strftime("%Y-%m-%d"),
        "session": "am" if kst_now.hour < 12 else "pm",
        "global_picture": result.get("global_picture", {}),
        "issues": result.get("issues", []),
    }


if __name__ == "__main__":
    with open("raw_input.json", encoding="utf-8") as f:
        items = json.load(f)
    archive = build_daily_archive(items)
    out_path = f"archive/{archive['date']}.json"
    os.makedirs("archive", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    print(f"저장 완료 → {out_path}")
