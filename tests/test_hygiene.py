from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_empty_python_files_outside_init():
    empties = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        if path.name == "__init__.py":
            continue
        if path.stat().st_size == 0:
            empties.append(str(path.relative_to(ROOT)))
    assert empties == []


def test_no_live_github_copilot_bot_default():
    forbidden = []
    skip_names = {"AI-PIPELINE-PR-FIXES.txt", "AI-PIPELINE-PR-FIXES.diff"}
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.example")):
        if any(part in {".venv", "venv", "__pycache__", "tests"} for part in path.parts):
            continue
        if path.name in skip_names:
            continue
        text = path.read_text(encoding="utf-8")
        allowed_context = (
            "ошибочн" in text
            or "не используем" in text
            or "erroneous" in text
            or "not used" in text
            or "wrong" in text
        )
        if "github-copilot[bot]" in text and not allowed_context:
            forbidden.append(str(path.relative_to(ROOT)))
    assert forbidden == []


def test_telegram_adapter_does_not_import_github():
    telegram_root = ROOT / "adapters" / "telegram"
    leaks = []
    for path in telegram_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "adapters.github" in text or "from ports.github" in text:
            leaks.append(str(path.relative_to(ROOT)))
    assert leaks == []
