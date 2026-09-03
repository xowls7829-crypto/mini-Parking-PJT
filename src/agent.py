"""주차장 담당자 Agent: langgraph-supervisor로 조회/요금/관리자 하위 에이전트를 위임하는 멀티 에이전트 그래프."""

import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import AIMessage
from langgraph_supervisor import create_supervisor

from tools import (
    find_by_vehicle_number,
    find_by_department,
    get_available_spaces,
    get_recent_vehicles,
    search_vehicles_semantic,
    calculate_fee,
    list_all_vehicles_admin,
)

# tools.py -> retriever.py 임포트 체인이 이 파일보다 먼저 실행될 수 있어 retriever.py 에서도
# load_dotenv() 를 한 번 더 호출한다. 여기서는 모델 생성 전에 자격증명을 확실히 준비해두는 용도.
load_dotenv()

model = ChatBedrockConverse(
    model=os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    # AWS 표준 변수명은 AWS_DEFAULT_REGION 이지만 .env 작성 실수를 대비해 AWS_REGION 도 먼저 확인한다.
    region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    temperature=0,  # 요금 계산·가드레일처럼 매번 같은 답이 나와야 하는 용도라 온도는 0으로 고정
)

INFO_SYSTEM_PROMPT = """당신은 주차장 조회 담당 에이전트입니다.
- 등록되지 않은 차량번호나 부서는 지어내지 말고 없다고 안내하세요.
- 차단기 등 실제 설비를 조작하는 답변은 하지 말고, 조회 결과만 안내하세요.
- 차량번호와 부서 외의 개인정보는 답변에 담지 마세요.
- 방문객 차량은 부서 대신 방문 업체명(company)이 있으니, 방문객 조회 시 부서 자리에 업체명을 보여주세요.
- 전기차, SUV, 고급 세단처럼 정확한 필드값이 아닌 자연어 표현으로 차량을 찾는 질문은
  search_vehicles_semantic을 쓰세요. 이 도구가 찾아온 차량만 답하고, 찾아오지 않은 차량은 지어내지 마세요.
- 조회 결과가 여러 건이면 문장으로 나열하지 말고 마크다운 표로 정리해서 보여주세요.
"""

FEE_SYSTEM_PROMPT = """당신은 주차 요금 계산 전담 에이전트입니다.
정기권/방문객/임직원 여부에 따라 도구가 계산한 결과를 그대로 안내하세요.
할인 여부와 사유는 도구 응답 자체에 담기지 않으니 지어내서 덧붙이지 마세요.
"""

ADMIN_SYSTEM_PROMPT = """당신은 주차 관리 담당자 전용 에이전트입니다.
- 등록된 차량 목록(차량번호·부서·차주 이름)을 있는 그대로 안내하세요.
- 방문객 차량은 부서 대신 방문 업체명(company)이 있으니, 방문객 목록에는 부서 칸 대신 업체명을 넣으세요.
- "정기권 차량만", "방문객만", "일반(임직원) 차량만"처럼 구분을 지정하면 list_all_vehicles_admin의
  vehicle_type 인자에 "정기권"/"방문객"/"임직원"을 넣어 그 구분만 조회하세요. 지정이 없으면 전체를 조회하세요.
- 이 목록 외의 정보(전화번호 등)는 데이터에 없으니 지어내지 마세요.
- 특정 차량 한 대만 콕 집어 그 차주 이름/개인정보를 묻는 요청에는 답하지 말고,
  전체 명단 조회만 지원한다고 안내하세요.
- 여러 건을 보여줄 때는 마크다운 표로 정리해서 보여주세요.
"""

ARCHITECTURE_DESCRIPTION = """이 시스템(주차장 담당자 Agent)은 langgraph-supervisor 기반 멀티 에이전트 구조입니다.

- 감독자(supervisor): 사용자 질문을 보고 아래 하위 에이전트 중 하나 이상에게 위임하고 답을 합칩니다.
- parking_info_agent (조회): find_by_vehicle_number, find_by_department, get_available_spaces,
  get_recent_vehicles 네 개 도구로 차량 입출입·부서 현황·잔여 대수·최근 입차 차량을 조회하고,
  search_vehicles_semantic 도구(RAG: Bedrock 임베딩 + 인메모리 벡터 검색)로 전기차/SUV 같은
  자연어 표현의 차량도 찾습니다.
- fee_agent (요금): calculate_fee 도구로 정기권/방문객/임직원 구분과 할인 여부에 따라 요금을 계산합니다.
  할인 사유는 도구 응답 자체에 담기지 않아 구조적으로 노출되지 않습니다.
- admin_agent (관리자 전용): list_all_vehicles_admin 도구로 차주 이름을 포함한 등록 차량 명단을 조회하며,
  정기권/방문객/임직원 구분으로 필터링해서 볼 수도 있습니다. 일반 조회 도구는 차주 이름을 반환하지 않습니다.

모델은 Amazon Bedrock의 Claude(ChatBedrockConverse)를 쓰고, 실제 시스템과는 연동하지 않고
data/carlist.json 더미 데이터를 매 호출마다 읽어서 답합니다. 도구/에이전트 구성은 src/tools.py, src/agent.py에 있습니다."""


