"""Accuracy evaluation against independent ground truth (results -> docs/benchmarks/results.json).

    python tools/evaluate.py duplicates /tmp/bench_data
    python tools/evaluate.py text
    python tools/evaluate.py photos

All data is synthetic, so labels are exact; see docs/BENCHMARKS caveats in the README.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import tempfile
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "benchmarks" / "results.json"


def save(key, data):
    d = json.loads(OUT.read_text()) if OUT.exists() else {}
    d.setdefault("evaluation", {})[key] = data
    OUT.write_text(json.dumps(d, indent=1))


def pair_metrics(truth: dict, pred: dict):
    """Pairwise precision / recall / F1 of a clustering, plus Adjusted Rand Index."""
    items = sorted(truth)
    tp = fp = fn = 0
    for a, b in combinations(items, 2):
        same_t, same_p = truth[a] == truth[b], pred[a] == pred[b]
        tp += same_t and same_p
        fp += (not same_t) and same_p
        fn += same_t and not same_p
    prec, rec = tp / (tp + fp) if tp + fp else 1.0, tp / (tp + fn) if tp + fn else 1.0
    n = len(items)
    comb2 = lambda x: x * (x - 1) / 2
    cont = Counter((truth[i], pred[i]) for i in items)
    sum_cells = sum(comb2(v) for v in cont.values())
    sum_t = sum(comb2(v) for v in Counter(truth.values()).values())
    sum_p = sum(comb2(v) for v in Counter(pred.values()).values())
    expected = sum_t * sum_p / comb2(n)
    ari = (sum_cells - expected) / (0.5 * (sum_t + sum_p) - expected) if (0.5 * (sum_t + sum_p) - expected) else 1.0
    return {"precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0, "ari": ari}


# ---------------------------------------------------------------- duplicates
def duplicates(dataset):
    from organizer.modules import hasher
    from organizer.modules.pipeline import Pipeline
    files = [p for p in Path(dataset).rglob("*") if p.is_file() and p.stat().st_size and not p.name.startswith(".")
             and "_Organizer_Logs" not in p.parts]
    groups = defaultdict(list)
    for p in files:                                           # the oracle: hash every byte of every file
        groups[hashlib.sha256(p.read_bytes()).hexdigest()].append(p)
    oracle = set()
    for g in groups.values():
        if len(g) > 1:
            g.sort(key=lambda x: (x.stat().st_mtime, len(str(x)), str(x)))
            oracle.update(g[1:])
    calls = {"full": 0}
    real = hasher.full_hash
    hasher.full_hash = lambda p: (calls.__setitem__("full", calls["full"] + 1), real(p))[1]
    try:
        plan = Pipeline(dataset, manifest_path=Path(tempfile.gettempdir()) / "eval_m.json", workers=1).run()
    finally:
        hasher.full_hash = real
    found = {d.features.record.path for d in plan.decisions if d.action == "duplicate"}
    tp = len(found & oracle)
    res = {"files": len(files), "duplicate_files_in_truth": len(oracle), "detected": len(found), "true_positives": tp,
           "false_positives": len(found - oracle), "false_negatives": len(oracle - found),
           "full_hashes_computed": calls["full"], "full_hash_share": calls["full"] / len(files)}
    print(res)
    save("duplicates", res)


# ---------------------------------------------------------------- text clustering
def text_corpus(kind, seed=7):
    rnd = random.Random(seed)
    if kind == "hard":          # 168-word vocabulary: topics overlap heavily (the benchmark dataset's generator)
        vocab = [f"{a}{b}" for a in ("sol", "ter", "nov", "pra", "mel", "quo", "ver", "lun", "fer", "gal", "hid", "kor")
                 for b in ("ing", "ate", "ion", "ard", "ent", "ory", "ial", "ous", "ish", "ive", "ful", "ant", "ize", "ment")]
        size, per_doc, topic_p = 25, (300, 700), 0.55
    else:                       # 2,000-word vocabulary: closer to real documents
        syll = ["ka", "to", "mi", "ren", "sol", "va", "lu", "pe", "dor", "xi", "nu", "ba", "ger", "fi", "zo", "han"]
        vocab = sorted({"".join(rnd.choice(syll) for _ in range(rnd.randint(2, 3))) for _ in range(6000)})[:2000]
        size, per_doc, topic_p = 40, (150, 500), 0.40
    topics = [rnd.sample(vocab, size) for _ in range(30)]
    docs, labels = [], {}
    for t in range(30):
        for j in range(20):
            words = [rnd.choice(topics[t]) if rnd.random() < topic_p else rnd.choice(vocab) for _ in range(rnd.randint(*per_doc))]
            labels[len(docs)] = t
            docs.append(" ".join(words))
    return docs, labels


def _cluster_metrics(kind, seed, threshold):
    from organizer.modules import clustering, text_features as tf
    docs, truth = text_corpus(kind, seed)
    toks = [tf.tokenize(d) for d in docs]
    model = tf.TfidfModel().fit(toks)
    vecs = {i: model.vectorize(t) for i, t in enumerate(toks)}
    clusters = clustering.agglomerative(vecs, threshold)
    pred = {i: ci for ci, c in enumerate(clusters) for i in c}
    m = pair_metrics(truth, pred)
    m["clusters_found"] = len([c for c in clusters if len(c) > 1])
    return m


def text():
    """Calibrate on seed 7, then report on five HELD-OUT seeds so the number is not circular."""
    from organizer import config
    sweep_ths = (0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30)
    out = {"default_threshold": config.COSINE_CLUSTER_THRESHOLD, "calibration_seed": 7, "held_out_seeds": [11, 13, 17, 19, 23], "sweep": {}, "held_out": {}}
    for kind in ("hard", "moderate"):
        out["sweep"][kind] = [{"threshold": th, **_cluster_metrics(kind, 7, th)} for th in sweep_ths]
        for label, th in (("current_default", config.COSINE_CLUSTER_THRESHOLD), ("old_default", 0.30)):
            runs = [_cluster_metrics(kind, sd, th) for sd in out["held_out_seeds"]]
            out["held_out"].setdefault(kind, {})[label] = {
                "threshold": th, "ari_mean": sum(r["ari"] for r in runs) / len(runs), "ari_min": min(r["ari"] for r in runs), "ari_max": max(r["ari"] for r in runs),
                "precision_mean": sum(r["precision"] for r in runs) / len(runs), "recall_mean": sum(r["recall"] for r in runs) / len(runs),
                "clusters_found_mean": sum(r["clusters_found"] for r in runs) / len(runs)}
            h = out["held_out"][kind][label]
            print(f"{kind:9s} {label:15s} threshold {th:.2f}: ARI {h['ari_mean']:.2f} [{h['ari_min']:.2f}-{h['ari_max']:.2f}]  precision {h['precision_mean']:.2f}  recall {h['recall_mean']:.2f}  clusters {h['clusters_found_mean']:.0f} (truth 30)")
    save("text_clustering", out)


# ---------------------------------------------------------------- photo grouping
def photos():
    from PIL import Image, ImageEnhance
    from organizer.models import Features, FileRecord
    from organizer.modules import clustering, image_features
    from tests import helpers
    tmp = Path(tempfile.mkdtemp())
    truth, hashes, kinds = {}, {}, {}
    idx = 0

    def add(img, group, kind, **save_kw):
        nonlocal idx
        p = tmp / f"{idx}.jpg"
        img.save(p, "JPEG", **save_kw)
        st = p.stat()
        f = Features(FileRecord(p, p.name, p.stem, ".jpg", st.st_size, st.st_mtime))
        image_features.analyze_image(f)
        truth[idx], hashes[idx], kinds[idx] = group, f.dhash, kind
        idx += 1

    for g in range(60):                                      # 60 distinct "scenes"
        base = helpers.photo_like(g, size=(800, 600), base_seed=1000 + g)
        add(base, g, "original", quality=92)
        add(base, g, "re-encoded (JPEG q=30)", quality=30)
        add(base.resize((400, 300), Image.Resampling.LANCZOS), g, "downscaled 50 %", quality=85)
        add(ImageEnhance.Brightness(base).enhance(1.18), g, "brightness +18 %", quality=85)
    clusters = clustering.cluster_hashes(hashes, 6)
    pred = {i: ci for ci, c in enumerate(clusters) for i in c}
    m = pair_metrics(truth, pred)
    per_kind = {}
    for kind in ("re-encoded (JPEG q=30)", "downscaled 50 %", "brightness +18 %"):
        ok = sum(1 for i in truth if kinds[i] == kind and pred[i] == pred[next(j for j in truth if truth[j] == truth[i] and kinds[j] == "original")])
        per_kind[kind] = ok / 60
    m.update({"images": idx, "scenes": 60, "groups_found": len(clusters), "variant_recovered": per_kind})
    print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()})
    save("photo_grouping", m)


if __name__ == "__main__":
    {"duplicates": lambda: duplicates(sys.argv[2] if len(sys.argv) > 2 else "/tmp/bench_data"), "text": text, "photos": photos}[sys.argv[1]]()
