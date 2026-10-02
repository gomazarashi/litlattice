import runpy
from pathlib import Path

check_document = runpy.run_path(
    Path(__file__).resolve().parents[1] / "scripts" / "check_docs.py"
)["check_document"]


def test_references_and_code_examples(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text("# README\n")
    (docs / "space name.md").write_text("# Guide\n")
    page = docs / "guide.md"
    page.write_text(
        '[root](../README.md) `README.md` [space](space%20name.md "title")\n'
        "[section](#heading) [web](https://example.com/)\n"
        "```markdown\n[example](missing.md)\n```\n"
    )
    assert check_document(page, tmp_path) == []


def test_missing_reference_and_unclosed_fence(tmp_path: Path) -> None:
    page = tmp_path / "guide.md"
    page.write_text("[missing](absent.md)\n```bash\n")
    assert check_document(page, tmp_path) == [
        "guide.md:1: missing absent.md",
        "guide.md: unclosed code fence",
    ]
