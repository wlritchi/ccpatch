"""Opt-in host checks against patched and stock executable request bodies."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    'variable', ['CCPATCH_RETRACTION_BINARY', 'CCPATCH_STOCK_BINARY']
)
def test_resume_does_not_send_archives(tmp_path: Path, variable: str) -> None:
    binary = os.environ.get(variable)
    if binary is None:
        pytest.skip(f'set {variable} for host resume checks')
    received: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers['Content-Length']))
            received.append(json.loads(body))
            message = {
                'id': 'msg_test',
                'type': 'message',
                'role': 'assistant',
                'model': 'claude-sonnet-4-6',
                'content': [{'type': 'text', 'text': 'done'}],
                'stop_reason': 'end_turn',
                'stop_sequence': None,
                'usage': {'input_tokens': 10, 'output_tokens': 1},
            }
            if received[-1].get('stream'):
                frames = [
                    {
                        'type': 'message_start',
                        'message': {**message, 'content': [], 'stop_reason': None},
                    },
                    {
                        'type': 'content_block_start',
                        'index': 0,
                        'content_block': {'type': 'text', 'text': ''},
                    },
                    {
                        'type': 'content_block_delta',
                        'index': 0,
                        'delta': {'type': 'text_delta', 'text': 'done'},
                    },
                    {'type': 'content_block_stop', 'index': 0},
                    {
                        'type': 'message_delta',
                        'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                        'usage': {'output_tokens': 1},
                    },
                    {'type': 'message_stop'},
                ]
                payload = ''.join(
                    f'event: {frame["type"]}\ndata: {json.dumps(frame)}\n\n'
                    for frame in frames
                ).encode()
                content_type = 'text/event-stream'
            else:
                payload = json.dumps(message).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    original_id = str(uuid.uuid4())
    row = {
        'type': 'user',
        'uuid': user_id,
        'parentUuid': None,
        'sessionId': session_id,
        'timestamp': '2026-09-20T00:00:00.000Z',
        'message': {'role': 'user', 'content': 'previous prompt'},
        'cwd': str(tmp_path),
        'version': '2.1.274',
        'isSidechain': False,
    }
    archive = {
        'type': 'ccpatch-retracted',
        'schemaVersion': 1,
        'archiveId': str(uuid.uuid4()),
        'originalUuid': original_id,
        'sessionId': session_id,
        'payloadJson': json.dumps(
            {
                'type': 'assistant',
                'uuid': original_id,
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'CCPATCH_ARCHIVE_SECRET'}],
                },
            }
        ),
    }
    transcript = tmp_path / 'resume.jsonl'
    transcript.write_text('\n'.join(map(json.dumps, [row, archive])) + '\n')
    config = tmp_path / 'config'
    config.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                'ANTHROPIC_',
                'CLAUDE_',
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
        }
    )
    try:
        result = subprocess.run(  # noqa: S603 - Explicit opt-in binary and local endpoint.
            [
                binary,
                '--resume',
                str(transcript),
                '--print',
                'continue',
                '--model',
                'claude-sonnet-4-6',
                '--tools',
                '',
                '--max-turns',
                '1',
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert received, result.stderr + result.stdout
        assert 'CCPATCH_ARCHIVE_SECRET' not in json.dumps(received)
        assert 'previous prompt' in json.dumps(received)
        assert 'CCPATCH_ARCHIVE_SECRET' in transcript.read_text()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
