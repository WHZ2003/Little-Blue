"""Private stdio transport for the local Cortico World. SSH pins the server key.

Only companion replication and proactive preferences are exposed; no remote shell or generic HTTP
tools are handed to the model. Secrets never occur in stdout, argv or the workspace.
"""
import http.client
import json
from pathlib import Path
import sys
import paramiko


class Transport:
    def __init__(self, config):
        self.config, self.ssh, self.token = config, None, None

    def connect(self):
        if self.ssh and self.ssh.get_transport().is_active():
            return
        self.close()
        c = paramiko.SSHClient()
        c.load_host_keys(self.config['known_hosts'])
        c.set_missing_host_key_policy(paramiko.RejectPolicy())
        c.connect(self.config['host'], username=self.config['username'],
                  key_filename=self.config['key'], allow_agent=False, look_for_keys=False,
                  timeout=12, auth_timeout=12, banner_timeout=12)
        c.get_transport().set_keepalive(20)
        with c.open_sftp() as s, s.open('/opt/dafeiyu/.secrets/core_token') as f:
            self.token = f.read().decode().strip()
        self.ssh = c

    def request(self, payload):
        endpoint = '/companion/sync'
        if isinstance(payload, dict) and '_control' in payload:
            if payload['_control'] not in ('proactive_preferences', 'shared_preferences'):
                raise ValueError('Unsupported control')
            control = payload['_control']
            payload = {k: v for k, v in payload.items() if k != '_control'}
            endpoint = '/companion/preferences' if control == 'proactive_preferences' else '/companion/shared-preferences'
        self.connect()
        channel = self.ssh.get_transport().open_channel('direct-tcpip', ('127.0.0.1', 8765), ('127.0.0.1', 0), timeout=15)
        channel.settimeout(30)
        connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=30)
        connection.sock = channel
        try:
            body = json.dumps(payload, ensure_ascii=False).encode()
            if len(body) > 900000:
                raise ValueError('Replication batch exceeds limit')
            connection.request('POST', endpoint, body, {'Authorization': 'Bearer '+self.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            if response.status != 200:
                if response.status == 409:
                    raise RuntimeError('Settings changed or maintenance in progress; refresh and retry')
                raise RuntimeError('Cloud request failed (HTTP '+str(response.status)+')')
            return result
        finally:
            connection.close()

    def close(self):
        if self.ssh:
            self.ssh.close()
        self.ssh = None


def main():
    transport = Transport(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    try:
        for line in sys.stdin:
            ident = None
            try:
                request = json.loads(line)
                ident = request['id']
                result = {'id': ident, 'ok': True, 'data': transport.request(request['payload'])}
            except Exception as e:
                transport.close()
                result = {'id': ident, 'ok': False, 'error': type(e).__name__}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        transport.close()


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    main()
