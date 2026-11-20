"""Render every README graphic from docs/benchmarks/results.json (never from hand-typed numbers).

    python tools/make_charts.py            # writes docs/assets/*.png
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RES = json.loads((ROOT / "docs" / "benchmarks" / "results.json").read_text())
OUT = ROOT / "docs" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

BLUE, BLUE_L, RED, RED_L = "#0969da", "#ddf4ff", "#cf222e", "#ffebe9"
GRAY, GRAY_L, GREEN, ORANGE, INK = "#6e7781", "#eaeef2", "#1a7f37", "#bc4c00", "#1f2328"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": "#d0d7de", "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": GRAY, "ytick.color": GRAY, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.axisbelow": True, "grid.color": "#eaeef2", "grid.linewidth": 0.8, "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white", "legend.frameon": False,
})


def title(fig, text, sub=None):
    fig.text(0.02, 0.965, text, fontsize=14, fontweight="bold", va="top", ha="left")
    if sub:
        fig.text(0.02, 0.905, sub, fontsize=9.5, color=GRAY, va="top", ha="left")


def save(fig, name):
    if name.startswith(("bench-", "evaluation")):             # hardware footnote on every measured chart
        fig.text(0.02, 0.012, "Measured on: " + machine_note(), fontsize=7.8, color=GRAY, va="bottom")
    fig.savefig(OUT / name, dpi=150)
    plt.close(fig)
    print("wrote", name)


def machine_note():
    m = RES["machine"]
    return f"{m['cpu']}, {m['cores']} core{'s' if m['cores'] != 1 else ''}, Python {m['python']}, {m['os'].split('-')[0]}"


# ------------------------------------------------------------------ clustering
def clustering():
    rows = RES["clustering"]
    old = [(r["n"], r["legacy_s"]) for r in rows if r["legacy_s"] is not None]
    new = [(r["n"], r["current_s"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4.9))
    fig.subplots_adjust(top=0.80, bottom=0.17, left=0.09, right=0.97)
    ax.plot(*zip(*old), "o-", color=RED, lw=2.2, ms=6, label="Original: rescan all pairs after every merge")
    ax.plot(*zip(*new), "o-", color=BLUE, lw=2.2, ms=6, label="Current: heap + sparse k-NN candidate pairs")
    ax.set_yscale("log")
    ax.set_xlim(0, 1850)
    ax.set_ylim(0.01, 1500)
    ax.plot([600], [300], marker="X", color=RED, ms=11, ls="none")
    ax.annotate("n = 600: did not finish,\naborted after 300 s", (600, 300), (760, 120), color=RED, fontsize=9,
                arrowprops=dict(arrowstyle="-", color=RED, lw=0.9), va="center")
    n4 = next(r for r in rows if r["n"] == 400)
    ax.annotate(f"{n4['legacy_s'] / n4['current_s']:.0f}× faster at n = 400\n({n4['legacy_s']:.1f} s → {n4['current_s']:.2f} s)", (400, n4["current_s"]), (520, 0.035),
                color=BLUE, fontsize=9, arrowprops=dict(arrowstyle="-", color=BLUE, lw=0.9))
    last = rows[-1]
    ax.annotate(f"{last['current_s']:.2f} s at n = {last['n']:,}", (last["n"], last["current_s"]), (last["n"] - 520, last["current_s"] * 4.5), color=BLUE, fontsize=9,
                arrowprops=dict(arrowstyle="-", color=BLUE, lw=0.9))
    ax.set_xlabel("Documents to cluster")
    ax.set_ylabel("Time, seconds (log scale)")
    ax.legend(loc="upper right", fontsize=9)
    title(fig, "Text clustering: from cubic to near-linear", "Same documents and threshold. With approximations off, the fast version equals textbook average linkage (property test).")
    save(fig, "bench-clustering.png")


# ------------------------------------------------------------------ images
def images():
    rows = RES["images"]
    fig, ax = plt.subplots(figsize=(9, 3.9))
    fig.subplots_adjust(top=0.74, bottom=0.21, left=0.22, right=0.95)
    y = np.arange(len(rows))[::-1]
    h = 0.34
    ax.barh(y + h / 2, [r["legacy_ms"] for r in rows], h, color=RED, label="Original: full decode, 3 conversions")
    ax.barh(y - h / 2, [r["current_ms"] for r in rows], h, color=BLUE, label="Current: JPEG draft decode, 1 thumbnail")
    for yi, r in zip(y, rows):
        ax.text(r["legacy_ms"] + 2, yi + h / 2, f"{r['legacy_ms']:.0f} ms", va="center", fontsize=9, color=RED)
        ax.text(r["current_ms"] + 2, yi - h / 2, f"{r['current_ms']:.1f} ms  ({r['legacy_ms'] / r['current_ms']:.1f}× faster)", va="center", fontsize=9, color=BLUE, fontweight="bold")
    ax.set_yticks(y)
    ax.set_yticklabels([r["class"] for r in rows])
    ax.set_xlabel("Median time per image (ms): EXIF + dHash + screenshot statistics")
    ax.set_xlim(0, max(r["legacy_ms"] for r in rows) * 1.25)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right", fontsize=9)
    title(fig, "Image analysis: decode a thumbnail, not the photo", f"Median of {rows[0]['files']} files per class; EXIF + dHash + screenshot statistics.")
    save(fig, "bench-images.png")


# ------------------------------------------------------------------ pipeline stages
def pipeline():
    d = RES["pipeline"]
    st = sorted(d["stages"], key=lambda s: s["seconds"])
    fig, ax = plt.subplots(figsize=(9, 4.6))
    fig.subplots_adjust(top=0.80, bottom=0.17, left=0.30, right=0.95)
    names = [s["stage"] for s in st]
    vals = [s["seconds"] for s in st]
    colors = [BLUE if v == max(vals) else "#8ab4f8" for v in vals]
    ax.barh(names, vals, color=colors)
    for i, v in enumerate(vals):
        ax.text(v + max(vals) * 0.01, i, f"{v:.2f} s  ({100 * v / d['total_s']:.0f}%)", va="center", fontsize=9)
    ax.set_xlim(0, max(vals) * 1.25)
    ax.set_xlabel("Seconds (single thread)")
    ax.grid(axis="y", visible=False)
    title(fig, f"Where the {d['total_s']:.1f} s go: {d['files']:,} mixed files, end to end", "Crawl → fingerprint → features → cluster → score → manifest; one thread.")
    save(fig, "bench-stages.png")


# ------------------------------------------------------------------ scaling
def scaling():
    rows = RES["scaling"]
    x = np.array([r["files"] for r in rows], float)
    y = np.array([r["seconds"] for r in rows], float)
    slope, icpt = np.polyfit(x, y, 1)
    r2 = 1 - ((y - (slope * x + icpt)) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    fig, ax = plt.subplots(figsize=(9, 4.4))
    fig.subplots_adjust(top=0.80, bottom=0.19, left=0.09, right=0.97)
    xs = np.linspace(0, x.max() * 1.05, 50)
    ax.plot(xs, slope * xs + icpt, "--", color=GRAY, lw=1.2, label=f"linear fit: {1 / slope:.0f} files/s, R² = {r2:.4f}")
    ax.plot(x, y, "o", color=BLUE, ms=9, label="measured (best of 2 runs)")
    for xi, yi, r in zip(x, y, rows):
        ax.annotate(f"{r['files']:,} files\n{r['seconds']:.2f} s", (xi, yi), (xi - 80, yi + 0.35), fontsize=8.5, ha="right", color=INK)
    ax.set_xlabel("Files in the folder")
    ax.set_ylabel("Full analysis time (s)")
    ax.set_xlim(0, x.max() * 1.1)
    ax.set_ylim(0, y.max() * 1.25)
    ax.legend(loc="upper left", fontsize=9)
    title(fig, "Throughput stays constant as folders grow", "Four dataset sizes with the same mix of file types.")
    save(fig, "bench-scaling.png")


# ------------------------------------------------------------------ latency
def latency():
    d = RES["latency"]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    fig.subplots_adjust(top=0.72, bottom=0.24, left=0.27, right=0.96)
    rnd = np.random.default_rng(3)
    for yi, key, color, label in ((1, "previous", RED, "Previous defaults\n(30 s interval, 5 s settle)"), (0, "current", BLUE, "Current defaults\n(5 s interval, fast re-check)")):
        s = d[key]["samples"]
        ax.scatter(s, yi + rnd.uniform(-0.12, 0.12, len(s)), s=70, color=color, alpha=0.85, zorder=3, edgecolor="white")
        med = statistics.median(s)
        ax.plot([med, med], [yi - 0.28, yi + 0.28], color=color, lw=3, zorder=4)
        ax.text(med, yi + 0.36, f"median {med:.1f} s", ha="center", fontsize=9.5, color=color, fontweight="bold")
        ax.text(-0.025, yi, label, ha="right", va="center", fontsize=9.5, transform=ax.get_yaxis_transform())
    ax.set_yticks([])
    ax.set_ylim(-0.6, 1.75)
    ax.set_xlim(0, max(max(d["previous"]["samples"]), 10) * 1.12)
    ax.set_xlabel("Seconds from 'file finished downloading' to 'change proposed on screen'")
    ax.grid(axis="y", visible=False)
    title(fig, "Reaction time: new file → proposal", f"Real clock, real watcher thread; each dot is one trial at a random point of the check cycle (n = {len(d['previous']['samples'])} and {len(d['current']['samples'])}).")
    save(fig, "bench-latency.png")


# ------------------------------------------------------------------ accuracy evaluation
def evaluation():
    ev = RES["evaluation"]
    tc = ev["text_clustering"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.9), gridspec_kw={"width_ratios": [1.05, 1]})
    fig.subplots_adjust(top=0.78, bottom=0.17, left=0.06, right=0.97, wspace=0.55)
    for kind, color, label in (("hard", BLUE, "hard corpus"), ("moderate", GREEN, "moderate corpus")):
        pts = tc["sweep"][kind]
        a1.plot([p["threshold"] for p in pts], [p["ari"] for p in pts], "o-", color=color, lw=2.2, ms=6, label=label)
    a1.axvline(0.30, color=RED, ls="--", lw=1.3)
    a1.text(0.293, 1.075, "old default 0.30", color=RED, va="center", ha="right", fontsize=9, fontweight="bold")
    a1.axvline(tc["default_threshold"], color=GRAY, ls="--", lw=1.3)
    a1.text(tc["default_threshold"] + 0.006, 1.075, f"new default {tc['default_threshold']:.2f}", color=GRAY, va="center", ha="left", fontsize=9, fontweight="bold")
    a1.set_xlabel("Clustering threshold (average cosine similarity)")
    a1.set_ylabel("Adjusted Rand Index (1.0 = perfect topics)")
    a1.set_ylim(-0.03, 1.12)
    a1.legend(loc="lower left", fontsize=9)
    a1.set_title("Text clustering: the threshold, calibrated", fontsize=10.5, loc="left", pad=8)
    d, ph = ev["duplicates"], ev["photo_grouping"]
    ho = tc["held_out"]
    bars = [(f"Exact duplicates\n{d['true_positives']}/{d['duplicate_files_in_truth']} found, {d['false_positives']} false", 1.0 if d["false_positives"] + d["false_negatives"] == 0 else d["true_positives"] / d["duplicate_files_in_truth"], BLUE),
            (f"Photo groups (re-encode, resize,\nbrightness): F1 on {ph['images']} images", ph["f1"], BLUE),
            ("Text topics, hard corpus\n(ARI, held-out seeds)", ho["hard"]["current_default"]["ari_mean"], BLUE),
            ("Text topics, moderate corpus\n(ARI, held-out seeds)", ho["moderate"]["current_default"]["ari_mean"], BLUE),
            ("Hard corpus, old default 0.30", ho["hard"]["old_default"]["ari_mean"], RED),
            ("Moderate corpus, old default 0.30", ho["moderate"]["old_default"]["ari_mean"], RED)]
    y = np.arange(len(bars))[::-1]
    a2.barh(y, [b[1] for b in bars], color=[b[2] for b in bars], height=0.62)
    for yi, b in zip(y, bars):
        a2.text(b[1] + 0.015, yi, f"{b[1]:.2f}", va="center", fontsize=9.5, fontweight="bold", color=b[2])
    a2.set_yticks(y)
    a2.set_yticklabels([b[0] for b in bars], fontsize=8.6)
    a2.set_xlim(0, 1.12)
    a2.grid(axis="y", visible=False)
    a2.set_xlabel("Score (1.0 = perfect)")
    a2.set_title("Accuracy against independent ground truth", fontsize=10.5, loc="left", pad=8)
    title(fig, "Accuracy evaluation (synthetic data with exact labels)", "Duplicates checked against a separate full SHA-256 of every byte; thresholds calibrated on one seed, scored on five unseen seeds.")
    save(fig, "evaluation.png")


# ------------------------------------------------------------------ hero summary
def summary():
    cl = {r["n"]: r for r in RES["clustering"]}
    im = next(r for r in RES["images"] if r["class"].startswith("12 MP"))
    lat = {k: statistics.median(RES["latency"][k]["samples"]) for k in ("previous", "current")}
    total = RES["pipeline"]
    cards = [
        (f"{total['files']:,} files, full analysis", ">300 s", "did not finish", f"{total['total_s']:.1f} s"),
        ("Cluster 400 documents", f"{cl[400]['legacy_s']:.1f} s", "", f"{cl[400]['current_s']:.2f} s"),
        ("Analyze a 12 MP photo", f"{im['legacy_ms']:.0f} ms", "", f"{im['current_ms']:.1f} ms"),
        ("New file → proposal (median)", f"{lat['previous']:.0f} s", "", f"{lat['current']:.1f} s"),
    ]
    fig = plt.figure(figsize=(12, 2.9))
    for i, (label, before, note, after) in enumerate(cards):
        ax = fig.add_axes([0.01 + i * 0.25, 0.04, 0.23, 0.92])
        ax.axis("off")
        ax.add_patch(FancyBboxPatch((0.0, 0.0), 1, 1, boxstyle="round,pad=0,rounding_size=0.04", fc="#f6f8fa", ec="#d0d7de", lw=1, transform=ax.transAxes))
        ax.text(0.06, 0.86, label, fontsize=10.5, color=GRAY, va="center")
        ax.text(0.06, 0.60, before, fontsize=19, color=RED, fontweight="bold", va="center")
        if note:
            ax.text(0.06, 0.43, note, fontsize=8.5, color=RED, va="center")
        ax.text(0.06, 0.27 if note else 0.38, "↓", fontsize=14, color=GRAY, va="center")
        ax.text(0.06, 0.10 if note else 0.17, after, fontsize=24, color=GREEN, fontweight="bold", va="center")
    save(fig, "summary.png")


# ------------------------------------------------------------------ architecture diagram
def box(ax, x, y, w, h, head, body, fc="#ffffff", ec="#d0d7de", head_color=INK):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012", fc=fc, ec=ec, lw=1.3))
    ax.text(x + w / 2, y + h - 0.045, head, ha="center", va="top", fontsize=10.5, fontweight="bold", color=head_color)
    ax.text(x + w / 2, y + h - 0.115, body, ha="center", va="top", fontsize=8, color=GRAY, linespacing=1.45)


def arrow(ax, x1, y1, x2, y2, color=GRAY):
    ax.annotate("", (x2, y2), (x1, y1), arrowprops=dict(arrowstyle="-|>", color=color, lw=1.6, shrinkA=0, shrinkB=0))


def architecture():
    fig = plt.figure(figsize=(12, 6.3))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.text(0.02, 0.965, "Architecture", fontsize=14, fontweight="bold", va="top")
    fig.text(0.02, 0.918, "A deterministic engine, wrapped by a background service. No model files, no network.", fontsize=9.5, color=GRAY, va="top")

    ax.text(0.02, 0.855, "ENGINE: one folder in, one reviewable manifest out", fontsize=9, color=BLUE, fontweight="bold")
    stages = [
        ("1  Crawl", "os.scandir walk\nskips hidden, links,\nprojects, own folders"),
        ("2  Inspect", "partial hash (64 KB\nhead + tail), magic\nbytes → real file type"),
        ("3  Features", "TF-IDF + bigrams · EXIF\n+ dHash · MP4 atoms · ID3\nzip listing · PE strings"),
        ("4  Group + score", "exact dupes · k-NN\nclustering · Hamming\nevents · confidence 0–1"),
        ("5  Decide", "≥ 0.60 rename / keep\n< 0.60 quarantine,\nname untouched"),
        ("6  Manifest", "old → new + reason\napply · undo ·\nnever overwrite"),
    ]
    w, gap, x0, y0, h = 0.138, 0.0245, 0.02, 0.57, 0.24
    for i, (head, body) in enumerate(stages):
        x = x0 + i * (w + gap)
        box(ax, x, y0, w, h, head, body, fc="#ffffff", ec=BLUE if i in (3, 4) else "#d0d7de")
        if i:
            arrow(ax, x - gap + 0.003, y0 + h / 2, x - 0.003, y0 + h / 2)

    ax.text(0.02, 0.485, "BACKGROUND SERVICE: watches folders, explains itself", fontsize=9, color=GREEN, fontweight="bold")
    svc = [
        ("Snapshot", "{path: (size, mtime)}\nevery 5 s: 2,400 files\nin ~6 ms"),
        ("Settle rule", "unchanged across two\nchecks and ≥ 3 s old,\nno .part / .crdownload"),
        ("Batch", "engine runs on the new\nfiles only (duplicates\nchecked vs same-size)"),
        ("Ask or Auto", "Ask: wait for approval\nAuto: apply at once\nboth are undoable"),
        ("Event log", "every file explained:\nnew · proposed · renamed\nleft alone, and why"),
        ("Dashboard · tray", "live status bar, activity\ntable, approve / undo,\ntray icon + fallback"),
    ]
    for i, (head, body) in enumerate(svc):
        x = x0 + i * (w + gap)
        box(ax, x, 0.215, w, 0.215, head, body, fc="#ffffff", ec=GREEN if i == 4 else "#d0d7de")
        if i:
            arrow(ax, x - gap + 0.003, 0.32, x - 0.003, 0.32)
    arrow(ax, x0 + 2 * (w + gap) + w / 2, 0.43, x0 + 2 * (w + gap) + w / 2, 0.57 - 0.0, color=BLUE)
    ax.text(x0 + 2 * (w + gap) + w / 2 + 0.008, 0.50, "runs stages 1–6 on the new files only", fontsize=8, color=BLUE, va="center")
    ax.add_patch(Rectangle((0.02, 0.04), 0.96, 0.095, fc=GRAY_L, ec="none"))
    ax.text(0.5, 0.087, "Storage: settings + history + 'seen' lists in the OS app-data folder (atomic JSON writes)  ·  manifests live beside the files in _Organizer_Logs/",
            ha="center", va="center", fontsize=8.8, color=INK)
    save(fig, "architecture.png")


# ------------------------------------------------------------------ confidence
def confidence():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8), gridspec_kw={"width_ratios": [1, 1.1]})
    fig.subplots_adjust(top=0.80, bottom=0.14, left=0.06, right=0.98, wspace=0.62)
    t = np.linspace(1, 90, 300)
    cov = np.minimum(1, t / 60)
    for strength, ls, lab in ((1.0, "-", "dominant topic terms"), (0.5, "--", "moderate"), (0.1, ":", "diffuse text")):
        a1.plot(t, 0.50 + 0.46 * cov * (0.6 + 0.4 * strength), ls, color=BLUE, lw=2.2, label=lab)
    a1.axhline(0.60, color=RED, lw=1.4)
    a1.axhspan(0, 0.60, color=RED_L, zorder=0)
    a1.text(45, 0.525, "below 0.60: file is set aside, name untouched", ha="center", fontsize=9, color=RED)
    a1.set_xlim(0, 90)
    a1.set_ylim(0.45, 1.0)
    a1.set_xlabel("Meaningful words extracted from the file")
    a1.set_ylabel("Confidence")
    a1.set_title("Text files: evidence quantity × term dominance", fontsize=10.5, loc="left", pad=8)
    a1.legend(loc="upper left", fontsize=9)
    rows = [("Exact duplicate (SHA-256)", 1.00, 1.00), ("Camera photo (EXIF)", 0.92, 0.98), ("Video (MP4 creation date)", 0.92, 0.98), ("Audio: artist + title", 0.96, 0.96),
            ("Executable (PE strings)", 0.90, 0.94), ("Screenshot (flat colours)", 0.85, 0.92), ("Voice memo (mono, low rate)", 0.85, 0.85), ("Audio: title only", 0.80, 0.80),
            ("Photo in a dHash group", 0.70, 0.85), ("Text / code / CSV / archive", 0.50, 0.96), ("Unlabeled lone photo", 0.45, 0.45)]
    rows.sort(key=lambda r: r[2])
    for i, (name, lo, hi) in enumerate(rows):
        c = RED if hi < 0.60 else BLUE
        a2.plot([lo, hi], [i, i], color=c, lw=7, solid_capstyle="round", alpha=0.85)
        if lo == hi:
            a2.plot([lo], [i], "o", color=c, ms=8)
    a2.set_yticks(range(len(rows)))
    a2.set_yticklabels([r[0] for r in rows], fontsize=8.6)
    a2.axvline(0.60, color=RED, lw=1.4)
    a2.set_xlim(0.4, 1.02)
    a2.grid(axis="y", visible=False)
    a2.set_xlabel("Confidence range")
    a2.set_title("Every signal has a published formula", fontsize=10.5, loc="left", pad=8)
    title(fig, "Explainable confidence: no learned weights", "Threshold 0.60 (configurable: Cautious 0.80 · Balanced 0.60 · Eager 0.45). Formulas live in organizer/modules/scoring.py.")
    save(fig, "confidence.png")


# ------------------------------------------------------------------ dashboard schematic
def dashboard():
    fig = plt.figure(figsize=(12, 6.0))
    ax = fig.add_axes([0, 0, 1, 0.88])
    ax.axis("off")
    ax.set_xlim(0, 120)
    ax.set_ylim(-9, 49)
    fig.text(0.02, 0.972, "Dashboard layout", fontsize=14, fontweight="bold", va="top")
    fig.text(0.02, 0.925, "Schematic drawn from the real UI structure; not a screenshot.", fontsize=9.5, color=GRAY, va="top")
    X, Y, W, H = 6, 4, 104, 41
    top = Y + H
    ax.add_patch(Rectangle((X, Y), W, H, fc="white", ec="#8c959f", lw=1.4))
    ax.add_patch(Rectangle((X, top - 4.5), W, 4.5, fc="#f6f8fa", ec="#d0d7de"))
    ax.text(X + 1.5, top - 2.25, "File Organizer", fontsize=9, fontweight="bold", va="center")
    for bx, bw, label in ((18, 9, "Add folder"), (28, 9, "Check now")):
        ax.add_patch(Rectangle((X + bx, top - 3.7), bw, 2.9, fc="white", ec="#d0d7de"))
        ax.text(X + bx + bw / 2, top - 2.25, label, fontsize=7.5, ha="center", va="center")
    ax.add_patch(Rectangle((X + 41, top - 3.7), 15, 2.9, fc="white", ec="#d0d7de"))
    ax.add_patch(plt.Circle((X + 43, top - 2.25), 0.6, color=GREEN))
    ax.text(X + 44.4, top - 2.25, "Monitoring: on", fontsize=7.5, va="center")
    ax.text(X + W - 2, top - 2.25, "Settings", fontsize=7.5, ha="right", va="center")
    ax.add_patch(Rectangle((X, Y + 3), 22, H - 7.5, fc="#f6f8fa", ec="#d0d7de"))
    ax.text(X + 1.5, top - 7, "FOLDERS", fontsize=7, color=GRAY, fontweight="bold")
    for i, (name, dot, badge) in enumerate((("All folders", None, "3"), ("Downloads", GREEN, "3"), ("Pictures", GREEN, ""), ("Projects archive", GRAY, ""))):
        yy = top - 11 - i * 3.4
        if i == 1:
            ax.add_patch(Rectangle((X, yy - 1.5), 22, 3.1, fc="#dde8f7", ec="none"))
        if dot:
            ax.add_patch(plt.Circle((X + 2, yy), 0.6, color=dot))
        ax.text(X + 4, yy, name, fontsize=8, va="center")
        if badge:
            ax.text(X + 20, yy, badge, fontsize=7, ha="right", va="center", color="white", bbox=dict(boxstyle="round,pad=0.25", fc=BLUE, ec="none"))
    ax.add_patch(Rectangle((X + 22, top - 9), W - 22, 4.5, fc="#f6f8fa", ec="#d0d7de"))
    tx = X + 23.5
    for i, t in enumerate(["Activity", "Awaiting approval (3)", "History", "Folder settings"]):
        ax.text(tx, top - 6.75, t, fontsize=8, va="center", fontweight="bold" if i == 0 else "normal", color=INK if i == 0 else GRAY)
        if i == 0:
            ax.plot([tx - 0.5, tx + 6.2], [top - 9, top - 9], color=BLUE, lw=2.2)
        tx += len(t) * 0.78 + 4
    cx = [X + 24, X + 33, X + 46, X + 80]
    for c, label in zip(cx, ["Time", "Event", "File", "Detail"]):
        ax.text(c, top - 11.4, label, fontsize=7.5, color=GRAY, fontweight="bold", va="center")
    log = [("10:41:07", "Renamed", GREEN, "a1b2c3d4.txt → 2026-11-18_Document_Quarterly-Sales…", "96%: 192 words; topic terms: quarterly sales"),
           ("10:41:04", "New", "#4a5b73", "a1b2c3d4.txt", "Waiting until it stops changing"),
           ("10:40:51", "Left alone", "#4a5b73", "Meeting notes.txt", "Name already clean; preserved"),
           ("10:40:51", "Set aside", ORANGE, "copy1.bin → _Review_Needed/", "Confidence 0.00: no readable signal"),
           ("10:40:49", "Awaiting OK", BLUE, "IMG_0412.jpg → 2024-05-03_101530_4000x3000.jpg", "Camera photo: EXIF present"),
           ("10:40:49", "Ignored", "#4a5b73", "movie.mp4.crdownload", "Still downloading")]
    for i, (tm, ev, col, f, d) in enumerate(log):
        yy = top - 14 - i * 3.6
        if i == 0:
            ax.add_patch(Rectangle((X + 22, yy - 1.6), W - 22, 3.4, fc="#fff8c5", ec="none"))
        ax.plot([X + 22, X + W], [yy - 1.8, yy - 1.8], color="#eaeef2", lw=0.8)
        ax.text(cx[0], yy, tm, fontsize=7.2, color=GRAY, va="center", family="DejaVu Sans Mono")
        ax.text(cx[1], yy, ev, fontsize=7.5, color=col, fontweight="bold", va="center")
        ax.text(cx[2], yy, f, fontsize=6.9, va="center", family="DejaVu Sans Mono")
        ax.text(cx[3], yy, d, fontsize=7, color=GRAY, va="center")
    ax.add_patch(Rectangle((X, Y), W, 3, fc="#f6f8fa", ec="#d0d7de"))
    ax.add_patch(plt.Circle((X + 2, Y + 1.5), 0.6, color=GREEN))
    ax.text(X + 3.4, Y + 1.5, "Watching 3 folders. Idle, 1 new file waiting to stop changing. Last check 2s ago, next in 3s.", fontsize=7.5, va="center")
    ax.text(X + W - 1.5, Y + 1.5, "Today: 12 files organized · 3 awaiting OK", fontsize=7.2, color=GRAY, ha="right", va="center")

    def marker(n, x, y):
        ax.text(x, y, str(n), ha="center", va="center", fontsize=9, color="white", fontweight="bold",
                bbox=dict(boxstyle="circle,pad=0.3", fc=BLUE, ec="none"), zorder=10)

    rx = X + W + 3.2
    for n, yy in ((1, top - 2.25), (2, top - 6.75), (5, top - 14), (3, Y + 1.5)):
        ax.plot([X + W, rx - 1.2], [yy, yy], color=BLUE, lw=1, zorder=9)
        marker(n, rx, yy)
    ax.plot([X, X - 1.7], [top - 14.4, top - 14.4], color=BLUE, lw=1)
    marker(4, X - 3.2, top - 14.4)
    legend = ["1  Toolbar: add folders, check now, pause or resume monitoring",
              "2  Tabs: live activity log · changes awaiting your approval · undo history · per-folder settings",
              "3  Status bar: what the program is doing right now, always visible (progress bar while analyzing)",
              "4  Folder list: state dot (watching / paused / missing) and a badge for changes awaiting approval",
              "5  Newest event flashes. Every row says what happened to which file, and why"]
    for i, line in enumerate(legend):
        ax.text(X, -0.8 - i * 1.75, line, fontsize=8.6, color=INK, va="center")
    save(fig, "dashboard-layout.png")


if __name__ == "__main__":
    clustering(); images(); pipeline(); scaling(); latency(); evaluation(); summary(); architecture(); confidence(); dashboard()
