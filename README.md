# 주차장 담당자 Agent

회사 사옥 주차장의 입출입 내역·잔여 대수·요금·환불을 안내하는 대화형 에이전트. LangChain·LangGraph
기반 멀티 에이전트 구조로 만든 미니 프로젝트다. 요구사항/정책은 [`SERVICE.md`](SERVICE.md), 기술
스택·코드 규약은 [`CLAUDE.md`](CLAUDE.md)에 있다.

## 구조

`langgraph-supervisor`로 감독자 하나가 하위 에이전트 3개에게 위임하는 구조다.

```
사용자 질문
   │
   ▼
supervisor (감독자) ── describe_architecture (자기 구조 설명, 직접 호출)
   │
   ├─ parking_info_agent (조회)
   │    find_by_vehicle_number, find_by_department, get_available_spaces,
   │    get_recent_vehicles, search_vehicles_semantic(RAG)
   │
   ├─ fee_agent (요금·환불)
   │    calculate_fee, describe_refund_policy, calculate_subscription_refund
   │
   └─ admin_agent (관리자 전용)
        list_all_vehicles_admin (차주 이름 포함, 일반 조회 도구는 이름을 반환하지 않음)
```

- **모델**: Amazon Bedrock의 Claude (`ChatBedrockConverse`), 임베딩은 Titan Embed v2
- **데이터**: 실제 시스템 연동 없이 `data/carlist.json` 더미 데이터를 매 호출마다 읽음
- **RAG**: `search_vehicles_semantic`만 임베딩 기반 유사도 검색을 쓴다. 차량번호·부서 같은 정확
  조회는 여전히 직접 필터링 — RAG는 "전기차", "SUV"처럼 필드에 없는 자연어 표현 전용
- **가드레일**: 차주 이름·할인 사유처럼 노출하면 안 되는 정보는 프롬프트뿐 아니라 도구 반환값
  자체에서 걸러낸다(코드 레벨 차단, 프롬프트 우회에 대비)

## 폴더 구조

```
app.py                     로컬 FastAPI 서버 (웹 채팅 UI 서빙 + /query API)
web/index.html             채팅 UI (메뉴바 + 마크다운/표 렌더링)
src/
  agent.py                 감독자·하위 에이전트 정의, final_answer() 답변 추출
  tools.py                 도메인 도구 (조회/요금/환불/관리자)
  retriever.py             더미 데이터 로딩 + RAG(임베딩/벡터 검색)
data/carlist.json          더미 차량 데이터 (59건: 임직원/방문객/정기권)
evaluation/
  test_queries.csv         자동 평가용 문항(positive/negative/edge/guardrail)
  run_eval.py              규칙 기반 평가(도구 호출·금칙어) → eval_report.md
  ragas_eval.py            RAGAS 기반 품질 평가(1차) → round1_report.md
  round2_eval.py           RAGAS 기반 품질 평가(2차, 1차 대비 개선폭) → round2_report.md
SERVICE.md                 서비스 정의·정책·성공 기준
CLAUDE.md                  기술 스택·폴더 구조·코드 규약
```

## 실행 방법

```powershell
# 가상환경 생성 및 패키지 설치
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

`.env` 파일에 AWS 자격증명과 모델 ID를 넣는다 (`.gitignore`에 이미 등록돼 있어 커밋되지 않음):

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-4-6
```

```powershell
# 콘솔에서 바로 시험
python src/agent.py

# 웹 채팅으로 실행
uvicorn app:app --host 127.0.0.1 --port 8000
# 브라우저에서 http://127.0.0.1:8000

# 평가
python evaluation/run_eval.py       # 규칙 기반 (도구 호출 확인 + 금칙어 검사)
python evaluation/ragas_eval.py     # RAGAS 품질 평가 1차
python evaluation/round2_eval.py    # RAGAS 품질 평가 2차 (1차 대비 개선폭)
```

## 트라이앤에러 회고

과제를 진행하며 실제로 겪은 문제와 원인, 고친 방법을 정리한다. 코드 자체보다 "왜 이렇게 됐는가"를
남기는 게 더 오래 쓸모 있을 거라 판단해서 따로 뺐다.

### 멀티 에이전트 전환

