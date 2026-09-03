"""주차장 더미 데이터 적재 및 RAG 파이프라인."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_aws import BedrockEmbeddings
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore

load_dotenv()  # 이 모듈이 agent.py보다 먼저 임포트돼도 AWS 자격증명이 준비되도록 여기서도 로드한다

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "carlist.json"

_embeddings = BedrockEmbeddings(
    model_id=os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"),
    region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
)

_vector_store = None  # 첫 검색 시점에 한 번만 만들어 재사용(레코드가 59건뿐이라 매번 새로 임베딩할 필요는 없음)

# 차종 이름만으로는 "전기차"/"SUV" 같은 자연어 질의와 임베딩이 잘 안 붙어서, 카테고리를 직접 붙여준다.
CAR_CATEGORY_BY_MODEL = {
    "EV6": "전기차 SUV",
    "아이오닉5": "전기차",
    "싼타페": "SUV",
    "투싼": "SUV",
    "스포티지": "SUV",
    "티볼리": "SUV",
    "팰리세이드": "SUV",
    "셀토스": "SUV",
    "QM6": "SUV",
    "그랜저": "세단 고급차",
    "G80": "세단 고급차",
    "K5": "세단",
    "K7": "세단",
    "쏘나타": "세단",
    "아반떼": "세단",
    "모닝": "경차",
    "레이": "경차",
    "포터": "트럭 상용차",
    "스타렉스": "밴 상용차",
}


def load_carlist() -> dict:
    """data/carlist.json 을 매번 새로 읽어 반환한다."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _record_to_text(record: dict) -> str:
    """레코드 하나를 임베딩용 문장으로 만든다. 차종 등 자연어로 물어볼 만한 항목을 문장에 다 풀어둔다."""
    status = "출차 완료" if record["exit_time"] else "주차 중"
    affiliation = record.get("company") or record.get("department") or "소속 없음"
    car_model = record.get("car_model", "미상")
    category = CAR_CATEGORY_BY_MODEL.get(car_model, "")
    return (
        f"차량번호 {record['vehicle_number']}, 차종 {car_model}({category}), "
        f"구분 {record.get('vehicle_type', '임직원')}, 소속/방문업체 {affiliation}, "
        f"입차시각 {record['entry_time']}, 현재 상태 {status}"
    )


def _build_vector_store() -> InMemoryVectorStore:
    """더미 데이터를 문서화해서 벡터 스토어를 만든다."""
    data = load_carlist()
    docs = [
        Document(page_content=_record_to_text(r), metadata={"vehicle_number": r["vehicle_number"]})
        for r in data["records"]
    ]
    return InMemoryVectorStore.from_documents(docs, _embeddings)


def search_vehicles(query: str, k: int = 5) -> list[dict]:
    """자연어 질의와 의미상 가까운 차량 레코드를 최대 k건 찾는다 (전기차/SUV처럼 필드에 없는 표현용)."""
    global _vector_store
    if _vector_store is None:
        _vector_store = _build_vector_store()

    hits = _vector_store.similarity_search(query, k=k)
    plates = [doc.metadata["vehicle_number"] for doc in hits]

    data = load_carlist()
    by_plate = {r["vehicle_number"]: r for r in data["records"]}
    return [by_plate[p] for p in plates if p in by_plate]
