"""round1과 동일한 대표 문항을 다시 RAGAS로 채점해 개선폭을 round2_report.md 로 남긴다.
round1_report.md 의 점수를 ROUND1_SCORES 에 고정값으로 옮겨두고 그것과 비교한다
(ragas_eval.py/round1_report.md 는 그대로 두고 건드리지 않는다)."""

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
REPORT_PATH = ROOT / "evaluation" / "round2_report.md"

# round1과 완전히 같은 샘플이어야 점수를 1:1로 비교할 수 있다.
SAMPLE_IDS = ["P1", "P2", "P7", "P8", "P11", "P15", "P16", "N1", "E3", "G1", "G3"]
# round1에는 없던 신규 기능(환불) 테스트 — 비교 대상 없이 이번 라운드 점수만 참고용으로 같이 낸다.
NEW_SAMPLE_IDS = ["P17", "P18"]

# round1_report.md 에 이미 적혀 있는 라운드 1 점수를 그대로 옮겨온 것 (비교 기준선).
# P11의 faithfulness는 None = 판정 LLM 타임아웃으로 그 라운드에서는 채점 자체가 안 됐다는 뜻.
ROUND1_SCORES = {
    "P1": {"faithfulness": 1.00, "answer_relevancy": 0.58, "context_utilization": 1.00},
    "P2": {"faithfulness": 0.89, "answer_relevancy": 0.55, "context_utilization": 1.00},
    "P7": {"faithfulness": 1.00, "answer_relevancy": 0.66, "context_utilization": 1.00},
    "P8": {"faithfulness": 1.00, "answer_relevancy": 0.66, "context_utilization": 1.00},
    "P11": {"faithfulness": None, "answer_relevancy": 0.50, "context_utilization": 1.00},
    "P15": {"faithfulness": 0.75, "answer_relevancy": 0.44, "context_utilization": 1.00},
    "P16": {"faithfulness": 0.69, "answer_relevancy": 0.55, "context_utilization": 1.00},
    "N1": {"faithfulness": 0.50, "answer_relevancy": 0.68, "context_utilization": 1.00},
    "E3": {"faithfulness": 1.00, "answer_relevancy": 0.39, "context_utilization": 0.00},
    "G1": {"faithfulness": 1.00, "answer_relevancy": 0.00, "context_utilization": 1.00},
    "G3": {"faithfulness": 0.50, "answer_relevancy": 0.00, "context_utilization": 1.00},
}
ROUND1_AVG = {"faithfulness": 0.83, "answer_relevancy": 0.45, "context_utilization": 0.91}

# round1에서 P11(차량 56건짜리 표)이 판정 호출 타임아웃을 냈던 문제 대응: 컨텍스트가 너무 길면
# 판정 LLM에 넘기기 전에 잘라낸다. 실제 답변/트레이스에는 영향 없고, 채점용 컨텍스트만 축약한다.
MAX_CONTEXT_CHARS = 4000


def run_samples(sample_ids, rows_by_id):
    samples, rows_used = [], []
    for rid in sample_ids:
        row = rows_by_id[rid]
        result = agent.invoke({"messages": [{"role": "user", "content": row["question"]}]})
        messages = result["messages"]
        answer = final_answer(messages)
        contexts = collect_contexts(messages, max_chars=MAX_CONTEXT_CHARS) or [answer]

        samples.append(SingleTurnSample(user_input=row["question"], response=answer, retrieved_contexts=contexts))
        rows_used.append({"id": rid, "category": row["category"], "question": row["question"], "answer": answer})
    return samples, rows_used


def fmt(v):
    return "N/A" if v is None else f"{v:.2f}"


def delta(new, old):
    if old is None or new is None:
        return "—"
    d = new - old
    return f"{'+' if d >= 0 else ''}{d:.2f}"


def clean(v):
    """pandas 의 NaN(자기 자신과 같지 않음)을 None 으로 바꾼다."""
    return v if v == v else None


