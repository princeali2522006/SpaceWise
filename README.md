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

**Cleanup** lists verified duplicate groups, old installer-like files found under the current user's known folders (default age threshold: 90 days), and large files (default threshold: 1 GB). Modification time is only an approximate age indicator, not evidence of last use. Windows/application locations, virtual machine disks, databases and common active project directories are excluded from large-file recommendations. Every item requires review; installer and large-file sizes are not guaranteed savings. Only verified duplicate groups contribute to the displayed potential duplicate-savings estimate. Double-click a recommendation to open its containing folder.

## Test checklist

1. Launch SpaceWise; confirm no files are scanned until you choose a scan action.
2. Use **Select Folder** and **Scan Folder** on a small test folder. Confirm the UI stays responsive and Dashboard/Explorer show the same scan.
3. Click **Scan My Files**. Confirm only Desktop, Downloads, Documents, Pictures, Videos and Music locations are included; redirected and overlapping locations should be handled.
4. Select a local drive and choose **Scan Drive**. Compare the displayed capacity/used/free figures with Windows and distinguish those values from the scanned-file total.
5. While scanning a larger location, confirm that no changing filenames or paths appear, the file counter and elapsed time update, and progress remains indeterminate. Choose **Cancel Scan** and verify the previous completed results remain visible and labeled.
6. Scan an empty folder and a folder with zero-byte files. Confirm counts remain correct and percentages safely display as zero where total size is zero.
7. Select a Dashboard category; check size, percentage, count and largest category files, then open it in Explorer and verify its category filter.
8. Check largest-subfolder sizes/counts/order against a nested test folder, including an empty subfolder.
9. In Explorer, combine filename search with category filtering, sort by filename, size and category, and check the full-path tooltip.
10. Test inaccessible directories and, where available, symlinks/junctions or mounted volumes; confirm skipped paths are reported and not traversed.
11. On Duplicates, confirm the analysis runs only after **Find Duplicates**, uses equal-size candidates, reports verified groups and unverified files, handles hard links without inflating savings, and cancels without committing partial results. Try the personal-files limit.
12. On Cleanup, confirm the defaults are 90 days and 1 GB. Test old and recent installer-like files under a known user folder, a large ordinary file, and protected VM/database/system/project paths. Verify no total combines overlapping file sizes and double-clicking opens only the containing folder.
13. Open Settings and confirm it remains a Coming soon placeholder.

Run the focused scanner and UI tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
