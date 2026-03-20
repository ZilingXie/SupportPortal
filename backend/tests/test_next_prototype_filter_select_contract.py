from __future__ import annotations

import unittest
from pathlib import Path


class NextPrototypeFilterSelectContractTests(unittest.TestCase):
    def test_filter_select_exposes_combobox_features(self) -> None:
        source = Path(
            "ui/client-ui/next-prototype/components/ui/select.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("function FilterSelect(", source)
        self.assertIn('type="hidden"', source)
        self.assertIn("allowFreeInput", source)
        self.assertIn("submitOnSelect", source)
        self.assertIn("No option found", source)
        self.assertIn('role="combobox"', source)
        self.assertIn('role="listbox"', source)
        self.assertIn('role="option"', source)
        self.assertIn("dedupeAndSortOptions", source)
        self.assertIn("aria-label={toggleAriaLabel}", source)

    def test_tickets_page_uses_filter_select(self) -> None:
        source = Path(
            "ui/client-ui/next-prototype/app/tickets/page.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn("FilterSelect", source)
        self.assertNotIn("<Select", source)


if __name__ == "__main__":
    unittest.main()
