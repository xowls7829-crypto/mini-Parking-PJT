"""ragas_eval.py 와 round2_eval.py 가 공유하는 RAGAS 평가 유틸리티.
(판정 LLM/임베딩 구성, 컨텍스트 수집, 채점 호출은 두 스크립트에서 완전히 동일해서 여기로 뺐다.)"""

import os

from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
from langchain_core.messages import ToolMessage
from ragas import EvaluationDataset, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import ContextUtilization, Faithfulness, answer_relevancy

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_utilization"]


def collect_contexts(messages, max_chars: int | None = None) -> list[str]:
    """ToolMessage 중 핸드오프 안내("Successfully transferred...")가 아닌 실제 도구 결과만 컨텍스트로 쓴다.
    RAGAS 지표는 이 contexts 를 "근거 자료"로 보고 답변이 여기서 벗어났는지(faithfulness) 등을 채점하므로,
    핸드오프 문구까지 섞으면 아무 의미 없는 문장이 근거로 잡혀 채점이 왜곡된다.
    max_chars 를 주면 (등록 차량 전체 명단처럼) 컨텍스트가 너무 길어 판정 호출이 타임아웃나는 걸 막기 위해
    잘라낸다 — 실제 답변/트레이스에는 영향 없고 채점용 사본만 축약된다."""
    contexts = []
    for m in messages:
        if isinstance(m, ToolMessage):
            text = str(m.content)
            if "transferred" in text.lower():
                continue
            if max_chars and len(text) > max_chars:
                text = text[:max_chars] + " …(이하 생략, 판정 비용 때문에 축약)"
            contexts.append(text)
    return contexts


def make_judge():
    """앱이 쓰는 것과 같은 Bedrock 모델/임베딩을 판정용으로 재사용한다 — 별도 계정·모델 없이
    지금 가능한 자원 안에서 "자체 평가"가 되도록 한 것."""
    judge_llm = ChatBedrockConverse(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        temperature=0,
    )
    judge_embeddings = BedrockEmbeddings(
        model_id=os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"),
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    return LangchainLLMWrapper(judge_llm), LangchainEmbeddingsWrapper(judge_embeddings)


def score(samples, llm, embeddings):
    """SingleTurnSample 목록을 faithfulness/answer_relevancy/context_utilization 으로 채점한다."""
    dataset = EvaluationDataset(samples=samples)
    result = evaluate(
        dataset,
        metrics=[Faithfulness(), answer_relevancy, ContextUtilization()],
        llm=llm,
        embeddings=embeddings,
    )
    return result.to_pandas()
