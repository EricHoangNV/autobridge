# ruff: noqa: B008, TC002, TC003, ARG001, SIM102, SIM105, SIM118, UP035, I001, UP006, UP007, RUF012, B027
"""Bench scorecards endpoint (P1.W7.1, plan §2.11)."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter

from autobridge.control.schemas import ScorecardEntry, ScorecardsResponse

router = APIRouter(tags=["bench"])


@router.get("/bench/scorecards", response_model=ScorecardsResponse)
def list_scorecards() -> ScorecardsResponse:
    # Look for scorecard files in experiments/ and experiments/benchmark_runs/
    candidates: list[ScorecardEntry] = []
    roots = [pathlib.Path("experiments"), pathlib.Path("experiments/benchmark_runs")]
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            try:
                stat = p.stat()
                content = p.read_text(encoding="utf-8")[:2000]
                # Try to extract scenario or verdict hints
                candidates.append(
                    ScorecardEntry(
                        path=str(p),
                        content_preview=content[:500],
                        created_at=str(stat.st_mtime),
                    )
                )
            except Exception:
                candidates.append(ScorecardEntry(path=str(p)))
        for p in sorted(root.rglob("*.json")):
            try:
                stat = p.stat()
                candidates.append(
                    ScorecardEntry(
                        path=str(p),
                        created_at=str(stat.st_mtime),
                        content_preview=p.read_text(encoding="utf-8")[:500],
                    )
                )
            except Exception:
                candidates.append(ScorecardEntry(path=str(p)))

    return ScorecardsResponse(scorecards=candidates, total=len(candidates))


@router.get("/bench/scorecard", response_model=dict)
def get_scorecard(path: str | None = None) -> dict:
    """Return a specific scorecard file content."""
    if path is None:
        # Default to experiments/scorecard.md if exists
        default = pathlib.Path("experiments/scorecard.md")
        if default.is_file():
            return {"path": str(default), "content": default.read_text(encoding="utf-8")}
        return {"path": None, "content": None, "scorecards": []}
    p = pathlib.Path(path)
    # Security: only allow under experiments/
    try:
        p_resolved = p.resolve()
        exp_resolved = pathlib.Path("experiments").resolve()
        if not str(p_resolved).startswith(str(exp_resolved)):
            # Also allow absolute under repo
            if "experiments" not in str(p):
                return {"error": "path must be under experiments/"}
    except Exception:
        pass
    if not p.is_file():
        return {"error": f"not found: {path}"}
    return {"path": str(p), "content": p.read_text(encoding="utf-8")}
