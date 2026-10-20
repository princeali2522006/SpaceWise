import itertools
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.scanner import DriveUsage, ScanCancelledError, ScanProgress, scan_folder, scan_folders


class ScanFolderTests(unittest.TestCase):
    def test_totals_categories_and_immediate_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "zeta" / "nested").mkdir(parents=True)
            (root / "alpha").mkdir()
            (root / "empty").mkdir()
            (root / "root.txt").write_bytes(b"")
            (root / "zeta" / "video.mp4").write_bytes(b"v" * 10)
            (root / "zeta" / "nested" / "clip.mkv").write_bytes(b"c" * 20)
            (root / "alpha" / "notes.txt").write_bytes(b"n" * 2)

            result = scan_folder(root)

            self.assertEqual(result.total_size, 32)
            self.assertEqual(len(result.files), 4)
            self.assertEqual(
                {item.name: (item.size, item.file_count) for item in result.categories},
                {"Documents": (2, 2), "Videos": (30, 2)},
            )
            self.assertEqual(
                [(folder.name, folder.size, folder.file_count) for folder in result.subfolders],
                [("zeta", 30, 2), ("alpha", 2, 1), ("empty", 0, 0)],
            )
            self.assertEqual(
                [(file.name, file.size) for file in result.largest_files],
                [("clip.mkv", 20), ("video.mp4", 10), ("notes.txt", 2), ("root.txt", 0)],
            )
            videos = next(item for item in result.categories if item.name == "Videos")
            self.assertEqual(
                [(file.name, file.size) for file in videos.largest_files],
                [("clip.mkv", 20), ("video.mp4", 10)],
            )

    def test_only_largest_ten_immediate_subfolders_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(12):
                folder = root / f"folder-{index:02}"
                folder.mkdir()
                (folder / "value.bin").write_bytes(b"x" * index)

            result = scan_folder(root)

        self.assertEqual(result.subfolder_count, 12)
        self.assertEqual(len(result.subfolders), 10)
        self.assertEqual([folder.name for folder in result.subfolders[:3]], [
            "folder-11",
            "folder-10",
            "folder-09",
        ])

    def test_empty_folder_has_zero_totals_and_no_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = scan_folder(temporary_directory)

        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.categories, ())
        self.assertEqual(result.subfolders, ())
        self.assertEqual(result.largest_files, ())

    def test_hidden_and_nested_files_are_counted_but_empty_folders_add_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "nested" / "deeper").mkdir(parents=True)
            (root / "empty").mkdir()
            (root / ".hidden").write_bytes(b"h")
            (root / "nested" / "deeper" / "value.txt").write_bytes(b"nested")

            result = scan_folder(root)

        self.assertEqual(len(result.files), 2)
        self.assertEqual(result.total_size, 7)
        self.assertEqual(
            {folder.name: folder.file_count for folder in result.subfolders},
            {"nested": 1, "empty": 0},
        )

    def test_directory_symlinks_are_not_counted_or_traversed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "root"
            target = Path(temporary_directory) / "target"
            root.mkdir()
            target.mkdir()
            (target / "outside.txt").write_bytes(b"outside")
            link = root / "linked"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Directory symlink creation unavailable: {error}")

            result = scan_folder(root)

        self.assertEqual(result.files, ())
        self.assertEqual(result.subfolders, ())

    def test_file_symlink_is_not_counted_or_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "root"
            target = Path(temporary_directory) / "outside.txt"
            root.mkdir()
            target.write_bytes(b"outside")
            link = root / "linked.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"File symlink creation unavailable: {error}")

            result = scan_folder(root)

        self.assertEqual(result.files, ())
        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.skipped_files, 1)

    def test_reparse_files_and_directories_are_not_counted_or_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            linked_file = root / "linked-file.bin"
            linked_file.write_bytes(b"target")
            linked_directory = root / "linked-directory"
            linked_directory.mkdir()
            (linked_directory / "hidden.bin").write_bytes(b"hidden")
            linked_names = {linked_file.name, linked_directory.name}
            with patch(
                "core.scanner._is_reparse_point",
                side_effect=lambda entry: entry.name in linked_names,
            ):
                result = scan_folder(root)

        self.assertEqual(result.files, ())
        self.assertEqual(result.total_size, 0)
        self.assertEqual(result.skipped_files, 1)
        self.assertEqual(result.skipped_directories, 1)

    def test_overlapping_roots_are_scanned_once_and_progress_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child = root / "Documents"
            child.mkdir()
            (child / "report.txt").write_bytes(b"report")
            messages: list[ScanProgress] = []

            result = scan_folders(
                (root, child),
                scan_scope="Scan My Files",
                progress_callback=messages.append,
            )

        self.assertEqual(result.scan_scope, "Scan My Files")
        self.assertEqual(len(result.scan_roots), 2)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.total_size, 6)
        self.assertTrue(messages)
        self.assertEqual(messages[-1].files_scanned, len(result.files))
        self.assertGreaterEqual(messages[-1].elapsed_seconds, 0)
        self.assertFalse(any(hasattr(message, "path") for message in messages))
        self.assertTrue(messages[-1].current_directory.startswith(str(root)))

    def test_progress_updates_are_throttled_and_include_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "first").mkdir()
            (root / "second").mkdir()
            for index in range(300):
                (root / ("first" if index % 2 else "second") / f"{index}.bin").write_bytes(b"x")
            messages: list[ScanProgress] = []
            clock = itertools.count()

            with patch("core.scanner.time.monotonic", side_effect=lambda: next(clock) / 100):
                result = scan_folder(root, progress_callback=messages.append)

        self.assertEqual(messages[-1].files_scanned, 300)
        self.assertTrue(messages[-1].current_directory)
        self.assertLess(len(messages), 20)
        self.assertEqual(len(result.files), 300)

    def test_hard_link_paths_are_counted_logically_and_reported_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "original.bin"
            linked = root / "linked.bin"
            original.write_bytes(b"same content")
            try:
                os.link(original, linked)
            except OSError as error:
                self.skipTest(f"Hard-link creation unavailable: {error}")

            result = scan_folder(root)

        self.assertEqual(len(result.files), 2)
        self.assertEqual(result.total_size, 2 * len(b"same content"))
        self.assertEqual(result.hard_link_paths, 1)

    def test_access_denied_directory_is_reported_and_other_files_are_counted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "visible.txt").write_bytes(b"visible")
            blocked = root / "blocked"
            blocked.mkdir()
            (blocked / "hidden.txt").write_bytes(b"hidden")
            original_scandir = os.scandir

            def scandir(path):
                if os.path.basename(os.fspath(path)).casefold() == "blocked":
                    raise PermissionError("access denied")
                return original_scandir(path)

            with patch("core.scanner.os.scandir", side_effect=scandir):
                result = scan_folder(root)

        self.assertEqual([file.name for file in result.files], ["visible.txt"])
        self.assertEqual(result.inaccessible_directories, 1)
        self.assertEqual(result.inaccessible_items, 1)

    def test_drive_metadata_is_separate_from_scanned_file_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "small.bin").write_bytes(b"123")
            usage = DriveUsage(total=1000, used=700, free=300)

            result = scan_folders(
                (root,),
                scan_scope="Drive scan: Test",
                drive_usage=usage,
                stay_on_volume=True,
            )

        self.assertEqual(result.total_size, 3)
        self.assertEqual(result.drive_usage, usage)
        self.assertEqual(result.display_scope, "Drive scan: Test")

    def test_cancelled_scan_raises_without_returning_partial_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cancel_event = threading.Event()
            cancel_event.set()

            with self.assertRaises(ScanCancelledError):
                scan_folder(temporary_directory, cancel_event=cancel_event)

    def test_progress_callback_cancellation_is_cooperative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cancel_event = threading.Event()

            def cancel_after_initial_update(_progress: ScanProgress) -> None:
                cancel_event.set()

            with self.assertRaises(ScanCancelledError):
                scan_folder(
                    temporary_directory,
                    progress_callback=cancel_after_initial_update,
                    cancel_event=cancel_event,
                )

    def test_missing_user_folder_is_reported_as_incomplete_without_losing_accessible_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "visible.txt").write_text("visible", encoding="utf-8")
            missing = root / "not-created"

            result = scan_folders(
                (root, missing),
                scan_scope="Scan My Files",
                strict_roots=False,
            )

        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.inaccessible_directories, 1)
        self.assertEqual(result.inaccessible_items, 1)


if __name__ == "__main__":
    unittest.main()
