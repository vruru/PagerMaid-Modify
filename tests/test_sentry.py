"""Exercise Sentry initialization without loading runtime configuration."""

import runpy
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


SENTRY_MODULE = Path(__file__).resolve().parents[1] / "pagermaid/modules/sentry.py"


class SentryInitializationTests(unittest.TestCase):
    def load_sentry(self, error_report, run_mock):
        sentry = MagicMock()
        config = SimpleNamespace(ERROR_REPORT=error_report, SENTRY_API="test-dsn")
        hooks = SimpleNamespace(
            on_startup=lambda: lambda function: function,
            process_error=lambda: lambda function: function,
        )
        stubs = {
            "sentry_sdk": sentry,
            "sentry_sdk.integrations": MagicMock(),
            "sentry_sdk.integrations.httpx": MagicMock(),
            "telethon": MagicMock(),
            "telethon.errors": SimpleNamespace(
                UnauthorizedError=type("UnauthorizedError", (Exception,), {}),
                UsernameInvalidError=type("UsernameInvalidError", (Exception,), {}),
            ),
            "pagermaid": MagicMock(),
            "pagermaid.config": SimpleNamespace(Config=config),
            "pagermaid.enums": SimpleNamespace(Client=object, Message=object),
            "pagermaid.hook": SimpleNamespace(Hook=hooks),
            "pagermaid.utils": SimpleNamespace(SessionFileManager=MagicMock()),
        }
        with patch.dict(sys.modules, stubs), patch("subprocess.run", run_mock):
            namespace = runpy.run_path(str(SENTRY_MODULE))
        return namespace, sentry

    def test_disabled_reporting_does_not_require_git(self):
        run_mock = MagicMock(side_effect=AssertionError("Git must not be invoked"))
        namespace, sentry = self.load_sentry(False, run_mock)

        run_mock.assert_not_called()
        sentry.init.assert_called_once_with()
        self.assertIsNone(namespace["sentry_sdk_git_hash"])

    def test_enabled_reporting_uses_git_release(self):
        run_mock = MagicMock(return_value=SimpleNamespace(stdout=b"  abc123\n"))
        namespace, sentry = self.load_sentry(True, run_mock)

        run_mock.assert_called_once_with(
            "git rev-parse HEAD", stdout=subprocess.PIPE, shell=True, check=True
        )
        sentry.init.assert_called_once()
        self.assertEqual(sentry.init.call_args.args, ("test-dsn",))
        self.assertEqual(sentry.init.call_args.kwargs["release"], "abc123")
        self.assertIs(
            sentry.init.call_args.kwargs["before_send"], namespace["sentry_before_send"]
        )


if __name__ == "__main__":
    unittest.main()
