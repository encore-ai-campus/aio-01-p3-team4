"""질문 항목에 고정 ID를 사용해 여러 차례 추가 답변을 구분한다."""
import copy

from app.services.rules import SIDOS
from app.services.josa import with_josa

CATEGORY_QUESTION = "어떤 문제인지 골라 주세요: 가로등 고장 / 도로 파손 / 쓰레기 무단투기"


def prepare_questions(data, conflict=None, *, routing_inquiry=False):
    data = copy.deepcopy(data)
    region = data["region"]
    region["sido"] = SIDOS.get(region["sido"], region["sido"])
    data["missing"], data["questions"] = [], []
    if conflict:
        text = conflict.get("text") or ((
            f"{with_josa(conflict['query'], '은/는')} 동작구 {conflict['dong']}에 있는 곳으로 확인돼요. "
            f"말씀하신 {conflict['stated']} 대신 {with_josa(conflict['dong'], '으로/로')} 접수할까요? "
            "맞으면 '네', 아니면 정확한 위치(동·도로명·근처 건물)를 알려 주세요"
        ) if conflict["kind"] == "dong" else (
            f"{with_josa(conflict['query'], '은/는')} {conflict['sido']} {conflict['sigungu']}에 있는 곳으로 확인돼요. "
            "서울 동작구 민원이 맞다면 동작구 안의 위치(동·도로명·근처 건물)만 다시 알려 주세요"
        ))
        data["missing"].append("location_detail")
        data["questions"].append({
            "id": "q_confirm_location" if conflict.get("kind") == "dong" else "q_location_detail", "field": "location_detail",
            "text": text,
        })
        if data["category"] is not None and data["confidence"] < 0.7:
            data["missing"].insert(0, "category")
            data["questions"].insert(0, {"id": "q_category", "text": CATEGORY_QUESTION, "field": "category"})
        return data, "needs_user", "NEEDS_MORE_INFO"
    outside = (region["sido"] and region["sido"] != "서울특별시") or (
        region["sigungu"] and region["sigungu"] != "동작구"
    )
    if outside or (data["category"] == "out_of_scope" and data["confidence"] >= 0.7):
        data["category"] = "out_of_scope"
        return data, "failed", "OUT_OF_SCOPE"
    if data["category"] is None:
        data["confidence"] = 0.0
        data["missing"].append("category")
        data["questions"].append({
            "id": "q_what", "text": "어떤 불편인지 한 줄로 알려주세요", "field": "category",
        })
        return data, "needs_user", "NEEDS_MORE_INFO"
    if data["confidence"] < 0.7:
        data["missing"].append("category")
        data["questions"].append({
            "id": "q_category", "text": CATEGORY_QUESTION, "field": "category",
        })
    if not routing_inquiry and (not data["details"]["location_detail"] or
            region["sido"] != "서울특별시" or region["sigungu"] != "동작구"):
        data["missing"].append("location_detail")
        data["questions"].append({
            "id": "q_location_detail",
            "text": "발생 위치의 시·구와 동·도로명·근처 건물을 알려 주세요",
            "field": "location_detail",
        })
    # 발생 시점·사진은 선택 정보다. 누락만으로 질문 대기를 반복하지 않는다.
    if data["questions"]:
        return data, "needs_user", "NEEDS_MORE_INFO"
    return data, "success", None