처음엔 도구 하나짜리 단일 에이전트로 시작했다가, 조회/요금/관리자 권한을 분리하고 싶어져서 멀티
에이전트로 바꿨다. 처음엔 "하위 에이전트를 도구로 감싸서 감독자가 호출"하는 방식(agent-as-tool)을
손으로 짰는데, 이후 `langgraph-supervisor` 라이브러리로 다시 바꿨다. 표준 라이브러리를 쓰는 게
유지보수에 유리하다는 판단이었지만, 라이브러리의 기본 동작(`output_mode="last_message"`)이
핸드오프 요약만 남기고 실제 도구 호출 흔적을 지워버려서, 평가 스크립트의 도구 호출 검증과
웹 API의 trace/contexts 추출이 전부 깨졌다. `output_mode="full_history"`로 바꾸고서야 해결됐다 —
**라이브러리를 새로 들여올 때는 기본값이 뭘 숨기는지부터 확인해야 한다**는 교훈.

### 감독자가 답을 뭉개는 문제

`full_history`로 바꾼 뒤에도, 감독자가 하위 에이전트의 상세 답변(표 포함)을 받고 나서 자기 말로
"~안내해 드렸습니다!" 같은 짧은 인사치레로 다시 요약해버리는 경우가 있었다. `messages[-1]`만 쓰면
이 빈껍데기 요약이 최종 답으로 나갔다. `final_answer()`를 만들어 감독자/하위 에이전트의 텍스트를
따로 추적하고, "하위 에이전트 답에 표가 있는데 감독자 답엔 없다" 또는 "감독자 답이 하위 에이전트
답의 절반도 안 된다"는 두 가지 신호로 원본을 되살리도록 했다. 처음엔 표 유무만 봤다가, 표 없는
일반 설명(환불 정책 등)도 같은 방식으로 뭉개지는 걸 나중에 발견하고 길이 비교 조건을 추가했다.

### 가드레일 프롬프트가 계속 과녁을 빗나감

"차주 이름·전화번호를 물으면 거절하라"는 규칙을 여러 번 고쳤다.
1. 처음엔 "특정 차량 한 대에 대한 질문"을 통째로 위임 금지 대상으로 잡았더니, "차량 있어?"처럼
   개인정보와 무관한 정상 질문까지 거절해버렸다 → "이름/전화번호를 명시적으로 요청할 때만"으로 좁힘.
2. 환불 기능을 추가한 뒤에는 반대로, 감독자가 "환불"이라는 단어를 요금 카테고리로 못 묶고 통째로
   거절하는 경우가 나왔다(프롬프트에 "환불"이라는 단어 자체가 없었음) → 라우팅 규칙에 명시적으로
   "환불 정책 설명, 환불 금액 계산 포함"이라고 못박아서 해결.

**교훈**: 이런 규칙은 넓게 잡으면 정상 요청을 막고, 좁게 잡으면 신규 기능을 놓친다 — 새 기능을
추가할 때마다 라우팅 프롬프트에 그 기능이 명시돼 있는지 같이 확인해야 한다.

### RAG 붙였다 뺐다 붙였다

RAG 파이프라인(임베딩 기반 자연어 검색)을 만들어달라는 요청을 받고 구현했는데, 중간에 "RAG는 빼라"는
지시로 되돌렸다가, 바로 다음 메시지에서 "다시 넣어달라"는 요청으로 재구현했다. 되돌리고 다시 넣는
과정에서 `load_dotenv()` 호출 순서 문제를 발견했다 — `retriever.py`가 `agent.py`의 `load_dotenv()`
보다 먼저 임포트되면서, `.env`가 로드되기 전에 임베딩 클라이언트가 자격증명 없이 만들어져
`NoCredentialsError`가 났다. `retriever.py` 자신도 `load_dotenv()`를 호출하도록 고쳐서, 어느 파일이
먼저 임포트되든 안전하게 만들었다.

같은 이유로, 문항 리포트도 "겹치는 것 같다"는 지적에 하나로 합쳤다가, "원래대로 되돌려달라"는
요청으로 다시 두 파일로 나눴다. 되돌리는 과정에서 마침 백그라운드로 돌고 있던 평가 스크립트가
새(병합) 로직으로 파일을 덮어쓰기 직전이어서, 먼저 그 작업을 멈추고 되돌렸다.

### forbidden 문자열 검사 오탐 두 번

