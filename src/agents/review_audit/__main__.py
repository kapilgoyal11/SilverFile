"""Command line tools.

    python -m src.agents.review_audit check-fixtures data/fixtures
    python -m src.agents.review_audit audit path/to/state.json
"""

import json
import sys

from . import audit_return, validate_fixture_dir


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "check-fixtures":
        problems = validate_fixture_dir(argv[1])
        for p in problems:
            print("FAIL", p)
        print("OK: only 900-999 test SSNs found" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if len(argv) == 2 and argv[0] == "audit":
        with open(argv[1], encoding="utf-8") as fh:
            record = audit_return(json.load(fh))
        print(json.dumps(record.to_dict(), indent=2, default=str))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
