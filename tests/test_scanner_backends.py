import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.scanner import scan_folder
from core.scanner_backends import (
    RecursiveScanner,
    create_backend,
    select_backend,
)


class ScannerBackendSelectionTests(unittest.TestCase):
    def test_selected_folder_uses_recursive_backend_without_volume_probe(self) -> None:
        with patch("core.scanner_backends._query_volume_filesystem") as probe:
            selection = select_backend(
                ("C:\\Users\\test",),
                scan_scope="Selected folder",
                stay_on_volume=False,
            )

        self.assertEqual(selection.label, "Standard scan")
        self.assertIsInstance(create_backend(selection), RecursiveScanner)
        probe.assert_not_called()

    def test_ntfs_drive_uses_explicit_recursive_fallback(self) -> None:
        with patch(
            "core.scanner_backends._query_volume_filesystem",
            return_value="NTFS",
        ):
            selection = select_backend(
                ("C:\\",),
                scan_scope="Drive scan: C:\\",
                stay_on_volume=True,
            )

        self.assertEqual(selection.filesystem, "NTFS")
        self.assertIn("fast NTFS scan unavailable", selection.label)
        self.assertIsNotNone(selection.fallback_reason)

    def test_unsupported_filesystem_uses_standard_scan(self) -> None:
        with patch(
            "core.scanner_backends._query_volume_filesystem",
            return_value="exFAT",
        ):
            selection = select_backend(
                ("E:\\",),
                scan_scope="Drive scan: E:\\",
                stay_on_volume=True,
            )

        self.assertEqual(selection.filesystem, "exFAT")
        self.assertEqual(selection.label, "Standard scan")

    def test_permission_denied_during_detection_falls_back_cleanly(self) -> None:
        with patch(
            "core.scanner_backends._query_volume_filesystem",
            side_effect=PermissionError("access denied"),
        ):
            selection = select_backend(
                ("C:\\",),
                scan_scope="Drive scan: C:\\",
                stay_on_volume=True,
            )

        self.assertEqual(selection.label, "Standard scan · fast scan unavailable")
        self.assertEqual(selection.fallback_reason, "access denied")

    def test_recursive_backend_preserves_result_semantics_and_records_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "nested").mkdir()
            (root / "nested" / "file.txt").write_bytes(b"spacewise")

            result = RecursiveScanner("Standard scan").scan(
                (str(root),),
                scan_scope="Selected folder",
                strict_roots=True,
            )
            expected = scan_folder(root)

        self.assertEqual(result.files, expected.files)
        self.assertEqual(result.total_size, expected.total_size)
        self.assertEqual(result.categories, expected.categories)
        self.assertEqual(result.subfolders, expected.subfolders)
        self.assertEqual(result.scanner_backend, "Standard scan")


if __name__ == "__main__":
    unittest.main()
