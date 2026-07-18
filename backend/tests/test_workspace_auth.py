from __future__ import annotations

import unittest

from backend.services.workspace_auth import (
    create_workspace_access_token,
    hash_workspace_password,
    verify_workspace_access_token,
    verify_workspace_password,
)


class WorkspaceAuthTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self) -> None:
        first = hash_workspace_password("strong-password-1")
        second = hash_workspace_password("strong-password-1")

        self.assertNotEqual(first, second)
        self.assertTrue(verify_workspace_password("strong-password-1", first))
        self.assertFalse(verify_workspace_password("wrong-password", first))

    def test_access_token_is_signed_and_expires(self) -> None:
        token = create_workspace_access_token(
            {"account_id": "Maya", "display_name": "Maya", "role": "engineer"},
            secret="test-secret",
            now=100,
            ttl_seconds=60,
        )

        principal = verify_workspace_access_token(token, secret="test-secret", now=120)
        expired = verify_workspace_access_token(token, secret="test-secret", now=161)
        tampered = verify_workspace_access_token(f"{token}x", secret="test-secret", now=120)

        self.assertIsNotNone(principal)
        assert principal is not None
        self.assertEqual(principal.account_id, "Maya")
        self.assertIsNone(expired)
        self.assertIsNone(tampered)


if __name__ == "__main__":
    unittest.main()
