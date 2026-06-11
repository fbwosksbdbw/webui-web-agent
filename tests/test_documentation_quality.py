from pathlib import Path


def test_knowledge_docs_do_not_reference_legacy_pagination_waits():
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "knowledge" / "README.md",
        root / "knowledge" / "references" / "code-rules.md",
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    assert "wait_for_function" not in combined
    assert "PAGINATION_JS" not in combined
