"""Image reproducibility tests (AR-2-5): every image builds from a
digest-pinned base and hash-pinned lockfiles — no floating ranges.

Static checks only (no Docker needed): the CI step
`scripts/check-image-repro.sh` rebuilds the verifier image twice against a
live daemon and diffs `pip freeze`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PYTHON_DOCKERFILES = {
    "Dockerfile.engine": "requirements.engine.lock",
    "verifier/Dockerfile": "verifier/requirements.lock",
    "simulators/Dockerfile": "simulators/requirements.lock",
}

FROM_PINNED = re.compile(r"^FROM\s+\S+@sha256:[0-9a-f]{64}(\s+AS\s+\S+)?$")
LOCK_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\\;]+)")


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def test_all_from_instructions_are_digest_pinned() -> None:
    for rel in [*PYTHON_DOCKERFILES, "ui/Dockerfile"]:
        from_lines = [
            line.strip()
            for line in _read(rel).splitlines()
            if line.strip().startswith("FROM ")
        ]
        assert from_lines, f"{rel}: no FROM instruction found"
        for line in from_lines:
            assert FROM_PINNED.match(line), f"{rel}: floating base image: {line}"


def test_python_images_install_from_hash_pinned_lockfiles() -> None:
    for dockerfile_rel, lock_rel in PYTHON_DOCKERFILES.items():
        text = _read(dockerfile_rel)
        assert "--require-hashes" in text, f"{dockerfile_rel}: no --require-hashes install"
        assert Path(lock_rel).name in text, f"{dockerfile_rel}: does not install {lock_rel}"
        lock = _read(lock_rel)
        assert lock.strip(), f"{lock_rel}: empty lockfile"


def _stanzas(lock_rel: str) -> list[tuple[str, list[str]]]:
    """Split a requirements lock into (pin-line, continuation-lines) stanzas."""
    stanzas: list[tuple[str, list[str]]] = []
    for raw in _read(lock_rel).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if LOCK_PIN.match(line):
            stanzas.append((line, []))
        else:
            assert stanzas, f"{lock_rel}: continuation before first pin: {line!r}"
            stanzas[-1][1].append(line)
    return stanzas


def test_every_locked_package_carries_hashes() -> None:
    for lock_rel in PYTHON_DOCKERFILES.values():
        stanzas = _stanzas(lock_rel)
        assert stanzas, f"{lock_rel}: no pinned stanzas"
        for pin, rest in stanzas:
            match = LOCK_PIN.match(pin)
            assert match, f"{lock_rel}: stanza without == pin: {pin!r}"
            assert any("--hash=sha256:" in line for line in rest), (
                f"{lock_rel}: {match.group(1)} pinned without hashes"
            )


def _locked_versions(lock_rel: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for pin, _ in _stanzas(lock_rel):
        match = LOCK_PIN.match(pin)
        assert match is not None
        versions[match.group(1).lower()] = match.group(2)
    return versions


def test_shared_pins_are_identical_across_images() -> None:
    """All images resolve from the single uv.lock: shared packages must agree."""
    versions = {rel: _locked_versions(rel) for rel in PYTHON_DOCKERFILES.values()}
    shared = set.intersection(*(set(v) for v in versions.values()))
    assert {"fastapi", "uvicorn", "pydantic", "pyyaml"} <= shared
    for package in sorted(shared):
        pinned = {v[package] for v in versions.values()}
        assert len(pinned) == 1, f"{package} resolves differently: {pinned}"


def test_ui_image_uses_npm_ci_from_committed_lockfile() -> None:
    text = _read("ui/Dockerfile")
    assert "npm ci" in text, "ui/Dockerfile: must install with `npm ci`"
    run_installs = [
        line for line in text.splitlines() if line.strip().startswith("RUN") and "npm install" in line
    ]
    assert not run_installs, f"ui/Dockerfile: floating `npm install`: {run_installs}"
    assert (ROOT / "ui" / "package-lock.json").is_file(), "ui/package-lock.json missing"
