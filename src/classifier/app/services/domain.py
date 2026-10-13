"""모델 출력의 값 영역 검사. 반환값은 개인정보 없는 고정 코드뿐이다."""
import math
import re
from app.services.address import DONGS

CATEGORIES = {'streetlight', 'road_damage', 'illegal_dumping', 'out_of_scope'}
QUESTION_IDS = {'q1', 'q_what', 'q_category', 'q_location_detail', 'q_confirm_location', 'q_occurred_at', 'q_has_photo'}


def validate_domain(data, grounded_dong=None):
    region, category, confidence = data['region'], data['category'], data['confidence']
    dong = region['dong']
    checks = {
        'CATEGORY': category in CATEGORIES,
        'REGION_CATEGORY': region['sigungu'] in (None, '동작구') or category == 'out_of_scope',
        'DONG': region['sigungu'] != '동작구' or dong is None or dong in DONGS,
        'PLACE_DONG': not (region['place_text'] and dong and grounded_dong) or re.sub(r'\d+', '', dong) == re.sub(r'\d+', '', grounded_dong),
        'CONFIDENCE': type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1,
        'QUESTION_ID': all(q['id'] in QUESTION_IDS for q in data['questions']),
    }
    return [code for code, valid in checks.items() if not valid]
