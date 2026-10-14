import tempfile
import threading
import unittest
from pathlib import Path

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
