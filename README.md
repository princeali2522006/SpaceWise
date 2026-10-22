# SpaceWise

SpaceWise is a read-only Windows desktop app for understanding storage. Its six pages provide selected-folder, known-user-folder and local-drive scans; interactive category and subfolder analysis; a searchable, sortable file explorer; background duplicate verification; cautious review-only cleanup opportunities; and persistent Scan History. The pages stay aligned while preserving evidence about what was scanned, what was skipped, and what is still pending verification.

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

## Sprint 3.1 scanning architecture

All scans use the shared `ScanResult` contract and currently execute through `RecursiveScanner`, which wraps the existing recursive traversal. Folder and known-user-folder scans always use this backend. A whole-drive scan checks the filesystem name with the read-only Windows `GetVolumeInformationW` API. Non-NTFS volumes use **Standard scan**; an NTFS volume is explicitly labeled **Standard scan · fast NTFS scan unavailable**. Detection errors also fall back to the standard scanner. This build does **not** implement an NTFS/MFT/USN fast scanner and makes no claim of NTFS scan-speed improvement.

`FSCTL_ENUM_USN_DATA` was not enabled because its records do not provide all of SpaceWise's required file sizes and directory-entry semantics. Correct reconstruction would require additional file-ID metadata lookups and careful handling of hard links, inaccessible paths, reparse points, and files changing during enumeration. Treating an incomplete enumeration as a complete scan could silently corrupt category totals, History, Cleanup and duplicate candidates. The reliable current alternative is the standard recursive scanner, which uses the access permissions of the running user, reports inaccessible/skipped entries and does not request elevation. The app does not modify the USN journal or filesystem permissions.

The scanner counts directory-entry paths, not unique physical file objects: two hard-link paths each contribute their logical file size, and are separately counted in the hard-link note. It avoids following reparse points. As with any live filesystem walk, changes made by other applications during a scan may affect which metadata is observed; there is no point-in-time filesystem snapshot. Results record the backend label in History, and the progress panel displays the active backend without showing rapidly changing paths.

The recursive traversal now obtains each entry's non-following metadata once on the supported platform path, reusing it for reparse detection, type, size and hard-link identity. This reduces redundant metadata requests while retaining the existing result semantics. It does not make an NTFS-fast-scan claim.

### Reproducible scanner benchmark

Run `.\.venv\Scripts\python.exe -m benchmarks.scan_benchmark` from the project directory to generate small (100 files), medium (2,000 files) and larger (10,000 files) temporary fixtures. The report includes backend, entry count, logical size, elapsed wall time, peak Python-traced allocation, inaccessible entries and skipped paths. Temporary benchmark files are deleted by the test harness; scanned files are only read for metadata. To benchmark a bounded existing test folder, use `--root <folder>`. The helper refuses a volume root. It does not launch a full-drive scan.

NTFS and recursive result comparisons cannot be reported until a reliable NTFS backend is implemented. For any future comparison, run both backends against the same unchanged controlled fixture and compare file paths/count, logical size, categories, hierarchy, hard links and warnings. Do not compare scans of different locations or infer a speedup from the prior full-drive timing.

Categories use filename extensions only. Installer, virtual-machine-disk, disk-image and database labels are approximate; SpaceWise does not inspect file contents. **Find Duplicates** first groups equal-size candidates and compares bounded beginning/end samples; only matching sample groups receive full, chunked SHA-256 verification. Samples never establish a duplicate. Zero-length files require no content reads, and hard-linked paths are hashed once per physical file identity. Changed or inaccessible candidates are reported as unverified. Work runs off the UI thread, progress is throttled, and cancellation discards the incomplete result while retaining completed scan results.

Completed full hashes are cached transactionally in `%LOCALAPPDATA%\SpaceWise\duplicate_hashes.sqlite3` on Windows (or the standard per-user cache directory on other platforms). The cache is outside the standard scanned personal folders, has a 32 MiB database limit and retains at most 50,000 file identities. An entry is reusable only when the device/file identity, size, modification and creation/change timestamps, bounded content sample, and a cache-record checksum still match. This metadata-based reuse cannot prove current bytes are unchanged if content and timestamps are deliberately preserved; the Duplicates page discloses this limitation. Only complete successful hashes are written. A failed or interrupted transaction is rolled back, and previously committed hashes remain reusable. Cache errors disable caching for that analysis but do not prevent verification. The cache contains no file content and is not used to automatically resume an analysis at launch.

