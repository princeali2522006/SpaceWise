"""Read-only SHA-256 duplicate analysis for a completed scan."""

from collections import defaultdict
from dataclasses import dataclass
import ctypes
import hashlib
import os
import stat
import threading
import time
from typing import Callable, Iterable, Sequence

from core.scanner import ScannedFile


CHUNK_SIZE = 1024 * 1024
DuplicateProgressCallback = Callable[["DuplicateProgress"], None]


@dataclass(frozen=True)
class DuplicateProgress:
    files_checked: int
    candidate_files: int
    elapsed_seconds: float


@dataclass(frozen=True)
class UnverifiedFile:
    path: str
    reason: str


@dataclass(frozen=True)
class DuplicateGroup:
    size: int
    digest: str
    paths: tuple[str, ...]
    verified_copy_count: int

    @property
    def recoverable_bytes(self) -> int:
        if self.size <= 0:
            return 0
        return max(0, self.verified_copy_count - 1) * self.size


@dataclass(frozen=True)
class DuplicateAnalysis:
    groups: tuple[DuplicateGroup, ...]
    unverified_files: tuple[UnverifiedFile, ...]
    candidate_files: int
    hashed_files: int
    elapsed_seconds: float

    @property
    def potential_recoverable_bytes(self) -> int:
        return sum(group.recoverable_bytes for group in self.groups)


class DuplicateAnalysisCancelled(Exception):
    """Raised when duplicate analysis is cancelled before it completes."""


@dataclass(frozen=True)
class _Candidate:
    file: ScannedFile
    initial_stat: os.stat_result


def _extended_path(path: str) -> str:
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    return stat.S_ISLNK(file_stat.st_mode) or bool(
        getattr(file_stat, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _file_identity(file_stat: os.stat_result, path: str) -> tuple[int, int | str]:
    if file_stat.st_ino:
        return file_stat.st_dev, file_stat.st_ino
    return file_stat.st_dev, os.path.normcase(os.path.abspath(path))


def _within_roots(path: str, roots: Sequence[str]) -> bool:
    candidate_path = os.path.normcase(os.path.abspath(path))
    for root in roots:
        normalized_root = os.path.normcase(os.path.abspath(root))
        try:
            if os.path.commonpath((candidate_path, normalized_root)) == normalized_root:
                return True
        except ValueError:
            continue
    return False


def _same_file_version(left: os.stat_result, right: os.stat_result) -> bool:
    stable_fields_match = (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
    )
    return stable_fields_match and (
        os.name == "nt" or left.st_ctime_ns == right.st_ctime_ns
    )


def _open_without_following_links(path: str):
    extended_path = _extended_path(path)
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            extended_path,
            0x80000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00200000 | 0x08000000,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            kernel32.CloseHandle(handle)
            raise
        return os.fdopen(descriptor, "rb")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    return os.fdopen(os.open(extended_path, flags), "rb")


def _hash_stream(
    file_handle,
    cancel_event: threading.Event | None,
) -> str:
    digest = hashlib.sha256()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        chunk = file_handle.read(CHUNK_SIZE)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def analyze_duplicates(
    files: Iterable[ScannedFile],
    *,
    personal_roots: Sequence[str] | None = None,
    progress_callback: DuplicateProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> DuplicateAnalysis:
    """Hash only equal-size candidates and group verified byte-identical files."""
    started = time.perf_counter()
    by_size: dict[int, list[ScannedFile]] = defaultdict(list)
    for file in files:
        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        if personal_roots is not None and not _within_roots(file.path, personal_roots):
            continue
        by_size[file.size].append(file)

    size_candidates = [
        (size, candidates)
        for size, candidates in by_size.items()
        if len(candidates) > 1
    ]
    unverified: list[UnverifiedFile] = []
    identities: dict[tuple[int, int | str], list[_Candidate]] = {}
    checked = 0

    def report_progress() -> None:
        if progress_callback is not None:
            progress_callback(
                DuplicateProgress(
                    files_checked=checked,
                    candidate_files=sum(len(items) for _, items in size_candidates),
                    elapsed_seconds=time.perf_counter() - started,
                )
            )

    report_progress()
    for _, candidates in size_candidates:
        for file in candidates:
            if cancel_event is not None and cancel_event.is_set():
                raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
            try:
                path_stat = os.lstat(_extended_path(file.path))
                if _is_reparse_point(path_stat) or not stat.S_ISREG(path_stat.st_mode):
                    raise OSError("Not a regular file; links are not followed.")
                if path_stat.st_size != file.size:
                    raise OSError("File size changed since the completed scan.")
                identity = _file_identity(path_stat, file.path)
            except OSError as error:
                unverified.append(UnverifiedFile(file.path, str(error)))
                checked += 1
                report_progress()
                continue
            identities.setdefault(identity, []).append(_Candidate(file, path_stat))

    hashed_groups: dict[tuple[int, str], list[tuple[str, tuple[int, int | str]]]] = defaultdict(list)
    hashed_files = 0
    for identity, aliases in identities.items():
        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        representative = aliases[0]
        try:
            with _open_without_following_links(representative.file.path) as file_handle:
                opened_stat = os.fstat(file_handle.fileno())
                if (
                    _is_reparse_point(opened_stat)
                    or not stat.S_ISREG(opened_stat.st_mode)
                    or not _same_file_version(representative.initial_stat, opened_stat)
                ):
                    raise OSError("File changed before hashing; copy was not verified.")
                digest = _hash_stream(file_handle, cancel_event)
                final_handle_stat = os.fstat(file_handle.fileno())
                final_path_stat = os.lstat(_extended_path(representative.file.path))
                if (
                    not _same_file_version(opened_stat, final_handle_stat)
                    or not _same_file_version(opened_stat, final_path_stat)
                    or _is_reparse_point(final_path_stat)
                ):
                    raise OSError("File changed during hashing; copy was not verified.")
        except DuplicateAnalysisCancelled:
            raise
        except OSError as error:
            unverified.extend(UnverifiedFile(alias.file.path, str(error)) for alias in aliases)
            checked += len(aliases)
            report_progress()
            continue

        hashed_files += 1
        for alias in aliases:
            try:
                alias_stat = os.lstat(_extended_path(alias.file.path))
                if (
                    _is_reparse_point(alias_stat)
                    or not stat.S_ISREG(alias_stat.st_mode)
                    or not _same_file_version(alias.initial_stat, alias_stat)
                ):
                    raise OSError("File changed during analysis; copy was not verified.")
            except OSError as error:
                unverified.append(UnverifiedFile(alias.file.path, str(error)))
            else:
                hashed_groups[(alias.file.size, digest)].append(
                    (alias.file.path, identity)
                )
            checked += 1
            report_progress()

    groups: list[DuplicateGroup] = []
    for (size, digest), copies in hashed_groups.items():
        if len(copies) < 2:
            continue
        identities_in_group = {identity for _, identity in copies}
        groups.append(
            DuplicateGroup(
                size=size,
                digest=digest,
                paths=tuple(sorted((path for path, _ in copies), key=str.casefold)),
                verified_copy_count=len(identities_in_group),
            )
        )
    groups.sort(key=lambda group: (-group.recoverable_bytes, -group.size, group.paths[0].casefold()))

    if cancel_event is not None and cancel_event.is_set():
        raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
    return DuplicateAnalysis(
        groups=tuple(groups),
        unverified_files=tuple(unverified),
        candidate_files=sum(len(items) for _, items in size_candidates),
        hashed_files=hashed_files,
        elapsed_seconds=time.perf_counter() - started,
    )
