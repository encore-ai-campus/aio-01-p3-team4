"""확인된 정보만 사용하는 민원 유형별 기본 신고글."""
CATEGORY_NAMES = {
    "streetlight": "가로등 고장",
    "road_damage": "도로 파손",
    "illegal_dumping": "쓰레기 무단투기",
    "out_of_scope": "생활 불편",
}


def make_template(inputs):
    region, details = inputs["region"], inputs["details"]
    problem = CATEGORY_NAMES[inputs["category"]]
    location = details["location_detail"]
    when = details["occurred_at"]
    fill_in = []
    if not location:
        location = "[정확한 위치를 입력해 주세요]"
        fill_in.append({
            "label": "정확한 위치", "placeholder": "예: ○○초등학교 앞 횡단보도",
        })
    if not when:
        fill_in.append({"label": "발생 시점", "placeholder": "예: 어제 저녁부터"})
    title_region = " ".join(filter(None, [region["sigungu"], region["dong"]]))
    body = f"{location}에서 "
    body += f"{when} {problem} 문제를 확인했습니다." if when else f"{problem} 문제가 있습니다."
    body += " 현장 확인과 필요한 조치를 부탁드립니다."
    if details["has_photo"] is True:
        body += " 현장 사진을 보유하고 있습니다."
    return {
        "title": f"{title_region or '위치 확인 필요'} {problem} 신고",
        "body": body, "fill_in": fill_in, "removed_pii": [],
    }
