"""Reproduce the C experiment (Linux/macOS):
    gcc -O2 -shared -fPIC $(python3-config --includes) fastdot.c -o fastdot$(python3-config --extension-suffix)
    python bench_native.py /tmp/bench_data
Result on the development machine: the C kernel is ~7x faster per cosine, but once the data is
converted to arrays and the algorithmic fixes are in, the end-to-end gain is under ~6%, so it
is NOT shipped. See docs/PERFORMANCE.md."""
import random, sys, time
from array import array
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2])); sys.path.insert(0, str(Path(__file__).parent))
from organizer.models import Features
from organizer.modules import crawler, extractors, text_features as tf
import fastdot

recs = [r for r in crawler.crawl(Path(sys.argv[1])) if r.extension in (".txt", ".md", ".csv", ".py")][:900]
docs = []
for r in recs:
    f = Features(r); extractors.inspect(f); extractors.enrich(f)
    docs += [f.tokens] if f.tokens else []
vecs = [tf.TfidfModel().fit(docs).vectorize(d) for d in docs[:1]] and None
model = tf.TfidfModel().fit(docs); vecs = [model.vectorize(d) for d in docs]
pairs = [(random.randrange(len(vecs)), random.randrange(len(vecs))) for _ in range(20000)]
t = time.perf_counter(); ids = {}; packed = []
for v in vecs:
    items = sorted((ids.setdefault(k, len(ids)), w) for k, w in v.items())
    packed.append((array("i", [i for i, _ in items]), array("d", [w for _, w in items])))
conv = time.perf_counter() - t
t = time.perf_counter(); [tf.cosine(vecs[i], vecs[j]) for i, j in pairs]; py = time.perf_counter() - t
t = time.perf_counter(); [fastdot.dot(packed[i][0], packed[i][1], packed[j][0], packed[j][1]) for i, j in pairs]; c = time.perf_counter() - t
print(f"python {py:.3f}s | C {c:.3f}s ({py / c:.1f}x) | conversion of {len(vecs)} vectors {conv:.3f}s")
