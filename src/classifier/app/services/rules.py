"""인터넷 없이 실행하는 보수적인 분류·지역·위치 추출."""
import re
from app.services.address import DONGS

CATEGORIES = {
    "streetlight": ("가로등", "보안등", "거리 조명"),
    "road_damage": ("도로 파손", "도로파손", "포트홀", "싱크홀", "도로에 구멍", "도로가 깨", "보도블록", "아스팔트"),
    "illegal_dumping": ("무단투기", "무단 투기", "쓰레기", "폐기물"),
}
SIDOS = {
    "서울": "서울특별시", "서울특별시": "서울특별시", "서울시": "서울특별시",
    "부산": "부산광역시", "부산광역시": "부산광역시", "부산시": "부산광역시",
    "인천": "인천광역시", "인천광역시": "인천광역시", "인천시": "인천광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시", "대구시": "대구광역시",
    "대전": "대전광역시", "광주": "광주광역시", "울산": "울산광역시",
    "대전시": "대전광역시", "광주시": "광주광역시", "울산시": "울산광역시",
    "경기도": "경기도", "경기": "경기도", "세종": "세종특별자치시",
    "강원도": "강원특별자치도", "제주": "제주특별자치도",
    "충청북도": "충청북도", "충청남도": "충청남도",
    "전라북도": "전북특별자치도", "전라남도": "전라남도",
    "경상북도": "경상북도", "경상남도": "경상남도",
}
# 2026 개편 포함: https://www.incheon.go.kr/open/OPEN030003
# https://www.hscity.go.kr/manse/index.do
DISTRICTS = set("""
종로구 중구 용산구 성동구 광진구 동대문구 중랑구 성북구 강북구 도봉구 노원구 은평구
서대문구 마포구 양천구 강서구 구로구 금천구 영등포구 동작구 관악구 서초구 강남구 송파구 강동구
서구 동구 영도구 부산진구 동래구 남구 북구 해운대구 사하구 금정구 연제구 수영구 사상구
수성구 달서구 미추홀구 연수구 남동구 부평구 계양구 제물포구 영종구 검단구 서해구 유성구 대덕구 광산구
장안구 권선구 팔달구 영통구 수정구 중원구 분당구 만안구 동안구 상록구 단원구
덕양구 일산동구 일산서구 원미구 소사구 오정구 처인구 기흥구 수지구
상당구 서원구 흥덕구 청원구 동남구 서북구 완산구 덕진구
의창구 성산구 마산합포구 마산회원구 진해구 만세구 효행구 병점구 동탄구
""".split())


def is_district(word, prefix=""):
    return bool(word in DISTRICTS and
                not re.search(r"\d+번\s*$", prefix))


def detect_category(text):
    # 정상이라고 명시한 시설은 다른 실제 민원의 유형 후보에 섞지 않는다.
    keywords = "|".join(re.escape(word) for words in CATEGORIES.values() for word in words)
    text = re.sub(r"(?:" + keywords + r")(?:은|는|이|가)?\s*"
                  r"(?:멀쩡(?!하지)|정상(?:입니다|이에요|인데|이고)|없(?:고|어요|습니다))", "", text)
    compact = re.sub(r"\s+", "", text).lower()
    if text.strip() in CATEGORIES:
        return text.strip(), 0.95
    matches = [category for category, words in CATEGORIES.items()
               if any(re.sub(r"\s+", "", word) in compact for word in words)]
    if len(matches) == 1:
        return matches[0], 0.9
    if re.search(r"지원\s*범위\s*밖|층간\s*소음|여권|세금|주차|불법\s*주정차", text) and not matches:
        return "out_of_scope", 0.95
    return (matches[0] if matches else "out_of_scope"), 0.4


def complaint_evidence(text):
    return bool(re.search(
        r"불편|고장|신고|파손|깨졌|깨져|들떠|꺼졌|패였|패인|구멍|꺼져|안\s*켜|버리고|버려|투기|이상|위험|문제|소음|주차|여권|세금|"
        r"streetlight|pothole|dumping|broken|街灯|故障|가로등|보안등|포트홀|보도블록|쓰레기|무단투기",
        text, re.I))


