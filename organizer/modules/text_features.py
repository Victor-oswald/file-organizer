"""Stage 2 (text): turn documents, code, CSVs and archive listings into sparse
TF-IDF vectors made of unigrams AND bigrams. Pure Python, no libraries.

Pipeline for one file:
    read a bounded sample -> lowercase/split -> drop stopwords -> tokens
    all files' tokens -> TfidfModel.fit -> vectorize -> L2-normalised sparse dict
"""

from __future__ import annotations

import csv
import html
import io
import math
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Iterable

from .. import config

# ----------------------------------------------------------------------------
# Tokenizing
# ----------------------------------------------------------------------------

STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
had has have having he her here hers him his how i if in into is it its just me more most
my no nor not now of off on once only or other our out over own same she should so some
such than that the their theirs them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your
yours also may might must shall via per etc one two three
""".split())

# Words that appear in nearly every source file and say nothing about its topic
CODE_STOPWORDS = frozenset("""
def class return import from self true false none null var let const function public private
static void int string bool else elif while break continue pass try catch except finally
throw throws new this super extends implements interface package include define ifndef endif
print println echo
""".split())

_CAMEL = re.compile(r"([a-z])([A-Z])")
_SPLIT = re.compile(r"[^a-z]+")        # digits, punctuation and underscores all split


def tokenize(text: str, extra_stop: frozenset[str] = frozenset()) -> list[str]:
    """Lowercase, split camelCase / snake_case, drop stopwords, digits and junk."""
    text = _CAMEL.sub(r"\1 \2", text).lower()
    return [
        t for t in _SPLIT.split(text)
        if config.MIN_TOKEN_LEN <= len(t) <= config.MAX_TOKEN_LEN
        and t not in STOPWORDS and t not in extra_stop
    ]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]


# ----------------------------------------------------------------------------
# Reading files safely
# ----------------------------------------------------------------------------

def decode_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def read_text_sample(path: Path) -> str:
    with open(path, "rb") as f:
        return decode_bytes(f.read(config.TEXT_READ_BYTES))


_TAGS = re.compile(r"<[^>]+>")
_OOXML_T = re.compile(r"<(?:w|a):t(?:\s[^>]*)?>([^<]*)</(?:w|a):t>")


def _zip_member(z: zipfile.ZipFile, name: str, limit: int = 5_000_000) -> str:
    """Read one zip member as text, refusing absurdly large ones (zip-bomb guard)."""
    try:
        info = z.getinfo(name)
    except KeyError:
        return ""
    if info.file_size > limit:
        return ""
    return z.read(name).decode("utf-8", errors="replace")


def _ooxml_title(z: zipfile.ZipFile) -> str | None:
    core = _zip_member(z, "docProps/core.xml")
    m = re.search(r"<dc:title>([^<]+)</dc:title>", core)
    return html.unescape(m.group(1)).strip() if m else None


def extract_docx(path: Path) -> tuple[str, str | None]:
    with zipfile.ZipFile(path) as z:
        xml = _zip_member(z, "word/document.xml")
        text = " ".join(html.unescape(t) for t in _OOXML_T.findall(xml))
        return text, _ooxml_title(z)


def extract_pptx(path: Path) -> tuple[str, str | None]:
    with zipfile.ZipFile(path) as z:
        slides = sorted(n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
        parts = []
        for name in slides[:10]:
            parts += [html.unescape(t) for t in _OOXML_T.findall(_zip_member(z, name))]
        return " ".join(parts), _ooxml_title(z)


def extract_odf(path: Path) -> tuple[str, str | None]:
    with zipfile.ZipFile(path) as z:
        xml = _zip_member(z, "content.xml")
    return html.unescape(_TAGS.sub(" ", xml)), None


def extract_xlsx(path: Path) -> tuple[str, str | None]:
    """Sheet names + header row + 3 data rows of the first sheet."""
    with zipfile.ZipFile(path) as z:
        workbook = _zip_member(z, "xl/workbook.xml")
        sheet_names = [html.unescape(n) for n in re.findall(r'<sheet\b[^>]*\bname="([^"]*)"', workbook)]
        shared_xml = _zip_member(z, "xl/sharedStrings.xml")
        shared = [
            html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si)))
            for si in re.findall(r"<si>(.*?)</si>", shared_xml, flags=re.S)
        ]
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in z.namelist():
            candidates = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))
            sheet_name = candidates[0] if candidates else ""
        sheet = _zip_member(z, sheet_name) if sheet_name else ""

    rows: list[str] = []
    for row_xml in re.findall(r"<row\b[^>]*>(.*?)</row>", sheet, flags=re.S)[:4]:
        cells = []
        for attrs, inner in re.findall(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row_xml, flags=re.S):
            v = re.search(r"<v>([^<]*)</v>", inner or "")
            if not v:
                continue
            if 't="s"' in attrs and v.group(1).isdigit() and int(v.group(1)) < len(shared):
                cells.append(shared[int(v.group(1))])
            elif 't="s"' not in attrs:
                cells.append(v.group(1))
        rows.append(" ".join(cells))
    return " ".join(sheet_names * 2 + rows), None


def extract_pdf(path: Path) -> tuple[str, str | None]:
    """Metadata title (stdlib), plus real text if the optional `pypdf` is installed."""
    title = None
    size = path.stat().st_size
    with open(path, "rb") as f:
        raw = f.read(65536)
        if size > 65536:
            f.seek(max(65536, size - 65536))
            raw += f.read(65536)
    m = re.search(rb"/Title\s*\(([^)]{1,200})\)", raw)
    if m:
        title = m.group(1).decode("latin-1", errors="replace").strip() or None
    text = ""
    try:
        from pypdf import PdfReader          # optional dependency
        reader = PdfReader(str(path))
        text = " ".join((p.extract_text() or "") for p in reader.pages[:3])
    except Exception:                         # not installed, encrypted, malformed...
        pass
    return text, title


def extract_csv_text(sample: str) -> str:
    """Header row (weighted x2) + the next 3 rows."""
    lines = sample.splitlines()[:4]
    if not lines:
        return ""
    delimiter = max(",;\t|", key=lines[0].count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    header = " ".join(rows[0]) if rows else ""
    body = " ".join(" ".join(r) for r in rows[1:])
    return f"{header} {header} {body}"


def guess_title(text: str) -> str | None:
    """First meaningful line: a markdown heading, an HTML <title>, or line 1."""
    m = re.search(r"<title>([^<]{1,120})</title>", text, flags=re.I)
    if m:
        return html.unescape(m.group(1)).strip()
    for line in text.splitlines()[:30]:
        line = line.strip().lstrip("#").strip()
        if 3 <= len(line) <= 100 and not line.startswith(("import ", "from ", "#!", "<?", "{", "[")):
            return line
    return None


def archive_member_text(names: list[str]) -> str:
    """Archive listing -> text. Top-level folder names count double."""
    stems = [n.rsplit(".", 1)[0] if "." in n.rsplit("/", 1)[-1] else n for n in names]   # drop extensions
    tops = {n.split("/")[0] for n in names if "/" in n}
    return " ".join(stems) + " " + " ".join(tops) * 2


# ----------------------------------------------------------------------------
# TF-IDF
# ----------------------------------------------------------------------------

Vector = dict[str, float]


def terms_of(tokens: list[str]) -> list[str]:
    """The vocabulary entries of a document: unigrams plus bigrams."""
    return tokens + bigrams(tokens)


class TfidfModel:
    """Sparse TF-IDF over unigrams + bigrams.

        tf(t, d)  = count of t in d / number of terms in d
        idf(t)    = ln((1 + N) / (1 + df(t))) + 1        ("smooth" idf, never zero)
        weight    = tf * idf, then the whole vector is L2-normalised
    """

    def __init__(self) -> None:
        self.n_docs = 0
        self.df: Counter[str] = Counter()

    def fit(self, docs: Iterable[list[str]]) -> "TfidfModel":
        for tokens in docs:
            self.n_docs += 1
            self.df.update(set(terms_of(tokens)))
        return self

    def idf(self, term: str) -> float:
        return math.log((1 + self.n_docs) / (1 + self.df.get(term, 0))) + 1.0

    def vectorize(self, tokens: list[str]) -> Vector:
        terms = terms_of(tokens)
        if not terms:
            return {}
        counts = Counter(terms)
        total = len(terms)
        vec = {t: (c / total) * self.idf(t) for t, c in counts.items()}
        norm = math.sqrt(sum(w * w for w in vec.values()))
        return {t: w / norm for t, w in vec.items()} if norm else {}


def cosine(a: Vector, b: Vector) -> float:
    """Dot product of two L2-normalised sparse vectors (= cosine similarity).

    `a.keys() & b.keys()` runs in C and leaves only the terms both documents contain, so the
    Python loop touches the few shared terms instead of every term of the smaller document."""
    if len(a) > len(b):
        a, b = b, a
    return sum([a[t] * b[t] for t in a.keys() & b.keys()])


def top_terms(vec: Vector, *, bigram: bool, k: int) -> list[tuple[str, float]]:
    """Highest-weight terms. Ties break alphabetically so output is deterministic."""
    items = [(t, w) for t, w in vec.items() if (" " in t) == bigram]
    items.sort(key=lambda tw: (-tw[1], tw[0]))
    return items[:k]
