"""Check local Markdown references and code fences without extra dependencies."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


def check_document(path: Path, root: Path) -> list[str]:
    errors = []
    fenced = False
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        references = re.findall(r"\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)", line)
        references += re.findall(r"`([^`\n]+\.md)`", line)
        for reference in references:
            target = urlsplit(reference)
            if target.scheme or not target.path:
                continue
            relative = unquote(target.path)
            if not (path.parent / relative).exists() and not (root / relative).exists():
                errors.append(f"{path.relative_to(root)}:{number}: missing {relative}")
    if fenced:
        errors.append(f"{path.relative_to(root)}: unclosed code fence")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    paths = sorted(root.glob("*.md"))
    for directory in ("docs", "src"):
        paths += sorted((root / directory).rglob("*.md"))
    errors = [error for path in paths for error in check_document(path, root)]
    for error in errors:
        print(error)
    if errors:
        return 1
    print(f"Checked {len(paths)} Markdown documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
