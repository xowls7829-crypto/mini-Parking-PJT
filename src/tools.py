"""주차장 담당자 Agent가 쓰는 도메인 도구."""

from datetime import datetime

from langchain.tools import tool

from retriever import CAR_CATEGORY_BY_MODEL, load_carlist, search_vehicles

FREE_MINUTES_BY_TYPE = {
    "임직원": 240,  # 4시간
    "방문객": 30,
}
DEFAULT_FREE_MINUTES = FREE_MINUTES_BY_TYPE["임직원"]
UNIT_MINUTES = 30
UNIT_FEE = 1000
DAY_MINUTES = 24 * 60


HIDDEN_FIELDS_GENERAL = {"discount_type", "owner_name"}
HIDDEN_FIELDS_ADMIN = {"discount_type"}


def _public_record(record: dict) -> dict:
    """일반 조회에서 노출하면 안 되는 필드(할인 여부, 차주 이름)를 뺀 사본을 만든다."""
    return {k: v for k, v in record.items() if k not in HIDDEN_FIELDS_GENERAL}


def _admin_record(record: dict) -> dict:
    """관리자 조회용 사본. 할인 여부만 빼고 차주 이름은 남긴다."""
    return {k: v for k, v in record.items() if k not in HIDDEN_FIELDS_ADMIN}


def _fee_for_period(minutes: int, free_minutes: int) -> int:
    """24시간을 넘지 않는 하나의 구간에 대한 요금. free_minutes 까지 무료, 이후 30분당 1000원(올림)."""
    if minutes <= free_minutes:
        return 0
    billable = minutes - free_minutes
    units = -(-billable // UNIT_MINUTES)
    return units * UNIT_FEE


def _calc_fee(total_minutes: int, free_minutes: int) -> int:
    """24시간 단위로 반복 적용해 총 요금을 계산한다."""
    full_days, remainder = divmod(total_minutes, DAY_MINUTES)
    return full_days * _fee_for_period(DAY_MINUTES, free_minutes) + _fee_for_period(remainder, free_minutes)


@tool
def find_by_vehicle_number(vehicle_number: str) -> str:
    """차량 번호로 입출입 기록을 조회한다. 등록되지 않은 번호면 등록되지 않았다고만 답하고 추측하지 않는다."""
    data = load_carlist()
    for record in data["records"]:
        if record["vehicle_number"] == vehicle_number:
            return str(_public_record(record))
    return f"{vehicle_number}는 등록되지 않은 차량입니다."


@tool
def find_by_department(department: str) -> str:
    """부서명으로 그 부서 소속 차량들의 입출입 현황을 조회한다. 해당 부서 차량이 없으면 없다고 답한다."""
    data = load_carlist()
    matched = [_public_record(r) for r in data["records"] if r["department"] == department]
    if not matched:
        return f"{department} 소속 차량 기록이 없습니다."
    return str(matched)


@tool
def search_vehicles_semantic(query: str) -> str:
    """전기차, SUV, 고급 세단처럼 정확한 필드값이 아니라 자연어 표현으로 차량을 찾을 때 쓴다.
    차량번호·부서명처럼 정확한 값을 아는 조회에는 이 도구를 쓰지 말고 다른 도구를 쓴다.
    이 도구가 실제로 찾아온 차량만 답하고, 찾아오지 않은 차량을 지어내지 않는다."""
    results = search_vehicles(query, k=5)
    if not results:
        return "의미상 비슷한 차량을 찾지 못했습니다."

    annotated = []
    for r in results:
        record = _public_record(r)
        record["car_category"] = CAR_CATEGORY_BY_MODEL.get(r.get("car_model"), "미분류")
        annotated.append(record)
    return str(annotated)


@tool
def get_recent_vehicles(count: int = 5) -> str:
    """가장 최근에 입차한 차량 순서로 상위 count 대를 알려준다. count 를 지정하지 않으면 5대를 기준으로 한다."""
    data = load_carlist()
    recent = sorted(data["records"], key=lambda r: r["entry_time"], reverse=True)[:count]
    return str([_public_record(r) for r in recent])


@tool
def get_available_spaces() -> str:
    """현재 전체 주차면수와 잔여 주차 가능 대수를 알려준다."""
    data = load_carlist()
    total = data["total_spaces"]
    parked = sum(1 for r in data["records"] if r["exit_time"] is None)
    available = total - parked
    return f"전체 {total}대 중 {available}대 주차 가능합니다."


@tool
def calculate_fee(vehicle_number: str) -> str:
    """차량 번호로 주차 요금을 계산한다.
    정기권 차량은 별도 요금이 없다. 방문객은 30분 무료, 임직원은 4시간 무료이며 이후 30분당 1000원이고,
    24시간이 지나면 같은 기준으로 다시 적용된다. 무료 시간 내에 출차하면 0원(무료회차 적용)으로 안내한다.
    할인이 적용된 차량이면 원래 금액과 할인된 최종 금액을 함께 알려주지만, 할인 사유는 결과에 담지 않는다.
    등록되지 않은 차량이면 등록되지 않았다고만 답한다."""
    data = load_carlist()
    record = next((r for r in data["records"] if r["vehicle_number"] == vehicle_number), None)
    if record is None:
        return f"{vehicle_number}는 등록되지 않은 차량입니다."

    vehicle_type = record.get("vehicle_type", "임직원")
    if vehicle_type == "정기권":
        return f"{vehicle_number}는 정기권 차량으로 별도 주차 요금이 없습니다."

    entry = datetime.fromisoformat(record["entry_time"])
    exit_ = datetime.fromisoformat(record["exit_time"]) if record["exit_time"] else datetime.now()
    minutes = max(0, int((exit_ - entry).total_seconds() // 60))

    free_minutes = FREE_MINUTES_BY_TYPE.get(vehicle_type, DEFAULT_FREE_MINUTES)
    original_fee = _calc_fee(minutes, free_minutes)
    status = "출차 완료 기준" if record["exit_time"] else "현재 시각 기준 예상 요금(아직 주차 중)"

    if original_fee == 0:
        return f"{vehicle_number} 주차 요금: 0원 (무료회차 적용, {status})"

    if record.get("discount_type"):
        final_fee = original_fee // 2
        return (
            f"{vehicle_number} 주차 요금: 원래 {original_fee}원에서 할인이 적용되어 "
            f"최종 {final_fee}원입니다. ({status})"
        )
    return f"{vehicle_number} 주차 요금: {original_fee}원 ({status})"


@tool
def list_all_vehicles_admin(vehicle_type: str | None = None) -> str:
    """[관리자 전용] 등록된 차량을 차량번호·부서·차주 이름과 함께 조회한다.
    vehicle_type을 "임직원"(일반 차량), "방문객", "정기권" 중 하나로 주면 그 구분의 차량만 필터링해서 보여주고,
    비워두면 전체를 보여준다. 담당자가 차량을 관리할 목적으로만 사용하며,
    일반 조회 도구에서는 차주 이름을 절대 포함하지 않는다."""
    data = load_carlist()
    records = data["records"]
    if vehicle_type:
        records = [r for r in records if r.get("vehicle_type") == vehicle_type]
        if not records:
            return f"'{vehicle_type}' 구분의 등록 차량이 없습니다."
    return str([_admin_record(r) for r in records])
