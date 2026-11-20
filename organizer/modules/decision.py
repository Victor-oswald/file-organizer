"""Stage 4: turn a Proposal into a final Decision.

    confidence >= threshold  -> SMART RENAME (or keep/move if the name is already clean)
    confidence <  threshold  -> SAFE QUARANTINE into _Review_Needed/ (name untouched)

One deliberate refinement of the flowchart: a file whose name is ALREADY clean is
never quarantined for low confidence. We would not change its name anyway, so
there is nothing to "guess", and dragging well-named files into a review folder
only creates work for the user.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Decision, Features, Proposal
from . import namer
from .directory_manager import DirectoryManager, PathReserver


def decide(f: Features, p: Proposal, *, folder: Path | None, reserver: PathReserver,
           dm: DirectoryManager, threshold: float) -> Decision:
    rec = f.record
    src = rec.path
    signals = {**p.signals, "threshold": threshold}
    if f.notes:
        signals["notes"] = list(f.notes)
    conf = round(p.confidence, 4)

    def keep(reason: str) -> Decision:
        return Decision(f, "keep", conf, reason, src, signals)

    if f.error:
        return keep(f"left untouched: {p.reason}")
    if f.category == "empty":
        return keep("left untouched: empty file")

    clean = namer.is_clean_name(rec.stem)

    if p.confidence >= threshold and p.stem:
        target_dir = folder or src.parent
        new_name = rec.name if clean else namer.sanitize(p.stem) + rec.extension
        dest = reserver.reserve(target_dir / new_name, current=src)
        if dest == src:
            return keep("already correctly named" if not clean else "name already clean; preserved")
        action = "rename" if dest.name != rec.name else "move"
        why = p.reason + ("; folder assigned by grouping" if folder else "")
        if clean:
            why += "; existing clean name preserved"
        return Decision(f, action, conf, why, dest, signals)

    if clean:
        return keep(f"confidence {conf:.2f} below {threshold:.2f} but name is already clean; preserved ({p.reason})")

    dest = reserver.reserve(dm.review_dir / rec.name, current=src)
    return Decision(f, "quarantine", conf, f"confidence {conf:.2f} below {threshold:.2f}: {p.reason}", dest, signals)


def decide_duplicate(f: Features, primary: Features, *, mode: str, reserver: PathReserver,
                     dm: DirectoryManager) -> Decision:
    rec = f.record
    reason = f"byte-identical (SHA-256) to {primary.record.path.name}"
    signals = {"sha256": f.sha256, "keeps": str(primary.record.path)}
    if mode == "move":
        dest = reserver.reserve(dm.duplicates_dir / rec.name, current=rec.path)
        return Decision(f, "duplicate", 1.0, reason + "; moved to _Duplicates", dest, signals, primary.record.path)
    return Decision(f, "duplicate", 1.0, reason + "; flagged only (use --dupes move to move it)",
                    rec.path, signals, primary.record.path)
