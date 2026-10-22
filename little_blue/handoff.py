"""Host orchestration: stop the old Docker core before publishing a handoff.

One operator, two cooperating installations. No shared cloud, automatic failover,
or new QR login. Durable fences survive reboot; receipts do not unlock sources.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import httpx

from .handoff_guard import read_state, write_state, require_active
from .handoff_payload import (ensure_key, load_key, public_bytes, device_id, make_request,
    validate_request, read_envelope, decrypt, verify_payload, make_receipt, verify_receipt)
from .memory_transfer import digest


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024:
        raise ValueError('Oversized control file')
    return json.loads(path.read_text(encoding='utf-8-sig'))


class Manager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.control = self.root / '.handoff'
        self.control.mkdir(exist_ok=True)
        self.key_path = self.control / 'device-private.pem'
        self.key = ensure_key(self.key_path)
        self.device = device_id(public_bytes(self.key))

    def run(self, *args, timeout=60):
        result = subprocess.run(list(args), cwd=self.root, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
        if result.returncode:
            # Docker config/logs can contain secrets; never echo arbitrary output.
            raise RuntimeError(f'Command failed ({args[0]}, exit {result.returncode}); data remains locked for retry')
        return result.stdout.strip()

    def compose(self, *args, **kwargs):
        return self.run('docker', 'compose', *args, **kwargs)

    def container(self):
        ids = self.compose('ps', '--all', '--quiet', 'core').splitlines()
        if len(ids) != 1:
            raise RuntimeError('Create this installation core first with start-core.ps1')
        ident = ids[0]
        mounts = json.loads(self.run('docker', 'inspect', '--format', '{{json .Mounts}}', ident))
        mount = next((m for m in mounts if m['Destination'] == '/data' and m['Type'] == 'volume'), None)
        if mount is None:
            raise RuntimeError('Handoff requires a named Docker data volume at /data')
        # Never inspect/print the full environment: it contains API credentials.
        data_env = self.run('docker', 'inspect', '--format',
                           '{{range .Config.Env}}{{if eq (index (split . "=") 0) "LITTLE_BLUE_DATA_DIR"}}{{.}}{{end}}{{end}}', ident)
        command = json.loads(self.run('docker', 'inspect', '--format', '{{json .Config.Cmd}}', ident)) or []
        data_dir = data_env.removeprefix('LITTLE_BLUE_DATA_DIR=')
        for index, argument in enumerate(command):
            if argument == '--data-dir':
                data_dir = command[index + 1] if index + 1 < len(command) else ''
            elif argument.startswith('--data-dir='):
                data_dir = argument.split('=', 1)[1]
        if data_dir != '/data':
            raise RuntimeError('Handoff requires the core data directory to be exactly /data')
        runtime = json.loads(self.run('docker', 'inspect', '--format', '{{json .State}}', ident))
        image = self.run('docker', 'inspect', '--format', '{{.Image}}', ident)
        return {'id': ident, 'volume': mount['Name'], 'image': image, 'running': runtime['Running'],
                'exit_code': runtime['ExitCode']}

    def api(self):
        address = self.compose('port', 'core', '8765').splitlines()[0]
        if not address.startswith('127.0.0.1:'):
            raise RuntimeError('Core API must bind to loopback')
        token = (self.root / '.secrets/core_token').read_text().strip()
        return httpx.Client(base_url='http://' + address, headers={'Authorization': 'Bearer ' + token},
                            trust_env=False, timeout=30)

    def stop(self, info):
        if info['running']:
            with self.api() as client:
                response = client.post('/handoff', json={'action': 'prepare'})
                if response.status_code == 404:
                    raise RuntimeError('Install/build the handoff-enabled core on this device first')
                response.raise_for_status()
                deadline = time.monotonic() + 300
                while True:
                    response = client.get('/state')
                    response.raise_for_status()
                    if response.json().get('handoff', {}).get('drained'):
                        break
                    if time.monotonic() > deadline:
                        raise RuntimeError('Drain timed out. Source is paused; retry Send after the current reply finishes')
                    time.sleep(.25)
            self.stop_desktop_clients()
            self.run('docker', 'update', '--restart=no', info['id'])
            self.compose('stop', '--timeout', '30', 'core')
        checked = self.container()
        fenced = False
        if not checked['running'] and checked['exit_code'] != 0:
            fenced = self.payload(checked, 'state').get('phase') in ('released', 'awaiting')
        if checked['running'] or (checked['exit_code'] != 0 and not fenced):
            raise RuntimeError('Core did not stop cleanly; no package was released. Inspect locally before retrying')
        if 'hermes' in self.compose('config', '--services').splitlines():
            self.compose('stop', '--timeout', '15', 'hermes')
        return checked

    def stop_desktop_clients(self):
        # Only this checkout's remote clients, after their core has drained.
        # Local-only pets and clients of other projects are never touched.
        if os.name != 'nt':
            return  # Headless Linux deployments do not run the Windows pet.
        import psutil
        executables = {(self.root / '.venv/Scripts' / name).resolve()
                       for name in ('python.exe', 'pythonw.exe')}
        clients = []
        for process in psutil.process_iter(['pid', 'name']):
            if process.info['name'].lower() not in ('python.exe', 'pythonw.exe'):
                continue
            try:
                args = process.cmdline()
                if (len(args) >= 3 and args[1:3] == ['-m', 'little_blue'] and
                        Path(args[0]).resolve() in executables and '--core-url' in args):
                    process.terminate()
                    clients.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive = psutil.wait_procs(clients, timeout=5)
        if alive:
            raise RuntimeError('Desktop client did not stop; retry after closing the pet')

    def payload(self, info, action, *args, exchange=None):
        command = ['docker', 'run', '--rm', '--network', 'none',
                   '--mount', f'type=volume,src={info["volume"]},dst=/data',
                   '--mount', f'type=bind,src={self.root / "little_blue"},dst=/code/little_blue,readonly',
                   '--mount', f'type=bind,src={self.control},dst=/control,readonly',
                   '-e', 'PYTHONPATH=/code', '--entrypoint', 'python']
        if exchange is not None:
            command += ['--mount', f'type=bind,src={Path(exchange).resolve()},dst=/exchange']
        command += [info['image'], '-m', 'little_blue.handoff_payload', action, *args]
        return json.loads(self.run(*command, timeout=180))

    def check_override(self):
        path = self.root / 'compose.override.yaml'
        if path.exists() and not path.read_text(encoding='utf-8').startswith('# Managed by Little Blue handoff\n'):
            raise RuntimeError('Existing compose.override.yaml is not managed by handoff; integrate it before switching')

    def prepare(self, output):
        self.check_override()
        state = read_state(self.control)
        request_path = self.control / 'receive-request.json'
        if state.get('phase') == 'awaiting' and request_path.exists():
            request = read_json(request_path)
        else:
            if state.get('phase') in ('sending', 'receiving'):
                raise RuntimeError('Finish the existing handoff before preparing another request')
            info = self.stop(self.container())
            old = self.payload(info, 'state')
            self.payload(info, 'fence')
            request = make_request(self.key)
            save_json(request_path, request)
            write_state(self.control, {'phase': 'awaiting', 'request_id': request['request_id'],
                                      'previous': info, 'lineage': old.get('lineage'),
                                      'generation': old.get('generation', 0)})
        output = Path(output).absolute()
        if output.exists() and read_json(output) != request:
            raise ValueError('Request output already contains a different file')
        save_json(output, request)
        return {'phase': 'awaiting', 'request': str(output), 'device': self.device[:12]}

    def send(self, request_path, package):
        request = read_json(request_path)
        validate_request(request)
        if request['device'] == self.device:
            raise ValueError('Receive request belongs to this device')
        state = read_state(self.control)
        if state.get('phase') not in (None, 'active', 'sending', 'released'):
            raise RuntimeError('This device is not the active sender')
        package = Path(package).absolute()
        package.parent.mkdir(parents=True, exist_ok=True)
        if state.get('phase') in ('sending', 'released'):
            if state.get('request_id') != request['request_id'] or state.get('package') != str(package):
                raise RuntimeError('Retry the SAME request and package path; source must not fork')
        elif package.exists():
            raise ValueError('Package output already exists')
        save_json(self.control / 'outgoing-request.json', request)
        write_state(self.control, {**state, 'phase': 'sending', 'request_id': request['request_id'], 'package': str(package)})
        info = self.stop(self.container())
        lock = self.payload(info, 'state')
        if lock.get('phase') in (None, 'active'):
            self.payload(info, 'drain')
        header = self.payload(info, 'export', '--request', '/control/outgoing-request.json',
                              '--package', '/exchange/' + package.name, '--device', self.device,
                              exchange=package.parent)
        write_state(self.control, {'phase': 'released', 'request_id': request['request_id'],
                                  'package': str(package), 'package_sha256': digest(package),
                                  'transfer': header, 'previous': info})
        return {'phase': 'released', 'package': str(package), 'transfer_id': header['transfer_id'],
                'generation': header['generation'], 'source_stopped': True}

    def receive(self, package, receipt):
        self.check_override()
        state = read_state(self.control)
        package = Path(package).absolute()
        request = read_json(self.control / 'receive-request.json')
        header, raw = decrypt(package, self.key, request)
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            release, memory, account = verify_payload(raw, header, Path(folder))
            from .memory_transfer import inspect_memory
            inspect_memory(memory)
        del raw
        accepted = self.control / 'accepted' / (header['transfer_id'] + '.json')
        if accepted.exists():
            if state.get('phase') not in ('active', 'receiving') or state.get('transfer', {}).get('transfer_id') != header['transfer_id']:
                raise RuntimeError('This package was already consumed; do not restore an old generation')
            if state['phase'] == 'receiving':
                # Receipt was persisted immediately before a process interruption.
                with self.api() as client:
                    client.get('/state').raise_for_status()
                write_state(self.control, {'phase': 'active', 'transfer': header, 'lineage': header['lineage'],
                                          'generation': header['generation'], 'volume': state['target_info']['volume']})
            save_json(receipt, read_json(accepted))
            return {'phase': 'active', 'receipt': str(Path(receipt).absolute()), 'already_received': True}
        if state.get('phase') not in ('awaiting', 'receiving') or state.get('request_id') != header['request_id']:
            raise RuntimeError('Prepare this device before importing this request')
        if state.get('lineage') == header['lineage'] and header['generation'] <= state.get('generation', 0):
            raise RuntimeError('Stale handoff generation')
        if state.get('phase') == 'receiving' and state.get('package_sha256') != digest(package):
            raise RuntimeError('Retry the same package')
        info = self.container()
        if info['running'] and state.get('phase') != 'receiving':
            raise RuntimeError('Receiver core must remain stopped')
        if state.get('phase') != 'receiving':
            new_info = {**info, 'volume': 'little-blue-handoff-' + uuid.uuid4().hex}
            state = {**state, 'phase': 'receiving', 'target_info': new_info, 'transfer': header,
                     'package_sha256': digest(package)}
            write_state(self.control, state)
        new_info = state['target_info']
        self.run('docker', 'volume', 'create', new_info['volume'])
        current = self.payload(new_info, 'state')
        if current.get('phase') == 'importing':
            # A partial volume is never activated or overwritten. Retain for diagnosis.
            new_info = {**new_info, 'volume': 'little-blue-handoff-' + uuid.uuid4().hex}
            state['target_info'] = new_info
            write_state(self.control, state)
            self.run('docker', 'volume', 'create', new_info['volume'])
        self.payload(new_info, 'import', '--request', '/control/receive-request.json',
                     '--key', '/control/device-private.pem', '--package', '/exchange/' + package.name,
                     exchange=package.parent)
        override = ('# Managed by Little Blue handoff\nservices:\n  core:\n'
                    '    environment:\n      LITTLE_BLUE_DATA_DIR: /data\n'
                    'volumes:\n  core-data:\n    external: true\n    name: ' + new_info['volume'] + '\n')
        path = self.root / 'compose.override.yaml'
        temp = path.with_suffix('.tmp')
        temp.write_text(override, encoding='utf-8')
        temp.replace(path)
        self.payload(new_info, 'activate', '--transfer-id', header['transfer_id'])
        if 'hermes' in self.compose('config', '--services').splitlines():
            self.compose('up', '-d', '--no-deps', '--wait', 'hermes', timeout=120)
        self.compose('up', '-d', '--no-deps', '--wait', 'core', timeout=120)
        with self.api() as client:
            response = client.get('/state')
            response.raise_for_status()
            wechat = response.json()['wechat']
        signed = make_receipt(self.key, header, digest(package))
        save_json(accepted, signed)
        write_state(self.control, {'phase': 'active', 'transfer': header, 'lineage': header['lineage'],
                                  'generation': header['generation'], 'volume': new_info['volume']})
        save_json(receipt, signed)
        return {'phase': 'active', 'receipt': str(Path(receipt).absolute()), 'wechat': wechat,
                'generation': header['generation'], 'new_volume': new_info['volume']}

    def finish(self, receipt):
        state = read_state(self.control)
        if state.get('phase') not in ('released', 'retired'):
            raise RuntimeError('No outgoing handoff awaits a receipt')
        verify_receipt(read_json(receipt), read_json(self.control / 'outgoing-request.json'),
                       state['transfer'], state['package_sha256'])
        write_state(self.control, {**state, 'phase': 'retired'})
        return {'phase': 'retired', 'source_remains_locked': True}


@contextmanager
def operation_lock(control):
    # OS lock releases on crash; the journal, not this file, carries recovery state.
    path = Path(control) / 'operation.lock'
    with path.open('a+b') as stream:
        stream.seek(0)
        if stream.read(1) == b'':
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description='Two-device sequential handoff')
    parser.add_argument('action', choices=['prepare', 'send', 'receive', 'finish', 'status', 'check'])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--request', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'check':
        require_active(args.root / '.handoff')
        return 0
    manager = Manager(args.root)
    with operation_lock(manager.control):
        if args.action == 'prepare' and args.request:
            result = manager.prepare(args.request)
        elif args.action == 'send' and args.request and args.package:
            result = manager.send(args.request, args.package)
        elif args.action == 'receive' and args.package and args.receipt:
            result = manager.receive(args.package, args.receipt)
        elif args.action == 'finish' and args.receipt:
            result = manager.finish(args.receipt)
        elif args.action == 'status':
            state = read_state(manager.control)
            result = {'phase': state.get('phase', 'active'), 'device': manager.device[:12],
                      'generation': state.get('generation', state.get('transfer', {}).get('generation', 0))}
        else:
            parser.error('Supply the required --request, --package and/or --receipt paths')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Keep secrets out of transport/validation exceptions.
        if type(error) in (ValueError, RuntimeError):
            print(str(error))
        else:
            print('Handoff failed: ' + type(error).__name__ + '. Keep both copies stopped and retry the same command.')
        raise SystemExit(1)
