"""Glue between stage 1 (inspect) and stage 2 (features).

`inspect` = cheap fingerprints for EVERY file (partial hash + magic bytes).
`enrich`  = expensive, type-specific feature extraction (only for non-duplicates).

Both swallow per-file errors into `f.notes` / `f.error`: one broken file must
never stop the run.
"""

from __future__ import annotations

from .. import config
from ..models import Features
from . import archive_features, hasher, image_features, magic, media_features, text_features

_TEXT_CATEGORIES = {"document", "code", "data"}


def inspect(f: Features) -> None:
    rec = f.record
    try:
        f.partial_hash, head = hasher.fingerprint(rec.path, rec.size)
        f.magic_kind, f.category, f.ext_mismatch = magic.sniff_bytes(head, rec.extension, rec.size)
        if f.ext_mismatch:
            f.notes.append(f"extension {rec.extension!r} does not match content type {f.magic_kind!r}")
    except OSError as exc:
        f.error = f"{type(exc).__name__}: {exc}"


def enrich(f: Features) -> None:
    if f.error:
        return
    try:
        if f.category == "image":
            image_features.analyze_image(f)
        elif f.category == "video":
            if f.magic_kind in ("mp4", "mov"):
                media_features.parse_mp4(f)
            else:
                f.notes.append(f"container {f.magic_kind!r} is not parsed (only mp4/mov)")
        elif f.category == "audio":
            media_features.analyze_audio(f)
        elif f.category == "archive":
            archive_features.analyze_archive(f)
        elif f.category == "binary":
            archive_features.analyze_binary(f)
        elif f.category in _TEXT_CATEGORIES:
            _enrich_text(f)
    except Exception as exc:        # deliberate: see module docstring
        f.notes.append(f"feature extraction failed: {type(exc).__name__}: {exc}")


def _enrich_text(f: Features) -> None:
    rec, kind = f.record, f.magic_kind
    title: str | None = None
    if kind == "docx":
        text, title = text_features.extract_docx(rec.path)
    elif kind == "pptx":
        text, title = text_features.extract_pptx(rec.path)
    elif kind in ("odt", "odp", "ods"):
        text, title = text_features.extract_odf(rec.path)
    elif kind == "xlsx":
        text, title = text_features.extract_xlsx(rec.path)
    elif kind == "pdf":
        text, title = text_features.extract_pdf(rec.path)
    elif kind == "text":
        text = text_features.read_text_sample(rec.path)
        title = text_features.guess_title(text)
        if rec.extension in (".csv", ".tsv"):
            text = text_features.extract_csv_text(text)
    else:
        return
    extra = text_features.CODE_STOPWORDS if f.category == "code" else frozenset()
    tokens = text_features.tokenize(text, extra)[: config.TEXT_TOKEN_LIMIT]
    if title:
        f.text_title = title
        tokens = text_features.tokenize(title) * 3 + tokens      # titles are strong topic signals
    f.tokens = tokens
