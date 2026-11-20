"""Stage 3 (scoring): every file gets a Proposal = confidence in [0, 1] + a
suggested name + the human-readable reasons. All formulas are transparent."""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..models import Features, Proposal
from . import namer
from .text_features import Vector, top_terms


@dataclass
class GroupInfo:
    """What clustering learned about one file (all optional)."""
    text_cluster_size: int = 1
    text_cluster_sim: float = 0.0
    photo_cluster_size: int = 1
    photo_mean_distance: float = 0.0


def text_confidence(n_tokens: int, vec: Vector, full_coverage: int) -> tuple[float, dict]:
    """
        coverage = min(1, tokens / full_coverage)       enough text to judge?
        strength = min(1, top3_mass / 0.10)             does a few terms dominate?
                   (top3_mass = sum of squared weights of the 3 best terms; the vector is
                    L2-normalised so all squared weights sum to 1)
        raw      = coverage * (0.6 + 0.4 * strength)
        confidence = 0.50 + 0.46 * raw                  range 0.50 .. 0.96
    """
    if n_tokens == 0 or not vec:
        return 0.0, {"coverage": 0.0, "strength": 0.0}
    coverage = min(1.0, n_tokens / full_coverage)
    top3 = sum(w * w for w in sorted(vec.values(), reverse=True)[:3])
    strength = min(1.0, top3 / 0.10)
    raw = coverage * (0.6 + 0.4 * strength)
    return 0.50 + 0.46 * raw, {"coverage": round(coverage, 3), "strength": round(strength, 3)}


def _pick_terms(vec: Vector) -> tuple[list[str], bool]:
    """Best bigram, then the best bigram sharing no word with it; else top unigrams."""
    bis = top_terms(vec, bigram=True, k=10)
    if bis:
        chosen = [bis[0][0]]
        used = set(bis[0][0].split())
        for term, _ in bis[1:]:
            if not used & set(term.split()):
                chosen.append(term)
                break
        return chosen, False
    return [t for t, _ in top_terms(vec, bigram=False, k=2)], True


def _date(f: Features) -> str:
    return f.best_date().strftime("%Y-%m-%d")


def _dims(f: Features) -> str:
    return f"{f.width}x{f.height}" if f.width and f.height else "unknown-size"


