#!/usr/bin/env python3
"""Verify canonical skills and optionally produce a deterministic plugin ZIP."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "hitkeep-analytics"
SKILLS = {
    "hitkeep-analytics", "hitkeep-traffic-diagnosis", "hitkeep-ai-visibility-analyst",
    "hitkeep-ecommerce-analyst", "hitkeep-tracking-verifier",
}

CANONICAL = {f"skills/{name}/{part}" for name in SKILLS for part in (
    "SKILL.md", "references/procedure.md", "agents/openai.yaml"
)} | {"LICENSE"}
GENERATED = {f"plugins/{NAME}/{path}" for path in CANONICAL | {
    "plugin.json", ".claude-plugin/plugin.json", "source.json", "README.md"
}} | {"LICENSE", ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"}


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError("cannot read immutable Git source")
    return result.stdout


def canonical(source_root, source_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source SHA must be a full immutable commit")
    if git(source_root, "rev-parse", source_commit + "^{commit}").decode().strip() != source_commit:
        raise ValueError("source SHA is not a commit")
    result = {}
    for relative in sorted(CANONICAL):
        entry = git(source_root, "ls-tree", source_commit, "--", relative).decode().strip()
        if not entry.startswith("100644 blob ") or entry.split("\t")[-1] != relative:
            raise ValueError(f"canonical source must be a regular non-executable file: {relative}")
        result[relative] = git(source_root, "show", f"{source_commit}:{relative}")
    return result


def verify_source(source_root: Path, source_commit: str, root: Path = ROOT) -> None:
    verify(root)
    plugin = root / "plugins" / NAME
    inventory = json.loads((plugin / "source.json").read_text())
    if inventory["commit"] != source_commit:
        raise ValueError("upstream source commit mismatch")
    if "packager_commit" in inventory and not re.fullmatch(r"[0-9a-f]{40}", inventory["packager_commit"]):
        raise ValueError("packager commit must be immutable")
    for relative, data in canonical(source_root, source_commit).items():
        if (plugin / relative).read_bytes() != data:
            raise ValueError(f"upstream content mismatch: {relative}")
    if (root / "LICENSE").read_bytes() != (plugin / "LICENSE").read_bytes():
        raise ValueError("upstream content mismatch: root LICENSE")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def sync(source_root: Path, source_commit: str, version: str, root: Path = ROOT) -> None:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("invalid release version")
    root = Path(root)
    if root.is_symlink():
        raise ValueError("symlink in output root")
    root = root.resolve()
    for relative in GENERATED:
        path = root / relative
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError("symlink in output path")
    verify(root)
    files = canonical(source_root, source_commit)
    packager_commit = git(ROOT, "rev-parse", "HEAD").decode().strip()
    # Stage and verify the entire generated tree before replacing any output.
    with tempfile.TemporaryDirectory(prefix=".hitkeep-package-", dir=root.parent) as temp:
        stage = Path(temp)
        for name in ("plugins", ".claude-plugin", ".agents"):
            shutil.copytree(root / name, stage / name)
        plugin = stage / "plugins" / NAME
        for relative, data in files.items():
            (plugin / relative).write_bytes(data)
        (stage / "LICENSE").write_bytes(files["LICENSE"])
        for relative in ("plugin.json", ".claude-plugin/plugin.json"):
            path = plugin / relative
            manifest = json.loads(path.read_text())
            manifest["version"] = version
            write_json(path, manifest)
        path = stage / ".claude-plugin/marketplace.json"
        catalog = json.loads(path.read_text())
        catalog["plugins"][0]["version"] = version
        write_json(path, catalog)
        write_json(plugin / "source.json", {
            "repository": "https://github.com/HitKeep/hitkeep", "commit": source_commit,
            "packager_commit": packager_commit,
            "sha256": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
        })
        verify_source(source_root, source_commit, stage)
        for relative in sorted(GENERATED):
            os.replace(stage / relative, root / relative)


def verify(root=ROOT):
    plugin = root / "plugins" / NAME
    if plugin.is_symlink():
        raise ValueError("symlink in package root")
    portable = json.loads((plugin / "plugin.json").read_text())
    claude = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
    if set(portable) != {"$schema", "name", "version", "description", "author", "license", "extensions"} or set(claude) != {"name", "version", "description", "author", "license", "skills"}:
        raise ValueError("unexpected native manifest fields or hooks")
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
    expected = CANONICAL
    if set(source["sha256"]) != expected:
        raise ValueError("canonical source allowlist changed")
    allowed = expected | {"plugin.json", ".claude-plugin/plugin.json", "source.json", "README.md"}
    allowed_dirs = {parent.as_posix() for relative in allowed for parent in Path(relative).parents if parent != Path(".")}
    actual = set()
    for path in plugin.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink in package: {path}")
        if path.is_dir():
            if path.relative_to(plugin).as_posix() not in allowed_dirs:
                raise ValueError("package directory allowlist mismatch")
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
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sync-from", type=Path)
    modes.add_argument("--verify-source", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--version")
    args = parser.parse_args()
    if (args.sync_from or args.verify_source) and not args.source_sha:
        parser.error("source mode requires --source-sha")
    if args.sync_from and not args.version:
        parser.error("--sync-from requires --version")
    if args.version and not args.sync_from:
        parser.error("--version requires --sync-from")
    if args.source_sha and not (args.sync_from or args.verify_source):
        parser.error("--source-sha requires a source mode")
    if args.sync_from:
        sync(args.sync_from, args.source_sha, args.version, ROOT)
    if args.verify_source:
        verify_source(args.verify_source, args.source_sha, ROOT)
    version = package(args.output) if args.output else verify()[0]
    print(f"Verified {NAME} {version}: five canonical skills, two native manifests")
