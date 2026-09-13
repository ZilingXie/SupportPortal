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

    def test_short_bootstrap_passwords_hash_and_verify(self) -> None:
        # WORKSPACE_BOOTSTRAP_ADMIN_PASSWORD=admin must not crash startup;
        # invitation strength lives in the invitation API layer instead.
        encoded = hash_workspace_password("admin")
        self.assertTrue(verify_workspace_password("admin", encoded))
        self.assertFalse(verify_workspace_password("admins", encoded))

    def test_empty_password_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            hash_workspace_password("")

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


class WorkspaceInvitationPasswordPolicyTests(unittest.TestCase):
    def test_invitation_complete_request_keeps_min_length_contract(self) -> None:
        # the hash-layer relaxation must not weaken invitation passwords:
        # the API model still rejects short ones with a 422 before hashing
        # (asserted on source, the repo's established pattern for main.py
        # contracts whose import requires runtime env)
        from pathlib import Path

        main_source = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(
            encoding="utf-8"
        )
        start = main_source.index("class WorkspaceInvitationCompleteRequest")
        end = main_source.index("class EngineerScheduleShiftRequest")
        model_source = main_source[start:end]
        self.assertIn('password: str = Field(min_length=10, max_length=512)', model_source)
        self.assertIn(
            'confirm_password: str = Field(min_length=10, max_length=512)', model_source
        )
