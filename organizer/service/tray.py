"""Always-available ways to reach the running app, so it can never be invisible.

    1. a system-tray icon (needs `pystray`, now a required dependency)
    2. if no tray is possible (some Linux desktops): a small control window (needs tkinter)
    3. if neither works: the app still runs, and starting it again re-opens the dashboard

Everything here is written so the logic can be unit-tested without a display.
"""

from __future__ import annotations

import threading
from typing import Callable


def describe(service) -> str:
    """One-line live status, used for the tray tooltip, its menu and the control window."""
    st = service.status()
    live = st.get("live") or {}
    active = [f for f in st["folders"] if f["state"] == "watching"]
    if live.get("phase") == "working":
        return f"{live.get('label', 'Working')} {live.get('done', 0)}/{live.get('total', 0)} ({live.get('folder', '')})"
    if not st["folders"]:
        return "No folders added yet"
    if st["paused"]:
        return "Paused"
    if not active:
        return "No folder is being watched"
    suffix = f", {st['pending']} awaiting your OK" if st["pending"] else ""
    return f"Watching {len(active)} folder{'s' if len(active) != 1 else ''}{suffix}"


def make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=10, fill=(24, 98, 196, 255))
    d.rounded_rectangle((13, 22, 51, 47), radius=3, fill=(255, 255, 255, 255))
    d.rounded_rectangle((13, 16, 29, 25), radius=3, fill=(255, 255, 255, 255))
    d.line((22, 35, 29, 41, 43, 28), fill=(24, 98, 196, 255), width=4)
    return img


def run_tray(service, open_dashboard: Callable[[], None], quit_app: Callable[[], None], quit_event,
             report: Callable[[str], None] = lambda _m: None) -> bool:
    """Show a tray icon. Blocks until the user quits. Returns False if no tray could be shown."""
    try:
        import pystray
    except Exception:
        return False

    def paused() -> bool:
        return service.store.config["paused"]

    menu = pystray.Menu(
        pystray.MenuItem("Open dashboard", lambda *_: open_dashboard(), default=True),   # left-click action
        pystray.MenuItem(lambda _i: describe(service), None, enabled=False),             # live status line
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda _i: "Resume monitoring" if paused() else "Pause monitoring",
                         lambda *_: service.set_paused(not paused())),
        pystray.MenuItem("Check folders now", lambda *_: service.request_scan()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit File Organizer", lambda icon, *_: (icon.stop(), quit_app())),
    )
    icon = pystray.Icon("file-organizer", make_icon_image(), "File Organizer", menu)

    def setup(ic) -> None:
        ic.visible = True
        report("tray")
        try:                       # Windows hides new tray icons in the "^" overflow, so say where it is
            ic.notify("Running in the background. Click this icon to open the dashboard.", "File Organizer")
        except Exception:
            pass
        def keep_tooltip_fresh() -> None:
            while not quit_event.wait(2.0):
                try:
                    ic.title = "File Organizer: " + describe(service)
                except Exception:
                    return
        threading.Thread(target=keep_tooltip_fresh, daemon=True).start()

    # "Quit" from the dashboard must also close the tray icon
    threading.Thread(target=lambda: (quit_event.wait(), icon.stop()), daemon=True).start()
    try:
        icon.run(setup=setup)
    except Exception:
        return False
    return True


def run_control_window(service, open_dashboard: Callable[[], None], quit_event,
                       report: Callable[[str], None] = lambda _m: None) -> bool:
    """Fallback when no tray exists: a small window that stays in the taskbar/dock.

    Closing it only minimizes it (the app keeps working). 'Quit' really quits."""
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:                      # tkinter missing, or no display
        return False
    root.title("File Organizer")
    root.resizable(False, False)
    status = tk.Label(root, text=describe(service), width=44, anchor="w", padx=12, pady=10)
    status.pack(fill="x")
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    pause_btn = tk.Button(bar, text="Pause monitoring")

    def toggle() -> None:
        service.set_paused(not service.store.config["paused"])

    def quit_now() -> None:
        quit_event.set()

    pause_btn.configure(command=toggle)
    pause_btn.pack(side="left", padx=(0, 6))
    tk.Button(bar, text="Open dashboard", command=open_dashboard).pack(side="left", padx=(0, 6))
    tk.Button(bar, text="Quit", command=quit_now).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.iconify)          # closing = minimize; the app keeps running

    def refresh() -> None:
        if quit_event.is_set():
            root.destroy()
            return
        status.configure(text=describe(service))
        pause_btn.configure(text="Resume monitoring" if service.store.config["paused"] else "Pause monitoring")
        root.after(1000, refresh)

    report("window")
    root.after(1000, refresh)
    root.mainloop()
    return True
