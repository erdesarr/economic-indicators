"""Generate the static JSON contract published on GitHub Pages.

Layout (``--out`` defaults to ``docs/api``):

    v1/indicators.json
    v1/latest.json
    v1/indicators/<slug>/latest.json
    v1/indicators/<slug>/history.json      (31-day window)
    v1/indicators/<slug>/variation.json

All payloads include: value, value_published, unit, unit_as_published, status,
base_gap, source_date and variation_pct.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etl.config import load_config  # noqa: E402
from etl.contract import (  # noqa: E402
    catalog_entry,
    history_payload,
    latest_payload,
    variation_payload,
)
from etl.storage import connect  # noqa: E402


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build(db_path: Path, config_path: Path, out_dir: Path) -> list[Path]:
    config = load_config(config_path)
    max_gap_days = config.settings.max_gap_days
    history_days = config.settings.history_days
    generated_at = datetime.now(UTC).isoformat()
    written: list[Path] = []

    conn = connect(db_path, read_only=True)
    try:
        indicators = conn.execute("SELECT * FROM indicator ORDER BY slug").fetchall()
        v1 = out_dir / "v1"
        catalog = {
            "generated_at": generated_at,
            "indicators": [catalog_entry(conn, row, max_gap_days) for row in indicators],
        }
        latest_all = {
            "generated_at": generated_at,
            "indicators": {
                str(row["slug"]): latest_payload(conn, row, max_gap_days) for row in indicators
            },
        }
        written.append(write_json(v1 / "indicators.json", catalog))
        written.append(write_json(v1 / "latest.json", latest_all))

        for row in indicators:
            slug = str(row["slug"])
            base = v1 / "indicators" / slug
            written.append(
                write_json(base / "latest.json", latest_payload(conn, row, max_gap_days) or {})
            )
            written.append(
                write_json(
                    base / "history.json",
                    history_payload(conn, row, max_gap_days, history_days),
                )
            )
            written.append(
                write_json(
                    base / "variation.json", variation_payload(conn, row, max_gap_days) or {}
                )
            )
    finally:
        conn.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the static Pages contract")
    parser.add_argument("--db", default="data/indicadores.db")
    parser.add_argument("--config", default="config/indicators.yaml")
    parser.add_argument("--out", default="docs/api")
    args = parser.parse_args()

    written = build(Path(args.db), Path(args.config), Path(args.out))
    print(f"pages contract: {len(written)} files written to {args.out}/v1")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
