#!/usr/bin/env python3
"""Write the recorded t.me pages as a seed corpus for the parser fuzzer.

    python3 scripts/fuzz_corpus.py rust/telegram-me-native/fuzz/corpus/pages

"Load more" answers are stored as JSON strings; the parser gets their HTML.
Standard library only.
"""

import gzip
import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "telegram"


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus")
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(FIXTURES.glob("*.txt.gz")):
        text = gzip.decompress(path.read_bytes()).decode("utf-8")
        if path.name.startswith("more_"):
            text = str(json.loads(text))
        (target / path.name.removesuffix(".txt.gz")).write_text(text, encoding="utf-8")
        count += 1
    print(f"{count} seed pages in {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
