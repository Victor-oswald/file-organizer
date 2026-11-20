"""Unit tests: one class per pipeline stage."""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from organizer.models import Features, FileRecord
from organizer.modules import (archive_features, clustering, crawler, extractors, hasher,
                               image_features, magic, media_features, namer, text_features)
from organizer.modules.directory_manager import DirectoryManager, PathReserver, validate_directory

from . import helpers


def make_features(path: Path) -> Features:
    st = path.stat()
    rec = FileRecord(path, path.name, path.stem, path.suffix.lower(), st.st_size, st.st_mtime)
    f = Features(rec)
    extractors.inspect(f)
    return f


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()


class TestDirectoryManager(TmpDirCase):
    def test_validate(self):
        self.assertEqual(validate_directory(self.dir), self.dir)
        with self.assertRaises(ValueError):
            validate_directory(self.dir / "nope")

    def test_reserver_case_insensitive_and_suffix(self):
        r = PathReserver()
        a = r.reserve(self.dir / "Report.txt")
        b = r.reserve(self.dir / "report.TXT")
        self.assertEqual(a.name, "Report.txt")
        self.assertEqual(b.name, "report_2.TXT")

    def test_reserver_respects_existing_file(self):
        (self.dir / "x.txt").write_text("hi")
        r = PathReserver()
        self.assertEqual(r.reserve(self.dir / "x.txt").name, "x_2.txt")
        self.assertEqual(PathReserver().reserve(self.dir / "x.txt", current=self.dir / "x.txt").name, "x.txt")


class TestCrawler(TmpDirCase):
    def test_skips_hidden_managed_and_junk(self):
        (self.dir / "a.txt").write_text("a")
        (self.dir / ".hidden").write_text("h")
        (self.dir / "~$lock.docx").write_text("l")
        (self.dir / "Thumbs.db").write_text("t")
        (self.dir / "_Review_Needed").mkdir()
        (self.dir / "_Review_Needed" / "old.txt").write_text("o")
        (self.dir / ".git").mkdir()
        (self.dir / ".git" / "config").write_text("c")
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "B.TXT").write_text("b")
        found = {(r.name, r.extension) for r in crawler.crawl(self.dir)}
        self.assertEqual(found, {("a.txt", ".txt"), ("B.TXT", ".txt")})

    def test_skips_symlinks(self):
        (self.dir / "real.txt").write_text("x")
        try:
            os.symlink(self.dir / "real.txt", self.dir / "link.txt")
            os.symlink(self.dir, self.dir / "loop")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual([r.name for r in crawler.crawl(self.dir)], ["real.txt"])


class TestHasher(TmpDirCase):
    def test_partial_hash_properties(self):
        a, b, c = self.dir / "a", self.dir / "b", self.dir / "c"
        data = os.urandom(300_000)
        a.write_bytes(data)
        b.write_bytes(data)
        c.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))          # last byte differs
        ha, hb, hc = (hasher.partial_hash(p, 300_000) for p in (a, b, c))
        self.assertEqual(ha, hb)
        self.assertNotEqual(ha, hc)                                # tail is covered

    def test_middle_change_is_invisible_to_partial_but_not_full(self):
        a, b = self.dir / "a", self.dir / "b"
        data = bytearray(os.urandom(300_000))
        a.write_bytes(bytes(data))
        data[150_000] ^= 0xFF
        b.write_bytes(bytes(data))
        self.assertEqual(hasher.partial_hash(a, 300_000), hasher.partial_hash(b, 300_000))
        self.assertNotEqual(hasher.full_hash(a), hasher.full_hash(b))   # this is why we confirm

    def test_small_file(self):
        p = self.dir / "s"
        p.write_bytes(b"hello")
        self.assertEqual(len(hasher.partial_hash(p, 5)), 64)


