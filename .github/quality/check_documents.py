#!/usr/bin/env python3
"""Apply the persisted document choice consistently in local and CI gates."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def document_mode(project: Path) -> str:
    config = project / ".github/quality/project.json"
    if not config.exists():
        return "standard"
    data = json.loads(config.read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
            or type(data.get("schema_version")) is not int
            or data.get("schema_version") != 1
            or data.get("managed_by") != "claude-agent-harness"
            or data.get("documents") not in ("standard", "minimal")):
        raise ValueError(f"Invalid project document configuration: {config}")
    return data["documents"]


def has_standard_documents(project: Path) -> bool:
    docs = project / "docs"
    return docs.is_dir() and any(
        re.match(r"^0[0-5](?:_|[.-]|$)", path.name)
        for path in docs.iterdir()
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    project = Path(args[0] if args else ".").resolve()
    try:
        mode = document_mode(project)
        if mode == "minimal" and not has_standard_documents(project):
            print("Standard documents omitted by saved project choice (minimal).")
            return 0
    except (OSError, ValueError) as exc:
        print(f"Document configuration error: {exc}", file=sys.stderr)
        return 1
    validator = Path(__file__).resolve().parent / "validator/validate_docs.py"
    return subprocess.run([sys.executable, str(validator), str(project / "docs")]).returncode


if __name__ == "__main__":
    sys.exit(main())
