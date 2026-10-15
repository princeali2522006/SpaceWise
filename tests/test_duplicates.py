import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import duplicates
from core.duplicates import DuplicateAnalysisCancelled, analyze_duplicates
from core.scanner import ScannedFile


class DuplicateAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _scan_files(self, *paths: Path) -> tuple[ScannedFile, ...]:
        return tuple(
            ScannedFile(path.name, str(path), path.stat().st_size, "Other")
            for path in paths
        )

    def test_groups_only_same_size_identical_files_and_computes_savings(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        different = self.root / "different.bin"
        unique = self.root / "unique.bin"
        first.write_bytes(b"same-data")
        second.write_bytes(b"same-data")
        different.write_bytes(b"diff-data")
        unique.write_bytes(b"unique!!!")

        result = analyze_duplicates(self._scan_files(first, second, different, unique))

        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual(set(group.paths), {str(first), str(second)})
        self.assertEqual(group.verified_copy_count, 2)
        self.assertEqual(group.recoverable_bytes, len(b"same-data"))
        self.assertEqual(result.potential_recoverable_bytes, len(b"same-data"))
        self.assertEqual(result.hashed_files, 4)
        self.assertEqual(result.candidate_files, 4)
        self.assertNotIn(str(different), group.paths)

    def test_three_identical_copies_include_every_path_and_count_only_extra_copies_as_savings(self) -> None:
        copies = tuple(self.root / f"copy-{index}.bin" for index in range(3))
        for path in copies:
            path.write_bytes(b"identical-content")
        unique = self.root / "unique.bin"
        unique.write_bytes(b"a-different-size")

        result = analyze_duplicates(self._scan_files(*copies, unique))

        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual(set(group.paths), {str(path) for path in copies})
        self.assertEqual(group.verified_copy_count, 3)
        self.assertEqual(group.recoverable_bytes, 2 * len(b"identical-content"))
        self.assertEqual(
            result.potential_recoverable_bytes,
            2 * len(b"identical-content"),
        )
        self.assertNotIn(str(unique), group.paths)

    def test_zero_byte_duplicates_do_not_add_recoverable_space(self) -> None:
        first = self.root / "empty-a"
        second = self.root / "empty-b"
        first.write_bytes(b"")
        second.write_bytes(b"")

        result = analyze_duplicates(self._scan_files(first, second))

        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].size, 0)
        self.assertEqual(result.groups[0].recoverable_bytes, 0)
        self.assertEqual(result.potential_recoverable_bytes, 0)

    def test_hard_links_do_not_increase_savings(self) -> None:
        original = self.root / "original.bin"
        hard_link = self.root / "hard-link.bin"
        independent = self.root / "independent.bin"
        original.write_bytes(b"same")
        independent.write_bytes(b"same")
        try:
            os.link(original, hard_link)
        except OSError as error:
            self.skipTest(f"Hard-link creation unavailable: {error}")

        result = analyze_duplicates(self._scan_files(original, hard_link, independent))

        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual(len(group.paths), 3)
        self.assertEqual(group.verified_copy_count, 2)
        self.assertEqual(group.recoverable_bytes, 4)
        self.assertEqual(result.hashed_files, 2)

    def test_changed_file_is_reported_as_unverified(self) -> None:
        first = self.root / "changing.bin"
        second = self.root / "same-size.bin"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        scanned = self._scan_files(first, second)
        original_hash = duplicates._hash_stream

        def change_after_read(file_handle, cancel_event):
            digest = original_hash(file_handle, cancel_event)
            original_stat = first.stat()
            first.write_bytes(b"diff")
            os.utime(
                first,
                ns=(
                    original_stat.st_atime_ns,
                    original_stat.st_mtime_ns + 10_000_000_000,
                ),
            )
            return digest

        with patch("core.duplicates._hash_stream", side_effect=change_after_read):
            result = analyze_duplicates(scanned)

        self.assertFalse(result.groups)
        self.assertEqual(len(result.unverified_files), 1)
        self.assertEqual(result.unverified_files[0].path, str(first))

    def test_missing_candidate_is_reported_as_unverified(self) -> None:
        first = self.root / "missing.bin"
        second = self.root / "present.bin"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        scanned = self._scan_files(first, second)
        first.unlink()

        result = analyze_duplicates(scanned)

        self.assertEqual(len(result.unverified_files), 1)
        self.assertEqual(result.unverified_files[0].path, str(first))
        self.assertFalse(result.groups)

    def test_cancellation_aborts_without_returning_partial_groups(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(DuplicateAnalysisCancelled):
            analyze_duplicates((), cancel_event=cancel_event)

    def test_cancellation_during_chunked_hashing_aborts_analysis(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        first.write_bytes(b"x" * (1024 * 1024 + 1))
        second.write_bytes(b"x" * (1024 * 1024 + 1))
        cancel_event = threading.Event()
        original_hash = duplicates._hash_stream

        def cancel_before_read(file_handle, event):
            event.set()
            return original_hash(file_handle, event)

        with patch("core.duplicates._hash_stream", side_effect=cancel_before_read):
            with self.assertRaises(DuplicateAnalysisCancelled):
                analyze_duplicates(
                    self._scan_files(first, second),
                    cancel_event=cancel_event,
                )


if __name__ == "__main__":
    unittest.main()
