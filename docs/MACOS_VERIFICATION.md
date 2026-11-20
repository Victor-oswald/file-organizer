# macOS verification checklist

macOS is the one platform not yet verified by hand. The engine, the dashboard and the background service are platform-neutral and covered by the test suite; what is specific to macOS is the **menu-bar icon**, the **LaunchAgent autostart**, and the **launcher script**. This checklist exercises exactly those, in about 15 minutes.

Run everything from Terminal in the project folder so any error is visible.

## 0. Setup

```bash
python3 --version                       # 3.10 or newer
python3 -m pip install -r requirements.txt
```

Note which Python you use (python.org installer, Homebrew, or the system one); it affects pip behaviour.

## 1. Tests

```bash
python3 -m unittest discover -s tests -t .
```

**Expect:** `OK`, 96 tests. (The UI smoke tests are skipped automatically if Node.js is not installed.)

## 2. Menu-bar icon

```bash
python3 -m organizer ui
```

| Check | Expected |
|---|---|
| Browser opens the dashboard | yes |
| A File Organizer icon appears in the **menu bar** (top right) | yes |
| A notification says it is running in the background | probably; not essential |
| Click the icon | opens the dashboard |
| Open the icon's menu | Open dashboard · live status line · Pause/Resume monitoring · Check folders now · Quit File Organizer |
| Dashboard, Settings, "Quick access" row | says "Tray icon" |

## 3. A real watch cycle

1. In the dashboard: **Add folder**, choose a **new empty test folder**, keep *Ask me first*.
2. Copy a file with a machine-made name (for example a screenshot renamed `tmp_8f3a9c1d2e.png`, or any photo named `IMG_0001.jpg`) into it.
3. **Expect** in the Activity tab within about 5 seconds: *New*, then *Awaiting OK*; the status bar shows what it is doing.
4. Open **Awaiting approval**, **Apply**, then **History, Undo**. The file returns to its original name.

## 4. Menu actions

| Action | Expected |
|---|---|
| Menu: **Pause monitoring** | dashboard status bar says "Monitoring is paused"; the menu now says *Resume monitoring* |
| Menu: **Resume monitoring** | back to "Watching…" |
| Close the browser tab | the menu-bar icon stays; monitoring continues |
| Run `python3 -m organizer ui` again in another Terminal | prints "already running" and reopens the dashboard; **no second icon** |
| Menu: **Quit File Organizer** | the icon disappears and `pgrep -f "organizer ui"` prints nothing |

## 5. Autostart (LaunchAgent)

```bash
python3 -m organizer autostart enable
ls ~/Library/LaunchAgents/com.fileorganizer.agent.plist
python3 -m organizer autostart status          # enabled
```

Log out and back in (or restart). **Expect:** the menu-bar icon appears by itself, without opening a browser window. Then:

```bash
python3 -m organizer autostart disable
ls ~/Library/LaunchAgents/com.fileorganizer.agent.plist     # should no longer exist
```

## 6. The launcher

Double-click `start_file_organizer.command`. The first time macOS may block it: **right-click, Open**. **Expect:** missing components are installed (once) and the dashboard opens.

If pip refuses to install ("externally managed environment"), the script retries with `--break-system-packages`; please note if that happens.

## What to report

If everything passes, a one-line "macOS X.Y, Python 3.Z (source): all passed" is enough, and the README status can move to *Verified*.

For any failure, please include: macOS version and chip (Intel / Apple silicon), Python version and where it came from, which step failed, and the Terminal output from `python3 -m organizer ui`.

Known places to look first if the menu-bar icon does not appear: `pystray` needs its macOS dependency (`pyobjc-framework-Quartz`, installed automatically with `pystray`), and the tray must run on the main thread (the app already does this).
