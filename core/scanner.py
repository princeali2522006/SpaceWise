"""Read-only recursive folder scanner."""

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time

from core.categorizer import categorize_file


@dataclass(frozen=True)
class ScannedFile:
    name: str
    path: str
    size: int
    category: str


@dataclass(frozen=True)
class ScanResult:
    root_path: str
    files: tuple[ScannedFile, ...]
    elapsed_seconds: float
    inaccessible_items: int

    @property
    def total_size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def category_sizes(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for file in self.files:
            totals[file.category] = totals.get(file.category, 0) + file.size
        return totals


def _is_junction(entry: os.DirEntry[str]) -> bool:
    """Detect Windows junctions/reparse points without following them."""
    junction_check = getattr(entry, "is_junction", None)
    if junction_check is not None and junction_check():
        return True
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except AttributeError:
        return False
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_point)


def scan_folder(folder: str | Path) -> ScanResult:
    """Scan regular files under an explicitly selected folder, without following links."""
    root = Path(folder).expanduser()
    try:
        root_stat = root.lstat()
    except OSError as error:
        raise OSError(f"Cannot access the selected folder: {error}") from error
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if stat.S_ISLNK(root_stat.st_mode) or getattr(root_stat, "st_file_attributes", 0) & reparse_point:
        raise OSError("The selected folder is a link or junction and will not be scanned.")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise NotADirectoryError(f"Selected path is not a folder: {root}")

    started = time.perf_counter()
    files: list[ScannedFile] = []
    inaccessible = 0
    pending = [str(root)]

    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            inaccessible += 1
            continue

        with entries:
            try:
                for entry in entries:
                    try:
                        if entry.is_symlink() or _is_junction(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        file_stat = entry.stat(follow_symlinks=False)
                        files.append(
                            ScannedFile(
                                name=entry.name,
                                path=entry.path,
                                size=file_stat.st_size,
                                category=categorize_file(entry.name),
                            )
                        )
                    except OSError:
                        inaccessible += 1
            except OSError:
                inaccessible += 1

    return ScanResult(
        root_path=str(root.resolve()),
        files=tuple(files),
        elapsed_seconds=time.perf_counter() - started,
        inaccessible_items=inaccessible,
    )
