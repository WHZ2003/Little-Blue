"""Encrypted, recipient-specific handoff payloads. No network calls."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import uuid
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .handoff_guard import read_state, write_state
from .memory_transfer import export_memory, unpack_verified, digest

MAGIC = b'LBHAND01'
MAX_PAYLOAD = 256 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def device_id(public):
    return hashlib.sha256(public).hexdigest()


def load_key(path):
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def ensure_key(path):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    return load_key(path)


def make_request(key):
    public = public_bytes(key)
    return {'format': 'little-blue-receive', 'version': 1, 'request_id': uuid.uuid4().hex,
            'device': device_id(public), 'public_key': public.decode(), 'created_at': now()}


def validate_request(request):
    if request.get('format') != 'little-blue-receive' or request.get('version') != 1:
        raise ValueError('Invalid receive request')
    if len(request.get('request_id', '')) != 32:
        raise ValueError('Invalid request identifier')
    public = request['public_key'].encode()
    key = serialization.load_pem_public_key(public)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 3072 or device_id(public) != request['device']:
        raise ValueError('Invalid recipient key')
    return key


def read_envelope(path):
    if Path(path).stat().st_size > MAX_PAYLOAD + 32768:
        raise ValueError('Handoff package exceeds size limit')
    with Path(path).open('rb') as stream:
        if stream.read(8) != MAGIC:
            raise ValueError('Not an encrypted handoff package')
        size = struct.unpack('>I', stream.read(4))[0]
        if not 0 < size <= 16384:
            raise ValueError('Invalid handoff header')
        header = json.loads(stream.read(size))
        encrypted = stream.read(MAX_PAYLOAD + 17)
    if len(encrypted) > MAX_PAYLOAD + 16 or header.get('version') != 1:
        raise ValueError('Unsupported or oversized handoff')
    return header, encrypted


def seal(payload, metadata, recipient):
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    wrapped = recipient.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    header = {**metadata, 'version': 1, 'nonce': base64.b64encode(nonce).decode(),
              'wrapped_key': base64.b64encode(wrapped).decode()}
    raw = canonical(header)
    encrypted = AESGCM(key).encrypt(nonce, payload, raw)
    return MAGIC + struct.pack('>I', len(raw)) + raw + encrypted


def decrypt(path, private_key, request):
    header, encrypted = read_envelope(path)
    if header['target'] != device_id(public_bytes(private_key)) or header['request_id'] != request['request_id']:
        raise ValueError('Package belongs to a different device or receive request')
    if type(header.get('generation')) is not int or header['generation'] < 1:
        raise ValueError('Invalid handoff generation')
    for name in ('transfer_id', 'lineage'):
        if not isinstance(header.get(name), str) or len(header[name]) != 32:
            raise ValueError('Invalid handoff identifier')
        int(header[name], 16)
    key = private_key.decrypt(base64.b64decode(header['wrapped_key'], validate=True),
                             padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    raw = AESGCM(key).decrypt(base64.b64decode(header['nonce'], validate=True), encrypted, canonical(header))
    return header, raw


def verify_payload(raw, header, folder):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        expected = {'memory.zip', 'release.json'}
        if 'wechat-account.json' in names:
            expected.add('wechat-account.json')
        if 'bili-credential.json' in names:
            expected.add('bili-credential.json')
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError('Invalid handoff contents')
        if sum(i.file_size for i in archive.infolist()) > MAX_PAYLOAD:
            raise ValueError('Expanded payload exceeds limit')
        if archive.getinfo('release.json').file_size > 16384:
            raise ValueError('Invalid release record')
        release = json.loads(archive.read('release.json'))
        for key in ('transfer_id', 'lineage', 'generation', 'source', 'target', 'request_id'):
            if release.get(key) != header.get(key):
                raise ValueError('Release identity mismatch')
        if release.get('phase') != 'released':
            raise ValueError('Source has not released this copy')
        memory = folder / 'memory.zip'
        memory.write_bytes(archive.read('memory.zip'))
        if digest(memory) != release['memory_sha256']:
            raise ValueError('Memory digest mismatch')
        account = None
        if 'bili-credential.json' in names:
            if archive.getinfo('bili-credential.json').file_size > 8192:
                raise ValueError('Oversized Bilibili credential')
            from .bili_api import save_credential
            save_credential(folder / 'bili-credential.json', json.loads(archive.read('bili-credential.json')))
        if 'wechat-account.json' in names:
            if archive.getinfo('wechat-account.json').file_size > 32768:
                raise ValueError('Oversized WeChat account')
            from .wechat import Account
            account = Account.model_validate_json(archive.read('wechat-account.json'))
        return release, memory, account


def export_handoff(data_dir, request, output, source_device):
    data_dir, output = Path(data_dir), Path(output)
    recipient = validate_request(request)
    if request['device'] == source_device:
        raise ValueError('Choose a different receiving device')
    state = read_state(data_dir)
    if state.get('phase') not in ('draining', 'exporting', 'released'):
        raise ValueError('Source must be stopped and drained before export')
    if state.get('phase') in ('exporting', 'released'):
        if state.get('request_id') != request['request_id'] or state.get('target') != request['device']:
            raise ValueError('A handoff is already issued to another request; do not fork it')
        metadata = {k: state[k] for k in ('transfer_id', 'lineage', 'generation', 'source', 'target', 'request_id')}
    else:
        if state.get('owner') not in (None, source_device):
            raise ValueError('This device does not own the source copy')
        metadata = {'transfer_id': uuid.uuid4().hex, 'lineage': state.get('lineage', uuid.uuid4().hex),
                    'generation': state.get('generation', 0) + 1, 'source': source_device,
                    'target': request['device'], 'request_id': request['request_id']}
    if output.exists():
        existing, _ = read_envelope(output)
        if any(existing.get(k) != v for k, v in metadata.items()):
            raise ValueError('Output is a different handoff')
        return metadata
    write_state(data_dir, {**metadata, 'phase': 'exporting'})
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        memory = folder / 'memory.zip'
        export_memory(data_dir, memory)
        release = {**metadata, 'phase': 'released', 'released_at': now(), 'memory_sha256': digest(memory)}
        from .wechat import read_account
        account = read_account(data_dir / 'wechat-account.json')
        from .bili_api import read_credential
        bili = read_credential(data_dir / 'bili-credential.json')
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(memory, 'memory.zip')
            archive.writestr('release.json', canonical(release))
            if account:
                archive.writestr('wechat-account.json', account.model_dump_json())
            if bili:
                archive.writestr('bili-credential.json', bili.model_dump_json())
        if len(payload.getvalue()) > MAX_PAYLOAD:
            raise ValueError('Handoff exceeds 256 MiB; source remains locked')
        encrypted = seal(payload.getvalue(), metadata, recipient)
        # Lock is durable BEFORE the transferable file becomes visible.
        write_state(data_dir, release)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_name(output.name + '.' + uuid.uuid4().hex + '.partial')
        try:
            with temporary_output.open('xb') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            # Hard-link publication is exclusive and atomic on NTFS/ext4.
            os.link(temporary_output, output)
        finally:
            temporary_output.unlink(missing_ok=True)
    return metadata


def import_handoff(package, data_dir, private_key, request):
    data_dir = Path(data_dir)
    header, raw = decrypt(package, private_key, request)
    existing = read_state(data_dir)
    if existing.get('transfer_id') == header['transfer_id'] and existing.get('phase') in ('staged', 'active'):
        if existing.get('package_sha256') != digest(Path(package)):
            raise ValueError('Different bytes for an existing transfer')
        return header
    if (data_dir / 'memory.sqlite3').exists() or existing:
        raise ValueError('Destination volume is not empty')
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        release, memory, account = verify_payload(raw, header, folder)
        staging = folder / 'restored'
        staging.mkdir()
        manifest = unpack_verified(memory, staging)
        # Unlike a general copy, a handoff keeps reminders, cursors, budgets,
        # delivery states and growth unchanged. Only machine permissions reset.
        for item in manifest['databases']:
            with closing(sqlite3.connect(staging / item['path'])) as db, db:
                row = db.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
                settings = json.loads(row[0]) if row else {}
                settings.update(tools_enabled=False, perception=False, sprite_manifest='')
                db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)', ('settings', json.dumps(settings)))
        from .wechat import save_account
        if account:
            save_account(staging / 'wechat-account.json', account)
        if (folder / 'bili-credential.json').exists():
            from .bili_api import read_credential, save_credential
            save_credential(staging / 'bili-credential.json', read_credential(folder / 'bili-credential.json').model_dump())
        data_dir.mkdir(parents=True, exist_ok=True)
        write_state(data_dir, {**release, 'phase': 'importing'})
        import shutil
        for item in staging.iterdir():
            if item.is_dir():
                shutil.copytree(item, data_dir / item.name)
            else:
                shutil.copy2(item, data_dir / item.name)
        write_state(data_dir, {**release, 'phase': 'staged', 'owner': header['target'],
                              'package_sha256': digest(Path(package))})
    return header


def activate(data_dir, transfer_id):
    state = read_state(data_dir)
    if state.get('phase') not in ('staged', 'active') or state.get('transfer_id') != transfer_id:
        raise ValueError('No matching staged transfer')
    write_state(data_dir, {**state, 'phase': 'active'})


def make_receipt(key, header, package_hash):
    data = {k: header[k] for k in ('transfer_id', 'lineage', 'generation', 'source', 'target', 'request_id')}
    data.update(phase='accepted', package_sha256=package_hash, accepted_at=now())
    signature = key.sign(canonical(data), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    return {'data': data, 'signature': base64.b64encode(signature).decode()}


def verify_receipt(receipt, request, header, package_hash):
    public = validate_request(request)
    data = receipt['data']
    public.verify(base64.b64decode(receipt['signature'], validate=True), canonical(data),
                  padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    if data.get('phase') != 'accepted' or data.get('package_sha256') != package_hash:
        raise ValueError('Receipt does not match package')
    if any(data.get(k) != header[k] for k in ('transfer_id', 'lineage', 'generation', 'source', 'target', 'request_id')):
        raise ValueError('Receipt belongs to another transfer')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['export', 'import', 'activate', 'state', 'fence', 'drain'])
    p.add_argument('--data-dir', type=Path, default=Path('/data'))
    p.add_argument('--request', type=Path)
    p.add_argument('--package', type=Path)
    p.add_argument('--key', type=Path)
    p.add_argument('--device')
    p.add_argument('--transfer-id')
    a = p.parse_args()
    if a.action == 'export':
        result = export_handoff(a.data_dir, json.loads(a.request.read_text()), a.package, a.device)
    elif a.action == 'import':
        result = import_handoff(a.package, a.data_dir, load_key(a.key), json.loads(a.request.read_text()))
    elif a.action == 'activate':
        activate(a.data_dir, a.transfer_id)
        result = {'phase': 'active'}
    elif a.action in ('fence', 'drain'):
        state = read_state(a.data_dir)
        if a.action == 'drain' and state.get('phase') not in (None, 'active', 'draining'):
            raise ValueError('Cannot drain a released copy')
        phase = 'awaiting' if a.action == 'fence' else 'draining'
        write_state(a.data_dir, {**state, 'phase': phase})
        result = {'phase': phase}
    else:
        result = read_state(a.data_dir)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Account validation errors can contain credentials; never echo them.
        raise SystemExit('Handoff payload failed: ' + type(error).__name__)
