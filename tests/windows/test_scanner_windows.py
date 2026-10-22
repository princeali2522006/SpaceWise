import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.scanner import _extended_path, scan_folder
from core.scanner_backends import _query_volume_filesystem


@unittest.skipUnless(os.name == "nt", "Windows-specific integration tests.")
class WindowsScannerIntegrationTests(unittest.TestCase):
    def test_filesystem_detection_uses_windows_volume_information(self) -> None:
        system_drive = os.environ.get("SystemDrive", "C:")

        filesystem = _query_volume_filesystem(system_drive + "\\")

        self.assertTrue(filesystem)

    def test_unicode_and_long_paths_are_scanned_without_extended_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            long_directory = root / "資料" / ("d" * 45) / ("e" * 45) / ("f" * 45)
            long_file = long_directory / "résumé.txt"
            try:
                os.makedirs(_extended_path(str(long_directory)), exist_ok=True)
                with open(_extended_path(str(long_file)), "wb") as scanned_file:
                    scanned_file.write(b"unicode")
            except OSError as error:
                self.skipTest(f"Long-path fixture is unavailable: {error}")

            result = scan_folder(root)

        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].path, str(long_file))
        self.assertEqual(result.total_size, len(b"unicode"))

    def test_file_disappearing_during_metadata_read_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "stable.txt").write_bytes(b"stable")
            disappearing = root / "disappearing.txt"
            disappearing.write_bytes(b"gone")
            original_stat = os.stat

            def stat_with_disappearing_entry(path, *args, **kwargs):
                if os.fspath(path).endswith("disappearing.txt"):
                    raise FileNotFoundError("file disappeared during scan")
                return original_stat(path, *args, **kwargs)

            with patch("core.scanner.os.stat", side_effect=stat_with_disappearing_entry):
                result = scan_folder(root)

        self.assertEqual([file.name for file in result.files], ["stable.txt"])
        self.assertEqual(result.inaccessible_items, 1)


if __name__ == "__main__":
    unittest.main()
