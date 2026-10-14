from datetime import datetime, timedelta, timezone
import os
import tempfile
import threading
import unittest
from pathlib import Path

from core.duplicates import DuplicateAnalysis, DuplicateGroup
from core.recommendations import (
    RecommendationAnalysisCancelled,
    build_cleanup_recommendations,
)
from core.scanner import ScannedFile


class CleanupRecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.now = datetime(2026, 10, 14, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _add_file(
        self,
        path: Path,
        content: bytes,
        category: str,
        *,
        age_days: int = 0,
    ) -> ScannedFile:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        timestamp = (self.now - timedelta(days=age_days)).timestamp()
        os.utime(path, (timestamp, timestamp))
        return ScannedFile(path.name, str(path), len(content), category)

    def test_filters_old_installers_by_personal_scope_and_approximate_age(self) -> None:
        personal = self.root / "Downloads"
        old_installer = self._add_file(
            personal / "setup.exe",
            b"installer",
            "Installers",
            age_days=120,
        )
        recent_installer = self._add_file(
            personal / "new.msi",
            b"recent",
            "Installers",
            age_days=30,
        )
        outside_installer = self._add_file(
            self.root / "Program Files" / "setup.exe",
            b"outside",
            "Installers",
            age_days=180,
        )

        result = build_cleanup_recommendations(
            (old_installer, recent_installer, outside_installer),
            personal_roots=(str(personal),),
            age_threshold_days=90,
            large_file_threshold_bytes=100,
            protected_roots=(),
            now=self.now,
        )

        self.assertEqual([item.file for item in result.old_installers], [old_installer])
        self.assertEqual(result.old_installers[0].modified_days_ago, 120)

    def test_large_file_filter_excludes_protected_categories_and_project_paths(self) -> None:
        large_user_file = self._add_file(
            self.root / "Downloads" / "video.bin",
            b"0123456789",
            "Other",
        )
        large_vm_disk = self._add_file(
            self.root / "Downloads" / "machine.vhdx",
            b"0123456789",
            "Virtual Machine Disks",
        )
        large_database = self._add_file(
            self.root / "Downloads" / "active.sqlite",
            b"0123456789",
            "Databases",
        )
        active_project = self._add_file(
            self.root / "project" / "node_modules" / "bundle.bin",
            b"0123456789",
            "Other",
        )
        protected_application = self._add_file(
            self.root / "Program Files" / "app" / "large.bin",
            b"0123456789",
            "Other",
        )

        result = build_cleanup_recommendations(
            (
                large_user_file,
                large_vm_disk,
                large_database,
                active_project,
                protected_application,
            ),
            personal_roots=(str(self.root / "Downloads"),),
            large_file_threshold_bytes=10,
            protected_roots=(),
            now=self.now,
        )

        self.assertEqual([item.file for item in result.large_files], [large_user_file])
        self.assertEqual(result.large_files[0].modified_days_ago, 0)

    def test_only_verified_duplicate_groups_contribute_duplicate_savings(self) -> None:
        verified = DuplicateGroup(
            size=8,
            digest="a" * 64,
            paths=("a.bin", "b.bin", "c.bin"),
            verified_copy_count=3,
        )
        hardlinks_only = DuplicateGroup(
            size=8,
            digest="b" * 64,
            paths=("d.bin", "e.bin"),
            verified_copy_count=1,
        )
        analysis = DuplicateAnalysis(
            groups=(verified, hardlinks_only),
            unverified_files=(),
            candidate_files=5,
            hashed_files=4,
            elapsed_seconds=1.0,
        )

        result = build_cleanup_recommendations(
            (),
            personal_roots=(),
            duplicate_analysis=analysis,
            protected_roots=(),
            now=self.now,
        )

        self.assertEqual(result.verified_duplicates, (verified,))
        self.assertEqual(result.duplicate_recoverable_bytes, 16)
        self.assertFalse(result.duplicate_analysis_pending)

    def test_inaccessible_candidate_is_reported_not_recommended(self) -> None:
        missing = ScannedFile("missing.bin", str(self.root / "missing.bin"), 10, "Other")

        result = build_cleanup_recommendations(
            (missing,),
            personal_roots=(),
            large_file_threshold_bytes=10,
            protected_roots=(),
            now=self.now,
        )

        self.assertEqual(result.large_files, ())
        self.assertEqual(len(result.inaccessible_paths), 1)

    def test_cancellation_stops_recommendation_analysis(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(RecommendationAnalysisCancelled):
            build_cleanup_recommendations(
                (),
                personal_roots=(),
                cancel_event=cancel_event,
            )


if __name__ == "__main__":
    unittest.main()
