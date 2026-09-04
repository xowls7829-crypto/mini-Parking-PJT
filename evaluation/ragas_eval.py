"""test_queries.csv 중 대표 문항을 뽑아 RAGAS 지표(faithfulness/answer_relevancy/context_utilization)로 채점한다."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evaluation"))

from agent import agent, final_answer  # noqa: E402
from ragas import SingleTurnSample  # noqa: E402
from ragas_common import METRIC_NAMES, collect_contexts, make_judge, score  # noqa: E402

QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
REPORT_PATH = ROOT / "evaluation" / "round1_report.md"

# 카테고리별로 고르게 뽑은 대표 문항 (판정 LLM 호출 비용을 고려해 전체 25건 중 일부만 채점)
SAMPLE_IDS = ["P1", "P2", "P7", "P8", "P11", "P15", "P16", "N1", "E3", "G1", "G3"]


def run_samples():
    with open(QUERIES_PATH, encoding="utf-8") as f:
        rows = {row["id"]: row for row in csv.DictReader(f)}

    samples, rows_used = [], []
    for rid in SAMPLE_IDS:
        row = rows[rid]
        result = agent.invoke({"messages": [{"role": "user", "content": row["question"]}]})
        messages = result["messages"]
        answer = final_answer(messages)
        # 가드레일 거절처럼 도구를 아예 안 부른 경우 contexts 가 비게 되는데, RAGAS 샘플은 빈 리스트를
        # 못 받으므로 답변 자체를 컨텍스트로 넣는다(그만큼 그 케이스의 지표는 참고용일 뿐임을 report에 명시).
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
    llm, embeddings = make_judge()
    scores_df = score(samples, llm, embeddings)

    write_report(rows_used, scores_df)

    print(scores_df[METRIC_NAMES])
    print(f"\n상세 결과: {REPORT_PATH}")


if __name__ == "__main__":
    main()