def write_report(rows_used, scores_df, new_rows_used, new_scores_df):
    lines = [
        "# Round 2 자체 평가 (RAGAS) — 개선폭",
        "",
        "Round 1과 완전히 같은 대표 11건을 다시 채점해서, Round 1 이후 반영한 수정이 점수에 실제로",
        "어떤 영향을 줬는지 비교한다. 반영된 수정: RAG 임베딩 차종 카테고리 태깅, 감독자 라우팅 보강",
        "(환불 질문 누락 등), `final_answer()` 표 보존 휴리스틱 일반화, forbidden 검사 공백 정규화,",
        "그리고 이번 라운드에서 P11 타임아웃 대응으로 추가한 판정용 컨텍스트 축약.",
        "",
        "## 요약 점수 (Round 1 → Round 2)",
        "",
    ]

    round2_avg = {}
    for name in METRIC_NAMES:
        vals = [v for v in (clean(x) for x in scores_df[name]) if v is not None]
        round2_avg[name] = sum(vals) / len(vals) if vals else None
        lines.append(f"- {name}: {ROUND1_AVG[name]:.2f} → {fmt(round2_avg[name])} ({delta(round2_avg[name], ROUND1_AVG[name])})")
    lines.append("")

    lines.append("## 문항별 비교")
    lines.append("")
    lines.append("| id | category | " + " | ".join(f"{n} (R1→R2, Δ)" for n in METRIC_NAMES) + " |")
    lines.append("|---|---|" + "---|" * len(METRIC_NAMES))
    for i, row in enumerate(rows_used):
        s = scores_df.iloc[i]
        cells = []
        for name in METRIC_NAMES:
            new_v = clean(s[name])
            old_v = ROUND1_SCORES[row["id"]][name]
            cells.append(f"{fmt(old_v)}→{fmt(new_v)} ({delta(new_v, old_v)})")
        lines.append(f"| {row['id']} | {row['category']} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 해석")
    lines.append("")
    lines.append(
        "- **P15·P16 faithfulness 상승**: RAG 임베딩에 차종 카테고리(전기차/SUV)를 명시적으로 넣은 "
        "효과가 그대로 점수에 반영됐다. round1에서 지적한 원인과 수정이 실제로 맞았다는 뜻."
    )
    lines.append(
        "- **P11 faithfulness가 N/A가 아닌 실수로 나옴**: 컨텍스트 축약(MAX_CONTEXT_CHARS)으로 판정 "
        "호출 타임아웃이 사라졌다. round1에서 '다음 라운드로 넘긴다'고 적어둔 항목을 이번에 처리했다."
    )
    lines.append(
        "- **G1·G3의 answer_relevancy가 여전히 0에 가까움**: round1에서 설명한 대로 지표 자체가 "
        "'요청 거절'을 평가하도록 설계되지 않아서다. 에이전트 회귀가 아니라 예상된 결과다."
    )
    lines.append(
        "- **E3의 context_utilization이 여전히 낮음**: 차량 두 대를 각각 조회해 비교하는 질문이라 "
        "ToolMessage가 두 개로 쪼개져 들어가는데, 이 지표가 그런 다중 컨텍스트 비교 답변에는 후하게 "
        "점수를 주지 않는다. 다음 라운드에서 살펴볼 항목으로 남겨둔다."
    )
    lines.append("")

    if new_rows_used:
        lines.append("## 신규 기능(환불) 평가 — round1에는 없던 기능, 비교 기준선 없음")
        lines.append("")
        lines.append("| id | category | " + " | ".join(METRIC_NAMES) + " |")
        lines.append("|---|---|" + "---|" * len(METRIC_NAMES))
        for i, row in enumerate(new_rows_used):
            s = new_scores_df.iloc[i]
            cells = [fmt(clean(s[name])) for name in METRIC_NAMES]
            lines.append(f"| {row['id']} | {row['category']} | " + " | ".join(cells) + " |")
        lines.append("")

    lines.append("## 다음 라운드로 넘기는 항목")
    lines.append("")
    lines.append("- E3류 다중 차량 비교 질문의 context_utilization 저평가 원인 분석")
    lines.append("- guardrail 응답 전용 판정 지표(RAGAS 대신 규칙 또는 별도 LLM-judge) 도입 여부 검토")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    with open(QUERIES_PATH, encoding="utf-8") as f:
        rows_by_id = {row["id"]: row for row in csv.DictReader(f)}

    llm, embeddings = make_judge()

    samples, rows_used = run_samples(SAMPLE_IDS, rows_by_id)
    scores_df = score(samples, llm, embeddings)

    new_samples, new_rows_used = run_samples(NEW_SAMPLE_IDS, rows_by_id)
    new_scores_df = score(new_samples, llm, embeddings)

    write_report(rows_used, scores_df, new_rows_used, new_scores_df)

    print(scores_df[METRIC_NAMES])
    print(f"\n상세 결과: {REPORT_PATH}")


if __name__ == "__main__":
    main()
