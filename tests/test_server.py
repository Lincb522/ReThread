import http.client
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import (
    CodexController,
    ConfirmationStore,
    Handler,
    OperationBusy,
    OperationError,
    SessionStore,
    build_startup_banner,
    is_loopback_host,
    is_temporary_workspace,
    parse_session,
    repair_legacy_text,
    validate_request_target,
)


SESSION_ID = "00000000-0000-0000-0000-000000000001"
OTHER_ID = "00000000-0000-0000-0000-000000000002"


def create_session(home: Path, session_id: str = SESSION_ID, archived: bool = False) -> Path:
    root = "archived_sessions" if archived else "sessions"
    folder = home / root / "2026" / "01" / "01"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{session_id}.jsonl"
    events = [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
         "payload": {"id": session_id, "cwd": "C:/demo"}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "真实问题"}]}},
    ]
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events), encoding="utf-8")
    return path


class SessionParserTests(unittest.TestCase):
    def test_recognizes_codex_managed_temporary_workspaces(self):
        home = Path("C:/Users/demo")
        self.assertTrue(is_temporary_workspace("C:/Users/demo/Documents/Codex/2026-06-21/new-chat", home))
        self.assertTrue(is_temporary_workspace("", home))
        self.assertFalse(is_temporary_workspace("C:/Users/demo/Desktop/my-project", home))

    def test_repairs_legacy_gbk_paths(self):
        broken = "D:\\01 " + "科研目录".encode("gbk").decode("latin-1")
        self.assertEqual(repair_legacy_text(broken), "D:\\01 科研目录")

    def test_reads_only_visible_messages_and_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"rollout-{SESSION_ID}.jsonl"
            events = [
                {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                 "payload": {"id": SESSION_ID, "cwd": "C:/demo"}},
                {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item",
                 "payload": {"type": "message", "role": "user", "content": [
                     {"type": "input_text", "text": "<environment_context>hidden</environment_context>"},
                     {"type": "input_text", "text": "<recommended_plugins>hidden</recommended_plugins>"},
                     {"type": "input_text", "text": "你好"},
                 ]}},
                {"timestamp": "2026-01-01T00:00:02Z", "type": "response_item",
                 "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                             "content": [{"type": "output_text", "text": "完成"}]}},
            ]
            path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n{broken", encoding="utf-8")
            result = parse_session(path)
            self.assertEqual([item["text"] for item in result["messages"]], ["你好", "完成"])

    def test_store_reads_active_and_archived_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_session(home)
            create_session(home, OTHER_ID, archived=True)
            rows = SessionStore(home).list()
            self.assertEqual(len(rows), 2)
            self.assertEqual({item["is_archived"] for item in rows}, {False, True})

    def test_store_rejects_invalid_or_traversal_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_session(home)
            store = SessionStore(home)
            self.assertIsNone(store.find_path("../../other"))
            self.assertIsNone(store.get("not-a-uuid"))


class CodexControllerTests(unittest.TestCase):
    def make_controller(self, runner=None, rpc_runner=None, event_logger=None):
        return CodexController(
            Path("C:/codex-home"), command=__file__, cli_runner=runner,
            rpc_runner=rpc_runner, event_logger=event_logger or (lambda _message: None),
        )

    def test_delete_uses_official_force_command_and_codex_home(self):
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "", "")

        self.make_controller(runner=runner).delete(SESSION_ID)
        args, kwargs = calls[0]
        self.assertEqual(args[1:], ["delete", "--force", SESSION_ID])
        self.assertEqual(Path(kwargs["env"]["CODEX_HOME"]), Path("C:/codex-home").resolve())
        self.assertFalse(kwargs["check"])

    def test_archive_and_unarchive_use_official_commands(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args[1:])
            return subprocess.CompletedProcess(args, 0, "", "")

        controller = self.make_controller(runner=runner)
        controller.set_archived(SESSION_ID, True)
        controller.set_archived(SESSION_ID, False)
        self.assertEqual(calls, [["archive", SESSION_ID], ["unarchive", SESSION_ID]])

    def test_rename_uses_official_app_server_method(self):
        calls = []
        controller = self.make_controller(rpc_runner=lambda method, params: calls.append((method, params)))
        controller.rename(SESSION_ID, "  新标题  ")
        self.assertEqual(calls, [("thread/name/set", {"threadId": SESSION_ID, "name": "新标题"})])

    def test_official_cli_command_and_result_are_logged(self):
        logs = []

        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, "", "")

        self.make_controller(runner=runner, event_logger=logs.append).delete(SESSION_ID)
        output = "\n".join(logs)
        self.assertIn(f"EXEC official-cli command=codex delete --force {SESSION_ID}", output)
        self.assertIn(f"OK official-cli command=codex delete --force {SESSION_ID}", output)
        self.assertIn("elapsed_ms=", output)

    def test_console_log_colors_make_codex_statuses_distinct(self):
        prefix = "[2026-07-16 12:00:00] [Codex History Manager]"
        cases = {
            "EXEC": "\033[1;36mEXEC\033[0m",
            "OK": "\033[1;32mOK\033[0m",
            "FAIL": "\033[1;31mFAIL\033[0m",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                colored = CodexController._format_console_message(
                    f"{prefix} {status} official-cli command=codex archive test", True,
                )
                self.assertIn("\033[1;35m[Codex History Manager]\033[0m", colored)
                self.assertIn(expected, colored)
                self.assertEqual(
                    CodexController._format_console_message(f"{prefix} {status} detail", False),
                    f"{prefix} {status} detail",
                )

    def test_startup_banner_contains_audit_details_and_author(self):
        banner = build_startup_banner(
            "http://127.0.0.1:8765", Path("C:/Users/test/.codex"), "C:/nodejs/codex.cmd",
        )
        output = "\n".join(banner)
        self.assertIn("CODEX HISTORY MANAGER", output)
        self.assertIn("Web UI    : http://127.0.0.1:8765", output)
        self.assertIn("Writes    : official Codex CLI / app-server", output)
        self.assertIn("by xinleung", output)
        self.assertTrue(banner[0].startswith("+---"))
        self.assertEqual(len(banner[0]), len(banner[-1]))

    def test_rename_log_redacts_title_content(self):
        logs = []
        private_title = "private project name"
        controller = self.make_controller(
            rpc_runner=lambda _method, _params: None, event_logger=logs.append,
        )
        controller.rename(SESSION_ID, private_title)
        output = "\n".join(logs)
        self.assertIn(f"EXEC official-app-server method=thread/name/set thread_id={SESSION_ID}", output)
        self.assertIn(f"title_length={len(private_title)}", output)
        self.assertIn("OK official-app-server method=thread/name/set", output)
        self.assertNotIn(private_title, output)

    def test_invalid_id_never_reaches_codex(self):
        controller = self.make_controller(runner=lambda *args, **kwargs: self.fail("runner must not be called"))
        with self.assertRaises(OperationError):
            controller.delete("../../other")

    def test_nonzero_codex_exit_is_safe_operation_error(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, "", "private detail\nSession is active")

        with self.assertRaisesRegex(OperationError, "Session is active"):
            self.make_controller(runner=runner).delete(SESSION_ID)

    def test_write_lock_fails_fast(self):
        controller = self.make_controller()
        controller._write_lock.acquire()
        try:
            with self.assertRaises(OperationBusy):
                controller.delete(SESSION_ID)
        finally:
            controller._write_lock.release()

    def test_batch_reports_partial_failures_without_hiding_success(self):
        def runner(args, **kwargs):
            code = 1 if args[-1] == OTHER_ID else 0
            return subprocess.CompletedProcess(args, code, "", "busy" if code else "")

        result = self.make_controller(runner=runner).delete_many([SESSION_ID, OTHER_ID])
        self.assertFalse(result["deleted"])
        self.assertEqual(result["deleted_ids"], [SESSION_ID])
        self.assertEqual(result["sessions_failed"], 1)


class WebSafetyTests(unittest.TestCase):
    def test_confirmation_is_bound_one_time_and_exact(self):
        plans = ConfirmationStore(ttl_seconds=60)
        token = plans.issue([SESSION_ID], SESSION_ID[:8])
        with self.assertRaises(OperationError):
            plans.consume(token, [OTHER_ID], SESSION_ID[:8])
        with self.assertRaises(OperationError):
            plans.consume(token, [SESSION_ID], SESSION_ID[:8])

        token = plans.issue([SESSION_ID], SESSION_ID[:8])
        plans.consume(token, [SESSION_ID], SESSION_ID[:8])
        with self.assertRaises(OperationError):
            plans.consume(token, [SESSION_ID], SESSION_ID[:8])

    def test_only_loopback_hosts_and_same_origin_ports_are_allowed(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("example.com"))
        validate_request_target("127.0.0.1:8765", "http://localhost:8765", 8765)
        with self.assertRaises(OperationError):
            validate_request_target("evil.example:8765", None, 8765)
        with self.assertRaises(OperationError):
            validate_request_target("127.0.0.1:8765", "https://evil.example", 8765)
        with self.assertRaises(OperationError):
            validate_request_target("127.0.0.1:9999", None, 8765)

    def test_http_write_requires_csrf_and_same_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            create_session(home)
            rpc_calls = []
            Handler.store = SessionStore(home)
            Handler.controller = CodexController(
                home, command=__file__, rpc_runner=lambda method, params: rpc_calls.append((method, params))
            )
            Handler.confirmations = ConfirmationStore()
            Handler.csrf_token = "test-csrf-token"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            host = f"127.0.0.1:{port}"
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port)
                connection.request("GET", "/api/status", headers={"Host": host})
                response = connection.getresponse()
                status = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(status["csrf_token"], "test-csrf-token")
                self.assertEqual(response.getheader("X-Frame-Options"), "DENY")

                body = json.dumps({"title": "New title"})
                connection.request("POST", f"/api/sessions/{SESSION_ID}/rename", body=body,
                                   headers={"Host": host, "Content-Type": "application/json",
                                            "Origin": f"http://127.0.0.1:{port}"})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                self.assertEqual(rpc_calls, [])

                connection.request("POST", f"/api/sessions/{SESSION_ID}/rename", body=body,
                                   headers={"Host": host, "Content-Type": "application/json",
                                            "Origin": "https://evil.example",
                                            "X-Codex-CSRF": "test-csrf-token"})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 403)

                connection.request("POST", f"/api/sessions/{SESSION_ID}/rename", body=body,
                                   headers={"Host": host, "Content-Type": "application/json",
                                            "Origin": f"http://localhost:{port}",
                                            "X-Codex-CSRF": "test-csrf-token"})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(rpc_calls[0][0], "thread/name/set")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
