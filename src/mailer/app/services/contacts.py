import csv
from functools import lru_cache
from app.core.config import CONTACTS_CSV

@lru_cache(maxsize=1)
def load():
    with CONTACTS_CSV.open(encoding="utf-8", newline="") as file:
        return {row.pop("channel_id"): row for row in csv.DictReader(file)}

def find(channel_id):
    return load().get(channel_id, {"dept": "가상 민원실", "officer": "가상 담당자",
                                   "email": f"{channel_id}@minwon.example"})
