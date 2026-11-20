# Performance: what was slow, what fixed it, and why there is no C code

This page records a real investigation. The method matters more than the numbers: **measure first, then fix the thing the measurements point at.**

> **Update.** The latest, reproducible measurements, charts and an accuracy evaluation are in the [README](../README.md#benchmarks)
> (`tools/bench_compare.py`, `tools/bench_latency.py`, `tools/evaluate.py`). Where this page quotes older single-run figures
> (for example "3 s" reaction time, "5.4 s" for the full folder), the README's multi-trial numbers supersede them.

## 1. The complaint

"It feels slow." There were really two different problems hiding under that one word:

| Kind | What the user feels | Cause |
|---|---|---|
| **Latency** | "I dropped a file in and nothing happened for a minute." | 30 s check interval + 5 s settle rule + needing two quiet scans = **a median of 45 s** (37 to 57 s measured, n = 5) before a proposal appeared. |
| **Throughput** | "Organizing a big folder takes forever." | Two badly scaled algorithms (below). |

## 2. Method

`tools/make_dataset.py` builds a deliberately nasty folder (2,399 files, 94 MB: 1,500 documents over 30 topics, 12-megapixel photos, 2-megapixel photos, screenshots, MP3/WAV/MP4/ZIP/EXE, duplicates).
`tools/bench.py` wraps every pipeline stage in a timer and can run `cProfile`.

```bash
python tools/make_dataset.py /tmp/bench_data
python tools/bench.py /tmp/bench_data [--profile]
```

## 3. What the profile said (357-file subset, before any change)

| Stage | Time | Share |
|---|---|---|
| Text clustering (`agglomerative`) | 3.22 s | **52 %** |
| Image analysis | 2.54 s | **41 %** |
| Everything else (crawl, hashing, magic bytes, tokenizing, TF-IDF, scoring, manifest) | < 0.3 s | < 5 % |

On the full 2,399 files the original code **did not finish in 300 seconds**: the clustering was cubic.

## 4. The fixes, in the order the profile demanded them

| # | Problem | Fix | Effect |
|---|---|---|---|
| 1 | Clustering rescanned every pair after every merge: O(n³). | Heap with lazy deletion; merged clusters store **sums** of similarities; sparse pairs. A property test proves it equals textbook average linkage. | > 300 s → 24 s |
| 2 | Candidate pairs exploded when documents share vocabulary. | k-nearest-neighbour candidates (10 per document), topic-term index with a posting-list cap. | 24 s → 0.8 s |
| 3 | Every photo decoded at full size; converted and resized **three** times. | JPEG `draft()` (decode at 1/8 scale), **one** 256-px working copy feeds the hash, entropy and colour stats. | 69 ms → ~9 ms per image |
| 4 | `cosine()` looped over every term of the smaller document. | `a.keys() & b.keys()` (a C-speed set intersection), then multiply only shared terms. Bit-identical results. | 1.5× on that kernel |
| 5 | `mean_similarity` compared up to 60 members per cluster (an *audit* number). | Estimate from 12 members. | ~50,000 cosines removed |
| 6 | Each file was opened twice in `inspect`. | Read once; reuse the header for magic bytes. | 3 s → 0.09 s |
| 7 | The crawler used `os.walk` + 3-4 syscalls per file. | `os.scandir` (one `stat`), plus a no-object `snapshot()` for the watcher. | 2,399 files: 6 ms |
| 8 | Latency: median 45 s to react. | 5 s default interval, **1.5 s re-check while files are settling**, 3 s settle rule, `snapshot()` makes checking free. | **median 4.1 s** (3.2 to 6.2 s, n = 8) |

### Result

| | Before | After |
|---|---|---|
| 2,399 files, full analysis | **did not finish (> 300 s)** | **5.4 s** (442 files/s) |
| 357 files in the running daemon | 6.2 s | **0.9 s** |
| New file → proposal visible (median) | 45 s | **4.1 s** |
| "Did anything change?" check on 2,399 files | ~160 ms | **6 ms** |

## 5. Should we use C or C++?

You asked, so it was tested instead of assumed. After fixes 1-3, one pure-Python kernel remained at the top of the profile: the sparse dot product inside `cosine()`.

A C extension (`tools/native_experiment/fastdot.c`, a merge-join over sorted term-id arrays) was written and benchmarked, **including the cost of converting the data into the arrays C needs**:

| | per cosine |
|---|---|
| Python (set intersection) | 41 µs |
| C merge-join | 6 µs (**6.9× faster in isolation**) |
| One-time conversion of 900 vectors to arrays | 0.23 s |

So the C kernel itself is real and fast. But end to end:

- After fixes 2 and 5 only about 17,000 cosines remain per 1,760 documents: ~0.7 s in Python.
- C would cost ~0.1 s for the math **plus ~0.4 s of conversion**, saving about **0.15 s out of 5.4 s: roughly 3 %**.
- The rest of the run is image decoding (already C inside Pillow/libjpeg/libpng), file I/O and hashing (already C inside `hashlib`), and Python dict building for TF-IDF.

Against a ~3 % gain, C would add: a compiler requirement on every user's machine (MSVC on Windows), per-OS and per-Python-version builds, a second implementation that must stay bit-identical to the first, and a harder install for non-technical users.

**Decision: not shipped.** The speed-ups came from algorithms, not from the language. The experiment is kept in `tools/native_experiment/` (with the exact commands to reproduce) in case a future workload (say, 100,000+ documents) changes the arithmetic.

## 6. Multi-core

`Pipeline(workers=N)` runs per-file feature extraction in a thread pool (Pillow releases the GIL while decoding). A test proves results are identical to the serial run. **The development machine had one core, so the speed-up on multi-core machines is expected but was not measured here.** Set `workers=1` to disable.

## 7. Honest limits

- The benchmark corpus has a 168-word vocabulary, which is a *worst case* for text clustering; real documents overlap less.
- Image decode time depends on your photos; 12-megapixel JPEGs are fast thanks to `draft()`, large PNGs have no such shortcut.
- Approximations (neighbour caps, 12-member similarity estimate) affect only the small cohesion bonus (+0.04 max) and the audit numbers, never which files are renamed on a pure-name basis.
