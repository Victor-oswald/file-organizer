"""Stage 3: algorithmic grouping. Four independent tools:

1. find_exact_duplicates : size -> partial hash -> full SHA-256 (cheap to expensive)
2. agglomerative         : average-linkage clustering over TF-IDF cosine similarity
3. cluster_hashes        : dHash groups with Hamming distance <= 6 (pigeonhole-accelerated)
4. event_windows         : split a timeline wherever the gap exceeds 4 hours
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Sequence

from ..models import Features
from . import hasher
from .text_features import Vector, cosine

# ----------------------------------------------------------------------------
# 1. exact duplicates
# ----------------------------------------------------------------------------

def find_exact_duplicates(features: Sequence[Features]) -> list[list[Features]]:
    """Return groups of byte-identical files. In each group index 0 is the one to KEEP
    (oldest modification time, then shortest path, then alphabetical)."""
    buckets: dict[tuple[int, str], list[Features]] = defaultdict(list)
    for f in features:
        if f.error or f.record.size == 0:
            continue
        buckets[(f.record.size, f.partial_hash)].append(f)

    groups: list[list[Features]] = []
    for candidates in buckets.values():
        if len(candidates) < 2:
            continue
        by_full: dict[str, list[Features]] = defaultdict(list)
        for f in candidates:
            try:
                f.sha256 = f.sha256 or hasher.full_hash(f.record.path)
            except OSError as exc:
                f.error = f"{type(exc).__name__}: {exc}"
                continue
            by_full[f.sha256].append(f)
        for same in by_full.values():
            if len(same) > 1:
                same.sort(key=lambda x: (x.record.modified, len(str(x.record.path)), str(x.record.path)))
                groups.append(same)
    groups.sort(key=lambda g: str(g[0].record.path))
    return groups


# ----------------------------------------------------------------------------
# 2. agglomerative clustering (average linkage) on cosine similarity
# ----------------------------------------------------------------------------

def _candidate_pairs(vectors: dict[int, Vector], top_terms: int, max_posting: int,
                     neighbors: int | None) -> set[tuple[int, int]]:
    """Which document pairs are worth comparing at all?

    1. Index every document only under its `top_terms` heaviest terms (its topic words).
       Two documents sharing no topic word cannot be similar enough to cluster.
    2. Ignore terms shared by more than `max_posting` documents: too generic to be evidence.
    3. For each document keep only the `neighbors` candidates that share the MOST topic
       words with it (a k-nearest-neighbour graph). This bounds the work at n*k pairs even
       when every document looks a bit like every other one."""
    index: dict[str, list[int]] = defaultdict(list)
    tops: dict[int, list[str]] = {}
    for doc in sorted(vectors):
        tops[doc] = [t for t, _ in heapq.nlargest(top_terms, vectors[doc].items(), key=lambda kv: (kv[1], kv[0]))]
        for term in tops[doc]:
            index[term].append(doc)                          # ascending doc order
    pairs: set[tuple[int, int]] = set()
    for doc, terms in tops.items():
        shared: Counter[int] = Counter()
        for term in terms:
            posting = index[term]
            if 1 < len(posting) <= max_posting:
                shared.update(posting)                       # C-speed counting
        del shared[doc]
        chosen = shared.most_common(neighbors) if neighbors else list(shared.items())
        for other, _ in chosen:
            pairs.add((doc, other) if doc < other else (other, doc))
    return pairs


def agglomerative(vectors: dict[int, Vector], threshold: float, *, top_terms: int = 12,
                  max_posting: int = 80, neighbors: int | None = 10, prune: float = 0.5) -> list[list[int]]:
    """Bottom-up clustering with AVERAGE linkage, in O(P log P) for P candidate pairs.

    Start with one cluster per document and repeatedly merge the two clusters with the
    highest average pairwise similarity, until the best pair is below `threshold`.

    The speed comes from three things (the previous version rescanned every pair after
    every merge, which is cubic):
      * a heap yields the best pair in O(log P); stale entries are skipped lazily
      * merging stores SUMS of similarities, so a merged cluster's links are just a + b
      * pairs with similarity below `threshold * prune` are treated as 0 (sparse)
      * each document is only compared with its `neighbors` best candidates (see above)
    Pass top_terms/max_posting very large, neighbors=None and prune=0 to get the exact textbook result.

    Deterministic: ties break on cluster ids."""
    ids = sorted(vectors)
    adj: dict[int, dict[int, float]] = {d: {} for d in ids}
    cutoff = threshold * prune
    for a, b in _candidate_pairs(vectors, top_terms, max_posting, neighbors):
        sim = cosine(vectors[a], vectors[b])
        if sim >= cutoff:
            adj[a][b] = adj[b][a] = sim                      # value = SUM of member-pair similarities

    members: dict[int, list[int]] = {d: [d] for d in ids}
    heap = [(-sim, a, b) for a in ids for b, sim in adj[a].items() if a < b]
    heapq.heapify(heap)
    next_id = (ids[-1] + 1) if ids else 0

    while heap:
        neg_avg, a, b = heapq.heappop(heap)
        if a not in members or b not in members:             # stale entry: one side was merged away
            continue
        if -neg_avg < threshold:
            break
        new, next_id = next_id, next_id + 1
        links_a, links_b = adj.pop(a), adj.pop(b)
        links_a.pop(b, None)
        links_b.pop(a, None)
        combined: dict[int, float] = defaultdict(float)
        for links in (links_a, links_b):
            for other, total in links.items():
                combined[other] += total
        members[new] = members.pop(a) + members.pop(b)
        adj[new] = {}
        for other, total in combined.items():
            adj[other].pop(a, None)
            adj[other].pop(b, None)
            adj[other][new] = adj[new][other] = total
            avg = total / (len(members[new]) * len(members[other]))
            heapq.heappush(heap, (-avg, other, new))         # `new` is the largest id so far

    return sorted((sorted(m) for m in members.values()), key=lambda c: c[0])


def mean_similarity(cluster: list[int], vectors: dict[int, Vector], cap: int = 12) -> float:
    """Mean pairwise cosine inside a cluster, estimated from its first `cap` members
    (66 pairs at most). It is an audit number, so an estimate is plenty, and exact all-pairs
    cost O(k^2) cosines per cluster, which dominated the run time."""
    sample = cluster[:cap]
    if len(sample) < 2:
        return 0.0
    sims = [cosine(vectors[a], vectors[b]) for a, b in combinations(sample, 2)]
    return sum(sims) / len(sims)


# ----------------------------------------------------------------------------
# 3. perceptual-hash grouping (Hamming distance)
# ----------------------------------------------------------------------------

def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


_CHUNKS = [(0, 9), (9, 9), (18, 9), (27, 9), (36, 9), (45, 9), (54, 10)]    # 7 chunks, 64 bits


def cluster_hashes(hashes: dict[int, int], max_dist: int) -> list[list[int]]:
    """Group 64-bit hashes whose Hamming distance is <= max_dist (single linkage).

    Pigeonhole trick: split each hash into 7 chunks. Two hashes differing in at
    most 6 bits must have at least one IDENTICAL chunk (6 differing bits cannot
    touch 7 chunks). So only hashes that share a chunk need an exact comparison.
    (Valid while max_dist < len(_CHUNKS).)"""
    assert max_dist < len(_CHUNKS), "pigeonhole bucketing needs max_dist < number of chunks"
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for key, h in hashes.items():
        for i, (shift, width) in enumerate(_CHUNKS):
            buckets[(i, (h >> shift) & ((1 << width) - 1))].append(key)

    parent = {k: k for k in hashes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for keys in buckets.values():
        for a, b in combinations(sorted(keys), 2):
            if hamming(hashes[a], hashes[b]) <= max_dist:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

    groups: dict[int, list[int]] = defaultdict(list)
    for k in hashes:
        groups[find(k)].append(k)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def mean_distance(member: int, cluster: list[int], hashes: dict[int, int], cap: int = 50) -> float:
    others = [o for o in cluster if o != member][:cap]
    if not others:
        return 0.0
    return sum(hamming(hashes[member], hashes[o]) for o in others) / len(others)


# ----------------------------------------------------------------------------
# 4. temporal event windows
# ----------------------------------------------------------------------------

def event_windows(items: list[tuple[int, datetime]], gap: timedelta) -> list[list[tuple[int, datetime]]]:
    """Sort by time, start a new window whenever the silence exceeds `gap`."""
    ordered = sorted(items, key=lambda it: (it[1], it[0]))
    windows: list[list[tuple[int, datetime]]] = []
    for item in ordered:
        if windows and item[1] - windows[-1][-1][1] <= gap:
            windows[-1].append(item)
        else:
            windows.append([item])
    return windows
