"""Validate ExporTo metadata and the files required in its release archive."""

import argparse
import json
from pathlib import Path
import re
import struct
import uuid

ROOT = Path(__file__).resolve().parents[1]
ICONS = {
    "16x16.png": 16,
    "32x32.png": 32,
    "16x16@2x.png": 32,
    "32x32@2x.png": 64,
}


def validate(root=ROOT):
    metadata = json.loads((root / ".fusion-addin.json").read_text(encoding="utf-8"))
    if metadata.get("name") != "ExporTo" or metadata.get("initialized") is not True:
        raise ValueError("ExporTo must be initialized in project metadata.")
    name = metadata["name"]
    package = root / name
    manifest = json.loads((package / f"{name}.manifest").read_text(encoding="utf-8"))
    if manifest.get("autodeskProduct") != "Fusion360" or manifest.get("type") != "addin":
        raise ValueError("Expected an Autodesk Fusion add-in manifest.")
    uuid.UUID(manifest["id"])
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest.get("version", "")):
        raise ValueError("Expected a numeric manifest version.")
    if manifest.get("supportedOS") != "windows|mac" or manifest.get("runOnStartup") is not True:
        raise ValueError("Expected macOS and Windows support with autostart enabled.")
    if manifest.get("description", {}).get("") != metadata.get("description"):
        raise ValueError("The project and manifest descriptions must match.")
    for filename in (f"{name}.py", "platform_apps.py"):
        source = package / filename
        compile(source.read_bytes(), str(source), "exec")
    for directory in ("application", "clear", "export", "stl"):
        for filename, size in ICONS.items():
            path = package / "resources" / directory / filename
            with path.open("rb") as stream:
                header = stream.read(24)
            expected_header = b"\x89PNG\r\n\x1a\n"
            if len(header) != 24 or header[:8] != expected_header:
                raise ValueError(f"Expected a PNG at {path}")
            if struct.unpack(">II", header[16:24]) != (size, size):
                raise ValueError(f"Expected a {size} by {size} PNG at {path}")
    for filename in ("README.md", "README.ru.md", "docs/RELEASING.md", "GitVersion.yml"):
        if not (root / filename).is_file():
            raise ValueError(f"Missing project file {filename}")
    release_paths = (root / ".github" / "release-paths.txt").read_text(encoding="utf-8")
    if f"{name}/**" not in release_paths.splitlines():
        raise ValueError("The add-in folder must trigger change-based releases.")
    return name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-template", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    product = validate()
    license_path = ROOT / "LICENSE"
    licensed = license_path.is_file() and not license_path.is_symlink() and license_path.stat().st_size > 0
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"product={product}\ninitialized=true\nlicensed={str(licensed).lower()}\n")
    print(f"Validated {product}; distribution license present: {licensed}")


if __name__ == "__main__":
    main()
