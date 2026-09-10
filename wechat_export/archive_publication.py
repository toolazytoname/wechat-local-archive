"""Build full archives on their destination filesystem; publish one complete tree."""
from __future__ import annotations

import os
from pathlib import Path

from wechat_export.output_locations import directory_identity, check_identity
from wechat_export.scratch import ScratchSpace


class ArchivePublication:
    def __init__(self, parent: Path, name: str):
        if not name or Path(name).name != name or name in {'.', '..'}:
            raise ValueError('invalid_archive_name')
        self.parent_identity = directory_identity(parent)
        self.parent = Path(self.parent_identity['path'])
        self.final = self.parent / name
        self.reservation = None
        self.space = None
        self.published = False

    def __enter__(self):
        check_identity(self.parent_identity)
        self.final.mkdir(mode=0o700, exist_ok=False)
        self.reservation = directory_identity(self.final)
        try:
            self.space = ScratchSpace(self.parent, 'full-archive')
            self.space.__enter__()
        except BaseException:
            self._remove_empty_reservation()
            raise
        return self

    @property
    def staging_data_root(self):
        return self.space.payload

    def publish(self, stage: Path, check=lambda: None):
        check()
        check_identity(self.parent_identity)
        check_identity(self.reservation)
        if any(self.final.iterdir()):
            raise FileExistsError('archive_reservation_occupied')
        if not stage.is_relative_to(self.staging_data_root) or stage.is_symlink():
            raise ValueError('archive_stage_outside_owned_space')
        # Stage and reservation are created under the same selected parent.
        # No cross-device rename fallback that could expose half a result.
        os.rename(stage, self.final)
        self.published = True
        return self.final

    def _remove_empty_reservation(self):
        if self.reservation and not self.published:
            try:
                check_identity(self.parent_identity)
                check_identity(self.reservation)
                self.final.rmdir()  # Never recursively delete a reservation.
            except (OSError, ValueError):
                pass

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.space:
                self.space.__exit__(exc_type, exc, tb)
        finally:
            self._remove_empty_reservation()
