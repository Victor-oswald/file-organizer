"""All tunable constants live here so nothing is a mystery number elsewhere."""

from datetime import timedelta

# --- decision ---------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.60      # >= rename / keep, < quarantine

# --- hashing / sniffing -----------------------------------------------------
PARTIAL_HASH_CHUNK = 64 * 1024   # first and last 64 KB
FULL_HASH_BLOCK = 1024 * 1024    # streaming block size for full hashes
HEADER_BYTES = 512               # magic bytes need 32; tar's marker sits at offset 257

# --- text -------------------------------------------------------------------
TEXT_READ_BYTES = 256 * 1024     # never read more than this from a text file
TEXT_TOKEN_LIMIT = 1000          # "top ~1,000 tokens"
MIN_TOKEN_LEN = 3
MAX_TOKEN_LEN = 25
COSINE_CLUSTER_THRESHOLD = 0.08  # average-linkage stop point (calibrated on labelled topics: see docs/BENCHMARKS)
TEXT_FULL_COVERAGE_TOKENS = 60   # tokens needed for full "coverage" score (docs, code)
ARCHIVE_FULL_COVERAGE_TOKENS = 20

# --- images -----------------------------------------------------------------
HAMMING_MAX = 6                  # dHash distance for "same picture / burst"
SCREENSHOT_UNIQUE_RATIO = 0.25   # fewer distinct colours than this => "flat"
SCREENSHOT_ENTROPY = 5.0         # or grayscale entropy below this

# --- events -----------------------------------------------------------------
EVENT_GAP = timedelta(hours=4)
EVENT_MIN_FILES = 3

# --- audio ------------------------------------------------------------------
VOICE_MEMO_MAX_RATE = 24000

# --- naming -----------------------------------------------------------------
MAX_NAME_LEN = 120

# --- folders the organizer owns (never crawled again => idempotent) ---------
REVIEW_DIR = "_Review_Needed"
DUPLICATES_DIR = "_Duplicates"
LOGS_DIR = "_Organizer_Logs"
EVENTS_DIR = "_Events"
GROUPS_DIR = "_Photo_Groups"
MANAGED_DIRS = frozenset({REVIEW_DIR, DUPLICATES_DIR, LOGS_DIR, EVENTS_DIR, GROUPS_DIR})

# --- things we never touch --------------------------------------------------
IGNORED_DIR_NAMES = frozenset({
    "node_modules", "__pycache__", "$recycle.bin", "system volume information",
})
IGNORED_FILE_NAMES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})

# --- extension -> category hints for plain-text files -----------------------
CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sh", ".bat", ".ps1", ".sql",
    ".html", ".css", ".scss", ".lua", ".r", ".m",
})
DATA_EXTENSIONS = frozenset({
    ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".log",
})