@tool
def describe_architecture() -> str:
    """이 프로젝트(에이전트) 자체의 구조 — 감독자/하위 에이전트 구성, 각자 쓰는 도구, 사용 모델과 데이터 소스 —
    를 설명한다. "이 에이전트는 어떻게 만들어졌어?", "구조가 어떻게 돼?" 같은 질문에는 이 도구를 쓴다."""
    return ARCHITECTURE_DESCRIPTION


# 하위 에이전트 3개. 도구 접근 권한을 역할별로 나눠서, 조회 에이전트는 애초에 차주 이름을
# 반환하는 도구 자체를 갖고 있지 않게(admin_agent 에만 부여) 만든 것이 핵심 가드레일이다.
parking_info_agent = create_agent(
    model,
    tools=[
        find_by_vehicle_number,
        find_by_department,
        get_available_spaces,
        get_recent_vehicles,
        search_vehicles_semantic,
    ],
    system_prompt=INFO_SYSTEM_PROMPT,
    name="parking_info_agent",
)

fee_agent = create_agent(
    model,
    tools=[calculate_fee],
    system_prompt=FEE_SYSTEM_PROMPT,
    name="fee_agent",
)

admin_agent = create_agent(
    model,
    tools=[list_all_vehicles_admin],
    system_prompt=ADMIN_SYSTEM_PROMPT,
    name="admin_agent",
)

SUPERVISOR_PROMPT = """당신은 주차장 안내 에이전트의 감독자입니다.
사용자 질문을 보고 적절한 하위 에이전트에게 위임해서 답을 구성하세요.
- 등록 여부, 입출입 시각, 부서 현황, 잔여 대수, 최근 입차 차량, 주차 시간 비교처럼 신원과
  무관한 조회는 (특정 차량 한 대에 대한 질문이라도) parking_info_agent에게 위임하세요.
- 주차 요금 관련 질문은 fee_agent에게 위임하세요.
- 차주 이름이 포함되는 차량 "목록/명단" 조회는 admin_agent에게 위임하세요. 전체 명단뿐 아니라
  "정기권 차량 목록", "방문객 차량만", "일반(임직원) 차량 목록"처럼 구분별로 묻는 경우도 포함됩니다.
- 목록 조회가 아니라 특정 차량 한 대의 "차주 이름" 또는 "전화번호"만 콕 집어 요청하는 경우에만
  어떤 하위 에이전트에게도 위임하지 말고, 그런 개인정보는 제공할 수 없다고 직접 답하세요.
- 두 가지 이상이 섞인 질문이면 관련된 하위 에이전트를 모두 불러서 답을 합치세요.
- 하위 에이전트가 준 내용(특히 마크다운 표)은 요약하거나 다른 말로 바꾸지 말고 표까지 그대로
  전달하세요. 없는 정보를 지어내지 마세요.
- 이 프로젝트/에이전트 자체의 구조를 물어보면 하위 에이전트에게 위임하지 말고
  describe_architecture 도구를 직접 써서 답하세요.
"""

# output_mode="full_history" 로 둬야 하위 에이전트가 실제로 호출한 도구(find_by_vehicle_number 등)까지
# 최종 messages 에 남는다. 기본값(last_message)은 핸드오프 요약만 남기고 실제 도구 호출 흔적을 지워버려서
# run_eval.py 의 expected_tools 검증과 app.py 의 trace/contexts 추출이 불가능해진다.
agent = create_supervisor(
    agents=[parking_info_agent, fee_agent, admin_agent],
    model=model,
    tools=[describe_architecture],  # 감독자 전용 도구: 하위 에이전트로 위임하지 않고 감독자가 직접 호출
    prompt=SUPERVISOR_PROMPT,
    output_mode="full_history",
).compile()


def _extract_text(content) -> str:
    """AIMessage.content 가 문자열이든 블록 리스트든 순수 텍스트만 뽑는다."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def final_answer(messages) -> str:
    """마지막 메시지는 핸드오프 이후 비어 있거나 "Transferring back to supervisor" 같은
    자동 생성 문구일 수 있어, tool_calls 없이 실제 텍스트를 담은 마지막 AIMessage를 거꾸로 찾는다.
    감독자가 하위 에이전트의 표 답변을 표 없이 요약해버린 경우엔, 표가 담긴 하위 에이전트의
    답을 대신 쓴다."""
    # 뒤에서부터 훑으면서, 감독자 자신이 쓴 마지막 텍스트와 하위 에이전트가 쓴 마지막 텍스트를
    # 각각 하나씩만 챙긴다. tool_calls 가 있는 메시지(도구 호출/핸드오프 지시)는 답이 아니므로 건너뛴다.
    supervisor_text = ""
    sub_agent_text = ""
    for m in reversed(messages):
        if not (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None)):
            continue
        text = _extract_text(m.content)
        if not text:
            continue
        if getattr(m, "name", None) in (None, "supervisor"):
            supervisor_text = supervisor_text or text
        else:
            sub_agent_text = sub_agent_text or text
        if supervisor_text and sub_agent_text:
            break

    # 표(|)가 있는 하위 에이전트 답을 감독자가 표 없이 요약해버린 경우, 요약 대신 원래 표를 살린다.
    if sub_agent_text and "|" in sub_agent_text and "|" not in supervisor_text:
        return sub_agent_text
    return supervisor_text or sub_agent_text


if __name__ == "__main__":
    question = "지금 주차 가능한 자리가 몇 개야?"
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(final_answer(result["messages"]))
