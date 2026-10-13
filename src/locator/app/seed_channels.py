"""python -m app.seed_channels --validate-only | --upsert"""
import argparse
import asyncio
from pathlib import Path
from app.clients.channels import ChannelRepository
from app.core.config import DATABASE_URL, REDIS_URL, SEEDS_DIR
from app.services.catalog import load_catalog, seed_paths


def main():
    parser = argparse.ArgumentParser(description="Validate/import operational channel CSVs only")
    parser.add_argument("--seeds-dir", type=Path, default=SEEDS_DIR)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--validate-only", action="store_true")
    action.add_argument("--upsert", action="store_true", help="Explicitly overwrite matching IDs (including active); retain other DB rows")
    args = parser.parse_args()
    rows = load_catalog(seed_paths(args.seeds_dir))
    if args.validate_only:
        print(f"Valid: {len(rows)} channels from 2 CSV files")
    else:
        count = asyncio.run(ChannelRepository(DATABASE_URL, args.seeds_dir, REDIS_URL).seed(upsert=args.upsert))
        print(f"Imported: {count} channels")


if __name__ == "__main__":
    main()
