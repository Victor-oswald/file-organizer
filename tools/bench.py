"""Time every pipeline stage on a dataset:  python tools/bench.py /tmp/bench_data"""
from __future__ import annotations

import cProfile
import pstats
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from organizer.modules import clustering, decision, extractors, manifest, pipeline  # noqa: E402

T: dict[str, float] = defaultdict(float)
N: dict[str, int] = defaultdict(int)


def timed(obj, name, label=None, key=None):
    fn = getattr(obj, name)

    def wrapper(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            lab = key(*a, **k) if key else (label or name)
            T[lab] += time.perf_counter() - t
            N[lab] += 1
    setattr(obj, name, wrapper)


def main(path: str, profile: bool) -> None:
    timed(pipeline, "crawl", "1 crawl (whole folder)")
    timed(extractors, "inspect", "2 inspect (partial hash + magic)")
    timed(clustering, "find_exact_duplicates", "3 duplicates (full hash)")
    timed(extractors, "enrich", key=lambda f: f"4 enrich: {f.category}")
    timed(clustering, "agglomerative", "5 agglomerative (text clustering)")
    timed(clustering, "cluster_hashes", "5 hamming grouping")
    timed(pipeline.TfidfModel, "fit", "5 tfidf fit")
    timed(pipeline.TfidfModel, "vectorize", "5 tfidf vectorize")
    timed(decision, "decide", "6 score+decide (per file)")
    timed(manifest, "save_manifest", "7 manifest write")
    p = pipeline.Pipeline(path, manifest_path=Path("/tmp/bench_manifest.json"))
    t0 = time.perf_counter()
    if profile:
        pr = cProfile.Profile(); plan = pr.runcall(p.run)
    else:
        plan = p.run()
    total = time.perf_counter() - t0
    print(f"\n{len(plan.decisions)} files in {total:.2f}s  ({len(plan.decisions) / total:.0f} files/s)\n")
    for k in sorted(T):
        print(f"{k:<42}{T[k]:8.2f}s  {100 * T[k] / total:5.1f}%   x{N[k]}")
    if profile:
        print()
        pstats.Stats(pr).sort_stats("tottime").print_stats(18)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        sys.exit("usage: python tools/bench.py <folder> [--profile]")
    main(sys.argv[1], "--profile" in sys.argv)
