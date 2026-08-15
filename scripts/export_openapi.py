"""Write or verify the committed Pangi Admin OpenAPI document."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pangi.openapi import render_openapi_document

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = _PROJECT_ROOT / "docs" / "openapi" / "pangi-admin-api.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the artifact has drifted")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = args.output.resolve()
    expected = render_openapi_document()
    if args.check:
        actual = output.read_text("utf-8") if output.is_file() else None
        if actual != expected:
            print(
                f"OpenAPI artifact has drifted: {output}\n"
                "Run scripts/export_openapi.py and regenerate the frontend API types.",
                file=sys.stderr,
            )
            return 1
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, "utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
