import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "autoskillit"


def _module_docstring(path: Path) -> str:
    tree = ast.parse(path.read_text())
    first = tree.body[0] if tree.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return first.value.value
    return ""


def test_session_is_labelled_l1():
    doc = _module_docstring(SRC / "execution" / "session.py")
    assert "L1" in doc, "session.py docstring must say L1, not L2"
    assert "L2" not in doc


def test_headless_has_l1_label():
    doc = _module_docstring(SRC / "execution" / "headless.py")
    assert "L1" in doc, "headless.py docstring must carry an L1 label"


def test_smoke_utils_documents_file_path_coupling():
    text = (SRC / "smoke_utils.py").read_text().lower()
    assert "limitation" in text or "known" in text, \
        "smoke_utils.py must document the file-path coupling limitation"


def test_claudemd_headless_label():
    claudemd = (Path(__file__).parents[2] / "CLAUDE.md").read_text()
    # Key Components entry must be fixed
    assert "L3 service module for headless" not in claudemd
    # Tree diagram entry must also be fixed
    assert "Headless Claude session orchestration (L3 service)" not in claudemd
