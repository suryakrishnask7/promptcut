"""
tests/test_batch_runner_script.py

Tests for scripts/batch_runner.py verifying IncuBrix Integration Standard #10.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from scripts.batch_runner import main as batch_main


def test_batch_runner_with_sample_session(tmp_path: Path):
    test_yaml = tmp_path / "test_session.yaml"
    test_yaml.write_text("""
id: "sample_case_1"
instructions:
  - say: "caption Dan only"
  - say: "undo"
""", encoding="utf-8")

    out_dir = tmp_path / "batch_out"

    exit_code = batch_main([str(test_yaml), "-o", str(out_dir)])
    assert exit_code == 0

    # Verify summary.json was created
    summary_path = out_dir / "summary.json"
    assert summary_path.is_file()

    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary_data["total_cases"] == 1
    assert summary_data["succeeded"] == 1
    assert summary_data["failed"] == 0

    # Verify case result file
    case_result = out_dir / "sample_case_1" / "result.json"
    assert case_result.is_file()
    result_data = json.loads(case_result.read_text(encoding="utf-8"))
    assert result_data["status"] == "succeeded"
    assert len(result_data["instructions_executed"]) == 2
