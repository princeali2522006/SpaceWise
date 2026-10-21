"""Shared display formatting helpers."""

from datetime import datetime


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value):,} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_scan_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).astimezone().strftime("%b %d, %Y · %H:%M")
    except ValueError:
        return timestamp
