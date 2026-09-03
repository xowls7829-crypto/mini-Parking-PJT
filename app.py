"""주차장 담당자 Agent를 웹 브라우저에서 대화형으로 쓸 수 있게 하는 로컬 API 서버."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from langchain_core.messages import ToolMessage

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

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
    result = agent.invoke({"messages": [{"role": "user", "content": req.question}]})
    messages = result["messages"]

    trace = []
    contexts = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            trace.append({"tool": call["name"], "args": call["args"]})
        if isinstance(m, ToolMessage):
            contexts.append(m.content)

    answer = final_answer(messages)
    return {"answer": answer, "contexts": contexts, "trace": trace}
