# SpaceWise

SpaceWise is a read-only Windows desktop app for understanding the contents of a folder. Sprint 1 includes a scan-driven dashboard, a filterable file explorer, and placeholder pages for future sections.

## Requirements

- Windows
- Python 3.10 or newer

## Install and run

From the project folder, create and activate a virtual environment, then install the dependency and start the app:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

If PowerShell blocks environment activation, use `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.\.venv\Scripts\python.exe main.py` instead.

SpaceWise scans only the folder explicitly selected by the user, and only after **Scan Folder** is clicked. It does not alter files, follow file or directory symlinks, or traverse Windows junctions.

## Sprint 1 test checklist

1. Launch the app and confirm the Dashboard shows an empty state without example statistics.
2. Select a small test folder, confirm its path appears, and verify selecting it alone does not start a scan.
3. Click **Scan Folder** and confirm the UI remains responsive while the scan runs.
4. Compare file count, total size, categories, scan time, category chart, and largest-file list against the contents of the selected test folder.
5. Scan an empty folder and confirm the actual result is zero files and zero size.
6. Open Explorer and test category filtering and sorting by filename and size; confirm it uses the latest scan.
7. Try a folder containing inaccessible paths and links/junctions; confirm inaccessible items are skipped and linked targets are not traversed.
8. Open Duplicates, Cleanup, and Settings and confirm each displays a Coming soon placeholder.
