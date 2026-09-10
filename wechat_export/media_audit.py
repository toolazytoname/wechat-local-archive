"""Read-only, local media candidate observations; never copies/downloads files."""
from __future__ import annotations
import os
from pathlib import Path
from wechat_export.media_resolve import resolve_media_file, sniff_media
from wechat_export.media_stream import open_local_media


class MediaProbe:
    def __init__(self, media_root: Path | None, hardlink_db: Path | None = None):
        self.root = media_root
        self.hardlink = hardlink_db

    @classmethod
    def from_archive(cls, root: Path):
        media = root / 'media'
        hardlink = root / 'hardlink.db'
        return cls(media if media.is_dir() and not media.is_symlink() else None,
                   hardlink if hardlink.is_file() and not hardlink.is_symlink() else None)

    def observe(self, rec: dict, ref: dict) -> dict:
        result = dict(ref, binary_included=False)
        if not self.root:
            return dict(result, availability='not_checked', reason='no_registered_media_root')
        if ref['slot'] != 'primary' or ref['kind'] not in {'image', 'voice', 'video'}:
            return dict(result, availability='unsupported', reason='reference_layout_not_supported')
        try:
            if self.root.is_symlink() or not self.root.is_dir():
                return dict(result, availability='not_checked', reason='media_root_unavailable')
            text = rec.get('text') or ''
            if not isinstance(text, str) or len(text) > 256000:
                return dict(result, availability='not_checked', reason='metadata_inspection_limit')
            resolved = resolve_media_file(media_root=self.root, hardlink_db=self.hardlink,
                conversation_id=rec.get('conversation_id') or '', timestamp_utc=rec.get('timestamp_utc'),
                payload=text, type_name=ref['kind'], media_kind=ref['kind'])
            if not resolved.get('md5'):
                return dict(result, availability='not_checked', reason='usable_reference_missing')
            if not resolved.get('found'):
                return dict(result, availability='not_found_in_supported_layout', reason='local_candidates_not_found')
            candidate = Path(resolved['path'])
            root = self.root.resolve(strict=True)
            with open_local_media(root, candidate) as (stream, size):
                before = os.fstat(stream.fileno())
                mime = sniff_media(stream.read(32))
                after = os.fstat(stream.fileno())
            fields = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if fields(before) != fields(after) or fields(after) != fields(candidate.stat()):
                return dict(result, availability='inspection_failed', reason='candidate_changed_during_inspection')
            expected = {'image': 'image/', 'voice': 'audio/', 'video': 'video/'}[ref['kind']]
            status = 'local_candidate' if mime and mime.startswith(expected) else (
                'preview_only' if ref['kind'] == 'video' and mime and mime.startswith('image/') else 'opaque_candidate')
            return dict(result, availability=status, reason='header_observation_only',
                        candidate_relative_path=candidate.relative_to(root).as_posix(), size_bytes=size,
                        header_mime=mime, content_integrity_verified=False)
        except Exception:
            # No source paths, SQL text or arbitrary payloads enter an error field.
            return dict(result, availability='inspection_failed', reason='local_inspection_failed')