`run_eval.py`의 가드레일 검사는 "답변에 금지어가 있으면 실패"라는 단순 포함 검사로 시작했는데,
거절 답변이 "차주 이름은 제공할 수 없습니다"처럼 금지어 자체를 언급하면서 거절하면 오탐이 났다.
① 문장 단위로 끊어서 "금지어가 있는 문장에 거절 표현도 같이 있으면 위반 아님"으로 고쳤는데,
② 그 다음엔 "제공해드릴"(모델 출력)과 "제공해 드릴"(내가 정의한 마커)의 띄어쓰기가 달라서 거절
표현 매칭이 실패, 다시 오탐이 났다. 마커와 문장 모두 공백을 지우고 비교하도록 고쳐서 해결했다.
**LLM 출력 문자열을 규칙 기반으로 검사할 땐, 의미는 같지만 표기가 미묘하게 다른 경우(띄어쓰기,
어미)를 항상 의심해야 한다.**

### RAGAS 설치부터 난관

`ragas` 최신판을 설치하려 하니, 이 환경(Python 3.14 + C++ 빌드 도구 없음)에서 `scikit-network`가
소스 빌드를 시도하다 실패했다. 오래된 버전으로 내려도 이번엔 `numpy` 자체가 소스 빌드를 시도해서
실패 — Python 3.14가 너무 최신이라 옛날 버전들의 사전 빌드된 wheel이 없었던 것. `--prefer-binary`
옵션으로 최신 `ragas`를 다시 설치하니 컴파일 없이 들어갔지만, 이번엔 `ragas`가 참조하는
`langchain_community.chat_models.vertexai` 모듈이 최신 `langchain-community`에서 사라져 임포트가
깨졌다. `langchain-community<0.4`로 내려서 해결. **오래된 라이브러리와 최신 라이브러리를 같이 쓸 땐
어느 한쪽만 최신이어도 깨질 수 있다.**

### Bedrock 모델 이슈

세션 도중 Sonnet, Haiku 순서로 일일 토큰 한도를 다 써서 여러 번 모델을 바꿔가며 작업했다. Amazon
Nova Pro로도 바꿔봤는데, 한글 차량번호를 잘못 읽거나("26우5291"→"26운5291"), 환불 질문을 엉뚱한
하위 에이전트로 보내거나, 정책과 반대로 답하는 등 실제 정확성 문제가 발견돼 Claude 계열로 되돌렸다.
**모델을 바꿀 땐 반드시 같은 질문 세트로 정확성을 재확인해야 한다** — 특히 한국어·구조화된 도구
호출이 많은 프로젝트에서는 모델 간 품질 차이가 크다.

### RAGAS 판정 자체의 노이즈

2차 평가에서 실제로 고친 항목(RAG 카테고리 태깅, 표/내용 보존)은 예상대로 점수가 올랐지만
(P15 faithfulness +0.25, P16 +0.31), 손대지 않은 문항 중 일부(N1 −0.50, G3 −0.10)는 오히려
점수가 떨어졌다. 직접 재실행해서 확인해보니 실제 답변은 여전히 정확했고, 판정 LLM이 "차량 번호를
다시 한번 확인해 보시거나" 같은 상투적인 안내 문장을 컨텍스트에 없는 별도 주장으로 보고 감점한
것이었다. **LLM-judge 채점은 온도를 0으로 둬도 실행마다 값이 흔들릴 수 있고, 특히 이런 부수적인
안내 문장에 민감하다** — 점수 변화를 바로 "회귀"로 단정하지 않고, 실제 답변을 다시 확인하는
과정이 필요했다.

## 평가 결과

- `evaluation/run_eval.py`: 규칙 기반 평가(도구 호출 확인 + 금칙어 검사), 28건 중 28건 통과(100%)
- `evaluation/round1_report.md`: RAGAS 1차 평가와 그 결과로 실제로 고친 버그(RAG 카테고리 태깅 누락)
- `evaluation/round2_report.md`: 1차와 동일한 샘플을 다시 채점한 개선폭 —
  answer_relevancy 0.45→0.65(+0.20), faithfulness 0.83→0.79(−0.04, 사유는 위 노이즈 항목 참고),
  context_utilization 0.91→0.91(±0). P11 타임아웃 문제는 컨텍스트 축약으로 해결.
