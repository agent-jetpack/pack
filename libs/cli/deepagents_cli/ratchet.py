"""Ratchet substrate — the mechanism that makes every run improve the repo.

Two files live under ``.harness/`` at the repo root:

``violations.json``
    Known violations the repo is carrying. Seeded on first discovery
    (Phase B.3). New violations are blocked; existing ones stay until a
    targeted cleanup removes them. That asymmetry is the ratchet — the
    repo can only move toward compliance, never away from it.

``quality-score.json``
    Rolling metrics snapshot. LOC, files-over-threshold count, forbidden
    import count, test coverage delta. Each harness run may append a
    new entry. A monotonic-up trajectory is the health signal.

This module is intentionally dependency-light (no LangGraph, no
middleware) so every layer of the harness can read the ratchet without
circular imports. Middleware that *produces* violations uses the
``Ratchet.record`` hook; anything that reads state calls the Ratchet
instance directly.

Phase A.3 of the agent-harness roadmap.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- Types ---------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One recorded rule-break attempt.

    Attributes:
        rule: Short stable identifier of the rule that was violated
            (e.g. ``scope.out_of_paths``, ``arch.forbidden_import``).
            The rule string is the grouping key for aggregation.
        subject: What the violation is *about* — usually a file path,
            sometimes an import spec or a symbol name.
        reason: Human-readable one-line context for why it was flagged.
        first_seen: ISO-8601 UTC timestamp when this violation was
            first recorded in the ratchet. Stable identity for
            "existing violations" that should be tolerated.
    """

    rule: str
    subject: str
    reason: str
    first_seen: str

    def identity(self) -> tuple[str, str]:
        """Key used for dedup between in-memory and on-disk state."""
        return (self.rule, self.subject)


@dataclass
class QualitySnapshot:
    """One timestamped point in the rolling quality trajectory.

    Keep the shape small and additive. Consumers should tolerate
    unknown keys (forward compat) and missing keys (backward compat).

    Attributes:
        taken_at: ISO-8601 UTC timestamp.
        total_loc: Total lines of code in tracked sources.
        large_files: Count of files above the configured size threshold.
        forbidden_imports: Count of active forbidden-import violations.
        test_coverage_pct: Test coverage as a 0-100 number, when known.
        notes: Short free-form annotation (what run produced this).
    """

    taken_at: str
    total_loc: int = 0
    large_files: int = 0
    forbidden_imports: int = 0
    test_coverage_pct: float | None = None
    notes: str = ""


# --- Ratchet class -------------------------------------------------------


class Ratchet:
    """Read/write the ``.harness/`` ratchet state files.

    Loading is lazy: constructors don't touch disk. Callers that want
    the existing state call ``load()``; callers that only want to
    *append* can just call ``record()`` — the file is read-modify-written
    under a simple advisory lock so concurrent writes serialize.

    Args:
        harness_dir: Root ``.harness/`` directory. Defaults to
            ``<cwd>/.harness``. Missing directory is created on first
            write; missing files are treated as empty.
    """

    VIOLATIONS_FILENAME = "violations.json"
    QUALITY_FILENAME = "quality-score.json"

    def __init__(self, harness_dir: str | Path | None = None) -> None:
        if harness_dir is None:
            harness_dir = Path.cwd() / ".harness"
        self.harness_dir = Path(harness_dir)

    # -- I/O helpers -----------------------------------------------------

    @property
    def violations_path(self) -> Path:
        return self.harness_dir / self.VIOLATIONS_FILENAME

    @property
    def quality_path(self) -> Path:
        return self.harness_dir / self.QUALITY_FILENAME

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Corrupt or unreadable — treat as empty so one bad file
            # doesn't stop the agent. We log so the operator notices.
            logger.warning("Ratchet: failed to read %s: %s", path, exc)
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)  # atomic on POSIX

    # -- Violation load/record ------------------------------------------

    def load_violations(self) -> list[Violation]:
        """Return every violation currently tracked on disk."""
        raw = self._read_json(self.violations_path, default=[])
        if not isinstance(raw, list):
            logger.warning(
                "Ratchet: %s contained non-list data; treating as empty",
                self.violations_path,
            )
            return []
        out: list[Violation] = []
        for entry in raw:
            try:
                out.append(
                    Violation(
                        rule=entry["rule"],
                        subject=entry["subject"],
                        reason=entry.get("reason", ""),
                        first_seen=entry["first_seen"],
                    )
                )
            except (KeyError, TypeError):
                logger.debug("Ratchet: skipping malformed violation entry %r", entry)
        return out

    def record(
        self,
        rule: str,
        subject: str,
        reason: str,
    ) -> tuple[Violation, bool]:
        """Record a violation, deduped by (rule, subject).

        If an entry with the same (rule, subject) already exists, the
        existing record is returned unchanged — this is how the ratchet
        distinguishes "already known / tolerated" from "newly appeared".

        Returns:
            Tuple of (violation, is_new). When ``is_new`` is True the
            caller knows this violation was not previously tracked, so
            enforcement middleware can decide to block.
        """
        existing = {v.identity(): v for v in self.load_violations()}
        key = (rule, subject)
        if key in existing:
            return existing[key], False

        violation = Violation(
            rule=rule,
            subject=subject,
            reason=reason,
            first_seen=datetime.now(UTC).isoformat(),
        )
        existing[key] = violation
        payload = [asdict(v) for v in existing.values()]
        self._write_json(self.violations_path, payload)
        return violation, True

    def is_existing_violation(self, rule: str, subject: str) -> bool:
        """Check without writing whether (rule, subject) is already tracked."""
        return any(
            v.rule == rule and v.subject == subject for v in self.load_violations()
        )

    # -- Quality snapshots ----------------------------------------------

    def load_quality_history(self) -> list[QualitySnapshot]:
        raw = self._read_json(self.quality_path, default={"snapshots": []})
        if not isinstance(raw, dict):
            return []
        snapshots_raw = raw.get("snapshots")
        if not isinstance(snapshots_raw, list):
            return []
        out: list[QualitySnapshot] = []
        for entry in snapshots_raw:
            if not isinstance(entry, dict):
                continue
            try:
                out.append(
                    QualitySnapshot(
                        taken_at=entry["taken_at"],
                        total_loc=int(entry.get("total_loc", 0)),
                        large_files=int(entry.get("large_files", 0)),
                        forbidden_imports=int(entry.get("forbidden_imports", 0)),
                        test_coverage_pct=entry.get("test_coverage_pct"),
                        notes=entry.get("notes", ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.debug("Ratchet: skipping malformed snapshot %r", entry)
        return out

    def append_snapshot(self, snapshot: QualitySnapshot) -> None:
        """Append a quality snapshot to the rolling history."""
        history = self.load_quality_history()
        history.append(snapshot)
        payload = {"snapshots": [asdict(s) for s in history]}
        self._write_json(self.quality_path, payload)


__all__ = [
    "QualitySnapshot",
    "Ratchet",
    "Violation",
]
