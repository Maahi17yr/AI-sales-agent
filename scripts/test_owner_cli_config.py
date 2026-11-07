"""Read-only owner CLI configuration checks using fictional values only."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from scripts import promote_owner


class OwnerCliConfigTests(unittest.TestCase):
    def invoke(self, uri, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"DATABASE_URL": uri}), \
             patch("sys.argv", ["promote_owner.py", *args]), \
             patch.object(promote_owner, "create_tables") as create, \
             patch.object(promote_owner, "promote_registered_customer") as promote, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                promote_owner.main()
                exit_code = 0
            except SystemExit as exc:
                exit_code = exc.code
        return exit_code, stdout.getvalue() + stderr.getvalue(), create, promote

    def test_check_config_accepts_valid_shape_without_database_access(self):
        code, output, create, promote = self.invoke(
            "postgresql://db.example.test/fictional", "--check-config"
        )
        self.assertEqual(code, 0)
        self.assertIn("no database connection", output)
        create.assert_not_called()
        promote.assert_not_called()

    def test_check_config_rejects_common_paste_errors_without_leaking_uri(self):
        uri = "postgresql://db.example.test/fictional"
        mistakes = {
            "prefix": ("DATABASE_URL=" + uri, "DATABASE_URL= prefix"),
            "quotes": ('"' + uri + '"', "quotation marks"),
            "psql": ("psql " + uri, "psql command"),
            "wrong_scheme": ("mysql://db.example.test/fictional", "PostgreSQL URI"),
            "no_database": ("postgresql://db.example.test", "PostgreSQL URI"),
            "missing": ("", "missing"),
        }
        for name, (value, message) in mistakes.items():
            with self.subTest(name=name):
                code, output, create, promote = self.invoke(value, "--check-config")
                self.assertEqual(code, 2)
                self.assertIn(message, output)
                self.assertNotIn(uri, output)
                create.assert_not_called()
                promote.assert_not_called()

    def test_invalid_uri_blocks_normal_promotion_before_schema_changes(self):
        uri = "DATABASE_URL=postgresql://db.example.test/fictional"
        code, output, create, promote = self.invoke(
            uri, "--email", "fictional@example.test",
            "--confirm-email", "fictional@example.test", "--acknowledge-owner-access",
        )
        self.assertEqual(code, 2)
        self.assertNotIn(uri, output)
        create.assert_not_called()
        promote.assert_not_called()

    def test_check_config_rejects_promotion_arguments(self):
        code, _, create, promote = self.invoke(
            "postgresql://db.example.test/fictional", "--check-config",
            "--email", "fictional@example.test",
        )
        self.assertEqual(code, 2)
        create.assert_not_called()
        promote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
