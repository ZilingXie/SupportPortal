from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_ROUTE = REPO_ROOT / "ui" / "client-ui" / "next-prototype" / "app" / "api" / "chat" / "route.ts"
TITLE_ROUTE = REPO_ROOT / "ui" / "client-ui" / "next-prototype" / "app" / "api" / "generate-title" / "route.ts"


class NextPrototypeModelContractTests(unittest.TestCase):
    def test_chat_route_uses_gpt_5_4_default_model(self) -> None:
        source = CHAT_ROUTE.read_text(encoding="utf-8")

        self.assertIn("PROTOTYPE_OPENAI_MODEL", source)
        self.assertIn("openai/gpt-5.4", source)

    def test_generate_title_route_uses_gpt_5_4_default_model(self) -> None:
        source = TITLE_ROUTE.read_text(encoding="utf-8")

        self.assertIn("PROTOTYPE_OPENAI_MODEL", source)
        self.assertIn("openai/gpt-5.4", source)


if __name__ == "__main__":
    unittest.main()