def extract_region(text):
    sido_match = re.search(
        r"(?<![가-힣])(" + "|".join(sorted(SIDOS, key=len, reverse=True)) + r")(?=\s|[,.]|$)", text
    )
    district = next((match for match in re.finditer(
        r"(?<![가-힣\d])([가-힣]{1,8}구)(?=(?:에서도|에서|에|의|은|는|로|쪽)?(?:\s|[,.]|$))", text)
        if is_district(match.group(1), text[:match.start()])), None)
    dong_match = None
    for match in re.finditer(r"(?<![가-힣])([가-힣]{1,8}(?:\d+)?동)(?=\s|[,.!?]|$|에서|에|이|을|가|하|해|되|돼)", text):
        before, after = text[:match.start()].split(), text[match.end():]
        if re.match(r"(?:이|을|가|은|는)?\s*(?:(?:안|잘|못|중)(?=\s|[,.!?]|$)|"
                    r"(?:안|잘|못)?(?:되|돼|하|해)(?:다|요|고|서|지|는|면|도|겠|야|$)|멈)", after):
            continue
        if (match.group(1) in DONGS or text.strip(' .!?') == match.group(1) or
                (before and (before[-1] in SIDOS or is_district(before[-1]))) or
                re.match(r"(?:에서|에)(?:\s|[,.]|$)|\s+[가-힣]+\s*역(?:\s|$)", after)):
            dong_match = match
            break
    sido = SIDOS[sido_match.group(1)] if sido_match else None
    sigungu = district.group(1) if district else None
    dong = dong_match.group(1) if dong_match else None
    # 알려진 동작구 행정 지명만 매핑한다. '집 앞'에는 지역을 채우지 않는다.
    if not sigungu and dong in DONGS and sido in (None, "서울특별시"):
        sigungu = "동작구"
    if not sido and sigungu == "동작구":
        sido = "서울특별시"
    return {"sido": sido, "sigungu": sigungu, "dong": dong, "place_text": None}


def strip_region_prefix(text, *, strip_dong=True):
    parts = text.split()
    while parts and (parts[0] in SIDOS or is_district(parts[0]) or
                     (strip_dong and re.fullmatch(r"[가-힣]+\d*동", parts[0]))):
        parts.pop(0)
    return " ".join(parts)


def extract_location(text, *, strip_dong=True):
    place = re.search(
        r"([가-힣A-Za-z0-9·\-\s]+?(?:맞은편|횡단보도|교차로|사거리|입구|출구|근처|앞|옆)"
        r"(?:\s*(?:앞|옆|근처|맞은편))?)"
        r"(?=\s|에서|에|[,.]|$)", text
    )
    if place:
        location = strip_region_prefix(place.group(1), strip_dong=strip_dong)
        if location and not re.fullmatch(
                r"(?:(?:우리\s*)?(?:집|동네|길)\s*)?"
                r"(?:앞|옆|근처|맞은편|횡단보도|교차로|사거리|입구|출구)", location):
            return location
    road = re.search(r"([가-힣]+(?:로|길)\s*\d+(?:-\d+)?)(?=\s|[,.]|$)", text)
    return road.group(1) if road else None


def extract_details(text):
    when = re.search(r"그저께|어제|오늘|방금|지난주|지난달|\d+\s*(?:일|주|개월|시간)\s*전|며칠째|\d{4}-\d{2}-\d{2}", text)
    photo = None
    if re.search(r"사진.{0,8}(?:없|안\s*찍|못\s*찍)|사진\s*미첨부", text):
        photo = False
    elif re.search(r"사진.{0,8}(?:있|첨부|찍|촬영)", text):
        photo = True
    return {
        "location_detail": extract_location(text),
        "occurred_at": when.group(0) if when else None,
        "has_photo": photo,
    }


