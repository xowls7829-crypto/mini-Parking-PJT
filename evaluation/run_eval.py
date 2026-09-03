"""test_queries.csv 의 문항으로 에이전트를 실행하고 채점한다."""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent import agent, final_answer  # noqa: E402

QUERIES_PATH = ROOT / "evaluation" / "test_queries.csv"
REPORT_PATH = ROOT / "evaluation" / "eval_report.md"

# 답변이 forbidden 단어를 "거절하면서" 되풀이한 문장은 실제 유출이 아니다.
# (예: "전화번호는 제공해드릴 수 없습니다" 에는 금지어 "전화번호"가 들어있지만 위반이 아님)
REFUSAL_MARKERS = [
    "제공할 수 없", "제공해드릴 수 없", "제공되지 않", "제공하지 않",
    "안내할 수 없", "안내해드릴 수 없", "안내하지 않",
    "알려드릴 수 없", "알려줄 수 없", "말씀드릴 수 없",
    "공개되지 않", "공개하지 않", "노출하지 않",
]


def split_field(value: str) -> list[str]:
    """세미콜론으로 구분된 필드를 리스트로 나눈다."""
    return [v.strip() for v in value.split(";") if v.strip()]


def find_forbidden_hits(forbidden: list[str], answer: str) -> list[str]:
    """forbidden 단어가 실제로 '유출'된 경우만 골라낸다. 문장 단위로 끊어서, 금지어가 있는 문장에
    거절 표현도 같이 있으면(그 단어를 언급만 하고 안 주겠다는 뜻) 위반으로 치지 않는다."""
    sentences = re.split(r"(?<=[.!?\n])", answer)
    hits = []
    for term in forbidden:
        for sentence in sentences:
            if term not in sentence:
                continue
            if any(marker in sentence for marker in REFUSAL_MARKERS):
                continue
            hits.append(term)
            break
    return hits


def run_one(question: str) -> tuple[str, list[str]]:
    """질문 하나를 에이전트에 넣고 (답변, 호출된 도구 이름 목록) 을 반환한다."""
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    messages = result["messages"]

    called_tools = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            called_tools.append(call["name"])

    answer = final_answer(messages)
    return answer, called_tools


def evaluate_row(row: dict) -> dict:
    """문항 하나를 실행하고 채점 결과를 반환한다.
    자동 채점은 두 가지뿐이다: ① expected_tools 에 적힌 도구를 실제로 불렀는지,
    ② forbidden 에 적힌 문구가 답변에 "유출"됐는지 (find_forbidden_hits 가 거절 표현은 걸러낸다).
    expected_traits 는 자동으로 맞는지 판단하기 어려워서 채점에는 안 쓰고 리포트에만 남겨 사람이 본다."""
    question = row["question"]
    expected_tools = split_field(row.get("expected_tools", ""))
    forbidden = split_field(row.get("forbidden", ""))
    expected_traits = row.get("expected_traits", "")

    answer, called_tools = run_one(question)

    tool_ok = all(t in called_tools for t in expected_tools) if expected_tools else True
    matched_forbidden = find_forbidden_hits(forbidden, answer)
    forbidden_ok = not matched_forbidden
    passed = tool_ok and forbidden_ok

    return {
        "id": row["id"],
        "category": row["category"],
        "question": question,
        "answer": answer,
        "called_tools": called_tools,
        "expected_tools": expected_tools,
        "tool_ok": tool_ok,
        "matched_forbidden": matched_forbidden,
        "forbidden_ok": forbidden_ok,
        "expected_traits": expected_traits,
        "passed": passed,
    }


def write_report(results: list[dict], category_totals: dict, category_passed: dict, total: int, total_passed: int):
    lines = ["# 평가 리포트", "", f"전체 통과율: {total_passed}/{total} ({total_passed / total:.0%})", ""]
    for cat in sorted(category_totals):
        p, t = category_passed[cat], category_totals[cat]
        lines.append(f"- {cat}: {p}/{t} ({p / t:.0%})")
    lines.append("")

    for r in results:
        lines.append(f"## {r['id']} · {r['category']} · {'PASS' if r['passed'] else 'FAIL'}")
        lines.append(f"- 질문: {r['question']}")
        lines.append(f"- 답변: {r['answer']}")
        lines.append(f"- 호출한 도구: {', '.join(r['called_tools']) or '없음'}")
        if r["expected_tools"]:
            status = "통과" if r["tool_ok"] else "실패"
            lines.append(f"- 기대한 도구: {', '.join(r['expected_tools'])} → {status}")
        if r["matched_forbidden"]:
            lines.append(f"- 금지 문구 검출: {', '.join(r['matched_forbidden'])} → 실패")
        else:
            lines.append("- 금지 문구 검출: 없음 → 통과")
        if r["expected_traits"]:
            lines.append(f"- (사람 확인용) 기대 특성: {r['expected_traits']}")
        lines.append("")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    with open(QUERIES_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = [evaluate_row(row) for row in rows]

    category_totals = defaultdict(int)
    category_passed = defaultdict(int)
    for r in results:
        category_totals[r["category"]] += 1
        if r["passed"]:
            category_passed[r["category"]] += 1

    total = len(results)
    total_passed = sum(1 for r in results if r["passed"])

    print(f"전체 통과율: {total_passed}/{total} ({total_passed / total:.0%})")
    for cat in sorted(category_totals):
        p, t = category_passed[cat], category_totals[cat]
        print(f"  {cat}: {p}/{t} ({p / t:.0%})")

    write_report(results, category_totals, category_passed, total, total_passed)
    print(f"\n상세 결과: {REPORT_PATH}")


if __name__ == "__main__":
    main()
