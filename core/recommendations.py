"""Read-only, review-only storage opportunity analysis."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Iterable, Sequence

from core.duplicates import DuplicateAnalysis, DuplicateGroup
from core.scanner import ScannedFile


RecommendationProgressCallback = Callable[["RecommendationProgress"], None]
PROTECTED_CATEGORIES = frozenset({"Virtual Machine Disks", "Databases"})
PROTECTED_DIRECTORY_NAMES = frozenset(
    {
        "$recycle.bin",
        "bin",
        "build",
        "common files",
        "dist",
        "env",
        "node_modules",
        "obj",
        "program files",
        "program files (x86)",
        "programdata",
        "system volume information",
        "target",
        "venv",
        "virtualenv",
        "windows",
        ".git",
        ".svn",
        ".venv",
    }
)


@dataclass(frozen=True)
class RecommendationProgress:
    files_checked: int
    candidate_files: int
    elapsed_seconds: float


@dataclass(frozen=True)
class FileReviewOpportunity:
    file: ScannedFile
    modified_days_ago: int


@dataclass(frozen=True)
class CleanupRecommendations:
    verified_duplicates: tuple[DuplicateGroup, ...]
    old_installers: tuple[FileReviewOpportunity, ...]
    large_files: tuple[FileReviewOpportunity, ...]
    files_checked: int
    inaccessible_paths: tuple[tuple[str, str], ...]
    duplicate_analysis_pending: bool

    @property
    def duplicate_recoverable_bytes(self) -> int:
        return sum(group.recoverable_bytes for group in self.verified_duplicates)


class RecommendationAnalysisCancelled(Exception):
    """Raised when review-opportunity analysis is cancelled."""


def _extended_path(path: str) -> str:
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _inside(path: str, roots: Sequence[str]) -> bool:
    candidate = os.path.normcase(os.path.abspath(path))
    for root in roots:
        normalized_root = os.path.normcase(os.path.abspath(root))
        try:
            if os.path.commonpath((candidate, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _default_protected_roots() -> tuple[str, ...]:
    environment_keys = (
        "SystemRoot",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "ProgramData",
        "APPDATA",
        "LOCALAPPDATA",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
    )
    return tuple(
        os.path.abspath(value)
        for key in environment_keys
        if (value := os.environ.get(key))
    )


def _protected_path(path: str, protected_roots: Sequence[str]) -> bool:
    segments = {part.casefold() for part in Path(os.path.abspath(path)).parts}
    if segments & PROTECTED_DIRECTORY_NAMES:
        return True
    return _inside(path, protected_roots)


def _file_age_days(file: ScannedFile, *, now: datetime) -> int:
    file_stat = os.lstat(_extended_path(file.path))
    if stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise OSError("Linked or reparse-point files are not recommended.")
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != file.size:
        raise OSError("File is no longer the same size as in the completed scan.")
    modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
    return max(0, int((now - modified_at).total_seconds() // 86400))


def build_cleanup_recommendations(
    files: Iterable[ScannedFile],
    *,
    personal_roots: Sequence[str],
    age_threshold_days: int = 90,
    large_file_threshold_bytes: int = 1024**3,
    duplicate_analysis: DuplicateAnalysis | None = None,
    protected_roots: Sequence[str] | None = None,
    now: datetime | None = None,
    progress_callback: RecommendationProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> CleanupRecommendations:
    """Build conservative review opportunities without suggesting file removal."""
    if age_threshold_days < 1:
        raise ValueError("The installer age threshold must be at least one day.")
    if large_file_threshold_bytes < 1:
        raise ValueError("The large-file threshold must be at least one byte.")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    protected = tuple(protected_roots) if protected_roots is not None else _default_protected_roots()
    if cancel_event is not None and cancel_event.is_set():
        raise RecommendationAnalysisCancelled("Recommendation analysis cancelled.")

    files_to_inspect: list[ScannedFile] = []
    for file in files:
        if cancel_event is not None and cancel_event.is_set():
            raise RecommendationAnalysisCancelled("Recommendation analysis cancelled.")
        if _protected_path(file.path, protected) or file.category in PROTECTED_CATEGORIES:
            continue
        installer_candidate = (
            file.category == "Installers" and _inside(file.path, personal_roots)
        )
        large_candidate = file.size >= large_file_threshold_bytes
        if installer_candidate or large_candidate:
            files_to_inspect.append(file)

    installers: list[FileReviewOpportunity] = []
    large_files: list[FileReviewOpportunity] = []
    inaccessible: list[tuple[str, str]] = []
    started = time.perf_counter()
    for index, file in enumerate(files_to_inspect, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise RecommendationAnalysisCancelled("Recommendation analysis cancelled.")
        try:
            age_days = _file_age_days(file, now=current_time)
        except OSError as error:
            inaccessible.append((file.path, str(error)))
        else:
            if (
                file.category == "Installers"
                and _inside(file.path, personal_roots)
                and age_days >= age_threshold_days
            ):
                installers.append(FileReviewOpportunity(file, age_days))
            if file.size >= large_file_threshold_bytes:
                large_files.append(FileReviewOpportunity(file, age_days))
        if progress_callback is not None:
            progress_callback(
                RecommendationProgress(
                    files_checked=index,
                    candidate_files=len(files_to_inspect),
                    elapsed_seconds=time.perf_counter() - started,
                )
            )

    installers.sort(key=lambda item: (-item.modified_days_ago, -item.file.size, item.file.path.casefold()))
    large_files.sort(key=lambda item: (-item.file.size, item.file.path.casefold()))
    duplicate_groups = tuple(
        group
        for group in (duplicate_analysis.groups if duplicate_analysis is not None else ())
        if group.verified_copy_count >= 2
    )
    return CleanupRecommendations(
        verified_duplicates=duplicate_groups,
        old_installers=tuple(installers),
        large_files=tuple(large_files),
        files_checked=len(files_to_inspect),
        inaccessible_paths=tuple(inaccessible),
        duplicate_analysis_pending=duplicate_analysis is None,
    )
