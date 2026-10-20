# SpaceWise

SpaceWise is a read-only Windows desktop app for understanding storage. It supports selected-folder, known-user-folder and local-drive scans; interactive category and subfolder analysis; a searchable, sortable file explorer; background duplicate verification; and cautious review-only cleanup opportunities. The current interface keeps the dashboard, explorer, duplicates, cleanup, and scan workflows aligned so large scans remain responsive while preserving evidence about what was scanned, what was skipped, and what is still pending verification.

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

SpaceWise never starts a scan or hashes files automatically. Choose **Scan My Files**, select a drive and choose **Scan Drive**, or select a folder and choose **Scan Folder**. The user-folder mode uses Windows known-folder locations (including redirected locations where Windows reports them); overlapping locations are deduplicated. Drive scans show Windows-reported drive capacity separately from the total logical size of accessible scanned paths and skip reparse points and directories on other volumes. Long scans run in a worker thread and show an indeterminate progress panel, a throttled discovered-file count, the current folder, elapsed time and cooperative cancellation; a cancelled scan leaves the last completed results intact. Permission errors, files that change during scanning, and skipped links/reparse points are reported as incomplete coverage.

Scanned-file size is the sum of logical file sizes for each distinct scanned path, not physical disk usage. Separate hard-link paths can therefore contribute to the logical total even though they share stored file content. Empty folders appear in subfolder summaries with zero files and zero bytes. Hidden files are included; symbolic links, junctions and other reparse points are not followed. Windows drive capacity, used space and free space are separate OS-reported figures: they need not match the scanned-file total because of inaccessible items, hard links, filesystem metadata, other folders, compression and other filesystem behavior. Scanning never executes or changes user files.

Categories use filename extensions only. Installer, virtual-machine-disk, disk-image and database labels are approximate; SpaceWise does not inspect file contents. **Find Duplicates** first groups equal-size candidates and compares bounded beginning/end samples; only matching sample groups receive full, chunked SHA-256 verification. Samples never establish a duplicate. Zero-length files require no content reads, and hard-linked paths are hashed once per physical file identity. Changed or inaccessible candidates are reported as unverified. Work runs off the UI thread, progress is throttled, and cancellation discards the incomplete result while retaining completed scan results.

Completed full hashes are cached transactionally in `%LOCALAPPDATA%\SpaceWise\duplicate_hashes.sqlite3` on Windows (or the standard per-user cache directory on other platforms). The cache is outside the standard scanned personal folders, has a 32 MiB database limit and retains at most 50,000 file identities. An entry is reusable only when the device/file identity, size, modification and creation/change timestamps, bounded content sample, and a cache-record checksum still match. This metadata-based reuse cannot prove current bytes are unchanged if content and timestamps are deliberately preserved; the Duplicates page discloses this limitation. Only complete successful hashes are written. A failed or interrupted transaction is rolled back, and previously committed hashes remain reusable. Cache errors disable caching for that analysis but do not prevent verification. The cache contains no file content and is not used to automatically resume an analysis at launch.

**Cleanup** lists verified duplicate groups, older installer-like files found under the current user's known folders (default age threshold: 90 days), and large files (default minimum: 1 GB, adjustable from 1 MB). Large-file and older-installer recommendations can be generated from scan metadata without waiting for duplicate verification; duplicates remain marked pending until a completed analysis is available. Threshold units use 1024-based conversions; values below 1 GB display in MB. After the initial recommendation analysis, threshold edits re-filter the checked candidates without restarting the folder scan or file analysis. Modification time is only an approximate age indicator, not evidence of last use. Windows/application locations, virtual machine disks, databases and common active project directories are excluded from large-file recommendations. Every item requires review; an old installer or large file is not automatically unnecessary or safe to remove. Only SHA-256-verified distinct duplicate copies contribute to the separate potential-savings estimate; hard-linked paths do not count as independently recoverable space, and no copy is selected automatically. Select a row to inspect its full path, reason, triggering condition and warning; use the read-only copy-path and Explorer actions as needed. SpaceWise never deletes, moves, renames, overwrites, or executes scanned files.

## Test checklist

1. Launch SpaceWise; confirm no files are scanned until you choose a scan action.
2. Use **Select Folder** and **Scan Folder** on a small test folder. Confirm the UI stays responsive and Dashboard/Explorer show the same scan.
3. Click **Scan My Files**. Confirm only Desktop, Downloads, Documents, Pictures, Videos and Music locations are included; redirected and overlapping locations should be handled.
4. Select a local drive and choose **Scan Drive**. Compare the displayed capacity/used/free figures with Windows and distinguish those values from the scanned-file total.
5. While scanning a larger location, confirm the discovered-file counter and current-folder indicator update at a calm rate while progress remains indeterminate. Choose **Cancel Scan** and verify the previous completed results remain visible and labeled.
6. Scan an empty folder and a folder with zero-byte files. Confirm counts remain correct and percentages safely display as zero where total size is zero.
7. Select a Dashboard category; check size, percentage, count and largest category files, then open it in Explorer and verify its category filter.
8. Check largest-subfolder sizes/counts/order against a nested test folder, including an empty subfolder.
9. In Explorer, combine filename search, category filtering and the **All files / Personal files** filter. Confirm Personal files follows the Windows user-folder locations, clear search without losing the selected filters, resize columns, and sort by filename, size and category. Right-click a result and choose **Show in Windows Explorer**; verify it opens the containing folder with the file selected and does not launch the file.
10. Resize the Dashboard window and its table columns. Scroll over a Dashboard table and confirm the page scrolls smoothly while the sidebar stays stationary.
11. Test inaccessible directories and, where available, symlinks/junctions or mounted volumes; confirm skipped paths are reported and not traversed.
12. On Duplicates, confirm analysis runs only after **Find Duplicates**, unique-size files are excluded, dissimilar samples avoid full hashing, and matching samples still receive full SHA-256 verification. Check stage, candidate/processed counts, bytes read, elapsed time, skipped files, and cache reuse while work runs. Confirm hard links do not inflate savings, cached-hash limitations are disclosed, cancellation discards incomplete results, and rerunning can reuse completed cached work. Try the personal-files limit.
13. On Cleanup, confirm the defaults are 90 days and 1 GB. Start Cleanup while duplicate verification is active and confirm older-installer/large-file recommendations remain available while duplicates are marked pending. Verify the large-file threshold can be typed or adjusted with the prominent minus/plus buttons down to 1 MB, that 1023 MB displays as MB and 1024 MB as 1 GB, and that minus is disabled at 1 MB. Test threshold edits after analysis and verify recommendations update without starting a new scan. Test old and recent installer-like files under a known user folder, a large ordinary file, and protected VM/database/system/project paths. Filter recommendation categories, sort the list, inspect full paths/reasons/warnings, copy a path, and reveal a selected file in Explorer. Verify no total combines overlapping file sizes and that duplicates show only potential savings for verified distinct copies.
14. Open Settings and confirm it remains a Coming soon placeholder.

Run the focused scanner and UI tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
