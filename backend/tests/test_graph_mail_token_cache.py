"""Atomicity guarantees for the shared Graph token cache file."""

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from backend.services.graph_mail import _read_token_cache, write_token_cache


class GraphTokenCacheTests(unittest.TestCase):
    def test_write_creates_complete_file_with_restricted_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "token.json"
            payload = {"access_token": "token-1", "refresh_token": "refresh-1"}
            write_token_cache(path, payload)
            self.assertEqual(_read_token_cache(path), payload)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            leftovers = [item for item in path.parent.iterdir() if item.name != path.name]
            self.assertEqual(leftovers, [])

    def test_overwrite_replaces_cache_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token.json"
            write_token_cache(path, {"access_token": "old"})
            write_token_cache(path, {"access_token": "new"})
            self.assertEqual(_read_token_cache(path)["access_token"], "new")
            leftovers = [item for item in path.parent.iterdir() if item.name != path.name]
            self.assertEqual(leftovers, [])

    def test_failed_write_keeps_previous_cache_and_no_temp_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "token.json"
            write_token_cache(path, {"access_token": "old"})
            with self.assertRaises(TypeError):
                write_token_cache(path, {"bad": object()})
            self.assertEqual(_read_token_cache(path)["access_token"], "old")
            leftovers = [item for item in path.parent.iterdir() if item.name != path.name]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
