"""The ORIGINAL (pre-optimization) kernels, preserved ONLY so benchmarks can compare before/after.

Do not use these in the application. They are exactly the algorithms that profiling showed to be
the bottlenecks (see docs/PERFORMANCE.md):

  legacy_agglomerative   : rescans every candidate pair after every merge  -> O(n^3)
  legacy_analyze_pixels  : decodes at full size, converts/resizes the image three separate times
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations

from PIL import Image


def _cosine_old(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def _candidate_similarities(vectors):
    index = defaultdict(list)
    for doc, vec in vectors.items():
        for term in vec:
            index[term].append(doc)
    limit = max(2, len(vectors) // 2)
    pairs = set()
    for docs in index.values():
        if 1 < len(docs) <= limit:
            pairs.update(combinations(sorted(docs), 2))
    return {(a, b): _cosine_old(vectors[a], vectors[b]) for a, b in pairs}


def legacy_agglomerative(vectors, threshold):
    sims = _candidate_similarities(vectors)
    members = {d: [d] for d in vectors}
    link = {k: v for k, v in sims.items() if v > 0}
    next_id = max(vectors, default=-1) + 1
    while link:
        (a, b), total = max(
            link.items(),
            key=lambda kv: (kv[1] / (len(members[kv[0][0]]) * len(members[kv[0][1]])), -kv[0][0], -kv[0][1]),
        )
        if total / (len(members[a]) * len(members[b])) < threshold:
            break
        new = next_id
        next_id += 1
        members[new] = members.pop(a) + members.pop(b)
        merged = defaultdict(float)
        for (x, y), s in list(link.items()):
            if {x, y} & {a, b}:
                other = y if x in (a, b) else x
                if other not in (a, b):
                    merged[tuple(sorted((new, other)))] += s
                del link[(x, y)]
        link.update(merged)
    return sorted((sorted(m) for m in members.values()), key=lambda c: c[0])


def legacy_analyze_pixels(path):
    """dHash + colour statistics the old way: full decode, three separate full-size conversions."""
    with Image.open(path) as img:
        img.getexif()
        img.load()
        px = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        small = img.convert("L").resize((128, 128), Image.Resampling.BILINEAR)
        hist = small.histogram()
        total = sum(hist)
        entropy = -sum((c / total) * math.log2(c / total) for c in hist if c)
        colors = img.convert("RGB").resize((64, 64), Image.Resampling.NEAREST).getcolors(maxcolors=4096) or []
        return bits, entropy, len(colors) / 4096
