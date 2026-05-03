from pathlib import Path


def test_quality_tooling_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "pyproject.toml").is_file()
    assert (root / "requirements.in").is_file()
    assert (root / "requirements.txt").is_file()
    assert (root / "requirements-dev.in").is_file()
    assert (root / "requirements-dev.txt").is_file()
    assert (root / ".pre-commit-config.yaml").is_file()
    assert (root / ".github" / "workflows" / "ci.yml").is_file()
