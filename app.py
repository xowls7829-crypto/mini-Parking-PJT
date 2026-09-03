"""주차장 담당자 Agent를 웹 브라우저에서 대화형으로 쓸 수 있게 하는 로컬 API 서버."""

import logging
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from langchain_core.messages import ToolMessage

logger = logging.getLogger("parking_agent")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))  # src/ 를 패키지가 아닌 평범한 모듈 폴더로 두고 쓰기 위한 경로 등록

from agent import agent, final_answer  # noqa: E402

app = FastAPI()


class QueryRequest(BaseModel):
    question: str


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.post("/query")
def query(req: QueryRequest):
    """CLAUDE.md 규약대로 question 을 받아 answer, contexts, trace 를 돌려준다."""
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": req.question}]})
    except Exception:
        # Bedrock 한도 초과(ThrottlingException) 등으로 실패할 수 있다. 원인은 서버 로그에만 남기고,
        # 브라우저에는 스택트레이스 대신 안전한 메시지만 내려준다.
        logger.exception("agent.invoke 실패: question=%r", req.question)
        raise HTTPException(
            status_code=503,
            detail="지금 에이전트가 응답하지 못했습니다. 잠시 후 다시 시도해주세요.",
        )
    messages = result["messages"]

    # output_mode="full_history"(agent.py) 덕분에 감독자→하위 에이전트 핸드오프뿐 아니라
    # 실제 도메인 도구 호출(find_by_vehicle_number 등)까지 messages 안에 그대로 남아 있어,
    # 이 메시지 리스트 하나만 훑으면 trace/contexts 를 재구성할 수 있다.
    trace = []
    contexts = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            trace.append({"tool": call["name"], "args": call["args"]})
        if isinstance(m, ToolMessage):
            contexts.append(m.content)  # 핸드오프 안내문("Successfully transferred...")도 섞여 들어오지만 그대로 둔다

    answer = final_answer(messages)  # messages[-1] 을 바로 못 쓰는 이유는 agent.py의 final_answer docstring 참고
    return {"answer": answer, "contexts": contexts, "trace": trace}
