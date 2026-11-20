"""Command line interface.

    python -m organizer ui                            # open the dashboard (starts background monitoring)
    python -m organizer daemon                        # same, without opening a browser (used at login)
    python -m organizer autostart enable|disable|status

    python -m organizer preview  <folder>             # dry run, writes manifest, touches nothing
    python -m organizer apply    <manifest.json>      # execute exactly what was previewed
    python -m organizer rollback <manifest.json>      # undo everything that was applied
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .modules import manifest
from .modules.pipeline import Pipeline


def _cmd_preview(args: argparse.Namespace) -> int:
    pipe = Pipeline(args.folder, dupes_mode=args.dupes, threshold=args.threshold,
                    skip_projects=not args.include_code_projects,
                    manifest_path=Path(args.manifest) if args.manifest else None,
                    log=lambda m: print(m, file=sys.stderr))
    plan = pipe.run()
    root = plan.dm.root
    print()
    for d in plan.decisions:
        if d.action == "keep" and not args.show_keep:
            continue
        old, new = plan.dm.relative(d.features.record.path), plan.dm.relative(d.dest)
        arrow = f"{old}  ->  {new}" if old != new else f"{old}  (stays)"
        print(f"[{d.action:<10} {d.confidence:.2f}] {arrow}")
        print(f"             {d.reason}")
    print("\nSummary:", plan.stats["by_action"])
    print(f"Manifest: {plan.manifest_path}")
    print(f"\nNothing has been changed. To execute:  python -m organizer apply \"{plan.manifest_path}\"")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    counts = manifest.apply_manifest(Path(args.manifest))
    print("Applied:", counts)
    print(f"Undo with:  python -m organizer rollback \"{args.manifest}\"")
    return 0 if not counts["failed"] else 1


def _cmd_rollback(args: argparse.Namespace) -> int:
    counts = manifest.rollback_manifest(Path(args.manifest))
    print("Rolled back:", counts)
    return 0 if not (counts["failed"] or counts["conflict"]) else 1


def _cmd_ui(args: argparse.Namespace) -> int:
    from .service import server
    return server.run(open_browser=args.command == "ui", use_tray=not args.no_tray, port=args.port)


def _cmd_autostart(args: argparse.Namespace) -> int:
    from .service import autostart
    if args.action == "enable":
        autostart.enable()
        print("File Organizer will now start automatically when you sign in.")
    elif args.action == "disable":
        autostart.disable()
        print("Automatic start is turned off.")
    else:
        print("enabled" if autostart.is_enabled() else "disabled")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="organizer", description="Deterministic local file organizer.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, helptext in (("ui", "open the dashboard and run in the background"),
                           ("daemon", "run in the background without opening a browser")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--no-tray", action="store_true", help="do not show a system-tray icon")
        p.add_argument("--port", type=int, default=8765)
        p.set_defaults(func=_cmd_ui)

    p = sub.add_parser("autostart", help="start automatically when you sign in")
    p.add_argument("action", choices=["enable", "disable", "status"])
    p.set_defaults(func=_cmd_autostart)

    p = sub.add_parser("preview", help="dry run: analyse a folder and write a manifest")
    p.add_argument("folder")
    p.add_argument("--dupes", choices=["flag", "move"], default="flag")
    p.add_argument("--threshold", type=float, default=config.CONFIDENCE_THRESHOLD)
    p.add_argument("--manifest", help="where to write the manifest (default: <folder>/_Organizer_Logs/)")
    p.add_argument("--include-code-projects", action="store_true",
                   help="also organize inside folders that look like software projects (not recommended)")
    p.add_argument("--show-keep", action="store_true", help="also list files that will not change")
    p.set_defaults(func=_cmd_preview)

    p = sub.add_parser("apply", help="execute a previewed manifest")
    p.add_argument("manifest")
    p.set_defaults(func=_cmd_apply)

    p = sub.add_parser("rollback", help="undo an applied manifest")
    p.add_argument("manifest")
    p.set_defaults(func=_cmd_rollback)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
