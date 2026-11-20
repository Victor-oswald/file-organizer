"""The orchestrator: runs the five stages of the flowchart in order.

    1 ingest + inspect   -> crawler, hasher, magic
    2 features           -> extractors (text / image / media / archive / binary)
    3 group + score      -> clustering, scoring
    4 decide             -> decision (rename | keep | move | quarantine | duplicate)
    5 preview + undo     -> manifest
"""

from __future__ import annotations

import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Collection

from .. import config
from ..models import Decision, Features
from . import clustering, decision, extractors, manifest
from .crawler import crawl
from .directory_manager import DirectoryManager, PathReserver
from .scoring import GroupInfo, Scorer
from .text_features import TfidfModel, Vector

_TEXTUAL = {"document", "code", "data", "archive"}


@dataclass
class Plan:
    dm: DirectoryManager
    decisions: list[Decision]
    duplicate_groups: list[list[Features]]
    stats: dict = field(default_factory=dict)
    manifest_path: Path | None = None


class Pipeline:
    def __init__(self, root: str | Path, *, dupes_mode: str = "flag",
                 threshold: float = config.CONFIDENCE_THRESHOLD,
                 manifest_path: Path | None = None,
                 only: Collection[Path] | None = None,
                 recursive: bool = True,
                 skip_projects: bool = True,
                 workers: int | None = None,
                 progress: Callable[[str, int, int, str], None] | None = None,
                 log: Callable[[str], None] = lambda _msg: None) -> None:
        if dupes_mode not in ("flag", "move"):
            raise ValueError("dupes_mode must be 'flag' or 'move'")
        self.dm = DirectoryManager(root)
        self.dupes_mode = dupes_mode
        self.threshold = threshold
        self.manifest_path = manifest_path
        self.only = set(only) if only is not None else None
        self.recursive = recursive
        self.skip_projects = skip_projects
        self.workers = workers if workers is not None else min(4, os.cpu_count() or 1)
        self.progress = progress
        self.log = log

    # ------------------------------------------------------------------
    def _report(self, stage: str, done: int, total: int, current: str = "") -> None:
        if self.progress and (done == total or done % 10 == 0 or done == 1):
            self.progress(stage, done, total, current)

    def _enrich_all(self, active: list[Features]) -> None:
        """Type-specific extraction. Files are independent, so on a multi-core machine they run
        in threads (Pillow's decoders release the GIL). Results are identical to the serial run."""
        total = len(active)
        if self.workers > 1 and total >= 16:
            with ThreadPoolExecutor(self.workers) as pool:
                for i, f in enumerate(pool.map(lambda x: (extractors.enrich(x), x)[1], active), 1):
                    self._report("Analyzing files", i, total, f.record.name)
        else:
            for i, f in enumerate(active, 1):
                extractors.enrich(f)
                self._report("Analyzing files", i, total, f.record.name)

    def run(self) -> Plan:
        dm = self.dm

        # Stage 1: ingest + cheap inspection (deterministic order!)
        self.log("Stage 1: crawling and inspecting files...")
        records = sorted(crawl(dm.root, recursive=self.recursive, skip_projects=self.skip_projects),
                         key=lambda r: str(r.path))
        if self.only is None:
            targets, context = records, []
        else:
            # Watch mode: decisions are made ONLY for `only`. Existing files of the same size
            # are inspected too, purely as context for duplicate detection.
            targets = [r for r in records if r.path in self.only]
            sizes = {r.size for r in targets if r.size > 0}
            context = [r for r in records if r.path not in self.only and r.size in sizes]
        target_paths = {r.path for r in targets}
        feats = [Features(record=r) for r in targets + context]
        feats.sort(key=lambda f: str(f.record.path))
        for i, f in enumerate(feats, 1):
            extractors.inspect(f)
            self._report("Reading files", i, len(feats), f.record.name)

        # Exact duplicates (cheap -> expensive: size, partial hash, full hash)
        dup_groups = clustering.find_exact_duplicates(feats)
        primary_of: dict[Path, Features] = {}
        for group in dup_groups:
            for extra in group[1:]:
                if extra.record.path in target_paths:          # never act on context files
                    primary_of[extra.record.path] = group[0]
        active = [f for f in feats if f.record.path in target_paths and f.record.path not in primary_of]
        self.log(f"  {len(targets)} files, {len(primary_of)} duplicates")

        # Stage 2: type-specific features (only for non-duplicates)
        self.log("Stage 2: extracting features...")
        self._enrich_all(active)

        # Stage 3: representation + grouping
        self._report("Grouping similar files", 0, 1)
        self.log("Stage 3: building TF-IDF, clustering, grouping...")
        vectors, groups = self._text_groups(active)
        photo_folder = self._photo_groups(active, groups)
        event_folder = self._events(active)

        # Stage 3b/4: score + decide
        self.log("Stage 4: scoring and deciding...")
        reserver = PathReserver([r.path for r in records])
        scorer = Scorer()
        decisions: list[Decision] = []
        for i, f in enumerate(active):
            self._report("Deciding", i + 1, len(active), f.record.name)
            proposal = scorer.score(f, vectors.get(i, {}), groups.get(i, GroupInfo()))
            folder = event_folder.get(i)
            if folder is None and proposal.confidence >= self.threshold:
                folder = photo_folder.get(i)
            d = decision.decide(f, proposal, folder=folder, reserver=reserver, dm=dm, threshold=self.threshold)
            if i in event_folder:
                d.signals["event_folder"] = dm.relative(event_folder[i])
            decisions.append(d)
        by_path = {f.record.path: f for f in feats}
        for extra_path, primary in primary_of.items():
            f = by_path[extra_path]
            decisions.append(decision.decide_duplicate(f, primary, mode=self.dupes_mode, reserver=reserver, dm=dm))
        decisions.sort(key=lambda d: str(d.features.record.path))

        # Stage 5: manifest
        plan = Plan(dm, decisions, dup_groups)
        plan.stats = self._stats(decisions)
        data = manifest.build_manifest(dm, decisions, threshold=self.threshold, dupes_mode=self.dupes_mode)
        path = self.manifest_path or dm.logs_dir / f"manifest_{datetime.now():%Y%m%d_%H%M%S}.json"
        manifest.save_manifest(data, path)
        plan.manifest_path = path
        self.log(f"Stage 5: manifest written to {path}")
        return plan

    # ------------------------------------------------------------------
    def _text_groups(self, active: list[Features]) -> tuple[dict[int, Vector], dict[int, GroupInfo]]:
        idx = [i for i, f in enumerate(active) if f.category in _TEXTUAL and f.tokens]
        model = TfidfModel().fit(active[i].tokens for i in idx)
        vectors = {i: model.vectorize(active[i].tokens) for i in idx}
        groups: dict[int, GroupInfo] = {}
        for cluster in clustering.agglomerative(vectors, config.COSINE_CLUSTER_THRESHOLD):
            sim = clustering.mean_similarity(cluster, vectors)
            for i in cluster:
                groups[i] = GroupInfo(text_cluster_size=len(cluster), text_cluster_sim=sim)
        return vectors, groups

    def _photo_groups(self, active: list[Features], groups: dict[int, GroupInfo]) -> dict[int, Path]:
        hashes = {i: f.dhash for i, f in enumerate(active)
                  if f.category == "image" and f.dhash is not None and not f.is_camera and not f.is_screenshot}
        folders: dict[int, Path] = {}
        n = 0
        for cluster in clustering.cluster_hashes(hashes, config.HAMMING_MAX):
            for i in cluster:
                groups.setdefault(i, GroupInfo()).photo_cluster_size = len(cluster)
                groups[i].photo_mean_distance = clustering.mean_distance(i, cluster, hashes)
            if len(cluster) > 1:
                n += 1
                for i in cluster:
                    folders[i] = self.dm.groups_dir / f"Group_{n:03d}"
        return folders

    def _events(self, active: list[Features]) -> dict[int, Path]:
        items = [(i, f.taken) for i, f in enumerate(active)
                 if f.taken is not None and f.category in ("image", "video") and (f.is_camera or f.category == "video")]
        folders: dict[int, Path] = {}
        for window in clustering.event_windows(items, config.EVENT_GAP):
            if len(window) >= config.EVENT_MIN_FILES:
                start = window[0][1]
                for i, _ in window:
                    folders[i] = self.dm.events_dir / f"{start:%Y-%m-%d_%H%M}"
        return folders

    @staticmethod
    def _stats(decisions: list[Decision]) -> dict:
        by_action = Counter(d.action for d in decisions)
        by_category = Counter(d.features.category for d in decisions)
        return {"total": len(decisions), "by_action": dict(by_action), "by_category": dict(by_category)}
