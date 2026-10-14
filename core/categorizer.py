"""File-extension based category classification."""

from pathlib import Path


CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "Videos": frozenset({".3g2", ".3gp", ".asf", ".avi", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ts", ".webm", ".wmv"}),
    "Images": frozenset({".bmp", ".gif", ".heic", ".ico", ".jpeg", ".jpg", ".png", ".raw", ".svg", ".tif", ".tiff", ".webp"}),
    "Documents": frozenset({".doc", ".docx", ".md", ".odt", ".pdf", ".ppt", ".pptx", ".rtf", ".tex", ".txt", ".xls", ".xlsx"}),
    "Audio": frozenset({".aac", ".aif", ".aiff", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}),
    "Archives": frozenset({".7z", ".bz2", ".cab", ".gz", ".rar", ".tar", ".xz", ".zip"}),
    "Code": frozenset({".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html", ".java", ".js", ".json", ".jsx", ".kt", ".lua", ".php", ".py", ".rb", ".rs", ".scss", ".sh", ".sql", ".tsx", ".xml", ".yaml", ".yml"}),
    "Installers": frozenset({".appx", ".appxbundle", ".exe", ".msi", ".msix", ".msixbundle"}),
    "Virtual Machine Disks": frozenset({".qcow2", ".vdi", ".vhd", ".vhdx", ".vmdk"}),
    "Disk Images": frozenset({".img", ".iso"}),
    "Databases": frozenset({".accdb", ".db", ".mdb", ".sqlite", ".sqlite3"}),
}


def categorize_file(path: str | Path) -> str:
    """Return the category for a path using its final extension."""
    extension = Path(path).suffix.lower()
    for category, extensions in CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return category
    return "Other"
