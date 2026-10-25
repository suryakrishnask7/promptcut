#!/usr/bin/env python3
"""
scripts/batch_runner.py

IncuBrix Integration Standard #10: Batch Runner
A command-line tool that runs a folder or file of test inputs against the API
and writes all results to one folder. Used by evaluators to benchmark and test submissions.

Usage:
    python scripts/batch_runner.py <input_folder_or_file> -o <output_folder> [--api-url http://localhost:8000]

Supported input formats:
    - A folder containing .yaml / .yml / .json test session files
    - A single session file (.yaml / .yml / .json)
    - A benchmark cases file (e.g. eval_benchmark.yaml)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


class BatchRunnerAPIClient:
    """HTTP client for interacting with the PromptCut API.
    
    If the remote server is unreachable, falls back to in-memory FastAPI TestClient
    so test runs can execute reliably even without a pre-started server.
    """

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url.rstrip("/")
        self._direct_client = None
        self._test_connection()

    def _test_connection(self) -> None:
        if httpx is not None:
            try:
                r = httpx.get(f"{self.base_url}/health", timeout=1.5)
                if r.status_code == 200:
                    print(f"[batch_runner] Connected to PromptCut API at {self.base_url}")
                    return
            except Exception:
                pass

        # Fall back to in-memory FastAPI TestClient
        try:
            from fastapi.testclient import TestClient
            from promptcut.api.app import app
            self._direct_client = TestClient(app)
            print(f"[batch_runner] PromptCut server at {self.base_url} unreachable. Using in-memory FastAPI TestClient.")
        except Exception as e:
            print(f"[batch_runner] Warning: In-memory fallback failed: {e}. Attempting direct requests to {self.base_url}")

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        if self._direct_client:
            return self._direct_client.request(method, path, **kwargs)
        return httpx.request(method, url, **kwargs)

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Any:
        return self.request("POST", path, **kwargs)


def load_input_spec(path: Path) -> list[dict[str, Any]]:
    """Parse session input file(s) into a list of executable test case specifications."""
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")

    files: list[Path] = []
    if path.is_dir():
        for p in sorted(path.iterdir()):
            if p.suffix.lower() in (".yaml", ".yml", ".json"):
                files.append(p)
    else:
        files.append(path)

    specs: list[dict[str, Any]] = []
    for f in files:
        raw_text = f.read_text(encoding="utf-8")
        if f.suffix.lower() in (".yaml", ".yml"):
            if yaml is None:
                raise ImportError("PyYAML is required to parse YAML test files: pip install pyyaml")
            data = yaml.safe_load(raw_text) or {}
        else:
            data = json.loads(raw_text)

        # Check if file has a top-level 'cases' list (e.g. benchmark suite)
        if isinstance(data, dict) and "cases" in data and isinstance(data["cases"], list):
            for idx, c in enumerate(data["cases"]):
                specs.append({
                    "name": c.get("id", f"{f.stem}_{idx}"),
                    "source_file": str(f),
                    "video": c.get("video"),
                    "transcript": c.get("transcript"),
                    "instructions": [c["instruction"]] if "instruction" in c else c.get("instructions", []),
                    "raw_case": c,
                })
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                specs.append({
                    "name": item.get("id", f"{f.stem}_{idx}"),
                    "source_file": str(f),
                    "video": item.get("video"),
                    "transcript": item.get("transcript"),
                    "instructions": item.get("instructions", []),
                    "raw_case": item,
                })
        elif isinstance(data, dict):
            specs.append({
                "name": data.get("id", f.stem),
                "source_file": str(f),
                "video": data.get("video"),
                "transcript": data.get("transcript"),
                "instructions": data.get("instructions", []),
                "render": data.get("render", False),
                "raw_case": data,
            })

    return specs


def run_case_against_api(
    client: BatchRunnerAPIClient,
    spec: dict[str, Any],
    case_out_dir: Path,
) -> dict[str, Any]:
    """Execute a single test case against the PromptCut API and write outputs."""
    case_out_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()
    result_record: dict[str, Any] = {
        "case_name": spec["name"],
        "source_file": spec.get("source_file"),
        "status": "pending",
        "session_id": None,
        "instructions_executed": [],
        "timeline_hash": None,
        "error": None,
        "duration_seconds": 0.0,
    }

    # Step 1: Create session
    video_target = spec.get("video")
    transcript_target = spec.get("transcript")

    session_id: str
    job_id: str

    if not video_target:
        # If no video supplied, call /sample-session endpoint
        r = client.post("/api/v1/sample-session")
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(f"Failed to create sample session: {r.status_code} {r.text}")
        init_data = r.json()
        session_id = init_data["session_id"]
        job_id = init_data.get("job_id", "")
    else:
        post_data = {"video_path": str(video_target)}
        if transcript_target:
            post_data["transcript_fixture"] = str(transcript_target)
        r = client.post("/api/v1/sessions", json=post_data)
        if r.status_code not in (200, 201, 202):
            raise RuntimeError(f"Failed to create session with video {video_target}: {r.status_code} {r.text}")
        init_data = r.json()
        session_id = init_data["session_id"]
        job_id = init_data.get("job_id", "")

    result_record["session_id"] = session_id

    # Step 2: Poll transcription job if present
    if job_id:
        deadline = time.time() + 60.0
        while time.time() < deadline:
            jr = client.get(f"/api/v1/jobs/{job_id}")
            if jr.status_code == 200:
                jdata = jr.json()
                if jdata.get("status") in ("succeeded", "completed"):
                    break
                if jdata.get("status") == "failed":
                    raise RuntimeError(f"Transcription job failed: {jdata.get('error')}")
            time.sleep(0.5)

    # Step 3: Run instructions
    instructions = spec.get("instructions", [])
    step_results = []
    for inst in instructions:
        inst_text = inst.get("say", inst) if isinstance(inst, dict) else str(inst)
        ir = client.post(
            f"/api/v1/sessions/{session_id}/instructions",
            json={"prompt": inst_text},
        )
        if ir.status_code not in (200, 202):
            raise RuntimeError(f"Instruction failed '{inst_text}': {ir.status_code} {ir.text}")
        step_data = ir.json()
        step_results.append({
            "prompt": inst_text,
            "status": step_data.get("status"),
            "action": step_data.get("action"),
            "response": step_data.get("response"),
            "time_policy": step_data.get("time_policy"),
        })

    result_record["instructions_executed"] = step_results

    # Step 4: Optional render
    if spec.get("render", False):
        rr = client.post(f"/api/v1/sessions/{session_id}/render")
        if rr.status_code in (200, 202):
            render_job_id = rr.json().get("job_id")
            deadline = time.time() + 120.0
            while time.time() < deadline:
                rjr = client.get(f"/api/v1/jobs/{render_job_id}")
                if rjr.status_code == 200 and rjr.json().get("status") == "succeeded":
                    result_record["download_url"] = rjr.json().get("download_url")
                    break
                time.sleep(1.0)

    # Step 5: Fetch timeline & log
    tr = client.get(f"/api/v1/sessions/{session_id}/timeline")
    if tr.status_code == 200:
        timeline_data = tr.json()
        result_record["timeline_hash"] = timeline_data.get("timeline_hash")
        result_record["total_duration"] = timeline_data.get("total_duration")
        (case_out_dir / "timeline.json").write_text(json.dumps(timeline_data, indent=2), encoding="utf-8")

    lr = client.get(f"/api/v1/sessions/{session_id}/log")
    if lr.status_code == 200:
        log_data = lr.json()
        (case_out_dir / "log.json").write_text(json.dumps(log_data, indent=2), encoding="utf-8")

    result_record["status"] = "succeeded"
    result_record["duration_seconds"] = round(time.time() - start_time, 3)

    # Write per-case result.json
    (case_out_dir / "result.json").write_text(json.dumps(result_record, indent=2), encoding="utf-8")
    return result_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PromptCut API Batch Runner (IncuBrix Standard #10)",
    )
    parser.add_argument(
        "input",
        help="Folder or file of test inputs (.yaml, .yml, .json)",
    )
    parser.add_argument(
        "-o", "--output",
        default="batch_results",
        help="Directory to save all results and summary.json (default: batch_results)",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("PROMPTCUT_API_URL", "http://localhost:8000"),
        help="PromptCut API base URL (default: http://localhost:8000)",
    )

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        specs = load_input_spec(input_path)
    except Exception as e:
        print(f"[batch_runner] Error loading input: {e}", file=sys.stderr)
        return 1

    if not specs:
        print(f"[batch_runner] No test specifications found in '{input_path}'", file=sys.stderr)
        return 1

    print(f"[batch_runner] Loaded {len(specs)} test case(s) from '{input_path}'")
    print(f"[batch_runner] Writing results to '{output_dir}'")

    client = BatchRunnerAPIClient(base_url=args.api_url)

    total_start = time.time()
    results: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    for idx, spec in enumerate(specs, 1):
        name = spec["name"]
        print(f"[{idx}/{len(specs)}] Running case '{name}'...")
        case_dir = output_dir / name
        try:
            res = run_case_against_api(client, spec, case_dir)
            succeeded += 1
            results.append(res)
            print(f"  [OK] '{name}' succeeded in {res['duration_seconds']}s")
        except Exception as e:
            failed += 1
            fail_record = {
                "case_name": name,
                "source_file": spec.get("source_file"),
                "status": "failed",
                "error": str(e),
                "duration_seconds": 0.0,
            }
            results.append(fail_record)
            (case_dir / "result.json").write_text(json.dumps(fail_record, indent=2), encoding="utf-8")
            print(f"  [FAIL] '{name}' failed: {e}", file=sys.stderr)

    summary = {
        "standard": "IncuBrix Integration Standard #10",
        "api_url": args.api_url,
        "input": str(input_path),
        "output_dir": str(output_dir),
        "total_cases": len(specs),
        "succeeded": succeeded,
        "failed": failed,
        "duration_seconds": round(time.time() - total_start, 3),
        "cases": results,
    }

    summary_file = output_dir / "summary.json"
    summary_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("=" * 60)
    print(f"[batch_runner] Completed: {succeeded}/{len(specs)} succeeded, {failed} failed")
    print(f"[batch_runner] Summary written to {summary_file}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
