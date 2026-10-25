"""
Interpreter Benchmark Evaluation Runner.

Runs YAML-defined benchmark test suites against one or more InterpreterAdapter
implementations, reporting:
- Exact-Match Rate
- Valid JSON / Schema Rate
- Guess-vs-Clarify Rate (for ambiguous & vague cases)
- Average Latency (ms)
- Total Estimated Cost ($)

Usage:
  python -m promptcut.eval_interpreters
  python -m promptcut.eval_interpreters --models mock,rule
  python -m promptcut.eval_interpreters --models groq --benchmark eval_benchmark.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from ruamel.yaml import YAML

from promptcut.interpreter import (
    GroqInterpreter,
    IntentTarget,
    InterpretationResult,
    InterpreterAdapter,
    InterpreterContext,
    MockInterpreter,
    RuleInterpreter,
)


def load_benchmark(benchmark_path: str | Path) -> list[dict[str, Any]]:
    path = Path(benchmark_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Benchmark file not found: {path}")

    yaml = YAML(typ="safe")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f)

    if not isinstance(data, dict) or "cases" not in data:
        raise ValueError(f"Invalid benchmark format in {path}: expected 'cases' list.")

    return data["cases"]


def get_interpreter(model_name: str) -> InterpreterAdapter:
    name = model_name.strip().lower()
    if name == "mock":
        return MockInterpreter()
    if name == "rule":
        return RuleInterpreter()
    if name in ("groq", "openai/gpt-oss-120b") or "gpt" in name or "groq" in name or "llama" in name:
        return GroqInterpreter(model=model_name if name != "groq" else None)
    return MockInterpreter()


def evaluate_model(
    model_name: str,
    adapter: InterpreterAdapter,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    total_cases = len(cases)
    valid_json_count = 0
    exact_match_count = 0
    clarify_target_count = 0
    clarified_count = 0
    total_latency_ms = 0.0
    total_cost_usd = 0.0
    results: list[dict[str, Any]] = []

    for case in cases:
        instr = case.get("instruction", "")
        exp_status = case.get("expected_status")
        exp_action = case.get("expected_action")
        exp_query = case.get("expected_query")
        exp_hint = case.get("expected_time_hint")
        exp_occ = case.get("expected_occurrence")
        category = case.get("category", "general")

        ctx_dict = case.get("context", {})
        ctx = InterpreterContext(
            duration=ctx_dict.get("duration", 10.0),
            time_policy=ctx_dict.get("time_policy", "current"),
            pending_clarification=ctx_dict.get("pending_clarification"),
        )

        try:
            interp = adapter.interpret(instr, ctx)
            valid_json_count += 1
        except Exception as e:
            results.append({
                "id": case.get("id"),
                "instruction": instr,
                "passed": False,
                "error": str(e),
            })
            continue

        total_latency_ms += interp.latency_ms
        total_cost_usd += interp.cost_usd

        # Track guess-vs-clarify for vague cases
        if category in ("vague", "clarify_candidate") or exp_status == "needs_clarification":
            clarify_target_count += 1
            if interp.status == "needs_clarification":
                clarified_count += 1

        # Check exact match
        status_match = (interp.status == exp_status)
        action_match = True
        query_match = True
        hint_match = True
        occ_match = True

        if exp_action:
            if not interp.targets or interp.targets[0].action != exp_action:
                action_match = False

        if exp_query and interp.targets:
            if interp.targets[0].query != exp_query:
                query_match = False

        if exp_hint is not None and interp.targets:
            t_hint = interp.targets[0].time_hint
            if t_hint is None or abs(t_hint - float(exp_hint)) > 0.01:
                hint_match = False

        if exp_occ is not None and interp.targets:
            if interp.targets[0].occurrence != exp_occ:
                occ_match = False

        is_match = status_match and action_match and query_match and hint_match and occ_match
        if is_match:
            exact_match_count += 1

        results.append({
            "id": case.get("id"),
            "instruction": instr,
            "expected_status": exp_status,
            "actual_status": interp.status,
            "passed": is_match,
            "latency_ms": interp.latency_ms,
        })

    exact_match_rate = (exact_match_count / total_cases * 100.0) if total_cases > 0 else 0.0
    valid_json_rate = (valid_json_count / total_cases * 100.0) if total_cases > 0 else 0.0
    guess_vs_clarify_rate = (
        (clarified_count / clarify_target_count * 100.0) if clarify_target_count > 0 else 100.0
    )
    avg_latency = (total_latency_ms / total_cases) if total_cases > 0 else 0.0

    return {
        "model": model_name,
        "total_cases": total_cases,
        "exact_match_count": exact_match_count,
        "exact_match_rate": exact_match_rate,
        "valid_json_rate": valid_json_rate,
        "guess_vs_clarify_rate": guess_vs_clarify_rate,
        "avg_latency_ms": avg_latency,
        "total_cost_usd": total_cost_usd,
        "results": results,
    }


def print_summary_table(eval_summaries: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 80)
    print("PROMPTCUT INTERPRETER BENCHMARK EVALUATION RESULTS")
    print("=" * 80)
    header = f"{'Model':<20} | {'Exact Match':<12} | {'Valid JSON':<12} | {'Guess-vs-Clarify':<18} | {'Avg Latency':<12} | {'Total Cost'}"
    print(header)
    print("-" * 80)
    for s in eval_summaries:
        row = (
            f"{s['model']:<20} | "
            f"{s['exact_match_rate']:>5.1f}% ({s['exact_match_count']}/{s['total_cases']}) | "
            f"{s['valid_json_rate']:>10.1f}% | "
            f"{s['guess_vs_clarify_rate']:>16.1f}% | "
            f"{s['avg_latency_ms']:>8.2f} ms   | "
            f"${s['total_cost_usd']:>8.6f}"
        )
        print(row)
    print("=" * 80 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate promptcut natural language interpreters.")
    parser.add_argument(
        "--benchmark",
        "-b",
        default="eval_benchmark.yaml",
        help="Path to YAML benchmark file (default: eval_benchmark.yaml)",
    )
    parser.add_argument(
        "--models",
        "-m",
        default="mock,rule",
        help="Comma-separated model names to evaluate (e.g. 'mock,rule', 'groq')",
    )

    args = parser.parse_args()

    cases = load_benchmark(args.benchmark)
    model_names = [m.strip() for m in args.models.split(",") if m.strip()]

    summaries: list[dict[str, Any]] = []
    for m in model_names:
        adapter = get_interpreter(m)
        summary = evaluate_model(m, adapter, cases)
        summaries.append(summary)

    print_summary_table(summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
