"""Read-only v1 torrent evidence; never infer identity from a filename or mtime."""
import base64
from hashlib import sha1
from pathlib import Path, PurePosixPath

from .domain import Awaiting, BridgeError, check_stop, file_signature

MAX_METADATA = 8 * 1024 * 1024
MAX_PIECE = 128 * 1024 * 1024


def _invalid():
    return BridgeError('原种子校验证据无效或与本批次不一致，保留文件', review=True)


def _decode(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_METADATA:
        raise _invalid()
    pos, nodes, info_span = 0, 0, None

    def read(depth=0):
        nonlocal pos, nodes, info_span
        nodes += 1
        if depth > 24 or nodes > 100000 or pos >= len(raw):
            raise ValueError()
        char = raw[pos:pos + 1]
        pos += 1
        if char == b'i':
            end = raw.index(b'e', pos)
            text = raw[pos:end]
            if len(text) > 20 or not text or text == b'-0' or text.startswith(b'0') and len(text) > 1:
                raise ValueError()
            number = int(text)
            if str(number).encode() != text:
                raise ValueError()
            pos = end + 1
            return number
        if char in (b'l', b'd'):
            value = [] if char == b'l' else {}
            while raw[pos:pos + 1] != b'e':
                if char == b'l':
                    value.append(read(depth + 1))
                else:
                    key = read(depth + 1)
                    if not isinstance(key, bytes) or key in value:
                        raise ValueError()
                    start = pos
                    value[key] = read(depth + 1)
                    if depth == 0 and key == b'info':
                        info_span = (start, pos)
            pos += 1
            return value
        pos -= 1
        end = raw.index(b':', pos)
        text = raw[pos:end]
        if not text.isdigit() or len(text) > 9 or len(text) > 1 and text.startswith(b'0'):
            raise ValueError()
        size = int(text)
        pos = end + 1
        value = raw[pos:pos + size]
        if len(value) != size:
            raise ValueError()
        pos += size
        return value

    try:
        value = read()
        if pos != len(raw):
            raise ValueError()
        return value, raw[slice(*info_span)] if info_span else None
    except (ValueError, TypeError, IndexError, RecursionError):
        raise _invalid() from None


def evidence(raw, download_hash):
    """Keep only the hash-authenticated info dictionary, never tracker credentials."""
    parsed, info_raw = _decode(raw)
    if not isinstance(parsed, dict) or not info_raw:
        raise _invalid()
    proof = {'hash': str(download_hash).lower(), 'info': base64.b64encode(info_raw).decode('ascii')}
    manifest(proof, download_hash)
    return proof


def manifest(proof, download_hash):
    try:
        key = str(download_hash).lower()
        if (len(key) != 40 or any(c not in '0123456789abcdef' for c in key)
                or proof['hash'] != key or len(proof['info']) > MAX_METADATA * 2):
            raise ValueError()
        raw = base64.b64decode(proof['info'], validate=True)
        if sha1(raw).hexdigest() != key:
            raise ValueError()
        info, _ = _decode(raw)
        piece = info[b'piece length']
        hashes = info[b'pieces']
        if type(piece) is not int or not 0 < piece <= MAX_PIECE or not isinstance(hashes, bytes):
            raise ValueError()

        def component(value):
            name = value.decode('utf-8')
            if not name or name in ('.', '..') or any(c in name for c in ('/', '\\', '\x00', ':')):
                raise ValueError()
            return name

        name = component(info.get(b'name.utf-8', info[b'name']))
        files, seen, padding, total = [], set(), set(), 0
        source = info.get(b'files')
        if source is None:
            source = [{b'path': [], b'length': info[b'length']}]
            multi = False
        else:
            multi = True
        if not isinstance(source, list) or not 0 < len(source) <= 20000:
            raise ValueError()
        for item in source:
            parts = item.get(b'path.utf-8', item[b'path'])
            if not isinstance(parts, list) or len(parts) > 64 or multi and not parts:
                raise ValueError()
            path = '/'.join([name] + [component(v) for v in parts])
            size = item[b'length']
            if type(size) is not int or not 0 <= size <= 10 ** 15 or path in seen:
                raise ValueError()
            seen.add(path)
            files.append((path, total, size))
            if b'p' in item.get(b'attr', b''):
                padding.add(path)
            total += size
        if not total or len(hashes) != ((total + piece - 1) // piece) * 20:
            raise ValueError()
        return {'files': files, 'padding': padding, 'total': total, 'piece_length': piece, 'pieces': hashes, 'hash': key}
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        raise _invalid() from None


def export_native(job):
    """Use an existing MP downloader instance. No login, search, or tracker request."""
    try:
        from app.helper.downloader import DownloaderHelper
        service = DownloaderHelper().get_service(name=job.get('downloader'))
        if not service or service.type != 'qbittorrent':
            raise _invalid()
        client = getattr(service.instance, 'qbc', None)
        export = getattr(client, 'torrents_export', None)
        if not callable(export):
            raise _invalid()
        return evidence(export(torrent_hash=job['download_hash']), job['download_hash'])
    except BridgeError:
        raise BridgeError('无法取得原种子校验证据，保留文件；下载器恢复后自动复核', review=True) from None
    except Exception:
        # SDK errors can contain authenticated URLs. Never persist raw exceptions.
        raise BridgeError('原种子校验证据暂不可读，保留文件并自动重试', review=True) from None


class PieceValidator:
    """Consume the same local read as SHA1, including authenticated boundary pieces."""
    def __init__(self, proof, job, candidate, stop, config):
        self.plan = manifest(proof, job['download_hash'])
        self.stop, self.neighbors, self.checked, self.received = stop, {}, 0, 0
        self.config = config
        self.organized = {}
        for item in job.get('owned_candidates', []):
            key = str(item.get('source') or '').replace('\\', '/')
            if key in self.organized and self.organized[key] != item['local']:
                raise _invalid()
            if key:
                self.organized[key] = item['local']
        self.local = Path(candidate['local'])
        self.before = file_signature(self.local)
        source = str(candidate.get('source') or '').replace('\\', '/')
        if not source or any(p in ('.', '..') for p in source.split('/')):
            raise _invalid()
        parts = PurePosixPath(source).parts
        matches = [f for f in self.plan['files'] if tuple(PurePosixPath(f[0]).parts) == parts[-len(PurePosixPath(f[0]).parts):]]
        if len(matches) != 1:
            raise _invalid()
        relative, self.start, self.size = matches[0]
        if relative in self.plan['padding']:
            raise _invalid()
        if job.get('download_manifest', {}).get(source) != self.size or self.before[0] != self.size or not self.size:
            raise BridgeError('本地文件大小或原下载清单与原种子不一致，保留文件', review=True)
        self.base = Path(source)
        for _ in PurePosixPath(relative).parts:
            self.base = self.base.parent
        if not self.base.is_absolute():
            raise _invalid()
        self.piece = self.plan['piece_length']
        self.index = self.start // self.piece
        self.last = (self.start + self.size - 1) // self.piece
        self.buffer = bytearray(self._range(self.index * self.piece, self.start))

    def _range(self, lo, hi):
        result = bytearray()
        for relative, offset, size in self.plan['files']:
            a, b = max(lo, offset), min(hi, offset + size)
            if a >= b:
                continue
            if relative in self.plan['padding']:
                result.extend(b'\x00' * (b - a))
                continue
            path = self.base.joinpath(*PurePosixPath(relative).parts)
            try:
                organized = self.organized.get(path.as_posix())
                if organized:
                    self.config.relative(organized)
                    path = Path(organized)
                else:
                    path.resolve(strict=True).relative_to(self.base.resolve(strict=True))
                    cursor = self.base
                    for part in PurePosixPath(relative).parts:
                        cursor /= part
                        if cursor.is_symlink():
                            raise _invalid()
                before = file_signature(path)
                if path in self.neighbors and self.neighbors[path] != before:
                    raise Awaiting('原种子边界文件仍在变化，稍后自动复核')
                self.neighbors[path] = before
                with path.open('rb') as stream:
                    stream.seek(a - offset)
                    remaining = b - a
                    while remaining:
                        check_stop(self.stop)
                        block = stream.read(min(4 * 1024 * 1024, remaining))
                        if not block:
                            raise OSError()
                        result.extend(block)
                        remaining -= len(block)
                if file_signature(path) != before:
                    raise Awaiting('原种子边界文件仍在变化，稍后自动复核')
            except (OSError, ValueError):
                raise Awaiting('原种子跨文件分块缺少可读附件，保留文件并自动复核') from None
        if len(result) != hi - lo:
            raise _invalid()
        return result

    def update(self, block):
        self.received += len(block)
        if self.received > self.size:
            raise _invalid()
        self.buffer.extend(block)
        while len(self.buffer) >= self.piece:
            self._check(bytes(self.buffer[:self.piece]))
            del self.buffer[:self.piece]

    def _stable(self):
        try:
            if file_signature(self.local) == self.before and all(file_signature(p) == s for p, s in self.neighbors.items()):
                return
        except OSError:
            pass
        raise Awaiting('校验期间文件仍在变化，稍后自动复核')

    def _check(self, data):
        check_stop(self.stop)
        if sha1(data).digest() != self.plan['pieces'][self.index * 20:(self.index + 1) * 20]:
            self._stable()
            error = BridgeError('本地文件内容与原种子不一致，继续拦截：' + self.local.name, review=True)
            error.content_rejection = {'signature': self.before,
                                      'neighbors': {str(p): s for p, s in self.neighbors.items()}}
            raise error
        self.index += 1
        self.checked += 1

    def finish(self):
        if self.received != self.size:
            raise _invalid()
        end = self.start + self.size
        self.update_tail(self._range(end, min((self.last + 1) * self.piece, self.plan['total'])))
        self._stable()
        if self.index != self.last + 1:
            raise _invalid()

    def update_tail(self, tail):
        self.buffer.extend(tail)
        if self.buffer:
            self._check(bytes(self.buffer))
            self.buffer.clear()
