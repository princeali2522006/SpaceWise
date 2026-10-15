# SpaceWise

SpaceWise is a read-only Windows desktop app for understanding storage. It supports selected-folder, known-user-folder and local-drive scans; interactive category and subfolder analysis; and a searchable, sortable file explorer. Sprint 2 adds extension-based storage categories, background duplicate verification, and cautious review-only cleanup opportunities.

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

SpaceWise never starts a scan or hashes files automatically. Choose **Scan My Files**, select a drive and choose **Scan Drive**, or select a folder and choose **Scan Folder**. The user-folder mode uses Windows known-folder locations (including redirected locations where Windows reports them); overlapping locations are deduplicated. Drive scans show Windows-reported drive capacity separately from the total size of accessible scanned files and skip reparse points and directories on other volumes. Long scans run in a worker thread and show a calm indeterminate progress panel with a file counter, elapsed time and cancellation; no changing filenames or paths are shown in progress messages. Permission errors and skipped locations are reported as incomplete results. Scanning never executes or changes user files.

Categories use filename extensions only. Installer, virtual-machine-disk, disk-image and database labels are approximate; SpaceWise does not inspect file contents. **Find Duplicates** hashes only equal-size candidates in chunks, verifies files before and after hashing, and reports inaccessible or changed files as unverified. Hard links do not count as additional recoverable copies. Duplicate detection is read-only and never selects a copy for deletion.

**Cleanup** lists verified duplicate groups, older installer-like files found under the current user's known folders (default age threshold: 90 days), and large files (default minimum: 1 GB, adjustable from 1 MB). Threshold units use 1024-based conversions; values below 1 GB display in MB. After the initial recommendation analysis, threshold edits re-filter the checked candidates without restarting the folder scan or file analysis. Modification time is only an approximate age indicator, not evidence of last use. Windows/application locations, virtual machine disks, databases and common active project directories are excluded from large-file recommendations. Every item requires review; an old installer or large file is not automatically unnecessary or safe to remove. Only SHA-256-verified distinct duplicate copies contribute to the separate potential-savings estimate; hard-linked paths do not count as independently recoverable space, and no copy is selected automatically. Select a row to inspect its full path, reason, triggering condition and warning; use the read-only copy-path and Explorer actions as needed. SpaceWise never deletes, moves, renames, overwrites, or executes scanned files.

## Test checklist

1. Launch SpaceWise; confirm no files are scanned until you choose a scan action.
2. Use **Select Folder** and **Scan Folder** on a small test folder. Confirm the UI stays responsive and Dashboard/Explorer show the same scan.
3. Click **Scan My Files**. Confirm only Desktop, Downloads, Documents, Pictures, Videos and Music locations are included; redirected and overlapping locations should be handled.
4. Select a local drive and choose **Scan Drive**. Compare the displayed capacity/used/free figures with Windows and distinguish those values from the scanned-file total.
5. While scanning a larger location, confirm that no changing filenames or paths appear, the file counter and elapsed time update, and progress remains indeterminate. Choose **Cancel Scan** and verify the previous completed results remain visible and labeled.
6. Scan an empty folder and a folder with zero-byte files. Confirm counts remain correct and percentages safely display as zero where total size is zero.
7. Select a Dashboard category; check size, percentage, count and largest category files, then open it in Explorer and verify its category filter.
8. Check largest-subfolder sizes/counts/order against a nested test folder, including an empty subfolder.
9. In Explorer, combine filename search, category filtering and the **All files / Personal files** filter. Confirm Personal files follows the Windows user-folder locations, clear search without losing the selected filters, resize columns, and sort by filename, size and category. Right-click a result and choose **Show in Windows Explorer**; verify it opens the containing folder with the file selected and does not launch the file.
10. Resize the Dashboard window and its table columns. Scroll over a Dashboard table and confirm the page scrolls smoothly while the sidebar stays stationary.
11. Test inaccessible directories and, where available, symlinks/junctions or mounted volumes; confirm skipped paths are reported and not traversed.
12. On Duplicates, confirm the analysis runs only after **Find Duplicates**, uses equal-size candidates, reports verified groups and unverified files, handles hard links without inflating savings, and cancels without committing partial results. Try the personal-files limit.
13. On Cleanup, confirm the defaults are 90 days and 1 GB. Verify the large-file threshold can be typed or adjusted with the prominent minus/plus buttons down to 1 MB, that 1023 MB displays as MB and 1024 MB as 1 GB, and that minus is disabled at 1 MB. Test threshold edits after analysis and verify recommendations update without starting a new scan. Test old and recent installer-like files under a known user folder, a large ordinary file, and protected VM/database/system/project paths. Filter recommendation categories, sort the list, inspect full paths/reasons/warnings, copy a path, and reveal a selected file in Explorer. Verify no total combines overlapping file sizes and that duplicates show only potential savings for verified distinct copies.
14. Open Settings and confirm it remains a Coming soon placeholder.

Run the focused scanner and UI tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
