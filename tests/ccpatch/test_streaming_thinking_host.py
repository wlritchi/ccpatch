"""Opt-in interactive checks against a patched executable and local SSE server."""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def test_thinking_renders_before_block_completion(tmp_path: Path) -> None:
    binary = os.environ.get('CCPATCH_THINKING_BINARY')
    if binary is None:
        pytest.skip('set CCPATCH_THINKING_BINARY for the interactive host check')
    if os.name != 'posix':
        pytest.skip('the interactive host check requires a POSIX terminal')
    script = shutil.which('script')
    if script is None:
        pytest.skip('the interactive host check requires util-linux script')
    first_seen = threading.Event()
    second_seen = threading.Event()
    stopped = threading.Event()
    errors: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            message = {
                'id': 'msg_test',
                'type': 'message',
                'role': 'assistant',
                'model': 'claude-sonnet-4-6',
                'content': [],
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 10, 'output_tokens': 0},
            }
            self.send_response(200)
            if not body.get('stream'):
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            **message,
                            'content': [{'type': 'text', 'text': 'Test title'}],
                            'stop_reason': 'end_turn',
                        }
                    ).encode()
                )
                return
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()

            def send(frame: dict[str, object]) -> None:
                self.wfile.write(
                    f'event: {frame["type"]}\ndata: {json.dumps(frame)}\n\n'.encode()
                )
                self.wfile.flush()

            try:
                send({'type': 'message_start', 'message': message})
                send(
                    {
                        'type': 'content_block_start',
                        'index': 0,
                        'content_block': {
                            'type': 'thinking',
                            'thinking': '',
                            'signature': '',
                        },
                    }
                )
                for text, seen in (
                    ('EARLY_THINKING_MARKER first streamed thought.', first_seen),
                    ('\n\nSECOND_THINKING_MARKER **formatted thought**.', second_seen),
                ):
                    send(
                        {
                            'type': 'content_block_delta',
                            'index': 0,
                            'delta': {'type': 'thinking_delta', 'thinking': text},
                        }
                    )
                    if not seen.wait(15):
                        errors.append(
                            f'thinking did not render before completion: {text}'
                        )
                        return
                send(
                    {
                        'type': 'content_block_delta',
                        'index': 0,
                        'delta': {'type': 'signature_delta', 'signature': 'test'},
                    }
                )
                stopped.set()
                send({'type': 'content_block_stop', 'index': 0})
                send(
                    {
                        'type': 'content_block_start',
                        'index': 1,
                        'content_block': {'type': 'text', 'text': ''},
                    }
                )
                send(
                    {
                        'type': 'content_block_delta',
                        'index': 1,
                        'delta': {
                            'type': 'text_delta',
                            'text': 'FINAL_ANSWER_MARKER done.',
                        },
                    }
                )
                send({'type': 'content_block_stop', 'index': 1})
                send(
                    {
                        'type': 'message_delta',
                        'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                        'usage': {'output_tokens': 40},
                    }
                )
                send({'type': 'message_stop'})
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    config = tmp_path / 'config'
    config.mkdir()
    (config / '.claude.json').write_text(
        json.dumps(
            {
                'hasCompletedOnboarding': True,
                'theme': 'dark',
                'projects': {
                    str(tmp_path): {
                        'hasTrustDialogAccepted': True,
                        'hasCompletedProjectOnboarding': True,
                    }
                },
                'customApiKeyResponses': {'approved': ['local-test-key']},
            }
        )
    )
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                'ANTHROPIC_',
                'CLAUDE',
                'CCPATCH_',
                'CC_OPENAI_',
                'OPENAI_',
                'AWS_',
                'GOOGLE_',
            )
        )
    }
    env.update(
        {
            'HOME': str(tmp_path),
            'CLAUDE_CONFIG_DIR': str(config),
            'ANTHROPIC_API_KEY': 'local-test-key',
            'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_AUTOUPDATER': '1',
            'TERM': 'xterm-256color',
            'TEST_BINARY': binary,
        }
    )
    process = subprocess.Popen(  # noqa: S603 - Explicit opt-in binary and local endpoint.
        [
            script,
            '-qefc',
            'stty cols 120 rows 40; exec "$TEST_BINARY" --model claude-sonnet-4-6 --tools ""',
            '/dev/null',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=tmp_path,
        env=env,
        start_new_session=True,
    )
    output = bytearray()
    start = time.monotonic()
    submitted = False
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        while time.monotonic() - start < 45:
            if not submitted and time.monotonic() - start > 4:
                process.stdin.write(b'Think briefly then say done.\r')
                process.stdin.flush()
                submitted = True
            if select.select([process.stdout], [], [], 0.1)[0]:
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    break
                output.extend(block)
                if not stopped.is_set():
                    if b'EARLY_THINKING_MARKER' in output:
                        first_seen.set()
                    if b'SECOND_THINKING_MARKER' in output:
                        second_seen.set()
                if b'FINAL_ANSWER_MARKER' in output:
                    break
            if process.poll() is not None or errors:
                break
        text = output.decode(errors='replace')
        assert not errors, '\n'.join(errors) + '\n' + text
        assert first_seen.is_set(), text
        assert second_seen.is_set(), text
        assert b'FINAL_ANSWER_MARKER' in output, text
    finally:
        first_seen.set()
        second_seen.set()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        server.shutdown()
        thread.join()
        server.server_close()
