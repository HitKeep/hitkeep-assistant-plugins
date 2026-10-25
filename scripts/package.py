#!/usr/bin/env python3
"""Verify canonical skills and optionally produce a deterministic plugin ZIP."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "hitkeep-analytics"
SKILLS = {
    "hitkeep-analytics", "hitkeep-traffic-diagnosis", "hitkeep-ai-visibility-analyst",
    "hitkeep-ecommerce-analyst", "hitkeep-tracking-verifier",
}


def verify(root=ROOT):
    plugin = root / "plugins" / NAME
    portable = json.loads((plugin / "plugin.json").read_text())
    claude = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
    version = portable["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("invalid release version")
    for key in ("name", "version", "description", "author", "license"):
        if portable[key] != claude[key]:
            raise ValueError(f"manifest mismatch: {key}")
    if portable["name"] != NAME or portable["license"] != "MIT":
        raise ValueError("invalid plugin identity")
    if portable["$schema"] != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise ValueError("invalid portable schema")
    if portable["extensions"]["com.openai"]["interface"]["capabilities"] != ["Read"]:
        raise ValueError("only read capabilities may be advertised")
    if claude["skills"] != "./skills/":
        raise ValueError("Claude skills must stay inside the plugin")

    catalog = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    openai = json.loads((root / ".agents/plugins/marketplace.json").read_text())
    if catalog["name"] != "hitkeep" or openai["name"] != "hitkeep":
        raise ValueError("marketplace identity changed")
    entry, = catalog["plugins"]
    expected_path = f"./plugins/{NAME}"
    if (entry["name"], entry["version"], entry["source"]) != (NAME, version, expected_path):
        raise ValueError("Claude catalog does not match the plugin")
    entry, = openai["plugins"]
    if entry["name"] != NAME or entry["source"] != {"source": "local", "path": expected_path}:
        raise ValueError("OpenAI catalog does not match the plugin")
    if entry["policy"] != {"installation": "AVAILABLE", "authentication": "ON_USE"}:
        raise ValueError("unexpected installation policy")

    source = json.loads((plugin / "source.json").read_text())
    if source["repository"] != "https://github.com/HitKeep/hitkeep":
        raise ValueError("unexpected canonical source")
    if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise ValueError("canonical commit must be immutable")
    expected = {f"skills/{name}/{part}" for name in SKILLS for part in (
        "SKILL.md", "references/procedure.md", "agents/openai.yaml"
    )} | {"LICENSE"}
    if set(source["sha256"]) != expected:
        raise ValueError("canonical source allowlist changed")
    allowed = expected | {"plugin.json", ".claude-plugin/plugin.json", "source.json", "README.md"}
    actual = set()
    for path in plugin.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in package: {path}")
        if path.is_dir():
            continue
        relative = path.relative_to(plugin).as_posix()
        if not path.is_file() or path.stat().st_mode & 0o111:
            raise ValueError(f"non-regular or executable package content: {relative}")
        actual.add(relative)
    if actual != allowed:
        raise ValueError(f"package allowlist mismatch: {actual ^ allowed}")
    for relative, digest in source["sha256"].items():
        if hashlib.sha256((plugin / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f"canonical content changed: {relative}")
    return version, sorted(actual)


def package(output, root=ROOT):
    version, paths = verify(root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in paths:
            entry = zipfile.ZipInfo(f"{NAME}/{relative}", (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (root / "plugins" / NAME / relative).read_bytes())
    return version


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the verified plugin ZIP")
    args = parser.parse_args()
    version = package(args.output) if args.output else verify()[0]
    print(f"Verified {NAME} {version}: five canonical skills, two native manifests")
