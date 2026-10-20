import os
import sqlite3
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
        self.cache_path_patcher = patch(
            "core.duplicates._default_cache_path",
            return_value=str(self.root / "application-cache" / "hashes.sqlite3"),
        )
        self.cache_path_patcher.start()

    def tearDown(self) -> None:
        self.cache_path_patcher.stop()
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
        self.assertEqual(result.hashed_files, 2)
        self.assertEqual(result.candidate_files, 4)
        self.assertNotIn(str(different), group.paths)

    def test_unique_sizes_are_excluded_before_file_hashing(self) -> None:
        paths = tuple(self.root / f"unique-{size}.bin" for size in range(1, 6))
        files = []
        for path, size in zip(paths, range(1, 6)):
            path.write_bytes(b"x" * size)
            files.append(ScannedFile(path.name, str(path), size, "Other"))

        with patch("core.duplicates._hash_stream") as hash_stream:
            result = analyze_duplicates(files, use_cache=False)

        self.assertEqual(result.candidate_files, 0)
        self.assertEqual(result.hashed_files, 0)
        self.assertEqual(result.bytes_processed, 0)
        hash_stream.assert_not_called()

    def test_different_same_size_samples_skip_full_hashing(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        first.write_bytes(b"a" * (2 * 1024 * 1024))
        second.write_bytes(b"b" * (2 * 1024 * 1024))

        with patch("core.duplicates._hash_stream") as hash_stream:
            result = analyze_duplicates(
                self._scan_files(first, second),
                use_cache=False,
            )

        self.assertFalse(result.groups)
        self.assertEqual(result.candidate_files, 2)
        self.assertEqual(result.hashed_files, 0)
        self.assertLess(result.bytes_processed, 2 * 1024 * 1024)
        hash_stream.assert_not_called()

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
        self.assertEqual(result.bytes_processed, 0)

    def test_progress_reports_real_stage_counts_and_bytes(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        payload = b"contents" * 250_000
        first.write_bytes(payload)
        second.write_bytes(payload)
        updates = []

        result = analyze_duplicates(
            self._scan_files(first, second),
            use_cache=False,
            progress_callback=updates.append,
        )

        self.assertEqual(updates[-1].stage, "complete")
        self.assertEqual(updates[-1].candidate_files, 2)
        self.assertEqual(updates[-1].files_checked, 2)
        self.assertEqual(updates[-1].hashed_files, 2)
        self.assertEqual(updates[-1].skipped_files, 0)
        self.assertGreaterEqual(updates[-1].bytes_processed, len(payload) * 2)
        self.assertGreater(updates[-1].elapsed_seconds, 0)
        self.assertIn("sampling candidates", {item.stage for item in updates})
        self.assertIn("verifying SHA-256", {item.stage for item in updates})
        self.assertEqual(result.bytes_processed, updates[-1].bytes_processed)

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

    def test_analysis_does_not_modify_scanned_files(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        payload = b"read-only content" * 10_000
        first.write_bytes(payload)
        second.write_bytes(payload)
        before = {
            path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
            for path in (first, second)
        }

        analyze_duplicates(self._scan_files(first, second), use_cache=False)

        after = {
            path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
            for path in (first, second)
        }
        self.assertEqual(after, before)

    def test_changed_file_is_reported_as_unverified(self) -> None:
        first = self.root / "changing.bin"
        second = self.root / "same-size.bin"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        scanned = self._scan_files(first, second)
        original_hash = duplicates._hash_stream

        def change_after_read(file_handle, cancel_event, bytes_callback=None):
            digest = original_hash(file_handle, cancel_event, bytes_callback)
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
        self.assertEqual(result.skipped_files, 1)
        self.assertFalse(result.groups)

    def test_completed_hashes_are_reused_from_cache(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        first.write_bytes(b"same content" * 100_000)
        second.write_bytes(b"same content" * 100_000)
        files = self._scan_files(first, second)
        cache_path = self.root / "app-cache" / "hashes.sqlite3"

        initial = analyze_duplicates(files, cache_path=cache_path)
        repeated = analyze_duplicates(files, cache_path=cache_path)

        self.assertEqual(len(initial.groups), 1)
        self.assertEqual(initial.hashed_files, 2)
        self.assertEqual(initial.cached_files_reused, 0)
        self.assertEqual(repeated.hashed_files, 0)
        self.assertEqual(repeated.cached_files_reused, 2)
        self.assertEqual(len(repeated.groups), 1)
        self.assertLess(repeated.bytes_processed, initial.bytes_processed)

    def test_changed_file_invalidates_cached_hash(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        original = bytearray(b"x" * (2 * 1024 * 1024))
        first.write_bytes(original)
        second.write_bytes(original)
        files = self._scan_files(first, second)
        cache_path = self.root / "app-cache" / "hashes.sqlite3"
        analyze_duplicates(files, cache_path=cache_path)

        changed = bytearray(original)
        changed[len(changed) // 2] = ord("y")
        second.write_bytes(changed)
        os.utime(second, ns=(second.stat().st_atime_ns, first.stat().st_mtime_ns + 5_000_000_000))
        refreshed_files = self._scan_files(first, second)

        result = analyze_duplicates(refreshed_files, cache_path=cache_path)

        self.assertFalse(result.groups)
        self.assertEqual(result.cached_files_reused, 1)
        self.assertEqual(result.hashed_files, 1)

    def test_corrupted_cache_digest_is_rejected(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        first_content = bytearray(b"a" * (2 * 1024 * 1024))
        second_content = bytearray(first_content)
        second_content[len(second_content) // 2] = ord("b")
        first.write_bytes(first_content)
        second.write_bytes(second_content)
        files = self._scan_files(first, second)
        cache_path = self.root / "app-cache" / "hashes.sqlite3"

        initial = analyze_duplicates(files, cache_path=cache_path)
        self.assertFalse(initial.groups)
        connection = sqlite3.connect(cache_path)
        rows = connection.execute(
            "SELECT device, file_id, digest FROM file_hashes ORDER BY file_id"
        ).fetchall()
        connection.execute(
            "UPDATE file_hashes SET digest=? WHERE device=? AND file_id=?",
            (rows[1][2], rows[0][0], rows[0][1]),
        )
        connection.commit()
        connection.close()

        result = analyze_duplicates(files, cache_path=cache_path)

        self.assertFalse(result.groups)
        self.assertEqual(result.cached_files_reused, 1)
        self.assertEqual(result.hashed_files, 1)

    def test_legacy_cache_entries_without_integrity_checks_are_not_reused(self) -> None:
        file_path = self.root / "legacy.bin"
        file_path.write_bytes(b"legacy data")
        file_stat = file_path.stat()
        identity = duplicates._file_identity(file_stat, str(file_path))
        cache_path = self.root / "app-cache" / "hashes.sqlite3"
        cache_path.parent.mkdir()
        connection = sqlite3.connect(cache_path)
        connection.execute(
            """
            CREATE TABLE file_hashes (
                device TEXT NOT NULL,
                file_id TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                ctime_ns INTEGER NOT NULL,
                sample_digest TEXT NOT NULL,
                digest TEXT NOT NULL,
                last_used_ns INTEGER NOT NULL,
                PRIMARY KEY (device, file_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO file_hashes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(identity[0]),
                str(identity[1]),
                file_stat.st_size,
                file_stat.st_mtime_ns,
                file_stat.st_ctime_ns,
                "b" * 64,
                "a" * 64,
                1,
            ),
        )
        connection.commit()
        connection.close()

        cache = duplicates._HashCache(cache_path)
        try:
            self.assertIsNone(cache.get(identity, file_stat, "b" * 64))
        finally:
            cache.close()

    def test_failed_cache_write_does_not_trust_partial_entry(self) -> None:
        first = self.root / "first.bin"
        second = self.root / "second.bin"
        first.write_bytes(b"identical data" * 20_000)
        second.write_bytes(b"identical data" * 20_000)
        cache_path = self.root / "app-cache" / "hashes.sqlite3"
        cache = duplicates._HashCache(cache_path)
        cache.close()
        connection = sqlite3.connect(cache_path)
        connection.execute(
            """
            CREATE TRIGGER fail_hash_write BEFORE INSERT ON file_hashes
            BEGIN
                SELECT RAISE(ABORT, 'interrupted cache write');
            END
            """
        )
        connection.commit()
        connection.close()

        first_result = analyze_duplicates(
            self._scan_files(first, second),
            cache_path=cache_path,
        )
        connection = sqlite3.connect(cache_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM file_hashes").fetchone()[0],
                0,
            )
            connection.execute("DROP TRIGGER fail_hash_write")
            connection.commit()
        finally:
            connection.close()

        repeated = analyze_duplicates(
            self._scan_files(first, second),
            cache_path=cache_path,
        )

        self.assertEqual(len(first_result.groups), 1)
        self.assertEqual(repeated.cached_files_reused, 0)
        self.assertEqual(repeated.hashed_files, 2)

    def test_cancellation_preserves_hashes_completed_before_interruption(self) -> None:
        paths = tuple(self.root / f"copy-{index}.bin" for index in range(3))
        for path in paths:
            path.write_bytes(b"shared contents" * 100_000)
        files = self._scan_files(*paths)
        cache_path = self.root / "app-cache" / "hashes.sqlite3"
        cancel_event = threading.Event()
        original_hash = duplicates._hash_stream
        call_count = 0

        def hash_then_cancel(file_handle, event, bytes_callback=None):
            nonlocal call_count
            digest = original_hash(file_handle, event, bytes_callback)
            call_count += 1
            if call_count == 1:
                cancel_event.set()
            return digest

        with patch("core.duplicates._hash_stream", side_effect=hash_then_cancel):
            with self.assertRaises(DuplicateAnalysisCancelled):
                analyze_duplicates(
                    files,
                    cache_path=cache_path,
                    cancel_event=cancel_event,
                )

        resumed = analyze_duplicates(files, cache_path=cache_path)

        self.assertEqual(resumed.cached_files_reused, 1)
        self.assertEqual(resumed.hashed_files, 2)
        self.assertEqual(len(resumed.groups), 1)

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

        def cancel_before_read(file_handle, event, bytes_callback=None):
            event.set()
            return original_hash(file_handle, event, bytes_callback)

        with patch("core.duplicates._hash_stream", side_effect=cancel_before_read):
            with self.assertRaises(DuplicateAnalysisCancelled):
                analyze_duplicates(
                    self._scan_files(first, second),
                    cancel_event=cancel_event,
                )


if __name__ == "__main__":
    unittest.main()
