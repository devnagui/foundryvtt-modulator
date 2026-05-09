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

from service.server import ActionEngine, AuthStore, MaintenanceLock, ResolverAPIHandler, ServiceConfig, _start_action_worker


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
            cookie_secure=False,
            auth_max_failed_attempts=3,
            auth_lockout_minutes=1,
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
        engine = ActionEngine()
        handler.action_engine = engine
        _start_action_worker(self.base_config, engine, handler.lock_store)
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
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=raw, headers=headers)
        response = conn.getresponse()
        payload_raw = response.read().decode("utf-8")
        payload = json.loads(payload_raw) if payload_raw else {}
        set_cookie = response.getheader("Set-Cookie")
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
                {"password": "supersecret", "confirmPassword": "supersecret"},
                port=port,
            )
            self.assertEqual(status, 201)
            self.assertIsNotNone(set_cookie)

            session_cookie = set_cookie.split(";", 1)[0]

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
            self.assertEqual(status, 400)
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
                {"password": "supersecret", "confirmPassword": "supersecret"},
                port=port,
            )
            self.assertEqual(status, 201)

            for _ in range(2):
                status, payload, _ = self._request(
                    "POST",
                    "/api/auth/login",
                    {"password": "wrong-password"},
                    port=port,
                )
                self.assertEqual(status, 401)
                self.assertEqual(payload.get("error"), "invalid_credentials")

            status, payload, _ = self._request(
                "POST",
                "/api/auth/login",
                {"password": "wrong-password"},
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
                {"password": "supersecret", "confirmPassword": "supersecret"},
                port=port,
            )
            self.assertEqual(status, 201)
            session_cookie = set_cookie.split(";", 1)[0]

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


if __name__ == "__main__":
    unittest.main()
