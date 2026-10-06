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

    def test_dynamic_disable_and_reenable(self):
        namespace, sdk = self.load_sentry(True, MagicMock(return_value=SimpleNamespace(stdout=b"abc")))
        options = sdk.init.call_args.kwargs
        event = {"message": "fixture"}
        namespace["Config"].ERROR_REPORT = False
        for name in ["before_send", "before_send_transaction", "before_send_log"]:
            self.assertIsNone(options[name](event, {}))
        self.assertEqual(options["traces_sampler"]({}), 0.0)
        namespace["Config"].ERROR_REPORT = True
        with patch.dict(options["before_send"].__globals__, time=lambda: 10**12):
            self.assertEqual(options["before_send"](event, {}), event)
        self.assertEqual(options["before_send_transaction"](event, {}), event)
        self.assertEqual(options["before_send_log"](event, {}), event)
        self.assertEqual(options["traces_sampler"]({}), 1.0)

    def test_session_cleanup_still_runs_when_dynamically_disabled(self):
        namespace, _ = self.load_sentry(True, MagicMock(return_value=SimpleNamespace(stdout=b"abc")))
        namespace["Config"].ERROR_REPORT = False
        error = namespace["UnauthorizedError"]()
        with self.assertRaises(SystemExit):
            namespace["sentry_before_send"]({}, {"exc_info": (type(error), error, None)})
        namespace["SessionFileManager"].safe_remove_session.assert_called_once()

    def test_real_sdk_offline_transport_drops_disabled_events(self):
        import sentry_sdk
        from sentry_sdk.transport import Transport
        from sentry_sdk.integrations.httpx import HttpxIntegration
        import httpx

        namespace, sdk = self.load_sentry(True, MagicMock(return_value=SimpleNamespace(stdout=b"abc")))
        options = sdk.init.call_args.kwargs

        class MemoryTransport(Transport):
            def __init__(self):
                super().__init__()
                self.envelopes = []

            def capture_envelope(self, envelope):
                self.envelopes.append(envelope)

        transport = MemoryTransport()
        client = sentry_sdk.Client(
            dsn="https://fixture@example.invalid/1", transport=transport,
            default_integrations=False, auto_session_tracking=False,
            integrations=[HttpxIntegration()],
            before_send=options["before_send"],
            before_send_transaction=options["before_send_transaction"],
            before_send_log=options["before_send_log"],
            traces_sampler=options["traces_sampler"],
        )
        namespace["Config"].ERROR_REPORT = False
        client.capture_event({"message": "disabled web error"})
        client.capture_event({"type": "transaction", "transaction": "disabled web", "contexts": {"trace": {"trace_id": "a" * 32, "span_id": "b" * 16}}})
        self.assertEqual(transport.envelopes, [])
        namespace["Config"].ERROR_REPORT = True
        with patch.dict(options["before_send"].__globals__, time=lambda: 10**12):
            client.capture_event({"message": "reenabled"})
        self.assertEqual(len(transport.envelopes), 1)
        # A sampled HTTP transaction started while enabled must also be dropped
        # if reporting is disabled before it finishes. MockTransport does no I/O.
        with sentry_sdk.new_scope() as scope:
            scope.set_client(client)
            with sentry_sdk.start_transaction(name="fixture-http"):
                namespace["Config"].ERROR_REPORT = False
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as http:
                    http.get("https://example.invalid/fixture")
        self.assertEqual(len(transport.envelopes), 1)
        client.close()


if __name__ == "__main__":
    unittest.main()