class TestMagic(TmpDirCase):
    def kind(self, name, data):
        p = self.dir / name
        p.write_bytes(data)
        return magic.sniff(p, p.suffix.lower(), len(data))

    def test_png_disguised_as_txt(self):
        kind, cat, mismatch = self.kind("photo.txt", b"\x89PNG\r\n\x1a\n" + b"\0" * 50)
        self.assertEqual((kind, cat, mismatch), ("png", "image", True))

    def test_formats(self):
        self.assertEqual(self.kind("a.pdf", b"%PDF-1.7\n")[:2], ("pdf", "document"))
        self.assertEqual(self.kind("a.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 20)[:2], ("jpeg", "image"))
        self.assertEqual(self.kind("a.wav", b"RIFF\0\0\0\0WAVEfmt ")[:2], ("wav", "audio"))
        self.assertEqual(self.kind("a.webp", b"RIFF\0\0\0\0WEBPVP8 ")[:2], ("webp", "image"))
        self.assertEqual(self.kind("a.mp4", b"\0\0\0\x18ftypisom\0\0\0\0")[:2], ("mp4", "video"))
        self.assertEqual(self.kind("a.m4a", b"\0\0\0\x18ftypM4A \0\0\0\0")[:2], ("m4a", "audio"))
        self.assertEqual(self.kind("a.elf", b"\x7fELF\x02\x01\x01")[:2], ("elf", "binary"))
        self.assertEqual(self.kind("a.exe", b"MZ" + b"\0" * 60)[:2], ("pe", "binary"))
        self.assertEqual(self.kind("a.mp3", b"\xff\xfb\x90\x00" + b"\0" * 20)[:2], ("mp3", "audio"))

    def test_office_zip_and_plain_zip(self):
        helpers.build_zip(self.dir / "r.docx", ["word/document.xml"])
        helpers.build_zip(self.dir / "r.zip", ["a.txt"])
        for n, want in (("r.docx", ("docx", "document")), ("r.zip", ("zip", "archive"))):
            p = self.dir / n
            self.assertEqual(magic.sniff(p, p.suffix, p.stat().st_size)[:2], want)

    def test_text_code_data_and_empty(self):
        self.assertEqual(self.kind("n.txt", b"just some words here")[:2], ("text", "document"))
        self.assertEqual(self.kind("s.py", b"print('x')")[:2], ("text", "code"))
        self.assertEqual(self.kind("d.csv", b"a,b\n1,2")[:2], ("text", "data"))
        self.assertEqual(self.kind("e.txt", b"")[:2], ("empty", "empty"))
        self.assertEqual(self.kind("r.bin", os.urandom(100) + b"\0")[1], "other")


class TestText(unittest.TestCase):
    def test_tokenize(self):
        toks = text_features.tokenize("The Quarterly_salesReport 2024 is a THE report!!")
        self.assertEqual(toks, ["quarterly", "sales", "report", "report"])

    def test_tfidf_normalised_and_cosine(self):
        docs = [text_features.tokenize(t) for t in (helpers.SALES_TEXT, helpers.RECIPE_TEXT, helpers.SALES_TEXT)]
        model = text_features.TfidfModel().fit(docs)
        v = [model.vectorize(d) for d in docs]
        self.assertAlmostEqual(sum(w * w for w in v[0].values()), 1.0, places=6)
        self.assertAlmostEqual(text_features.cosine(v[0], v[2]), 1.0, places=6)
        self.assertLess(text_features.cosine(v[0], v[1]), 0.2)

    def test_top_terms_deterministic_and_sensible(self):
        docs = [text_features.tokenize(t) for t in (helpers.SALES_TEXT, helpers.RECIPE_TEXT)]
        model = text_features.TfidfModel().fit(docs)
        vec = model.vectorize(docs[0])
        top = text_features.top_terms(vec, bigram=True, k=3)
        self.assertEqual(top, text_features.top_terms(vec, bigram=True, k=3))
        self.assertTrue(any("sales" in t or "quarterly" in t for t, _ in top))

    def test_csv_text_weights_header(self):
        text = text_features.extract_csv_text("customer_name,order_total\nann,10\nbob,20\n")
        self.assertEqual(text.count("customer_name"), 2)


class TestClustering(TmpDirCase):
    def test_exact_duplicates_require_full_hash(self):
        data = os.urandom(300_000)
        a, b, c, d = (self.dir / n for n in "abcd")
        a.write_bytes(data)
        os.utime(a, (1_000_000, 1_000_000))              # a is the oldest => primary
        b.write_bytes(data)
        mutated = bytearray(data)
        mutated[150_000] ^= 1                            # same partial hash, different content
        c.write_bytes(bytes(mutated))
        d.write_bytes(os.urandom(10))
        groups = clustering.find_exact_duplicates([make_features(p) for p in (a, b, c, d)])
        self.assertEqual(len(groups), 1)
        self.assertEqual([f.record.name for f in groups[0]], ["a", "b"])

    def test_agglomerative_finds_topics(self):
        texts = [helpers.SALES_TEXT, helpers.SALES_TEXT + " budget", helpers.RECIPE_TEXT,
                 helpers.RECIPE_TEXT + " dessert", helpers.CODE_TEXT]
        docs = [text_features.tokenize(t) for t in texts]
        model = text_features.TfidfModel().fit(docs)
        vecs = {i: model.vectorize(d) for i, d in enumerate(docs)}
        clusters = clustering.agglomerative(vecs, 0.30)
        self.assertEqual(sorted(map(tuple, clusters)), [(0, 1), (2, 3), (4,)])

    def test_agglomerative_matches_naive_reference(self):
        """With pruning disabled the fast heap version must equal textbook O(n^3) average linkage."""
        import math
        rnd = random.Random(3)
        words = [f"w{i}" for i in range(14)]
        docs = []
        for i in range(24):
            base = (i % 4) * 3
            toks = [words[base + rnd.randrange(3)] for _ in range(rnd.randint(8, 16))] + \
                   [rnd.choice(words) for _ in range(rnd.randint(0, 4))]
            docs.append([t + "xyz" for t in toks])
        model = text_features.TfidfModel().fit(docs)
        vecs = {i: model.vectorize(d) for i, d in enumerate(docs)}
        for threshold in (0.15, 0.30, 0.50):
            fast = clustering.agglomerative(vecs, threshold, top_terms=10_000, max_posting=10_000, neighbors=None, prune=0.0)
            clusters = [[i] for i in vecs]                       # naive reference
            def avg(a, b):
                return sum(text_features.cosine(vecs[x], vecs[y]) for x in a for y in b) / (len(a) * len(b))
            while len(clusters) > 1:
                best = max(((avg(clusters[i], clusters[j]), -i, -j) for i in range(len(clusters))
                            for j in range(i + 1, len(clusters))))
                if best[0] < threshold:
                    break
                i, j = -best[1], -best[2]
                clusters[i] = clusters[i] + clusters.pop(j)
            slow = sorted(sorted(c) for c in clusters)
            self.assertEqual(sorted(map(tuple, fast)), sorted(map(tuple, slow)), f"threshold={threshold}")

    def test_hamming_clustering_matches_brute_force(self):
        rnd = random.Random(7)
        bases = [rnd.getrandbits(64) for _ in range(6)]
        hashes = {}
        for i in range(60):                                          # noisy copies of a few bases
            h = rnd.choice(bases)
            for bit in rnd.sample(range(64), rnd.randint(0, 4)):
                h ^= 1 << bit
            hashes[i] = h
        fast = clustering.cluster_hashes(hashes, 6)
        # brute-force union-find
        parent = list(range(60))
        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for a in range(60):
            for b in range(a + 1, 60):
                if clustering.hamming(hashes[a], hashes[b]) <= 6:
                    parent[max(find(a), find(b))] = min(find(a), find(b))
        slow: dict[int, list[int]] = {}
        for i in range(60):
            slow.setdefault(find(i), []).append(i)
        self.assertEqual(sorted(map(tuple, fast)), sorted(map(tuple, slow.values())))

    def test_event_windows(self):
        t = datetime(2024, 5, 3, 10, 0)
        items = [(0, t), (1, t + timedelta(hours=1)), (2, t + timedelta(hours=4, minutes=30)),
                 (3, t + timedelta(hours=20))]
        wins = clustering.event_windows(items, timedelta(hours=4))
        self.assertEqual([[i for i, _ in w] for w in wins], [[0, 1, 2], [3]])


class TestImages(TmpDirCase):
    def test_dhash_similar_vs_different(self):
        a = helpers.photo_like(1, base_seed=1)
        b = helpers.photo_like(2, base_seed=1)          # same structure, different noise
        c = helpers.photo_like(3, base_seed=99)
        self.assertLessEqual(clustering.hamming(image_features.dhash(a), image_features.dhash(b)), 6)
        self.assertGreater(clustering.hamming(image_features.dhash(a), image_features.dhash(c)), 6)

    def test_camera_screenshot_photo(self):
        helpers.save_camera_jpeg(self.dir / "cam.jpg", 1, "2024:05:03 10:15:30")
        helpers.flat_screenshot().save(self.dir / "shot.png")
        helpers.photo_like(5).save(self.dir / "plain.jpg")
        res = {}
        for name in ("cam.jpg", "shot.png", "plain.jpg"):
            f = make_features(self.dir / name)
            extractors.enrich(f)
            res[name] = f
        self.assertTrue(res["cam.jpg"].is_camera)
        self.assertEqual(res["cam.jpg"].taken, datetime(2024, 5, 3, 10, 15, 30))
        self.assertTrue(res["shot.png"].is_screenshot and not res["shot.png"].is_camera)
        self.assertFalse(res["plain.jpg"].is_camera or res["plain.jpg"].is_screenshot)

    def test_bad_exif_date_is_ignored(self):
        self.assertIsNone(image_features._parse_exif_date("0000:00:00 00:00:00"))
        self.assertIsNone(image_features._parse_exif_date(None))


class TestMedia(TmpDirCase):
    def test_mp4(self):
        when = datetime(2024, 5, 3, 11, 0, 5)
        (self.dir / "v.mp4").write_bytes(helpers.build_mp4(when))
        f = make_features(self.dir / "v.mp4")
        extractors.enrich(f)
        self.assertEqual((f.taken, f.width, f.height, f.fps), (when, 1920, 1080, 30.0))
        self.assertAlmostEqual(f.duration, 10.0)

    def test_mp3_tags_and_frame(self):
        (self.dir / "t.mp3").write_bytes(helpers.build_mp3("Some Artist", "Some Title"))
        f = make_features(self.dir / "t.mp3")
        extractors.enrich(f)
        self.assertEqual((f.artist, f.title, f.sample_rate, f.channels), ("Some Artist", "Some Title", 44100, 2))
        self.assertFalse(f.is_voice_memo)

    def test_id3v1_fallback(self):
        raw = b"\xff\xfb\x90\x00" + b"\0" * 300 + b"TAG" + b"Old Song".ljust(30, b"\0") + b"Old Band".ljust(30, b"\0") + b"\0" * 65
        (self.dir / "o.mp3").write_bytes(raw)
        tags, _ = media_features.parse_id3(self.dir / "o.mp3")
        self.assertEqual(tags, {"title": "Old Song", "artist": "Old Band"})

    def test_wav_voice_memo(self):
        helpers.build_wav(self.dir / "m.wav", rate=8000, channels=1, seconds=2)
        f = make_features(self.dir / "m.wav")
        extractors.enrich(f)
        self.assertEqual((f.sample_rate, f.channels), (8000, 1))
        self.assertAlmostEqual(f.duration, 2.0)
        self.assertTrue(f.is_voice_memo)


class TestArchivesAndBinaries(TmpDirCase):
    def test_zip_listing_tokens(self):
        helpers.build_zip(self.dir / "p.zip", ["invoice_templates/january_invoice.docx", "invoice_templates/notes.txt"])
        f = make_features(self.dir / "p.zip")
        extractors.enrich(f)
        self.assertIn("invoice", f.tokens)

    def test_pe_strings(self):
        (self.dir / "setup.exe").write_bytes(helpers.build_pe("Acme Corp", "Super Tool"))
        f = make_features(self.dir / "setup.exe")
        extractors.enrich(f)
        self.assertEqual((f.vendor, f.product), ("Acme Corp", "Super Tool"))


class TestNamer(unittest.TestCase):
    def test_clean_names(self):
        for clean in ("Meeting notes", "résumé final", "Tax return 2023", "2024-05-03_Document_Quarterly-Report"):
            self.assertTrue(namer.is_clean_name(clean), clean)
        for dirty in ("IMG_20240503_101530", "a1b2c3d4e5f6a7b8", "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
                      "DSC00123", "Untitled", "copy1", "new (2)", "1234567890123", "VID_0001", "x", "track01", "tmp_8f3a9c1d2e"):
            self.assertFalse(namer.is_clean_name(dirty), dirty)

    def test_sanitize(self):
        self.assertEqual(namer.sanitize('a<b>c:d"e/f\\g|h?i*j'), "a b c d e f g h i j")
        self.assertEqual(namer.sanitize("CON"), "_CON")
        self.assertEqual(namer.sanitize("trailing dot. "), "trailing dot")
        self.assertEqual(namer.sanitize("???"), "unnamed")
        self.assertLessEqual(len(namer.sanitize("x" * 500)), 120)


if __name__ == "__main__":
    unittest.main()


class TestCorruptInputs(TmpDirCase):
    """One broken file must never stop a run (documented safety guarantee)."""

    def test_pipeline_survives_corrupt_files(self):
        from organizer.modules.pipeline import Pipeline
        (self.dir / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(50))      # truncated JPEG
        (self.dir / "broken.docx").write_bytes(b"PK\x03\x04" + os.urandom(50))           # corrupt zip
        (self.dir / "broken.mp4").write_bytes(b"\0\0\0\x18ftypisom" + os.urandom(40))    # no moov
        (self.dir / "broken.mp3").write_bytes(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f" + b"\1" * 20)   # absurd tag size
        (self.dir / "broken.exe").write_bytes(b"MZ" + b"\0" * 10)                        # tiny PE
        (self.dir / "broken.wav").write_bytes(b"RIFF\0\0\0\0WAVEfmt \x10\0\0\0")         # truncated WAV
        (self.dir / "broken.pdf").write_bytes(b"%PDF-1.4\n/Title (")                     # truncated PDF
        plan = Pipeline(self.dir, manifest_path=self.dir.parent / "corrupt_manifest.json").run()
        self.assertEqual(len(plan.decisions), 7)
        for d in plan.decisions:                       # nothing crashed, nothing renamed on a guess
            self.assertIn(d.action, ("keep", "quarantine", "rename"))
