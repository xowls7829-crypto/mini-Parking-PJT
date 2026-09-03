"""주차장 더미 데이터 적재."""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "carlist.json"


def load_carlist() -> dict:
    """data/carlist.json 을 매번 새로 읽어 반환한다."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
