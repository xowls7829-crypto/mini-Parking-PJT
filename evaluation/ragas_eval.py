"""test_queries.csv 중 대표 문항을 뽑아 RAGAS 지표(faithfulness/answer_relevancy/context_utilization)로 채점한다."""

import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent import agent, final_answer  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse  # noqa: E402
from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics import ContextUtilization, Faithfulness, answer_relevancy  # noqa: E402

QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
REPORT_PATH = ROOT / "evaluation" / "round1_report.md"

# 카테고리별로 고르게 뽑은 대표 문항 (판정 LLM 호출 비용을 고려해 전체 25건 중 일부만 채점)
SAMPLE_IDS = ["P1", "P2", "P7", "P8", "P11", "P15", "P16", "N1", "E3", "G1", "G3"]

METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_utilization"]


def collect_contexts(messages) -> list[str]:
    """ToolMessage 중 핸드오프 안내가 아닌 실제 도구 결과만 컨텍스트로 쓴다."""
    contexts = []
    for m in messages:
        if isinstance(m, ToolMessage):
            text = str(m.content)
            if "transferred" in text.lower():
                continue
            contexts.append(text)
    return contexts


def run_samples():
    with open(QUERIES_PATH, encoding="utf-8") as f:
        rows = {row["id"]: row for row in csv.DictReader(f)}

    samples, rows_used = [], []
    for rid in SAMPLE_IDS:
        row = rows[rid]
        result = agent.invoke({"messages": [{"role": "user", "content": row["question"]}]})
        messages = result["messages"]
        answer = final_answer(messages)
        contexts = collect_contexts(messages) or [answer]

        samples.append(SingleTurnSample(user_input=row["question"], response=answer, retrieved_contexts=contexts))
        rows_used.append(
            {"id": rid, "category": row["category"], "question": row["question"], "answer": answer, "contexts": contexts}
        )
    return samples, rows_used


def write_report(rows_used, scores_df):
    lines = [
        "# Round 1 평가 리포트 (RAGAS)",
        "",
        f"test_queries.csv 중 대표 {len(rows_used)}건을 뽑아 RAGAS(faithfulness/answer_relevancy/context_utilization)로 채점했다.",
        "",
    ]

    avg = scores_df[METRIC_NAMES].mean()
    for name in METRIC_NAMES:
        lines.append(f"- 평균 {name}: {avg[name]:.2f}")
    lines.append("")

    for i, row in enumerate(rows_used):
        s = scores_df.iloc[i]
        lines.append(f"## {row['id']} · {row['category']}")
        lines.append(f"- 질문: {row['question']}")
        lines.append(f"- 답변: {row['answer']}")
        lines.append(f"- 컨텍스트: {row['contexts']}")
        scores = " / ".join(f"{name}={s[name]:.2f}" for name in METRIC_NAMES)
        lines.append(f"- 점수: {scores}")
        lines.append("")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    samples, rows_used = run_samples()
    dataset = EvaluationDataset(samples=samples)

    judge_llm = ChatBedrockConverse(
        model=os.environ.get("BEDROCK_MODEL_ID"),
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        temperature=0,
    )
    judge_embeddings = BedrockEmbeddings(
        model_id=os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"),
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    result = evaluate(
        dataset,
        metrics=[Faithfulness(), answer_relevancy, ContextUtilization()],
        llm=LangchainLLMWrapper(judge_llm),
        embeddings=LangchainEmbeddingsWrapper(judge_embeddings),
    )

    scores_df = result.to_pandas()
    write_report(rows_used, scores_df)

    print(scores_df[METRIC_NAMES])
    print(f"\n상세 결과: {REPORT_PATH}")


if __name__ == "__main__":
    main()
