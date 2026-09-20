#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insights_app.pdf_parser import parse_insights_pdf


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: scripts/parse_pdf.py path/to/profile.pdf", file=sys.stderr)
        return 2
    parsed = parse_insights_pdf(argv[1])
    print(json.dumps(parsed, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
