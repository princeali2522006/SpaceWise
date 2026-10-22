"""Scanner backend selection with an accuracy-first recursive implementation."""

from dataclasses import dataclass
import ctypes
import logging
import ntpath
import os
import threading
from typing import Protocol

from core.scanner import DriveUsage, ProgressCallback, ScanResult, scan_folders


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendSelection:
    filesystem: str | None
    label: str
    fallback_reason: str | None = None


class ScannerBackend(Protocol):
    def scan(
        self,
        folders: tuple[str, ...],
        *,
        scan_scope: str,
        drive_usage: DriveUsage | None,
        stay_on_volume: bool,
        progress_callback: ProgressCallback | None,
        cancel_event: threading.Event | None,
        strict_roots: bool,
    ) -> ScanResult: ...


class RecursiveScanner:
    """The established directory traversal backend."""

    def __init__(self, label: str = "Standard scan") -> None:
        self.label = label

    def scan(
        self,
        folders: tuple[str, ...],
        *,
        scan_scope: str,
        drive_usage: DriveUsage | None = None,
        stay_on_volume: bool = False,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        strict_roots: bool = False,
    ) -> ScanResult:
        return scan_folders(
            folders,
            scan_scope=scan_scope,
            drive_usage=drive_usage,
            stay_on_volume=stay_on_volume,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            strict_roots=strict_roots,
            scanner_backend=self.label,
        )


def _query_volume_filesystem(path: str) -> str:
    if os.name != "nt":
        raise OSError("NTFS volume detection is only available on Windows.")
    drive, _ = ntpath.splitdrive(os.path.abspath(path))
    if not drive:
        raise OSError("The scan root is not on a drive-letter volume.")
    volume_root = drive + "\\"
    filesystem_name = ctypes.create_unicode_buffer(64)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_volume_information = kernel32.GetVolumeInformationW
    get_volume_information.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_wchar_p,
        ctypes.c_uint32,
    )
    get_volume_information.restype = ctypes.c_int
    serial_number = ctypes.c_uint32()
    maximum_component_length = ctypes.c_uint32()
    filesystem_flags = ctypes.c_uint32()
    succeeded = get_volume_information(
        volume_root,
        None,
        0,
        ctypes.byref(serial_number),
        ctypes.byref(maximum_component_length),
        ctypes.byref(filesystem_flags),
        filesystem_name,
        len(filesystem_name),
    )
    if not succeeded:
        raise ctypes.WinError(ctypes.get_last_error())
    return filesystem_name.value


def select_backend(
    folders: tuple[str, ...],
    *,
    scan_scope: str,
    stay_on_volume: bool,
) -> BackendSelection:
    """Select only a backend known to preserve the scanner's established semantics."""
    if not stay_on_volume or len(folders) != 1 or not scan_scope.startswith("Drive scan:"):
        return BackendSelection(
            filesystem=None,
            label="Standard scan",
            fallback_reason="The selected scan scope is not a whole-volume scan.",
        )

    try:
        filesystem = _query_volume_filesystem(folders[0])
    except OSError as error:
        logger.info("Fast scanner unavailable; using recursive scanning: %s", error)
        return BackendSelection(
            filesystem=None,
            label="Standard scan · fast scan unavailable",
            fallback_reason=str(error),
        )

    if filesystem.casefold() != "ntfs":
        return BackendSelection(
            filesystem=filesystem,
            label="Standard scan",
            fallback_reason=f"The volume uses {filesystem}, not NTFS.",
        )

    reason = (
        "A reliable NTFS metadata enumerator is not implemented. USN enumeration does not "
        "provide all file sizes and directory-entry semantics, and needs additional file-ID "
        "lookups that cannot currently guarantee parity for hard links, inaccessible paths, "
        "and entries changing during the scan."
    )
    logger.info("Using recursive scanner on NTFS volume: %s", reason)
    return BackendSelection(
        filesystem=filesystem,
        label="Standard scan · fast NTFS scan unavailable",
        fallback_reason=reason,
    )


def create_backend(selection: BackendSelection) -> ScannerBackend:
    """Build the selected scanner; unsupported fast paths remain transparent."""
    return RecursiveScanner(selection.label)
