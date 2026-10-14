"""Windows known-folder and local-drive discovery."""

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import uuid

from core.scanner import DriveUsage


KNOWN_FOLDERS = (
    ("Desktop", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"),
    ("Downloads", "374DE290-123F-4565-9164-39C4925E467B"),
    ("Documents", "FDD39AD0-238F-46AF-ADB4-6C85480369C7"),
    ("Pictures", "33E28130-4E1E-4676-835A-98395C3BC3BB"),
    ("Videos", "18989B1D-99B5-455B-841C-AB7C74E4DDFC"),
    ("Music", "4BD8D571-6D19-48D3-BE97-422220080E43"),
)


@dataclass(frozen=True)
class LocalDrive:
    path: str
    drive_type: str
    usage: DriveUsage

    @property
    def label(self) -> str:
        return f"{self.path} ({self.drive_type})"


def _known_folder_from_windows(folder_id: str) -> str:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    known_folder_id = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    output = ctypes.c_wchar_p()
    get_path = shell32.SHGetKnownFolderPath
    get_path.argtypes = [
        ctypes.POINTER(ctypes.c_byte * 16),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    get_path.restype = ctypes.c_long
    result = get_path(ctypes.byref(known_folder_id), 0, None, ctypes.byref(output))
    if result != 0:
        raise OSError(result, "Windows could not resolve a known user folder.")
    try:
        return output.value or ""
    finally:
        ole32.CoTaskMemFree(ctypes.cast(output, ctypes.c_void_p))


def discover_user_folders() -> tuple[str, ...]:
    """Resolve existing standard user folders, honoring Windows redirection."""
    folders: list[str] = []
    seen: set[str] = set()
    for name, folder_id in KNOWN_FOLDERS:
        try:
            candidate = (
                _known_folder_from_windows(folder_id)
                if os.name == "nt"
                else str(Path.home() / name)
            )
        except OSError:
            candidate = str(Path.home() / name)
        if not candidate or not os.path.isdir(candidate):
            continue
        key = os.path.normcase(os.path.normpath(os.path.abspath(candidate)))
        if key not in seen:
            seen.add(key)
            folders.append(candidate)
    return tuple(folders)


def discover_local_drives() -> tuple[LocalDrive, ...]:
    """Return available local Windows fixed and removable drives with disk usage."""
    if os.name != "nt":
        anchor = Path.home().anchor
        if not anchor:
            return ()
        roots = (anchor,)
    else:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_logical_drives = kernel32.GetLogicalDrives
        get_logical_drives.argtypes = []
        get_logical_drives.restype = ctypes.c_uint32
        drive_mask = get_logical_drives()
        if not drive_mask:
            error = ctypes.get_last_error()
            if error:
                raise OSError(error, "Could not enumerate Windows logical drives.")
            return ()
        roots = tuple(
            f"{chr(ord('A') + index)}:\\"
            for index in range(26)
            if drive_mask & (1 << index)
        )

    drives: list[LocalDrive] = []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
    get_drive_type = None
    if kernel32 is not None:
        get_drive_type = kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint32
    for path in roots:
        if os.name == "nt":
            drive_type_code = get_drive_type(path) if get_drive_type is not None else 0
            if drive_type_code == 3:
                drive_type = "Fixed"
            elif drive_type_code == 2:
                drive_type = "Removable"
            else:
                continue
        else:
            drive_type = "Local"
        try:
            if not os.path.isdir(path):
                continue
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        drives.append(
            LocalDrive(
                path=path,
                drive_type=drive_type,
                usage=DriveUsage(total=usage.total, used=usage.used, free=usage.free),
            )
        )
    return tuple(drives)


def refresh_drive_usage(path: str) -> DriveUsage:
    """Read current Windows-reported capacity, used, and free space for a drive."""
    usage = shutil.disk_usage(path)
    return DriveUsage(total=usage.total, used=usage.used, free=usage.free)
