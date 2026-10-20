"""Read-only SHA-256 duplicate analysis for a completed scan."""

from collections import defaultdict
from dataclasses import dataclass
import ctypes
import hashlib
import logging
import os
import sqlite3
import stat
import threading
import time
from typing import Callable, Iterable, Sequence

from core.scanner import ScannedFile


CHUNK_SIZE = 1024 * 1024
SAMPLE_SIZE = 64 * 1024
PROGRESS_INTERVAL_SECONDS = 0.5
CACHE_MAX_ENTRIES = 50_000
CACHE_MAX_PAGES = 8192
logger = logging.getLogger(__name__)
DuplicateProgressCallback = Callable[["DuplicateProgress"], None]


@dataclass(frozen=True)
class DuplicateProgress:
    files_checked: int
    candidate_files: int
    elapsed_seconds: float
    hashed_files: int = 0
    bytes_processed: int = 0
    skipped_files: int = 0
    stage: str = "grouping"
    cached_files_reused: int = 0


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
    bytes_processed: int = 0
    skipped_files: int = 0
    cached_files_reused: int = 0

    @property
    def potential_recoverable_bytes(self) -> int:
        return sum(group.recoverable_bytes for group in self.groups)


class DuplicateAnalysisCancelled(Exception):
    """Raised when duplicate analysis is cancelled before it completes."""


@dataclass(frozen=True)
class _Candidate:
    file: ScannedFile
    initial_stat: os.stat_result


class _ProgressReporter:
    def __init__(
        self,
        callback: DuplicateProgressCallback | None,
        started: float,
    ) -> None:
        self.callback = callback
        self.started = started
        self.files_checked = 0
        self.candidate_files = 0
        self.hashed_files = 0
        self.bytes_processed = 0
        self.skipped_files = 0
        self.cached_files_reused = 0
        self.stage = "grouping"
        self._last_report = 0.0

    def report(self, *, force: bool = False) -> None:
        if self.callback is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_report < PROGRESS_INTERVAL_SECONDS:
            return
        self.callback(
            DuplicateProgress(
                files_checked=self.files_checked,
                candidate_files=self.candidate_files,
                elapsed_seconds=now - self.started,
                hashed_files=self.hashed_files,
                bytes_processed=self.bytes_processed,
                skipped_files=self.skipped_files,
                stage=self.stage,
                cached_files_reused=self.cached_files_reused,
            )
        )
        self._last_report = now

    def set_stage(self, stage: str) -> None:
        if self.stage != stage:
            self.stage = stage
            self.report(force=True)


