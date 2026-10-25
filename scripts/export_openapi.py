"""
scripts/export_openapi.py

IncuBrix Integration Standard #8:
Exports openapi.yaml from the running FastAPI app.
Includes a CI check mode (--check) to verify that the committed openapi.yaml is up to date.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import yaml

# Ensure project src is in PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from promptcut.api.app import create_app


def export_openapi(output_path: Path, check_only: bool = False) -> int:
    app = create_app()
    schema = app.openapi()

    yaml_str = yaml.dump(schema, sort_keys=False, allow_unicode=True)

    if check_only:
        if not output_path.is_file():
            print(f"Error: {output_path} does not exist.", file=sys.stderr)
            return 1
        existing_yaml = output_path.read_text(encoding="utf-8")
        existing_schema = yaml.safe_load(existing_yaml)
        if existing_schema == schema:
            print(f"OK: {output_path} is up to date.")
            return 0
        else:
            print(f"Error: {output_path} is out of date. Re-run without --check to update.", file=sys.stderr)
            return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_str, encoding="utf-8")
    print(f"Successfully exported OpenAPI schema ({len(schema.get('paths', {}))} endpoints) -> {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export FastAPI OpenAPI spec as openapi.yaml")
    parser.add_argument(
        "-o",
        "--output",
        default=str(REPO_ROOT / "openapi.yaml"),
        help="Target output YAML file path (default: repo root openapi.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check if committed openapi.yaml is up to date without overwriting",
    )
    args = parser.parse_args()
    return export_openapi(Path(args.output), check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
