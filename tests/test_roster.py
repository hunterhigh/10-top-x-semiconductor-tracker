import json
import tempfile
import unittest
from pathlib import Path

from scripts.roster import RosterError, github_matrix, load_bloggers, select_bloggers, verify_artifacts


def account(index, *, active=True, signal_type="opinion"):
    return {
        "id": f"account_{index}",
        "display_name": f"Account {index}",
        "handle": f"@account_{index}",
        "x_url": f"https://x.com/account_{index}",
        "signal_type": signal_type,
        "active": active,
    }


class RosterTests(unittest.TestCase):
    def write_config(self, root, rows):
        path = Path(root) / "bloggers.json"
        path.write_text(json.dumps({"bloggers": rows}), encoding="utf-8")
        return path

    def test_dynamic_active_counts(self):
        for count in (8, 11, 12):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td:
                rows = load_bloggers(self.write_config(td, [account(i) for i in range(count)]))
                self.assertEqual(len(rows), count)
                self.assertEqual(len(github_matrix(rows)["include"]), count)

    def test_inactive_metadata_is_retained_but_not_selected(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.write_config(td, [account(1), account(2, active=False)])
            self.assertEqual([row["id"] for row in load_bloggers(path)], ["account_1"])
            self.assertEqual(len(load_bloggers(path, active_only=False)), 2)

    def test_rejects_empty_duplicate_and_invalid_rosters(self):
        cases = [
            [],
            [account(1), account(1)],
            [{**account(1), "signal_type": "neutral"}],
            [{**account(1), "x_url": "https://x.com/different"}],
        ]
        for rows in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(RosterError):
                    load_bloggers(self.write_config(td, rows))

    def test_requested_ids_must_be_active_and_preserve_requested_order(self):
        rows = [account(1), account(2), account(3)]
        self.assertEqual(
            [row["id"] for row in select_bloggers(rows, "account_3,account_1")],
            ["account_3", "account_1"],
        )
        with self.assertRaises(RosterError):
            select_bloggers(rows, "missing")

    def test_artifact_set_must_equal_selected_roster(self):
        rows = [account(1), account(2)]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "blogger-data-account_1").mkdir()
            with self.assertRaises(RosterError):
                verify_artifacts(root, rows)
            (root / "blogger-data-account_2").mkdir()
            verify_artifacts(root, rows)


if __name__ == "__main__":
    unittest.main()
