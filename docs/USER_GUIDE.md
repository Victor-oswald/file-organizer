# File Organizer: User Guide

File Organizer watches the folders you choose and gives new files clear names.
It **never deletes or overwrites anything**, and **every change can be undone**.

## 1. Starting it

You need Python 3.10 or newer, and once: `pip install -r requirements.txt`.

| Your computer | How to start |
|---|---|
| Windows | Double-click **Start File Organizer.bat** |
| Mac | Double-click **start_file_organizer.command** (first time: right-click, Open) |
| Linux | Run **start_file_organizer.sh** |

The first time, the launcher installs the small components it needs (this needs an internet connection once).
The dashboard then opens in your browser. It runs only on your computer; nothing is sent anywhere.
Closing the tab does not stop it: **monitoring continues in the background.**

## 1a. Finding the app again (the tray icon)

While running, File Organizer has an icon next to the clock:

| System | Where | Tip |
|---|---|---|
| Windows | Notification area, bottom-right. **New icons are often hidden under the ^ arrow**: drag the icon out to keep it visible. | |
| macOS | Menu bar, top-right | |
| Linux | System tray / top bar (some desktops need an "AppIndicator" extension) | |

- **Click the icon** to open the dashboard.
- **Right-click** for: current status, *Pause / Resume monitoring*, *Check folders now*, *Quit*.
- Hover over it to see what it is doing right now.
- A short notification says where the icon is when it starts.

If your system has no tray, a **small control window** stays in the taskbar instead (closing it only minimizes it). And whatever happens, **starting File Organizer again re-opens the dashboard** (it never starts a second copy). *Settings* shows which of these is active.

## 2. Knowing what it is doing

The **status bar** at the bottom of the window always tells you:

| You see | It means |
|---|---|
| ● *Watching 2 folders. Idle. Last check 2s ago, next in 3s.* | Working normally. The green dot **pulses on every check**. |
| *… 1 new file waiting to stop changing* | A file just appeared; I wait a few seconds so I never touch a half-copied file. |
| *Analyzing files 14/40: IMG_0042.jpg (Downloads)* + progress bar | Busy right now. |
| *Monitoring is paused* | Nothing is being watched (toolbar button). |
| *Lost contact with File Organizer* | It was closed. Start it again. |

The **Activity** tab lists everything as it happens, one line per file, with the reason:

| Event | Meaning |
|---|---|
| **New** | A file appeared; waiting for it to finish. |
| **Awaiting OK** | I propose a change; waiting for you (see *Awaiting approval*). |
| **Renamed / Moved / Set aside** | Done. *Set aside* = moved to `_Review_Needed`, name unchanged, because I wasn't sure. |
| **Left alone** | I looked and changed nothing. The reason is shown, for example "name already clean". |
| **Ignored** | Not touched, for example still downloading. |
| **Duplicate** | An exact copy of another file. |
| **Problem** | Something failed; the message says what. |

## 3. Add a folder

**Add folder**, then **Browse…** and pick one (Downloads is a good first choice). The dialog tells you
**how many files the folder already contains** and asks what to do with them:

| Setting | Options |
|---|---|
| **Files already in it** | **Organize them now** (default) or *Leave them alone; only organize new files from now on*. |
| **New files** | *Ask me first, then apply* (default) or *Apply automatically*. |
| **How careful** | *Cautious* (only when very sure), *Balanced*, *Eager*. |
| **Exact copies** | *Only report them* or *Move extra copy to `_Duplicates`*. |
| **Subfolders** | Include or not. |

With *Ask me first*, organizing the existing files only **proposes** changes: you review them under **Awaiting approval** and nothing is touched until you apply. With *Apply automatically* they are renamed right away (undo from **History**).

Changed your mind about the existing files? Open the folder, then **Folder settings, Organize existing files…** at any time. The result is always reported in **Activity** (for example *"Checked 142 existing files: 38 changes proposed, 104 left alone"*), even when there is nothing to change.

Change any other setting later under **Folder settings** (they save immediately).

## 4. Everyday use

- **Awaiting approval**: proposed changes from folders set to "Ask me first". Untick anything you want left alone, then **Apply selected**, or **Skip all**.
- **History**: every batch with an **Undo** button.
- **Check now** looks immediately instead of waiting for the next check.
- **Settings**: start at sign-in, how often to look (2 s to 5 min), light/dark.

## 5. "Why wasn't my file renamed?"

Look in **Activity** (search for the file name). The line says why:

| Reason shown | What to do |
|---|---|
| *"…existing files are ignored"* (logged when you added the folder with "Leave them alone") | **Folder settings, Organize existing files…** |
| *name already clean; preserved* | Nothing: it already had a good name. |
| *confidence … below …* / Set aside | I wasn't sure. Try **Eager**, or rename it yourself. |
| *Still downloading* | It will be handled when it finishes. |
| Not listed at all | It may be in a software-project folder (skipped on purpose) or a hidden file. |
| Waiting under **Awaiting approval** | The folder is set to "Ask me first": press Apply. |

## 6. Safety

- Nothing is deleted or overwritten. Undo restores everything.
- Files still being written are left alone.
- Software project folders and system folders (drive roots, your home folder, Windows, Program Files...) are never touched.
- Folders starting with `_` (`_Review_Needed`, `_Duplicates`, `_Events`, `_Photo_Groups`, `_Organizer_Logs`) belong to the program.

Settings are stored in: Windows `%APPDATA%\FileOrganizer`, Mac `~/Library/Application Support/FileOrganizer`, Linux `~/.config/file-organizer`.
