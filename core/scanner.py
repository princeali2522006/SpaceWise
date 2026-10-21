"""Read-only recursive scanning and scan-result aggregation."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import heapq
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Iterable

from core.categorizer import categorize_file


@dataclass(frozen=True)
class ScanProgress:
    files_scanned: int
    elapsed_seconds: float
    current_directory: str = ""


ProgressCallback = Callable[[ScanProgress], None]


@dataclass(frozen=True)
class DriveUsage:
    total: int
    used: int
    free: int


@dataclass(frozen=True)
class ScannedFile:
    name: str
    path: str
    size: int
    category: str


@dataclass(frozen=True)
class CategorySummary:
    name: str
    size: int
    file_count: int
    largest_files: tuple[ScannedFile, ...]


@dataclass(frozen=True)
class SubfolderSummary:
    name: str
    path: str
    size: int
    file_count: int


@dataclass(frozen=True)
class ScanResult:
    root_path: str
    files: tuple[ScannedFile, ...]
    categories: tuple[CategorySummary, ...]
    subfolders: tuple[SubfolderSummary, ...]
    subfolder_count: int
    largest_files: tuple[ScannedFile, ...]
    scanned_size: int
    elapsed_seconds: float
    inaccessible_items: int
    inaccessible_directories: int
    skipped_directories: int
    scan_scope: str
    scan_roots: tuple[str, ...]
    drive_usage: DriveUsage | None = None
    hard_link_paths: int = 0
    skipped_files: int = 0
    requested_roots: tuple[str, ...] = ()
    completed_at: str = ""

    @property
    def total_size(self) -> int:
        return self.scanned_size

    @property
    def category_sizes(self) -> dict[str, int]:
        return {category.name: category.size for category in self.categories}

    @property
    def display_scope(self) -> str:
        if self.scan_scope == "Selected folder":
            return f"Folder: {self.root_path}"
        if self.scan_scope == "Scan My Files":
            return f"My Files · {len(self.scan_roots)} folder location(s) scanned"
        return self.scan_scope


class ScanCancelledError(Exception):
    """Raised when the user cancels an in-progress scan."""


def _extended_path(path: str) -> str:
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _display_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(_display_path(path)))


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    junction_check = getattr(entry, "is_junction", None)
    if junction_check is not None and junction_check():
        return True
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except AttributeError:
        return False
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def _is_reparse_stat(file_stat: os.stat_result) -> bool:
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0) & reparse_point
    )


def _normalize_roots(folders: Iterable[str | Path]) -> tuple[str, ...]:
    candidates: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        absolute = os.path.abspath(os.fspath(Path(folder).expanduser()))
        key = os.path.normcase(os.path.normpath(absolute))
        if key not in seen:
            seen.add(key)
            candidates.append(absolute)
    return tuple(candidates)


def scan_folders(
    folders: Iterable[str | Path],
    *,
    scan_scope: str,
    drive_usage: DriveUsage | None = None,
    stay_on_volume: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    strict_roots: bool = False,
) -> ScanResult:
    """Scan one or more roots in a single pass, deduplicating paths and summaries."""
    roots = _normalize_roots(folders)
    if not roots:
        raise ValueError("No scan locations were found.")

    started = time.perf_counter()
    files: list[ScannedFile] = []
    categories_size: dict[str, int] = defaultdict(int)
    categories_count: dict[str, int] = defaultdict(int)
    categories_files: dict[str, list[ScannedFile]] = defaultdict(list)
    folders_size: dict[str, int] = defaultdict(int)
    folders_count: dict[str, int] = defaultdict(int)
    folders_name: dict[str, str] = {}
    seen_files: set[str] = set()
    seen_directories: set[str] = set()
    seen_inaccessible: set[str] = set()
    seen_skipped_directories: set[str] = set()
    seen_skipped_files: set[str] = set()
    seen_file_identities: set[tuple[int, int]] = set()
    hard_link_paths = 0
    skipped_files = 0
    scanned_size = 0
    inaccessible_items = 0
    inaccessible_directories = 0
    skipped_directories = 0
    last_progress_at = 0.0
    last_current_directory = ""

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ScanCancelledError("Scan cancelled. The last completed scan is unchanged.")

    def checked_files(items: Iterable[ScannedFile]) -> Iterable[ScannedFile]:
        for index, item in enumerate(items):
            if index % 1024 == 0:
                check_cancelled()
            yield item

    def record_inaccessible(path: str, *, directory: bool = False) -> None:
        nonlocal inaccessible_items, inaccessible_directories
        key = _path_key(path)
        if key in seen_inaccessible:
            return
        seen_inaccessible.add(key)
        inaccessible_items += 1
        if directory:
            inaccessible_directories += 1

    def record_skipped_directory(path: str) -> None:
        nonlocal skipped_directories
        key = _path_key(path)
        if key not in seen_skipped_directories:
            seen_skipped_directories.add(key)
            skipped_directories += 1

    def record_skipped_file(path: str) -> None:
        nonlocal skipped_files
        key = _path_key(path)
        if key not in seen_skipped_files:
            seen_skipped_files.add(key)
            skipped_files += 1

    def report_progress(*, force: bool = False, current_directory: str = "") -> None:
        nonlocal last_progress_at, last_current_directory
        if progress_callback is None:
            return
        if current_directory:
            last_current_directory = _display_path(current_directory)
        now = time.monotonic()
        if force or now - last_progress_at >= 0.5:
            progress_callback(
                ScanProgress(
                    files_scanned=len(files),
                    elapsed_seconds=time.perf_counter() - started,
                    current_directory=last_current_directory,
                )
            )
            last_progress_at = now

    report_progress(force=True)
    valid_roots: list[str] = []
    for root_path in roots:
        check_cancelled()
        io_root = _extended_path(root_path)
        try:
            root_stat = os.lstat(io_root)
        except OSError as error:
            if strict_roots:
                raise OSError(f"Cannot access the selected folder: {error}") from error
            record_inaccessible(root_path, directory=True)
            continue
        if _is_reparse_stat(root_stat):
            if strict_roots:
                raise OSError("The selected folder is a link or junction and will not be scanned.")
            record_skipped_directory(root_path)
            continue
        if not stat.S_ISDIR(root_stat.st_mode):
            if strict_roots:
                raise NotADirectoryError(f"Selected path is not a folder: {root_path}")
            record_inaccessible(root_path)
            continue

        valid_roots.append(root_path)
        root_device = root_stat.st_dev
        pending = [io_root]
        while pending:
            check_cancelled()
            current = pending.pop()
            current_key = _path_key(current)
            if current_key in seen_directories:
                continue
            try:
                entries = os.scandir(current)
            except OSError:
                record_inaccessible(current, directory=True)
                continue
            seen_directories.add(current_key)
            report_progress(current_directory=current)

            with entries:
                try:
                    for entry in entries:
                        check_cancelled()
                        try:
                            if entry.is_symlink() or _is_reparse_point(entry):
                                entry_stat = entry.stat(follow_symlinks=False)
                                directory_attribute = getattr(
                                    stat,
                                    "FILE_ATTRIBUTE_DIRECTORY",
                                    0x10,
                                )
                                is_directory = stat.S_ISDIR(entry_stat.st_mode) or bool(
                                    getattr(entry_stat, "st_file_attributes", 0)
                                    & directory_attribute
                                )
                                if is_directory:
                                    record_skipped_directory(entry.path)
                                else:
                                    record_skipped_file(entry.path)
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                entry_stat = (
                                    os.stat(entry.path, follow_symlinks=False)
                                    if stay_on_volume
                                    else entry.stat(follow_symlinks=False)
                                )
                                if stay_on_volume and entry_stat.st_dev != root_device:
                                    record_skipped_directory(entry.path)
                                    continue
                                pending.append(entry.path)
                                relative_path = os.path.relpath(entry.path, io_root)
                                parts = Path(relative_path).parts
                                if len(parts) == 1:
                                    folder_key = _path_key(entry.path)
                                    folders_size.setdefault(folder_key, 0)
                                    folders_count.setdefault(folder_key, 0)
                                    folders_name[folder_key] = entry.name
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            file_stat = (
                                os.stat(entry.path, follow_symlinks=False)
                                if stay_on_volume or os.name == "nt"
                                else entry.stat(follow_symlinks=False)
                            )
                            if stay_on_volume and file_stat.st_dev != root_device:
                                continue
                            file_path = _display_path(entry.path)
                            file_key = _path_key(file_path)
                            if file_key in seen_files:
                                continue
                            seen_files.add(file_key)
                            scanned_size += file_stat.st_size
                            identity = (file_stat.st_dev, file_stat.st_ino)
                            if file_stat.st_nlink > 1 and file_stat.st_ino:
                                if identity in seen_file_identities:
                                    hard_link_paths += 1
                                else:
                                    seen_file_identities.add(identity)

                            category = categorize_file(entry.name)
                            scanned_file = ScannedFile(
                                name=entry.name,
                                path=file_path,
                                size=file_stat.st_size,
                                category=category,
                            )
                            files.append(scanned_file)
                            categories_files[category].append(scanned_file)
                            categories_size[category] += file_stat.st_size
                            categories_count[category] += 1

                            relative_path = os.path.relpath(entry.path, io_root)
                            path_parts = Path(relative_path).parts
                            if len(path_parts) > 1:
                                immediate_path = os.path.join(io_root, path_parts[0])
                                folder_key = _path_key(immediate_path)
                                folders_size[folder_key] += file_stat.st_size
                                folders_count[folder_key] += 1
                                folders_name.setdefault(folder_key, path_parts[0])
                            if len(files) % 128 == 0:
                                report_progress()
                        except OSError:
                            record_inaccessible(entry.path)
                except OSError:
                    record_inaccessible(current, directory=True)

    check_cancelled()
    categories = tuple(
        CategorySummary(
            name=name,
            size=categories_size[name],
            file_count=categories_count[name],
            largest_files=tuple(
                heapq.nsmallest(
                    5,
                    categories_files[name],
                    key=lambda file: (-file.size, file.name.casefold(), file.path.casefold()),
                )
            ),
        )
        for name in sorted(categories_count)
    )
    check_cancelled()
    def subfolder_summaries() -> Iterable[SubfolderSummary]:
        for index, (folder_key, size) in enumerate(folders_size.items()):
            if index % 1024 == 0:
                check_cancelled()
            yield SubfolderSummary(
                name=folders_name[folder_key],
                path=folder_key,
                size=size,
                file_count=folders_count[folder_key],
            )

    subfolders = tuple(
        heapq.nsmallest(
            10,
            subfolder_summaries(),
            key=lambda folder: (-folder.size, folder.name.casefold(), folder.path.casefold()),
        )
    )
    check_cancelled()
    largest_files = tuple(
        heapq.nsmallest(
            10,
            checked_files(files),
            key=lambda file: (-file.size, file.name.casefold(), file.path.casefold()),
        )
    )
    check_cancelled()
    files_result = tuple(files)
    check_cancelled()

    if len(valid_roots) == 1 and scan_scope == "Selected folder":
        root_path = str(Path(valid_roots[0]).resolve())
    elif scan_scope == "Scan My Files":
        root_path = scan_scope
    else:
        root_path = scan_scope
    report_progress(
        force=True,
        current_directory=pending[-1] if pending else (valid_roots[-1] if valid_roots else ""),
    )

    completed_at = datetime.now(timezone.utc).isoformat()
    elapsed_seconds = time.perf_counter() - started
    return ScanResult(
        root_path=root_path,
        files=files_result,
        categories=categories,
        subfolders=subfolders,
        subfolder_count=len(folders_size),
        largest_files=largest_files,
        scanned_size=scanned_size,
        elapsed_seconds=elapsed_seconds,
        inaccessible_items=inaccessible_items,
        inaccessible_directories=inaccessible_directories,
        skipped_directories=skipped_directories,
        scan_scope=scan_scope,
        scan_roots=tuple(valid_roots),
        drive_usage=drive_usage,
        hard_link_paths=hard_link_paths,
        skipped_files=skipped_files,
        requested_roots=roots,
        completed_at=completed_at,
    )


def scan_folder(
    folder: str | Path,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanResult:
    """Scan a selected directory, without following links or reparse points."""
    return scan_folders(
        (folder,),
        scan_scope="Selected folder",
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        strict_roots=True,
    )
