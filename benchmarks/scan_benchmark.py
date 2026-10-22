"""Benchmark the standard scanner on generated fixtures or an explicit folder."""

import argparse
import os
from pathlib import Path
import tempfile
import time
import tracemalloc

from core.scanner_backends import RecursiveScanner


FIXTURES = (("small", 100), ("medium", 2_000), ("larger", 10_000))


def _make_fixture(root: Path, file_count: int) -> None:
    directory_count = min(100, max(1, file_count // 10))
    for index in range(file_count):
        directory = root / f"folder-{index % directory_count:03}"
        directory.mkdir(exist_ok=True)
        (directory / f"file-{index:05}.bin").write_bytes(
            b"x" * (index % 8192)
        )


def _scan(label: str, root: Path) -> None:
    tracemalloc.start()
    started = time.perf_counter()
    result = RecursiveScanner().scan(
        (str(root),),
        scan_scope="Selected folder",
        strict_roots=True,
    )
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"{label}: backend={result.scanner_backend}; "
        f"files={len(result.files):,}; logical_size={result.total_size:,} bytes; "
        f"duration={elapsed:.3f} s; Python_peak={peak_bytes / 1024**2:.2f} MiB; "
        f"inaccessible={result.inaccessible_items:,}; "
        f"skipped_directories={result.skipped_directories:,}; "
        f"skipped_files={result.skipped_files:,}"
    )


def _reject_volume_root(root: Path) -> None:
    absolute = os.path.abspath(root)
    drive, tail = os.path.splitdrive(absolute)
    if drive and tail in ("\\", "/"):
        raise ValueError(
            "This benchmark helper refuses a volume root. Use generated fixtures or "
            "a bounded test folder; full-drive benchmarking requires manual approval."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help="Benchmark one existing folder (volume roots are refused).",
    )
    args = parser.parse_args()
    if args.root is not None:
        if not args.root.is_dir():
            parser.error(f"Folder does not exist: {args.root}")
        try:
            _reject_volume_root(args.root)
        except ValueError as error:
            parser.error(str(error))
        _scan("provided-folder", args.root)
        return

    with tempfile.TemporaryDirectory(prefix="spacewise-scan-benchmark-") as temporary:
        temporary_root = Path(temporary)
        for label, count in FIXTURES:
            fixture = temporary_root / label
            fixture.mkdir()
            _make_fixture(fixture, count)
            _scan(label, fixture)


if __name__ == "__main__":
    main()