class Scorer:
    def score(self, f: Features, vec: Vector, group: GroupInfo) -> Proposal:
        if f.error:
            return Proposal(0.0, f"file could not be read ({f.error})")
        if f.category == "empty":
            return Proposal(0.0, "empty file")
        handler = {
            "image": self._image, "video": self._video, "audio": self._audio, "binary": self._binary,
        }.get(f.category)
        if handler:
            return handler(f, group)
        if f.category in ("document", "code", "data", "archive"):
            return self._textual(f, vec, group)
        return Proposal(0.0, "no deterministic signal for this file type")

    # -- documents, code, CSVs, archive listings -----------------------------
    def _textual(self, f: Features, vec: Vector, group: GroupInfo) -> Proposal:
        full = config.ARCHIVE_FULL_COVERAGE_TOKENS if f.category == "archive" else config.TEXT_FULL_COVERAGE_TOKENS
        conf, parts = text_confidence(len(f.tokens), vec, full)
        if conf == 0.0:
            return Proposal(0.0, "no readable text could be extracted", signals={"tokens": 0})
        terms, unigram_only = _pick_terms(vec)
        if unigram_only:
            conf -= 0.10
        cohesion_bonus = 0.0
        if group.text_cluster_size > 1:
            cohesion_bonus = min(0.04, 0.10 * group.text_cluster_sim)
            conf += cohesion_bonus
        conf = max(0.0, min(0.96, conf))
        label = f.category.capitalize()
        stem = "_".join([_date(f), label] + [namer.title_slug(t) for t in terms])
        signals = {
            "tokens": len(f.tokens), **parts,
            "top_bigrams": [[t, round(w, 4)] for t, w in top_terms(vec, bigram=True, k=5)],
            "top_unigrams": [[t, round(w, 4)] for t, w in top_terms(vec, bigram=False, k=5)],
            "unigram_only_penalty": unigram_only,
            "cluster_size": group.text_cluster_size, "cluster_mean_similarity": round(group.text_cluster_sim, 3),
            "cohesion_bonus": round(cohesion_bonus, 3),
        }
        return Proposal(conf, f"{len(f.tokens)} tokens; topic terms: {', '.join(terms)}", stem, signals)

    # -- images ---------------------------------------------------------------
    def _image(self, f: Features, group: GroupInfo) -> Proposal:
        dims = _dims(f)
        if f.is_camera:
            conf = min(0.98, 0.92 + (0.03 if f.taken else 0.0) + (0.03 if f.has_gps else 0.0))
            when = f.best_date().strftime("%Y-%m-%d_%H%M%S")
            return Proposal(conf, "camera photo: EXIF present" + (" with capture date" if f.taken else ""),
                            f"{when}_{dims}", {"exif_date": bool(f.taken), "gps": f.has_gps})
        if f.is_screenshot:
            conf = min(0.92, 0.85 + 0.07 * f.flatness)
            return Proposal(conf, "screenshot: no EXIF and flat colours (low colour entropy)",
                            f"{_date(f)}_Screenshot_{dims}", {"flatness": round(f.flatness, 3)})
        if group.photo_cluster_size > 1:
            conf = max(0.70, 0.85 - 0.025 * group.photo_mean_distance)
            return Proposal(conf, f"unlabeled photo; {group.photo_cluster_size} visually similar photos (dHash)",
                            f"{_date(f)}_Photo_{dims}",
                            {"cluster_size": group.photo_cluster_size,
                             "mean_hamming_distance": round(group.photo_mean_distance, 2)})
        return Proposal(0.45, "unlabeled photo with no EXIF and no visually similar neighbours",
                        f"{_date(f)}_Photo_{dims}", {"cluster_size": 1})

    # -- video ----------------------------------------------------------------
    def _video(self, f: Features, group: GroupInfo) -> Proposal:
        if f.taken_source != "mp4":
            return Proposal(0.40, "video container not parsed or has no creation date")
        conf = min(0.98, 0.92 + (0.03 if f.duration else 0) + (0.03 if f.fps else 0))
        when = f.taken.strftime("%Y-%m-%d_%H%M%S")                 # type: ignore[union-attr]
        stem = f"{when}_Video_{_dims(f)}"
        return Proposal(conf, "video: creation date from mp4 header atoms", stem,
                        {"duration_s": f.duration, "fps": f.fps})

    # -- audio ----------------------------------------------------------------
    def _audio(self, f: Features, group: GroupInfo) -> Proposal:
        if f.artist and f.title:
            return Proposal(0.96, "audio: ID3 artist and title", namer.sanitize(f"{f.artist} - {f.title}"),
                            {"artist": f.artist, "title": f.title})
        if f.title:
            return Proposal(0.80, "audio: ID3 title only", namer.sanitize(f.title), {"title": f.title})
        if f.is_voice_memo:
            when = f.best_date().strftime("%Y-%m-%d_%H%M%S")
            return Proposal(0.85, "voice memo: mono, low sample rate, no artist tag", f"{when}_VoiceMemo",
                            {"sample_rate": f.sample_rate, "channels": f.channels})
        return Proposal(0.45, "audio with no usable tags")

    # -- executables ----------------------------------------------------------
    def _binary(self, f: Features, group: GroupInfo) -> Proposal:
        if f.product:
            conf = 0.90 + (0.04 if f.vendor else 0.0)
            parts = [namer.sanitize(f.vendor)] if f.vendor else []
            parts.append(namer.sanitize(f.product))
            return Proposal(conf, "binary: vendor/product strings from PE version resource",
                            "_".join(p.replace(" ", "-") for p in parts), {"vendor": f.vendor, "product": f.product})
        return Proposal(0.0, "binary without readable product strings")
