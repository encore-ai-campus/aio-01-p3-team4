"""Validate the two operational seed CSVs and select jurisdiction-safe channels."""
import csv
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from app.core.config import SEEDS_DIR

with (SEEDS_DIR / "reference/complaint_routing/dongjak_dong_mapping.csv").open(encoding="utf-8-sig") as stream:
    DONG_MAPPING = {row["legal_dong"]: row["administrative_dongs"].split("|") for row in csv.DictReader(stream)}
ADMIN_DONGS = {dong for dongs in DONG_MAPPING.values() for dong in dongs}

COLUMNS = ("channel_id", "sido", "sigungu", "category", "name", "org", "method", "url", "jurisdiction", "verified_at", "source_url", "active")
PUBLIC_COLUMNS = ("channel_id", "name", "org", "method", "url", "jurisdiction", "verified_at", "source_url")
CATEGORIES = {"streetlight", "road_damage", "illegal_dumping", "all"}
OFFICIAL_DOMAINS = ("go.kr", "gov.kr", "120dasan.or.kr")


def official_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme in {"https", "http"} and not parsed.username
                and not parsed.password and parsed.port in {None, 80, 443}
                and any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS))
    except ValueError:
        return False


def seed_paths(directory: Path) -> tuple[Path, Path]:
    # Never glob: mock_contacts.csv and reference/ use different contracts.
    return directory / "channels.csv", directory / "integrated_channels.csv"


def load_catalog(paths) -> list[dict]:
    result, seen = [], set()
    for path in paths:
        path = Path(path)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != list(COLUMNS):
                raise ValueError(f"{path.name}: expected the 12 channel columns in order")
            for line, raw in enumerate(reader, start=2):
                try:
                    if None in raw or any(value is None for value in raw.values()):
                        raise ValueError("incorrect column count")
                    row = {key: value.strip() for key, value in raw.items()}
                    if any(not row[key] for key in COLUMNS if key not in {"sido", "sigungu"}):
                        raise ValueError("required value is empty")
                    ident, category = row["channel_id"], row["category"]
                    dong_row = (row["sido"] == "서울특별시" and row["sigungu"] == "동작구"
                                and row["jurisdiction"] in ADMIN_DONGS and category == "all"
                                and ident == f"dj-dong-{row['jurisdiction']}-01")
                    if category not in CATEGORIES or not (dong_row or re.fullmatch(r"[a-z0-9]+-" + category + r"-\d+", ident)):
                        raise ValueError("invalid category or channel_id")
                    if ident in seen:
                        raise ValueError(f"duplicate channel_id: {ident}")
                    if row["sigungu"] and not row["sido"]:
                        raise ValueError("sigungu requires sido")
                    expected_area = row["sigungu"] or row["sido"] or "전국"
                    if row["jurisdiction"] != expected_area and not dong_row:
                        raise ValueError("jurisdiction does not match region")
                    method = row["method"]
                    if method not in {"web", "app", "phone"}:
                        raise ValueError("invalid method")
                    valid_target = bool(re.fullmatch(r"tel:\+?[0-9][0-9-]{2,19}", row["url"])) if method == "phone" else official_url(row["url"])
                    if not valid_target or not official_url(row["source_url"]):
                        raise ValueError("invalid target or non-official source_url")
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["verified_at"]):
                        raise ValueError("verified_at must be YYYY-MM-DD")
                    row["verified_at"] = date.fromisoformat(row["verified_at"])
                    if row["active"].lower() not in {"true", "false"}:
                        raise ValueError("active must be true or false")
                    row["active"] = row["active"].lower() == "true"
                    row["sido"] = row["sido"] or None
                    row["sigungu"] = row["sigungu"] or None
                    seen.add(ident)
                    result.append(row)
                except ValueError as exc:
                    raise ValueError(f"{path.name}:{line}: {exc}") from exc
    if not result:
        raise ValueError("channel catalog is empty")
    return result


def select_channels(rows: list[dict], inputs: dict, include_phone: bool = False) -> dict:
    category, region = inputs["category"], inputs["region"]
    excluded = set(inputs["exclude_channel_ids"])
    supported = category != "out_of_scope" and region["sido"] == "서울특별시" and region["sigungu"] == "동작구"
    groups = {"district": [], "city": [], "national": []}
    dong = region.get("dong")
    dongs = [dong] if dong in ADMIN_DONGS else DONG_MAPPING.get(dong, [])
    explicit = [name for name in dongs if re.search(
        r"(?<![가-힣\d])" + re.escape(name) + r"(?![가-힣\d])", region.get("place_text") or "")]
    dongs = explicit or dongs
    centers = []
    for row in rows:
        if not row["active"] or row["channel_id"] in excluded:
            continue
        if row["method"] not in ({"web", "app", "phone"} if include_phone else {"web", "app"}):
            continue
        if row["category"] not in {category, "all"}:
            continue
        if not row["sido"] and not row["sigungu"] and row["jurisdiction"] == "전국":
            groups["national"].append(row)
        elif supported and row["sido"] == region["sido"]:
            if (row["sigungu"] == region["sigungu"] and row["jurisdiction"] in dongs
                    and row["channel_id"] == f"dj-dong-{row['jurisdiction']}-01"):
                centers.append(row)
            elif row["sigungu"] == region["sigungu"] and row["jurisdiction"] == region["sigungu"]:
                groups["district"].append(row)
            elif not row["sigungu"] and row["jurisdiction"] == region["sido"]:
                groups["city"].append(row)
    for level, candidates in groups.items():
        if candidates:
            # Exact category first, then clickable channels, recent verification, stable ID.
            candidates.sort(key=lambda row: (row["category"] != category, {"web": 0, "app": 1, "phone": 2}[row["method"]], -date.fromisoformat(str(row["verified_at"])).toordinal(), row["channel_id"]))
            selected = candidates[:3]
            if level == "district":
                centers.sort(key=lambda row: (dongs.index(row["jurisdiction"]), row["channel_id"]))
                selected += centers[:2]
            channels = [{key: str(row[key]) if key == "verified_at" else row[key] for key in PUBLIC_COLUMNS} for row in selected]
            return {"channels": channels, "fallback_level": level}
    return {"channels": [], "fallback_level": "national"}
