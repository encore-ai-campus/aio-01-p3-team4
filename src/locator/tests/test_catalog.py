import csv
from datetime import date
import pytest
from app.core.config import SEEDS_DIR
from app.services.catalog import COLUMNS, PUBLIC_COLUMNS, load_catalog, seed_paths, select_channels


@pytest.fixture
def rows():
    return load_catalog(seed_paths(SEEDS_DIR))


def inputs(category="streetlight"):
    return {"category": category, "region": {"sido": "서울특별시", "sigungu": "동작구"}, "exclude_channel_ids": []}


def write_csv(path, rows, columns=COLUMNS):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def raw(row):
    return {**row, "active": "true" if row["active"] else "false"}


def test_only_operational_csvs_loaded(rows):
    assert len(rows) >= 14
    assert all(isinstance(row["active"], bool) and isinstance(row["verified_at"], date) for row in rows)
    assert {path.name for path in seed_paths(SEEDS_DIR)} == {"channels.csv", "integrated_channels.csv"}


@pytest.mark.parametrize("change", [
    {"active": "yes"}, {"verified_at": "2026-13-01"}, {"verified_at": "20261007"},
    {"url": "https://dongjak.go.kr.evil.example"}, {"source_url": "http://127.0.0.1"},
    {"category": "unsupported"}, {"channel_id": "dj-road_damage-99"},
    {"jurisdiction": "관악구"}, {"sido": ""}, {"name": ""}, {"method": "email"},
    {"url": "https://user:pass@dongjak.go.kr/"},
])
def test_invalid_seed_rejected(tmp_path, rows, change):
    path = tmp_path / "channels.csv"
    write_csv(path, [{**raw(rows[0]), **change}])
    with pytest.raises(ValueError, match="channels.csv:2"):
        load_catalog([path])


def test_duplicates_across_files_rejected(tmp_path, rows):
    paths = [tmp_path / "channels.csv", tmp_path / "integrated_channels.csv"]
    for path in paths:
        write_csv(path, [raw(rows[0])])
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(paths)


def test_header_and_column_count(tmp_path, rows):
    path = tmp_path / "bad.csv"
    write_csv(path, [], columns=("name", "url"))
    with pytest.raises(ValueError, match="12 channel"):
        load_catalog([path])
    write_csv(path, [raw(rows[0])])
    with path.open("a", encoding="utf-8") as file:
        file.write("too,few\n")
    with pytest.raises(ValueError, match="column count"):
        load_catalog([path])


def test_phone_opt_in_and_eight_field_output(rows):
    result = select_channels(rows, inputs(), include_phone=True)
    assert any(row["method"] == "phone" for row in result["channels"])
    assert all(set(row) == set(PUBLIC_COLUMNS) for row in result["channels"])
    assert all(row["method"] != "phone" for row in select_channels(rows, inputs())["channels"])


def test_max_three_stable_order_and_exact_category(rows):
    exact = next(row for row in rows if row["category"] == "illegal_dumping" and row["method"] == "web" and row["sigungu"])
    many = [{**exact, "channel_id": f"dj-illegal_dumping-{i:02}"} for i in range(20, 10, -1)] + rows
    first = select_channels(many, inputs("illegal_dumping"))
    assert len(first["channels"]) == 3
    assert all("illegal_dumping" in row["channel_id"] for row in first["channels"])
    assert first == select_channels(list(reversed(many)), inputs("illegal_dumping"))


def test_phone_only_tier_falls_through(rows):
    rows = [row for row in rows if not row["sigungu"] or row["method"] == "phone"]
    assert select_channels(rows, inputs())["fallback_level"] == "city"
