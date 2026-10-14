import unittest

from core.categorizer import CATEGORY_EXTENSIONS, categorize_file


class CategorizerTests(unittest.TestCase):
    def test_known_extensions_map_to_expected_categories(self) -> None:
        expected = {
            ".mp4": "Videos",
            ".png": "Images",
            ".pdf": "Documents",
            ".flac": "Audio",
            ".zip": "Archives",
            ".py": "Code",
            ".exe": "Installers",
            ".msixbundle": "Installers",
            ".vmdk": "Virtual Machine Disks",
            ".vhdx": "Virtual Machine Disks",
            ".iso": "Disk Images",
            ".img": "Disk Images",
            ".sqlite3": "Databases",
            ".accdb": "Databases",
        }
        for extension, category in expected.items():
            with self.subTest(extension=extension):
                self.assertEqual(categorize_file(f"sample{extension}"), category)

    def test_unknown_and_extensionless_files_fall_back_to_other(self) -> None:
        self.assertEqual(categorize_file("unknown.extension"), "Other")
        self.assertEqual(categorize_file("LICENSE"), "Other")
        self.assertEqual(categorize_file("README."), "Other")

    def test_each_mapped_extension_belongs_to_exactly_one_category(self) -> None:
        owners: dict[str, list[str]] = {}
        for category, extensions in CATEGORY_EXTENSIONS.items():
            for extension in extensions:
                owners.setdefault(extension, []).append(category)

        self.assertTrue(all(len(categories) == 1 for categories in owners.values()))
        self.assertEqual(categorize_file("package.EXE"), "Installers")
        self.assertEqual(categorize_file("database.DB"), "Databases")


if __name__ == "__main__":
    unittest.main()