class _HashCache:
    """Bounded SQLite cache of completed hashes and their screening samples."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=0.25)
        self.connection.execute("PRAGMA page_size=4096")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(f"PRAGMA max_page_count={CACHE_MAX_PAGES}")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS file_hashes (
                device TEXT NOT NULL,
                file_id TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                ctime_ns INTEGER NOT NULL,
                sample_digest TEXT NOT NULL,
                digest TEXT NOT NULL,
                entry_checksum TEXT NOT NULL,
                last_used_ns INTEGER NOT NULL,
                PRIMARY KEY (device, file_id)
            )
            """
        )
        columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(file_hashes)")
        }
        if "entry_checksum" not in columns:
            self.connection.execute(
                "ALTER TABLE file_hashes ADD COLUMN entry_checksum TEXT NOT NULL DEFAULT ''"
            )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS file_hashes_age ON file_hashes(last_used_ns)"
        )
        self.connection.commit()
        self._entry_count = self.connection.execute(
            "SELECT COUNT(*) FROM file_hashes"
        ).fetchone()[0]

    def get(
        self,
        identity: tuple[int, int | str],
        file_stat: os.stat_result,
        sample_digest: str,
    ) -> str | None:
        key = (str(identity[0]), str(identity[1]))
        row = self.connection.execute(
            """
            SELECT size, mtime_ns, ctime_ns, sample_digest, digest, entry_checksum
            FROM file_hashes WHERE device=? AND file_id=?
            """,
            key,
        ).fetchone()
        if row is None:
            return None
        metadata_matches = row[:3] == (
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )
        checksum_matches = (
            row[3] == sample_digest
            and _valid_sha256(row[3])
            and _valid_sha256(row[4])
            and row[5]
            == _cache_record_checksum(
                key,
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
            )
        )
        if not metadata_matches or not checksum_matches:
            with self.connection:
                self.connection.execute(
                    "DELETE FROM file_hashes WHERE device=? AND file_id=?", key
                )
            self._entry_count = max(0, self._entry_count - 1)
            return None
        return str(row[4])

    def store(
        self,
        identity: tuple[int, int | str],
        file_stat: os.stat_result,
        sample_digest: str,
        digest: str,
    ) -> None:
        with self.connection:
            key = (str(identity[0]), str(identity[1]))
            exists = self.connection.execute(
                "SELECT 1 FROM file_hashes WHERE device=? AND file_id=?", key
            ).fetchone()
            self.connection.execute(
                """
                INSERT INTO file_hashes (
                    device, file_id, size, mtime_ns, ctime_ns,
                    sample_digest, digest, entry_checksum, last_used_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device, file_id) DO UPDATE SET
                    size=excluded.size,
                    mtime_ns=excluded.mtime_ns,
                    ctime_ns=excluded.ctime_ns,
                    sample_digest=excluded.sample_digest,
                    digest=excluded.digest,
                    entry_checksum=excluded.entry_checksum,
                    last_used_ns=excluded.last_used_ns
                """,
                (
                    *key,
                    file_stat.st_size,
                    file_stat.st_mtime_ns,
                    file_stat.st_ctime_ns,
                    sample_digest,
                    digest,
                    _cache_record_checksum(
                        key,
                        file_stat.st_size,
                        file_stat.st_mtime_ns,
                        file_stat.st_ctime_ns,
                        sample_digest,
                        digest,
                    ),
                    time.time_ns(),
                ),
            )
            if exists is None:
                self._entry_count += 1
            excess = max(0, self._entry_count - CACHE_MAX_ENTRIES)
            if excess:
                self.connection.execute(
                    """
                    DELETE FROM file_hashes
                    WHERE (device, file_id) IN (
                        SELECT device, file_id FROM file_hashes
                        ORDER BY last_used_ns ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
                self._entry_count -= excess

    def close(self) -> None:
        self.connection.close()


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


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _cache_record_checksum(
    identity: tuple[str, str],
    size: int,
    mtime_ns: int,
    ctime_ns: int,
    sample_digest: str,
    digest: str,
) -> str:
    fields = (
        *identity,
        str(size),
        str(mtime_ns),
        str(ctime_ns),
        sample_digest,
        digest,
    )
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


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
    bytes_callback: Callable[[int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        chunk = file_handle.read(CHUNK_SIZE)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)
        if bytes_callback is not None:
            bytes_callback(len(chunk))


def _sample_stream(
    file_handle,
    size: int,
    cancel_event: threading.Event | None,
    bytes_callback: Callable[[int], None] | None = None,
) -> str:
    sample = hashlib.sha256()
    sample.update(size.to_bytes(8, "big", signed=False))
    ranges = [(0, min(SAMPLE_SIZE, size))]
    if size > SAMPLE_SIZE * 2:
        ranges.append((size - SAMPLE_SIZE, size))
    for start, end in ranges:
        file_handle.seek(start)
        remaining = end - start
        while remaining:
            if cancel_event is not None and cancel_event.is_set():
                raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
            chunk = file_handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                raise OSError("File ended while sample data was being read.")
            sample.update(chunk)
            remaining -= len(chunk)
            if bytes_callback is not None:
                bytes_callback(len(chunk))
    return sample.hexdigest()


def _default_cache_path() -> str:
    if os.name == "nt":
        cache_root = os.environ.get("LOCALAPPDATA")
        if not cache_root:
            cache_root = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(cache_root, "SpaceWise", "duplicate_hashes.sqlite3")
    return os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache")),
        "spacewise",
        "duplicate_hashes.sqlite3",
    )


def _close_cache(cache: _HashCache) -> None:
    try:
        cache.close()
    except sqlite3.Error as error:
        logger.warning("Duplicate hash cache could not close cleanly: %s", error)


def _candidate_cache_path(path: str, cache_directory: str | None) -> bool:
    if cache_directory is None:
        return False
    return _within_roots(path, (cache_directory,))


def analyze_duplicates(
    files: Iterable[ScannedFile],
    *,
    personal_roots: Sequence[str] | None = None,
    progress_callback: DuplicateProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    cache_path: str | os.PathLike[str] | None = None,
    use_cache: bool = True,
) -> DuplicateAnalysis:
    """Screen equal-size files, then SHA-256 candidates sharing a content sample."""
    started = time.perf_counter()
    progress = _ProgressReporter(progress_callback, started)
    effective_cache_path = (
        os.fspath(cache_path) if cache_path is not None else _default_cache_path()
    )
    cache_directory = (
        os.path.dirname(os.path.abspath(effective_cache_path)) if use_cache else None
    )
    by_size: dict[int, list[ScannedFile]] = defaultdict(list)
    for file in files:
        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        if personal_roots is not None and not _within_roots(file.path, personal_roots):
            continue
        if _candidate_cache_path(file.path, cache_directory):
            continue
        by_size[file.size].append(file)

    cache: _HashCache | None = None
    size_candidates = [
        (size, candidates)
        for size, candidates in by_size.items()
        if len(candidates) > 1
    ]
    progress.candidate_files = sum(len(items) for _, items in size_candidates)
    progress.set_stage("validating candidates")
    progress.report(force=True)
    if use_cache and progress.candidate_files:
        try:
            cache = _HashCache(effective_cache_path)
        except (OSError, sqlite3.Error) as error:
            logger.warning("Duplicate hash cache is unavailable: %s", error)

    unverified: list[UnverifiedFile] = []
    identities: dict[tuple[int, int | str], list[_Candidate]] = {}
    sampled: dict[tuple[int, str], list[tuple[tuple[int, int | str], list[_Candidate], str]]] = defaultdict(list)
    hash_by_identity: dict[tuple[int, int | str], str] = {}
    hashed_groups: dict[tuple[int, str], list[tuple[str, tuple[int, int | str]]]] = defaultdict(list)

    def count_bytes(count: int) -> None:
        progress.bytes_processed += count
        progress.report()

    try:
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
                    progress.files_checked += 1
                    progress.skipped_files += 1
                    progress.report()
                    continue
                identities.setdefault(identity, []).append(_Candidate(file, path_stat))

        progress.set_stage("sampling candidates")
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
                        raise OSError("File changed before verification; copy was not verified.")
                    sample_digest = _sample_stream(
                        file_handle,
                        representative.file.size,
                        cancel_event,
                        count_bytes,
                    )
                    final_handle_stat = os.fstat(file_handle.fileno())
                    final_path_stat = os.lstat(_extended_path(representative.file.path))
                    if (
                        not _same_file_version(opened_stat, final_handle_stat)
                        or not _same_file_version(opened_stat, final_path_stat)
                        or _is_reparse_point(final_path_stat)
                    ):
                        raise OSError("File changed while screening; copy was not verified.")
            except DuplicateAnalysisCancelled:
                raise
            except OSError as error:
                unverified.extend(
                    UnverifiedFile(alias.file.path, str(error)) for alias in aliases
                )
                progress.files_checked += len(aliases)
                progress.skipped_files += len(aliases)
                progress.report()
                continue

            cached_digest: str | None = None
            if cache is not None:
                try:
                    cached_digest = cache.get(
                        identity, representative.initial_stat, sample_digest
                    )
                except (OSError, sqlite3.Error) as error:
                    logger.warning("Duplicate hash cache read failed: %s", error)
                    _close_cache(cache)
                    cache = None
            sampled[(representative.file.size, sample_digest)].append(
                (identity, aliases, sample_digest)
            )
            if cached_digest is not None:
                hash_by_identity[identity] = cached_digest
                progress.cached_files_reused += 1

        progress.set_stage("verifying SHA-256")
        completed_identities: set[tuple[int, int | str]] = set()
        for (_, sample_digest), sample_candidates in sampled.items():
            if cancel_event is not None and cancel_event.is_set():
                raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
            if len(sample_candidates) < 2:
                progress.files_checked += sum(len(aliases) for _, aliases, _ in sample_candidates)
                progress.report()
                continue
            failed_identities: set[tuple[int, int | str]] = set()
            for identity, aliases, _ in sample_candidates:
                if identity in hash_by_identity:
                    continue
                if cancel_event is not None and cancel_event.is_set():
                    raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
                representative = aliases[0]
                if representative.file.size == 0:
                    digest = hashlib.sha256(b"").hexdigest()
                    progress.hashed_files += 1
                    hash_by_identity[identity] = digest
                    completed_identities.add(identity)
                    progress.files_checked += len(aliases)
                    progress.report()
                    if cache is not None:
                        try:
                            cache.store(
                                identity,
                                representative.initial_stat,
                                sample_digest,
                                digest,
                            )
                        except (OSError, sqlite3.Error) as error:
                            logger.warning("Duplicate hash cache write failed: %s", error)
                            _close_cache(cache)
                            cache = None
                    continue
                try:
                    with _open_without_following_links(representative.file.path) as file_handle:
                        opened_stat = os.fstat(file_handle.fileno())
                        if (
                            _is_reparse_point(opened_stat)
                            or not stat.S_ISREG(opened_stat.st_mode)
                            or not _same_file_version(representative.initial_stat, opened_stat)
                        ):
                            raise OSError("File changed before hashing; copy was not verified.")
                        digest = _hash_stream(file_handle, cancel_event, count_bytes)
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
                    unverified.extend(
                        UnverifiedFile(alias.file.path, str(error)) for alias in aliases
                    )
                    failed_identities.add(identity)
                    completed_identities.add(identity)
                    progress.files_checked += len(aliases)
                    progress.skipped_files += len(aliases)
                    progress.report()
                    continue

                progress.hashed_files += 1
                hash_by_identity[identity] = digest
                completed_identities.add(identity)
                progress.files_checked += len(aliases)
                progress.report()
                if cache is not None:
                    try:
                        cache.store(
                            identity,
                            representative.initial_stat,
                            sample_digest,
                            digest,
                        )
                    except (OSError, sqlite3.Error) as error:
                        logger.warning("Duplicate hash cache write failed: %s", error)
                        _close_cache(cache)
                        cache = None

            progress.set_stage("validating file paths")
            for identity, aliases, _ in sample_candidates:
                if identity in failed_identities:
                    continue
                digest = hash_by_identity.get(identity)
                if digest is not None:
                    for alias in aliases:
                        try:
                            alias_stat = os.lstat(_extended_path(alias.file.path))
                            if (
                                _is_reparse_point(alias_stat)
                                or not stat.S_ISREG(alias_stat.st_mode)
                                or not _same_file_version(alias.initial_stat, alias_stat)
                            ):
                                raise OSError(
                                    "File changed during analysis; copy was not verified."
                                )
                        except OSError as error:
                            unverified.append(UnverifiedFile(alias.file.path, str(error)))
                            progress.skipped_files += 1
                        else:
                            hashed_groups[(alias.file.size, digest)].append(
                                (alias.file.path, identity)
                            )
                        if identity not in completed_identities:
                            progress.files_checked += 1
                        progress.report()
                else:
                    if identity not in completed_identities:
                        progress.files_checked += len(aliases)
                    progress.report()
            progress.set_stage("verifying SHA-256")

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
        progress.set_stage("finalizing results")
        groups.sort(
            key=lambda group: (
                -group.recoverable_bytes,
                -group.size,
                group.paths[0].casefold(),
            )
        )

        if cancel_event is not None and cancel_event.is_set():
            raise DuplicateAnalysisCancelled("Duplicate analysis cancelled.")
        progress.stage = "complete"
        progress.report(force=True)
        return DuplicateAnalysis(
            groups=tuple(groups),
            unverified_files=tuple(unverified),
            candidate_files=progress.candidate_files,
            hashed_files=progress.hashed_files,
            elapsed_seconds=time.perf_counter() - started,
            bytes_processed=progress.bytes_processed,
            skipped_files=progress.skipped_files,
            cached_files_reused=progress.cached_files_reused,
        )
    finally:
        if cache is not None:
            _close_cache(cache)
