import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from service.server import (
    ActionEngine,
    AuthStore,
    MaintenanceLock,
    RequestRateLimiter,
    ResolverAPIHandler,
    RuntimeConfigStore,
    ServiceConfig,
    _start_action_worker,
)


class TestServiceApiIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state_dir = root / "state"
        self.reports_dir = root / "reports"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.base_config = ServiceConfig(
            tool_root=root,
            data_root=str(root / "data"),
            cache_dir=str(root / ".cache"),
            reports_dir=self.reports_dir,
            state_dir=self.state_dir,
            auth_file=self.state_dir / "auth.json",
            bind_host="127.0.0.1",
            bind_port=0,
            python_bin=sys.executable,
            session_ttl_hours=2,
            pbkdf2_iterations=100000,
            require_foundry_offline=True,
            foundry_host="127.0.0.1",
            foundry_port=65500,
            foundry_process_name="Foundry Virtual Tabletop.exe",
            cookie_secure=False,
            auth_max_failed_attempts=3,
            auth_lockout_minutes=1,
            request_rate_limit_per_minute=200,
            max_sessions=10,
            audit_file=self.state_dir / "audit.log.jsonl",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _start_server(self):
        handler = ResolverAPIHandler
        handler.config = self.base_config
        handler.auth_store = AuthStore(self.base_config)
        handler.lock_store = MaintenanceLock(self.base_config.state_dir)
        handler.config_store = RuntimeConfigStore(self.base_config)
        handler.rate_limiter = RequestRateLimiter(self.base_config.request_rate_limit_per_minute)
        engine = ActionEngine()
        handler.action_engine = engine
        _start_action_worker(self.base_config, handler.config_store, engine, handler.lock_store)
        server = ThreadingHTTPServer((self.base_config.bind_host, 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.05)
        return server, thread

    def _request(self, method: str, path: str, body=None, cookie: str | None = None, port: int = 0):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
            for part in cookie.split(";"):
                item = part.strip()
                if item.startswith("mm_csrf="):
                    headers["X-CSRF-Token"] = item.split("=", 1)[1]
                    break
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        payload_raw = response.read().decode("utf-8")
        payload = json.loads(payload_raw) if payload_raw else {}
        set_cookie = [value for key, value in response.getheaders() if key.lower() == "set-cookie"]
        conn.close()
        return response.status, payload, set_cookie

    def test_auth_flow_and_protected_action(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]

            status, payload, _ = self._request("GET", "/api/auth/status", port=port)
            self.assertEqual(status, 200)
            self.assertEqual(payload["passwordConfigured"], False)

            status, _payload, set_cookie = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)
            self.assertIsNotNone(set_cookie)

            session_cookie = "; ".join([item.split(";", 1)[0] for item in set_cookie])

            with patch("service.server.subprocess.run") as run_mock, \
                patch.object(ResolverAPIHandler, "_foundry_status", return_value={"online": False, "status": "offline", "host": "127.0.0.1", "port": 30000, "source": "tcp"}):
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = "ok"
                run_mock.return_value.stderr = ""

                status, payload, _ = self._request(
                    "POST",
                    "/api/actions/dry-run",
                    {"modules": ["alpha", "beta"], "batchSize": 10},
                    cookie=session_cookie,
                    port=port,
                )

            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            cmd = payload["command"]
            self.assertIn("--dry-run", cmd)
            self.assertIn("--module", cmd)

            status, payload, _ = self._request(
                "POST",
                "/api/actions/force-compat",
                {"modules": [], "targetVersion": "13.351"},
                cookie=session_cookie,
                port=port,
            )
            self.assertIn(status, (400, 412))
            self.assertIn("error", payload)
            self.assertTrue(self.base_config.audit_file.exists())
            audit_lines = [line for line in self.base_config.audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertGreaterEqual(len(audit_lines), 2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_login_lockout_after_repeated_failures(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]
            status, _, _ = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)

            for _ in range(2):
                status, payload, _ = self._request(
                    "POST",
                    "/api/auth/login",
                    {"username": "admin", "password": "wrong-password"},
                    port=port,
                )
                self.assertEqual(status, 401)
                self.assertEqual(payload.get("error"), "invalid_credentials")

            status, payload, _ = self._request(
                "POST",
                "/api/auth/login",
                {"username": "admin", "password": "wrong-password"},
                port=port,
            )
            self.assertEqual(status, 429)
            self.assertEqual(payload.get("error"), "too_many_attempts")
            self.assertTrue(int(payload.get("retryAfterSeconds") or 0) > 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_submit_action_job_lifecycle(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]
            status, _, set_cookie = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)
            session_cookie = "; ".join([item.split(";", 1)[0] for item in set_cookie])

            with patch("service.server._execute_action_job", return_value={"ok": True, "returnCode": 0, "generatedAt": "now"}):
                status, payload, _ = self._request(
                    "POST",
                    "/api/actions/submit",
                    {"action": "dry-run", "payload": {"modules": ["alpha"], "batchSize": 10}},
                    cookie=session_cookie,
                    port=port,
                )
                self.assertEqual(status, 202)
                job_id = payload.get("jobId")
                self.assertTrue(job_id)

                deadline = time.time() + 2.0
                latest = {}
                while time.time() < deadline:
                    status, latest, _ = self._request("GET", f"/api/actions/jobs/{job_id}", cookie=session_cookie, port=port)
                    self.assertEqual(status, 200)
                    if latest.get("status") in {"success", "failed"}:
                        break
                    time.sleep(0.05)

                self.assertEqual(latest.get("status"), "success")
                self.assertEqual(latest.get("progress"), 100)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_set_foundry_root_validation(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]
            status, _, set_cookie = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)
            session_cookie = "; ".join([item.split(";", 1)[0] for item in set_cookie])

            status, payload, _ = self._request(
                "POST",
                "/api/config/foundry-root",
                {"path": "Z:\\path\\does\\not\\exist"},
                cookie=session_cookie,
                port=port,
            )
            self.assertEqual(status, 400)
            self.assertEqual(payload.get("error"), "invalid_foundry_root")

            root = Path(self.tmp.name) / "foundry-valid"
            (root / "Data").mkdir(parents=True, exist_ok=True)
            (root / "Logs").mkdir(parents=True, exist_ok=True)
            (root / "Config").mkdir(parents=True, exist_ok=True)
            (root / "Logs" / "diagnostics.json").write_text("{\"foundry\": {\"generation\":\"13\",\"build\":\"351\"}}", encoding="utf-8")

            status, payload, _ = self._request(
                "POST",
                "/api/config/foundry-root",
                {"path": str(root)},
                cookie=session_cookie,
                port=port,
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload.get("valid"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_suggest_module_workflow(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]
            status, _, set_cookie = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)
            session_cookie = "; ".join([item.split(";", 1)[0] for item in set_cookie])

            root = Path(self.tmp.name) / "foundry-valid"
            (root / "Data").mkdir(parents=True, exist_ok=True)
            (root / "Logs").mkdir(parents=True, exist_ok=True)
            (root / "Config").mkdir(parents=True, exist_ok=True)
            (root / "Logs" / "diagnostics.json").write_text("{\"foundry\": {\"generation\":\"13\",\"build\":\"351\"}}", encoding="utf-8")
            self._request(
                "POST",
                "/api/config/foundry-root",
                {"path": str(root)},
                cookie=session_cookie,
                port=port,
            )

            with patch("service.server._build_candidate_module") as build_mock, patch("service.server._suggest_best_release_for_module") as suggest_mock:
                build_mock.return_value = object()
                suggest_mock.return_value = {
                    "recommendedVersion": "1.2.3",
                    "isCompatible": True,
                    "checkedReleases": 20,
                }
                status, payload, _ = self._request(
                    "POST",
                    "/api/actions/suggest-module",
                    {"moduleId": "my-module", "projectUrl": "https://github.com/example/repo"},
                    cookie=session_cookie,
                    port=port,
                )
            self.assertEqual(status, 200)
            self.assertTrue(payload.get("ok"))
            self.assertEqual((payload.get("suggestion") or {}).get("recommendedVersion"), "1.2.3")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_report_v3_bootstrap_page_when_missing(self) -> None:
        server, thread = self._start_server()
        try:
            port = server.server_address[1]
            status, _, set_cookie = self._request(
                "POST",
                "/api/auth/setup",
                {"username": "admin", "password": "Sup3r$ecret1!", "confirmPassword": "Sup3r$ecret1!"},
                port=port,
            )
            self.assertEqual(status, 201)
            session_cookie = "; ".join([item.split(";", 1)[0] for item in set_cookie])

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request("GET", "/api/report/v3", headers={"Cookie": session_cookie})
            response = conn.getresponse()
            body = response.read().decode("utf-8")
            conn.close()

            self.assertEqual(response.status, 200)
            self.assertIn("Initial setup", body)
            self.assertIn("Start Initial Scan", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()


