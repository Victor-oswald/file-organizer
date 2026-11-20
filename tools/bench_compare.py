"""Reproducible before/after benchmarks. Results are written to docs/benchmarks/results.json.

    python tools/bench_compare.py clustering  [dataset]   # legacy vs current clustering, scaling with n
    python tools/bench_compare.py images      [dataset]   # legacy vs current image analysis, per image class
    python tools/bench_compare.py pipeline    [dataset]   # per-stage breakdown of the whole pipeline
    python tools/bench_compare.py scaling     d1 d2 ...   # end-to-end throughput on datasets of increasing size

Datasets come from `python tools/make_dataset.py <dir> [scale]`.
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

OUT = ROOT / "docs" / "benchmarks" / "results.json"


def load() -> dict:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def save(section: str, data) -> None:
    results = load()
    results[section] = data
    results["machine"] = {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}",
                          "cpu": next((l.split(":")[1].strip() for l in open("/proc/cpuinfo") if "model name" in l), platform.processor()),
                          "cores": __import__("os").cpu_count()}
    OUT.write_text(json.dumps(results, indent=1))


def clustering(dataset: str) -> None:
    from organizer.models import Features
    from organizer.modules import clustering as new, crawler, extractors, text_features as tf
    import legacy

    recs = [r for r in crawler.crawl(Path(dataset)) if r.extension in (".txt", ".md", ".csv", ".py")]
    docs = []
    for r in recs:
        f = Features(r); extractors.inspect(f); extractors.enrich(f)
        if f.tokens:
            docs.append(f.tokens)
    model = tf.TfidfModel().fit(docs)
    all_vecs = [model.vectorize(d) for d in docs]
    print(f"{len(all_vecs)} documents available")
    rows, legacy_alive = [], True
    LEGACY_CAP = 400       # beyond this the original code needs minutes to hours (n=600 was aborted after 300 s)
    for n in (100, 200, 300, 400, 600, 800, 1200, 1700):
        if n > len(all_vecs):
            break
        vecs = {i: all_vecs[i] for i in range(n)}
        t = time.perf_counter(); new_clusters = new.agglomerative(vecs, 0.30); t_new = time.perf_counter() - t
        t_old = None
        if legacy_alive:
            t = time.perf_counter(); old_clusters = legacy.legacy_agglomerative(vecs, 0.30); t_old = time.perf_counter() - t
            if n >= LEGACY_CAP:
                legacy_alive = False           # the next size would take minutes: stop measuring it
        rows.append({"n": n, "legacy_s": t_old, "current_s": t_new, "clusters_current": len(new_clusters)})
        print(f"n={n:5d}  legacy={'%.2fs' % t_old if t_old is not None else 'skipped':>9}  current={t_new:.3f}s", flush=True)
        save("clustering", rows)


def images(dataset: str) -> None:
    from organizer.models import Features, FileRecord
    from organizer.modules import image_features
    import legacy

    d = Path(dataset)
    classes = {"12 MP JPEG (4000×3000)": sorted(d.glob("IMG_*.jpg"))[:20], "2 MP JPEG (1600×1200)": sorted(d.glob("p*.jpg"))[:20],
               "1080p PNG screenshot": sorted(d.glob("tmp_*.png"))[:20]}
    rows = []
    for label, files in classes.items():
        def run(fn):
            ts = []
            for p in files:
                t = time.perf_counter(); fn(p); ts.append((time.perf_counter() - t) * 1000)
            return statistics.median(ts)
        def cur(p):
            st = p.stat(); f = Features(FileRecord(p, p.name, p.stem, p.suffix.lower(), st.st_size, st.st_mtime))
            image_features.analyze_image(f)
        run(legacy.legacy_analyze_pixels), run(cur)          # warm the file cache and the decoders
        old, new = run(legacy.legacy_analyze_pixels), run(cur)
        rows.append({"class": label, "files": len(files), "legacy_ms": old, "current_ms": new})
        print(f"{label:<26} legacy {old:7.1f} ms   current {new:6.1f} ms   {old / new:4.1f}x", flush=True)
    save("images", rows)


def pipeline(dataset: str) -> None:
    from collections import defaultdict
    from organizer.modules import clustering as cl, decision, extractors, manifest, pipeline as pl
    T, N = defaultdict(float), defaultdict(int)

    def timed(obj, name, key):
        fn = getattr(obj, name)
        def wrapper(*a, **k):
            t = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                lab = key(*a, **k) if callable(key) else key
                T[lab] += time.perf_counter() - t; N[lab] += 1
        setattr(obj, name, wrapper)

    timed(pl, "crawl", "Crawl folder")
    timed(extractors, "inspect", "Fingerprint + magic bytes")
    timed(cl, "find_exact_duplicates", "Duplicate detection")
    timed(extractors, "enrich", lambda f: "Images (EXIF, dHash)" if f.category == "image" else "Other media + text extraction")
    timed(pl.TfidfModel, "fit", "TF-IDF + clustering"); timed(pl.TfidfModel, "vectorize", "TF-IDF + clustering")
    timed(cl, "agglomerative", "TF-IDF + clustering"); timed(cl, "cluster_hashes", "Photo grouping")
    timed(decision, "decide", "Score + decide"); timed(manifest, "save_manifest", "Write manifest")
    t0 = time.perf_counter()
    plan = pl.Pipeline(dataset, manifest_path=Path("/tmp/bench_manifest.json"), workers=1).run()
    total = time.perf_counter() - t0
    stages = [{"stage": k, "seconds": v} for k, v in T.items()]
    other = total - sum(T.values())
    stages.append({"stage": "Other (grouping, I/O glue)", "seconds": max(0.0, other)})
    save("pipeline", {"files": len(plan.decisions), "total_s": total, "stages": stages})
    print(f"{len(plan.decisions)} files in {total:.2f}s")
    for s in stages: print(f"  {s['stage']:<34}{s['seconds']:7.2f}s")


def scaling(datasets: list[str]) -> None:
    from organizer.modules import pipeline as pl
    rows = []
    for ds in datasets:
        best = None
        for _ in range(2):          # best of two: the first run of a process pays one-off warm-up costs
            t = time.perf_counter(); plan = pl.Pipeline(ds, manifest_path=Path("/tmp/bench_manifest.json"), workers=1).run()
            best = min(best, time.perf_counter() - t) if best else time.perf_counter() - t
        dt = best
        rows.append({"files": len(plan.decisions), "seconds": dt, "files_per_s": len(plan.decisions) / dt})
        print(f"{len(plan.decisions):6d} files  {dt:6.2f}s  {len(plan.decisions) / dt:6.0f} files/s", flush=True)
    save("scaling", rows)


if __name__ == "__main__":
    {"clustering": lambda: clustering(sys.argv[2] if len(sys.argv) > 2 else "/tmp/bench_data"),
     "images": lambda: images(sys.argv[2] if len(sys.argv) > 2 else "/tmp/bench_data"),
     "pipeline": lambda: pipeline(sys.argv[2] if len(sys.argv) > 2 else "/tmp/bench_data"),
     "scaling": lambda: scaling(sys.argv[2:])}[sys.argv[1]]()
