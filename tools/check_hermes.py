"""Run the actual pinned Hermes tool loop with synthetic data and a local model stub."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    token = secrets.token_hex(32)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers.get('Authorization') == 'Bearer ' + token
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/tool'):
                seen.append('memory_search')
                result = {'ok': True, 'data': [{'text': 'synthetic remembered fact'}]}
            else:
                seen.append('completion')
                count = seen.count('completion')
                if count == 1:
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_synthetic', 'type': 'function',
                        'function': {'name': 'dafeiyu_memory_search', 'arguments': '{"query":"synthetic"}'}}]}
                elif count == 2:
                    assert any(m['role'] == 'tool' and 'synthetic remembered fact' in m.get('content', '') for m in body['messages'])
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_memory', 'type': 'function',
                        'function': {'name': 'memory', 'arguments': json.dumps({'action': 'add', 'target': 'user', 'content': 'Synthetic owner prefers blue notebooks.'})}}]}
                else:
                    message = {'role': 'assistant', 'content': 'HERMES_LOOP_OK'}
                result = {'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 1, 'model': 'deepseek-flash',
                    'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if count < 3 else 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    volume = 'dafeiyu-hermes-check-' + uuid.uuid4().hex[:12]
    payload = {'run_token': token, 'task_id': uuid.uuid4().hex, 'model': 'deepseek-flash',
               'text': 'Recall the synthetic fact.', 'history': [], 'system': 'You are a test companion.',
               'tools': [{'type': 'function', 'function': {'name': 'dafeiyu_memory_search', 'description': 'Search memory',
                          'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}]}
    try:
        args = ['docker', 'run', '--rm', '-i', '--memory', '512m', '--pids-limit', '128',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '-v', volume + ':/data', '-e', f'DAFEIYU_PROXY_URL=http://host.docker.internal:{server.server_port}/harness',
                'dafeiyu-hermes:55ee4ac', 'python', '/adapter/worker.py']
        result = subprocess.run(args, input=json.dumps(payload).encode(), capture_output=True, timeout=240)
        (ROOT / 'qa-output/hermes-worker-check.log').write_bytes(result.stderr)
        if result.returncode:
            print(result.stderr.decode('utf-8', 'replace')[-5000:])
            raise RuntimeError('Hermes worker integration failed')
        reply = json.loads(result.stdout)
        assert reply['ok'] and reply['content'] == 'HERMES_LOOP_OK', reply
        assert seen == ['completion', 'memory_search', 'completion', 'completion'], seen
        memory = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '-v', volume + ':/data:ro',
                                         'dafeiyu-hermes:55ee4ac', 'cat', '/data/hermes/memories/USER.md'])
        assert b'Synthetic owner prefers blue notebooks.' in memory
        print('PASS: actual Hermes AIAgent bridged old memories, ran its native memory tool, and persisted new memory across process exit inside 512 MiB.')
    finally:
        server.shutdown()
        subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)


if __name__ == '__main__':
    main()
