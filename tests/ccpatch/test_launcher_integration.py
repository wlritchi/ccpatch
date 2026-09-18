"""Opt-in checks for the built launcher and its proxy capability handshake."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

import pytest


@pytest.fixture
def launcher() -> Path:
    configured = os.environ.get("CCPATCH_TEST_LAUNCHER")
    if configured is None:
        pytest.skip("set CCPATCH_TEST_LAUNCHER to the built bin/claude")
    path = Path(configured)
    assert path.is_file(), f"launcher not found: {path}"
    return path


@pytest.fixture
def capability_server() -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.headers.get("Authorization", ""))
            body = json.dumps({"openaiAuthUsable": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_launcher_accepts_authenticated_proxy(
    launcher: Path, capability_server: tuple[str, list[str]]
) -> None:
    url, requests = capability_server
    result = subprocess.run(  # noqa: S603 - Explicit opt-in build output.
        [str(launcher), "--version"],
        env={
            **os.environ,
            "CC_OPENAI_PROXY_URL": url,
            "CC_OPENAI_PROXY_AUTH_TOKEN": "test-bearer",
            "DISABLE_AUTOUPDATER": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Claude Code" in result.stdout
    assert requests == ["Bearer test-bearer"]


def test_launcher_rejects_explicit_proxy_without_token(launcher: Path) -> None:
    result = subprocess.run(  # noqa: S603 - Explicit opt-in build output.
        [str(launcher), "--version"],
        env={
            **os.environ,
            "CC_OPENAI_PROXY_URL": "http://127.0.0.1:1",
            "CC_OPENAI_PROXY_AUTH_TOKEN": "",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "requires CC_OPENAI_PROXY_AUTH_TOKEN" in result.stderr
    assert not result.stdout