**Cleanup** lists verified duplicate groups, older installer-like files found under the current user's known folders (default age threshold: 90 days), and large files (default minimum: 1 GB, adjustable from 1 MB). Large-file and older-installer recommendations can be generated from scan metadata without waiting for duplicate verification; duplicates remain marked pending until a completed analysis is available. Threshold units use 1024-based conversions; values below 1 GB display in MB. After the initial recommendation analysis, threshold edits re-filter the checked candidates without restarting the folder scan or file analysis. Modification time is only an approximate age indicator, not evidence of last use. Windows/application locations, virtual machine disks, databases and common active project directories are excluded from large-file recommendations. Every item requires review; an old installer or large file is not automatically unnecessary or safe to remove. Only SHA-256-verified distinct duplicate copies contribute to the separate potential-savings estimate; hard-linked paths do not count as independently recoverable space, and no copy is selected automatically. Select a row to inspect its full path, reason, triggering condition and warning; use the read-only copy-path and Explorer actions as needed. SpaceWise never deletes, moves, renames, overwrites, or executes scanned files.

## Scan History

Each successfully completed scan is saved automatically as metadata in `%LOCALAPPDATA%\SpaceWise\scan_history.sqlite3` on Windows (or the per-user state directory on other platforms). In-progress and cancelled scans are not saved. The database contains file names, paths, sizes, categories and scan summaries, but no file contents. History can be searched and sorted. Open Scan displays the saved information in Dashboard and Explorer; the banner identifies the active Saved Scan and provides **Exit Saved Scan** and **Rescan Location** actions.

Saved Scan data is historical and may no longer match current files. Duplicate verification and Cleanup analysis remain unavailable until rescanning. Exiting Saved Scan restores the most recent fresh scan still in memory, or returns to the ready-to-scan Dashboard when no fresh scan is available. It does not rescan or alter History. Rescan Location uses the saved locations and scan mode where possible and creates a separate History entry; the previous entry remains. Drive-capacity figures describe the original scan time.

History loading and saving run outside the Qt UI thread, and each save uses a transaction so interruption cannot expose a partially committed scan. The database is app-owned and may grow as scans are saved; remove entries from History to reclaim their data. Records whose file count or logical byte total does not match their saved file rows remain visible with an integrity explanation, but cannot be opened. SpaceWise does not invent missing rows or silently adjust saved totals. Inaccessible paths encountered during the original scan are recorded as coverage metadata; saved scans do not retest their availability until rescanned. Older schemas are migrated additively when their required data is available; incompatible or inconsistent records are preserved and reported rather than deleted.

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
12. On Duplicates, confirm analysis runs only after **Find Duplicates**, unique-size files are excluded, dissimilar samples avoid full hashing, and matching samples still receive full SHA-256 verification. Check stage, candidate/processed counts, bytes read, elapsed time, skipped files, and cache reuse while work runs. Confirm hard links do not inflate savings, cached-hash limitations are disclosed, cancellation discards incomplete results, and rerunning can reuse completed hashes.
13. On Cleanup, confirm the defaults are 90 days and 1 GB. Start Cleanup while duplicate verification is active and confirm older-installer/large-file recommendations remain available while duplicates are marked pending. Verify the large-file threshold can be typed or adjusted down to 1 MB, that 1023 MB displays as MB and 1024 MB as 1 GB, and that minus is disabled at 1 MB. Test threshold edits after analysis; inspect paths, reasons and warnings, copy a path, and reveal a selected file in Explorer. Verify category savings are not combined into a misleading total.
14. Open **History** and confirm the completed scan appears. Search and sort, press Enter to confirm the matching-scan count, then Open Scan. Verify the Saved Scan banner persists while navigating, analysis is disabled with a rescan explanation, Exit Saved Scan restores the fresh result (or a ready state), and Rescan Location creates a separate entry.
15. Remove a saved scan and confirm only its History data disappears; verify the original files remain unchanged. Open **Settings** and confirm it remains a Coming soon placeholder.

Run the focused scanner and UI tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