def _location_answer(inputs):
    answers = inputs.get("answers", {})
    if "q_confirm_location" in answers and not is_affirmative(answers["q_confirm_location"]):
        return answers["q_confirm_location"]
    # q1은 원문 유형이 확실할 때만 위치 답변이다. 빈 답도 원문을 복원하지 않는다.
    if "q1" in answers and detect_category(inputs["text"])[1] >= 0.7:
        return answers["q1"]
    return next((answers[key] for key in ("q_location_detail", "location_detail")
                 if key in answers), None)


def is_affirmative(text):
    return bool(re.fullmatch(
        r"(?:네네|네|예요|예|응|그래요?|맞아요?|맞습니다|맞음|ㅇㅇ|넵|그렇습니다|좋아요|확인)"
        r"(?:맞아요|맞습니다|그쪽이요|거기요|그곳이요)?",
        re.sub(r"[\W_]+", "", text or "")))


def get_location_answer(inputs):
    answer = _location_answer(inputs)
    return None if is_affirmative(answer) else answer


def has_confirmation(inputs):
    answers = inputs.get("answers", {})
    return is_affirmative(answers.get("q_confirm_location", _location_answer(inputs)))


def answer_location(text):
    location = extract_location(text, strip_dong=False)
    if location is None:
        location = " ".join(match.group() for match in re.finditer(r"\S+", text)
                            if match.group() not in SIDOS and
                            not is_district(match.group(), text[:match.start()]))
    location = location.strip(" \t\n.,!?")
    if (len(location) < 2 or re.fullmatch(
            r"몰라요|모름|모르겠.*|없어요|없음|ㅇㅇ|네|예|아니요|아니오|아뇨|"
            r"(?:우리\s*)?(?:집|동네|길)\s*(?:앞|옆|근처)", location)):
        return None
    return location


def classify_by_rules(inputs):
    answers = inputs.get("answers", {})
    text = "\n".join(filter(None, [inputs["text"], answers.get("q_what")]))
    combined = "\n".join([text, *answers.values()])
    category, confidence = detect_category(text)
    category_answer = answers.get("q_category") or answers.get("category")
    location_answer = get_location_answer(inputs)
    # 기존 q1 샘플도 지원한다. 새 질문은 필드별 고정 ID를 사용한다.
    if answers.get("q1"):
        if confidence < 0.7:
            category_answer = answers["q1"]
    if category_answer:
        category, confidence = detect_category(category_answer)
    region = extract_region(location_answer if location_answer is not None else combined)
    details = extract_details(combined)
    if location_answer is not None:
        if not region["sido"] and not region["sigungu"]:
            original = extract_region(text)
            for field in ("sido", "sigungu"):
                region[field] = original[field]
        details["location_detail"] = answer_location(location_answer)
    if answers.get("q_occurred_at"):
        details["occurred_at"] = answers["q_occurred_at"].strip() or None
    if answers.get("q_has_photo"):
        answer = answers["q_has_photo"].strip().lower()
        details["has_photo"] = (True if answer in ("예", "네", "있어요", "있음", "있습니다", "첨부", "true") else
                                False if answer in ("아니오", "아니요", "없어요", "없음", "없습니다", "false") else None)
    region["place_text"] = details["location_detail"]
    if location_answer is None and not region["place_text"] and re.search(r"집\s*앞", combined):
        region["place_text"] = "집 앞"
    if (region["sido"] and region["sido"] != "서울특별시") or (
        region["sigungu"] and region["sigungu"] != "동작구"
    ):
        category, confidence = "out_of_scope", 0.95
    elif not category_answer and not (category == "out_of_scope" and confidence >= 0.7) and (
        (not complaint_evidence(text) and not any(region.values()) and (
            not re.search(r"[가-힣A-Za-z]", text) or
            re.fullmatch(r"안녕(?:하세요)?[.!?\s]*|https?://\S+", text))) or
        (confidence < 0.7 and re.search(r"멀쩡|정상(?:입니다|이에요)", text)) or
        re.search(r"(?:꺼지면|고장\s*나면|파손되면).*(?:어떻게|어디)", text)
    ):
        category, confidence = None, 0.0
    return {
        "category": category, "confidence": confidence,
        "region": region, "details": details, "missing": [], "questions": [],
    }
