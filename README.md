# SpaceWise

SpaceWise is a read-only Windows desktop app for understanding storage. Sprint 1.1 includes interactive category details and immediate-subfolder analysis, a searchable and sortable file explorer, plus three explicit scan modes: selected folder, the current user's known folders, and an available local drive.

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

SpaceWise never starts a scan automatically. Choose **Scan My Files**, select a drive and choose **Scan Drive**, or select a folder and choose **Scan Folder**. The user-folder mode uses Windows known-folder locations (including redirected locations where Windows reports them); overlapping locations are deduplicated. Drive scans show Windows-reported drive capacity separately from the total size of accessible scanned files and skip reparse points and directories on other volumes. Long scans run in a worker thread, show their current location, and can be cancelled. Permission errors and skipped locations are reported as incomplete results. Scanning never executes or changes user files.

## Test checklist

1. Launch SpaceWise; confirm no files are scanned until you choose a scan action.
2. Use **Select Folder** and **Scan Folder** on a small test folder. Confirm the UI stays responsive and Dashboard/Explorer show the same scan.
3. Click **Scan My Files**. Confirm only Desktop, Downloads, Documents, Pictures, Videos and Music locations are included; redirected and overlapping locations should be handled.
4. Select a local drive and choose **Scan Drive**. Compare the displayed capacity/used/free figures with Windows and distinguish those values from the scanned-file total.
5. While scanning a larger location, confirm current-location progress appears. Choose **Cancel Scan** and verify the previous completed results remain visible and labeled.
6. Scan an empty folder and a folder with zero-byte files. Confirm counts remain correct and percentages safely display as zero where total size is zero.
7. Select a Dashboard category; check size, percentage, count and largest category files, then open it in Explorer and verify its category filter.
8. Check largest-subfolder sizes/counts/order against a nested test folder, including an empty subfolder.
9. In Explorer, combine filename search with category filtering, sort by filename, size and category, and check the full-path tooltip.
10. Test inaccessible directories and, where available, symlinks/junctions or mounted volumes; confirm skipped paths are reported and not traversed.
11. Open Duplicates, Cleanup and Settings and confirm each remains a Coming soon placeholder.

Run the focused scanner and UI tests with:

```powershell
python -m unittest discover -s tests -v
```
