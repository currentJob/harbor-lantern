"""Export only browser assets to a fresh directory, with a public HTTPS API origin."""

import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def export(destination: Path, api_base: str) -> None:
    url = urlsplit(api_base)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in ("", "/") or url.query or url.fragment):
        raise ValueError("API base must be a public HTTPS origin without credentials, path or query")
    source = Path(__file__).resolve().parents[1] / "src" / "harbor_lantern" / "web"
    shutil.copytree(source, destination)
    config = {"apiBase": api_base.rstrip("/")}
    (destination / "config.js").write_text(
        "window.HARBOR_CONFIG = Object.freeze(" + json.dumps(config) + ");\n", encoding="utf-8")
    (destination / ".nojekyll").touch()
    shutil.copy2(source.parents[2] / "LICENSE", destination / "LICENSE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-base", required=True)
    args = parser.parse_args()
    export(args.output, args.api_base)
