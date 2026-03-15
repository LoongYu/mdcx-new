from __future__ import annotations

import argparse
from pathlib import Path


def normalize_body(text: str) -> str:
    return text.replace("\\n", "\n").strip()


def load_release_body(tag: str, input_body: str) -> str:
    if input_body.strip():
        return normalize_body(input_body)

    root = Path(__file__).resolve().parent.parent
    if tag:
        notes_path = root / "release-notes" / f"{tag}.md"
        if notes_path.exists():
            return notes_path.read_text(encoding="utf-8").strip()

    changelog_path = root / "changelog.md"
    if changelog_path.exists():
        return changelog_path.read_text(encoding="utf-8").strip()

    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="Release tag, e.g. 22026031505")
    parser.add_argument("--input-body", default="", help="Manual release body from workflow input")
    args = parser.parse_args()
    print(load_release_body(args.tag, args.input_body))


if __name__ == "__main__":
    main()
