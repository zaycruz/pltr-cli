"""Tests for the skill reference command drift checker."""

from pathlib import Path

from scripts.check_skill_command_drift import main


FIXTURE_MANIFEST = (
    Path(__file__).parent / "fixtures" / "skill_command_drift" / "manifest.json"
)


def _write_reference_dir(tmp_path: Path, content: str) -> Path:
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    (reference_dir / "example.md").write_text(content)
    return reference_dir


def test_drift_check_passes_against_fixture_manifest(tmp_path, capsys) -> None:
    reference_dir = _write_reference_dir(
        tmp_path,
        """
```bash
pltr dataset list
pltr ontology object-type-list ONTOLOGY_RID
```
""",
    )

    result = main(
        ["--manifest", str(FIXTURE_MANIFEST), "--reference-dir", str(reference_dir)]
    )

    assert result == 0
    assert "Result: no drift detected" in capsys.readouterr().out


def test_drift_check_reports_reference_and_manifest_drift(tmp_path, capsys) -> None:
    reference_dir = _write_reference_dir(
        tmp_path, "`pltr dataset list`\n`pltr legacy show RID`\n"
    )

    result = main(
        ["--manifest", str(FIXTURE_MANIFEST), "--reference-dir", str(reference_dir)]
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "Reference-only paths: 1" in output
    assert "legacy show" in output
    assert "Manifest-only paths:" in output
    assert "ontology object-type-list" in output


def test_drift_check_missing_manifest_is_actionable(tmp_path, capsys) -> None:
    reference_dir = _write_reference_dir(tmp_path, "`pltr dataset list`\n")
    missing_manifest = tmp_path / "missing-manifest.json"

    result = main(
        ["--manifest", str(missing_manifest), "--reference-dir", str(reference_dir)]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Manifest file not found" in captured.err
    assert "uv run pltr agent-manifest" in captured.err
