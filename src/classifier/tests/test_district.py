import pytest

from app.services.rules import answer_location, classify_by_rules, extract_region, strip_region_prefix


@pytest.mark.parametrize("word", [
    "출구", "입구", "지구", "가구", "친구", "창구", "도구", "요구", "연구",
    "야구", "축구", "농구", "배구", "탁구", "하구", "전구", "상구", "문구",
    "기구", "공구", "식구", "외구", "구구", "출입구", "비상구", "배수구", "하수구",
    "투입구", "운동기구", "환풍구", "임의구",
])
def test_common_nouns_do_not_hide_a_later_district(word):
    assert extract_region(f"{word} 앞 가로등")["sigungu"] is None
    assert extract_region(f"{word} 앞 서울 동작구 가로등")["sigungu"] == "동작구"


def test_entrance_next_to_known_dong_infers_dongjak():
    data = classify_by_rules({"text": "흑석동 입구 사거리 쓰레기 무단투기"})
    assert data["category"] == "illegal_dumping"
    assert data["region"]["sigungu"] == "동작구"
    assert data["region"]["dong"] == "흑석동"


@pytest.mark.parametrize("district", ["중구", "남구", "북구", "동구", "서구", "동작구", "강남구", "해운대구", "종로구", "분당구", "오정구", "마산회원구", "영종구", "동탄구"])
def test_real_districts_are_preserved_except_after_exit_numbers(district):
    assert extract_region(f"서울 {district} 명동 가로등")["sigungu"] == district
    for particle in ("에서", "에", "의", "은", "는", "로", "쪽", "에서도"):
        assert extract_region(f"{district}{particle} 가로등 고장")["sigungu"] == district
    for prefix in ("1번 ", "12번", "3번  "):
        assert extract_region(f"{prefix}{district} 앞 가로등")["sigungu"] is None


@pytest.mark.parametrize("text,expected", [
    ("1번 출구 앞", "1번 출구 앞"),
    ("서울 동작구 1번 출구 앞", "1번 출구 앞"),
    ("출구 안내판", "출구 안내판"),
    ("입구 안내판", "입구 안내판"),
    ("서울 동작구 배수구 안내판", "배수구 안내판"),
    ("1번 강남구 안내판", "1번 강남구 안내판"),
])
def test_location_answers_preserve_non_district_words(text, expected):
    assert answer_location(text) == expected
    assert strip_region_prefix(text) == expected
