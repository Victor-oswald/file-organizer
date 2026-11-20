"""Record the REAL command line on a messy demo folder and render it as an animated GIF.

    python tools/make_demo_gif.py            # -> docs/assets/demo.gif

Every command below is actually executed; its real output is what appears in the animation.
Only two cosmetic edits are made: the temporary folder path is shown as ~/Downloads, and lines
longer than the terminal width are cut with an ellipsis.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.test_end_to_end import build_messy_folder  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
COLS, ROWS, SIZE = 124, 29, 13
BG, FG, DIM, GREEN, ORANGE, YELLOW, BLUE, RED = "#0d1117", "#e6edf3", "#8b949e", "#3fb950", "#d29922", "#e3b341", "#58a6ff", "#ff7b72"


def run(cmd: list[str] | str, cwd: Path, shell=False) -> str:
    # stderr is merged INTO stdout so the lines appear in the order a terminal would show them
    r = subprocess.run(cmd, cwd=cwd, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    return r.stdout.rstrip("\n")


def colorize(line: str):
    if line.startswith("#"):
        return [(line, "#6e7681")]
    if line.startswith("["):
        tag = line[1:].split()[0]
        c = {"rename": GREEN, "move": BLUE, "quarantine": ORANGE, "duplicate": YELLOW}.get(tag, FG)
        head, _, rest = line.partition("]")
        return [(head + "]", c), (rest, FG)]
    if line.startswith(("Summary", "Manifest", "Applied", "Rolled", "Nothing", "Undo")):
        return [(line, FG)]
    if line.startswith("Stage") or line.startswith("  "):
        return [(line, DIM)]
    return [(line, FG)]


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    folder = tmp / "Downloads"
    folder.mkdir()
    build_messy_folder(folder)
    show = lambda s: s.replace(str(folder), "~/Downloads")          # noqa: E731
    fp = ("find . -path ./_Organizer_Logs -prune -o -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16")

    steps = []
    steps.append(("# fingerprint of every file, before", "cd ~/Downloads && " + fp, run(fp, folder, shell=True)))
    prev = run([sys.executable, "-m", "organizer", "preview", str(folder), "--dupes", "move"], tmp)
    manifest = re.search(r"Manifest: (\S+)", prev).group(1)
    steps.append(("# 1. PREVIEW: analyse everything, change nothing", "python -m organizer preview ~/Downloads --dupes move", show(prev)))
    steps.append(("# 2. APPLY: execute exactly what was previewed", "python -m organizer apply ~/Downloads/_Organizer_Logs/manifest.json", show(run([sys.executable, "-m", "organizer", "apply", manifest], tmp))))
    tree = run("find . -path ./_Organizer_Logs -prune -o -type f -print | sort | head -22", folder, shell=True)
    steps.append(("# the folder now", "find ~/Downloads -type f | sort | head -22", tree.replace("./", "~/Downloads/")))
    steps.append(("# 3. ROLLBACK: undo everything", "python -m organizer rollback ~/Downloads/_Organizer_Logs/manifest.json", show(run([sys.executable, "-m", "organizer", "rollback", manifest], tmp))))
    steps.append(("# fingerprint of every file, after rollback: identical", "cd ~/Downloads && " + fp, run(fp, folder, shell=True)))

    assert steps[0][2] == steps[-1][2], "rollback did not restore the folder byte-for-byte; refusing to render a false claim"
    font, bold = ImageFont.truetype(FONT, SIZE), ImageFont.truetype(FONT_B, SIZE)
    cw = font.getlength("M")
    lh = SIZE + 7
    W, H = int(COLS * cw + 36), ROWS * lh + 52
    frames, durs = [], []
    buf: list[list[tuple[str, str]]] = []

    def render(cursor=True) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, W, 32), fill="#161b22")
        for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
            d.ellipse((14 + i * 22, 10, 26 + i * 22, 22), fill=c)
        d.text((W / 2, 16), "file-organizer: messy Downloads folder", font=font, fill=DIM, anchor="mm")
        view = buf[-ROWS:]
        for r, segs in enumerate(view):
            x = 18
            for text, color in segs:
                d.text((x, 42 + r * lh), text, font=bold if color == FG and text.startswith("$") else font, fill=color)
                x += cw * len(text)
        if cursor and view:
            x = 18 + cw * sum(len(t) for t, _ in view[-1])
            d.rectangle((x, 42 + (len(view) - 1) * lh + 1, x + cw * 0.7, 42 + (len(view) - 1) * lh + lh - 4), fill=FG)
        return img

    def snap(ms, cursor=True):
        frames.append(render(cursor))
        durs.append(ms)

    def trunc(s):
        return s if len(s) <= COLS - 1 else s[:COLS - 2] + "…"

    for comment, cmd, output in steps:
        buf.append(colorize(comment))
        snap(250)
        buf.append([("$ ", GREEN)])
        for i in range(0, len(cmd), 6):                           # typing
            buf[-1] = [("$ ", GREEN), (cmd[:i + 6], FG)]
            snap(45)
        snap(350)
        lines = [trunc(l) for l in output.splitlines()]
        for i in range(0, len(lines), 4):                         # output streams in
            for l in lines[i:i + 4]:
                buf.append(colorize(l))
            snap(70, cursor=False)
        buf.append([("", FG)])
        snap(1500 if comment.startswith("# 1") or comment.startswith("# 3") else 900, cursor=False)
    snap(3500, cursor=False)

    out = ROOT / "docs" / "assets" / "demo.gif"
    pal = [f.quantize(colors=24, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=durs, loop=0, optimize=True, disposal=1)
    print(f"wrote {out.name}: {len(frames)} frames, {out.stat().st_size / 1024:.0f} KB, {W}x{H}")
    for c, cmd, o in steps[:1] + steps[-1:]:
        print(cmd[-30:], "->", o)


if __name__ == "__main__":
    main()
