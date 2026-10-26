"""Bounded CD2 RPCs. No dependency on the MoviePilot CloudDrive storage plugin."""

from pathlib import PurePosixPath
from urllib.parse import urlsplit
import re
import time

from .domain import Awaiting, BridgeError, child_path, check_stop
from .cooldown import CloudCooldown, TransientPause


class CD2:
    def __init__(self, config, stop, stub=None):
        import grpc
        from clouddrive2_client.proto import clouddrive_pb2, clouddrive_pb2_grpc
        self.grpc, self.pb = grpc, clouddrive_pb2
        self.config, self.stop, self.channel = config, stop, None
        self.guard = None
        self.created_directories = {}
        if stub is None:
            url = urlsplit(config.cd2_address if "://" in config.cd2_address else "http://" + config.cd2_address)
            endpoint = f"[{url.hostname}]:{url.port}" if ":" in url.hostname else f"{url.hostname}:{url.port}"
            if url.scheme == "https":
                self.channel = grpc.secure_channel(endpoint, grpc.ssl_channel_credentials())
            else:
                self.channel = grpc.insecure_channel(endpoint)
            stub = clouddrive_pb2_grpc.CloudDriveFileSrvStub(self.channel)
        self.stub = stub
        self.metadata = (("authorization", "Bearer " + config.cd2_token),)

    def close(self):
        if self.channel is not None:
            self.channel.close()

    def _rpc_pause(self, error):
        if error.code() in (self.grpc.StatusCode.UNAUTHENTICATED, self.grpc.StatusCode.PERMISSION_DENIED):
            raise BridgeError('CD2 令牌无效或无操作权限，请核对令牌范围', review=True) from None
        if self.guard:
            # Inspect only for classification; authenticated RPC details are never
            # persisted or exposed. Rate limits keep the original hourly policy.
            details = str(error.details() or '')
            if (error.code() == self.grpc.StatusCode.RESOURCE_EXHAUSTED or
                    re.search(r'(?<!\d)429(?!\d)|达到.*访问上限|too many requests', details, re.I)):
                raise CloudCooldown(self.guard.defer(time.time() + 3600, 'CD2 返回访问上限')['until']) from None
            raise TransientPause(self.guard.transient()['until']) from None

    def _children(self, path):
        check_stop(self.stop)
        if self.guard:
            self.guard.before()
        try:
            replies = self.stub.GetSubFiles(self.pb.ListSubFileRequest(path=path, forceRefresh=True),
                                           metadata=self.metadata, timeout=30)
            children = []
            names = set()
            for reply in replies:
                check_stop(self.stop)
                for item in reply.subFiles:
                    if item.name in names:
                        raise BridgeError("CD2 返回重复名称，无法唯一核对目录", review=True)
                    expected = child_path(path, item.name)
                    if str(PurePosixPath(item.fullPathName)) != expected:
                        raise BridgeError("CD2 返回的子文件路径不匹配", review=True)
                    names.add(item.name)
                    children.append(item)
                    if len(children) > 10000:
                        raise BridgeError("单个目录超过 10000 项，请缩小移交范围", review=True)
            return children
        except self.grpc.RpcError as error:
            if error.code() == self.grpc.StatusCode.NOT_FOUND:
                return None
            if error.code() == self.grpc.StatusCode.UNAUTHENTICATED:
                raise BridgeError("CD2 令牌无效或已过期，请更新插件中的令牌", review=True) from None
            if error.code() == self.grpc.StatusCode.PERMISSION_DENIED:
                raise BridgeError("CD2 令牌无目录读取权限，请检查令牌的根目录范围与权限", review=True) from None
            self._rpc_pause(error)
            raise BridgeError("CD2 目录读取失败，请检查令牌、权限和连接") from None

    def ensure_directory(self, path):
        if not path.startswith(self.config.cd2_prefix.rstrip('/') + '/'):
            raise BridgeError('目录越出配置范围', review=True)
        item = self._lookup(path)
        if item is not None:
            if not item.isDirectory:
                raise BridgeError('目标目录位置被文件占用', review=True)
            return str(item.id or '')
        parent = str(PurePosixPath(path).parent)
        if parent != self.config.cd2_prefix:
            self.ensure_directory(parent)
        check_stop(self.stop)
        if self.guard:
            self.guard.before()
        try:
            result = self.stub.CreateFolder(self.pb.CreateFolderRequest(parentPath=parent,
                folderName=PurePosixPath(path).name), metadata=self.metadata, timeout=30)
        except self.grpc.RpcError as error:
            self._rpc_pause(error)
            raise Awaiting('建立日期目录结果未确认，稍后按原路径核对') from None
        if not result.result.success:
            raise Awaiting('日期目录尚未建立，稍后重新核对')
        # A successful path lookup is not a creation receipt: visibility may have
        # changed or another same-name directory may have appeared. Only the ID
        # returned by CreateFolder can grant cleanup ownership of that directory.
        receipt = result.folderCreated
        created_id = str(receipt.id or '')
        proven = (receipt.isDirectory and receipt.name == PurePosixPath(path).name
                  and receipt.fullPathName == path
                  and re.fullmatch(r'[1-9][0-9]{0,39}', created_id))
        ident = self.directory_id(path)
        if proven:
            if ident != created_id:
                raise Awaiting('建目录回执与当前目录身份尚未一致，保留现场等待重新核对')
            self.created_directories[path] = created_id
        return ident

    def _lookup(self, path):
        prefix = self.config.cd2_prefix
        if not path.startswith(prefix.rstrip("/") + "/"):
            raise BridgeError("操作越出配置的 115 挂载目录", review=True)
        relative = str(PurePosixPath(path).relative_to(prefix))
        current = prefix
        found = None
        for name in relative.split("/"):
            entries = self._children(current)
            if entries is None:
                return None
            found = next((e for e in entries if e.name == name), None)
            if found is None:
                return None
            current = child_path(current, name)
            if current != path and not found.isDirectory:
                raise BridgeError("CD2 路径的中间层不是目录", review=True)
        return found

    @staticmethod
    def _file(item):
        digest = str(item.fileHashes.get(2, "")).lower()
        if digest and not re.fullmatch(r"[0-9a-f]{40}", digest):
            raise BridgeError("CD2 文件 SHA1 格式无效", review=True)
        return {"size": item.size, "sha1": digest, "id": item.id}

    def exists(self, path):
        return self._lookup(path) is not None

    def directory_id(self, path):
        """115 cloud directory identity, not a path or a local upload placeholder.

        Live CD2/115 checks confirm numeric folder IDs survive same-drive moves
        and removal of their children. Unknown identity formats cannot prove a move.
        """
        item = self._lookup(path)
        if item is None:
            raise Awaiting("等待 CD2 读取批次目录身份")
        if not item.isDirectory:
            raise BridgeError("批次目录位置被文件占用，已暂停移交", review=True)
        identifier = str(item.id or "")
        return identifier if re.fullmatch(r"[1-9][0-9]{0,39}", identifier) else ""

    def directories(self, path):
        if path != self.config.cd2_prefix:
            item = self._lookup(path)
            if item is None or not item.isDirectory:
                raise Awaiting("归档目录暂不可读，请稍后重新检查")
        entries = self._children(path)
        if entries is None:
            raise Awaiting("归档目录暂不可读，请稍后重新检查")
        return sorted((item.fullPathName for item in entries if item.isDirectory), key=str.casefold)

    def require_inbox(self, path):
        item = self._lookup(path)
        if item is None or not item.isDirectory:
            raise Awaiting("请先在 CD2 创建配置的 Symedia 待归档目录")

    def directory_only_children(self, path):
        """A cheap *hint*, never proof that descendants are empty.

        Housekeeping must also verify directory identity and recursively inspect
        the native 115 inventory immediately before retiring or recycling.
        """
        rows = self._children(path)
        if rows is None:
            raise Awaiting('CD2 目录暂不可读，保留目录')
        return all(item.isDirectory for item in rows)

    def file_at(self, path):
        item = self._lookup(path)
        if item is None:
            return None
        if item.isDirectory:
            raise BridgeError("云端文件位置被同名目录占用", review=True)
        return self._file(item)

    def tree(self, path):
        root = self._lookup(path)
        if root is None or not root.isDirectory:
            raise Awaiting("等待 CD2 读取到完整暂存目录")
        result = {}
        pending = [(path, 0)]
        visited = set()
        while pending:
            current, depth = pending.pop()
            if current in visited or depth > 24 or len(visited) > 1000:
                raise BridgeError("目录层级或数量异常，已停止遍历", review=True)
            visited.add(current)
            entries = self._children(current)
            if entries is None:
                raise Awaiting("CD2 目录在核对期间发生变化")
            for item in entries:
                relative = str(PurePosixPath(item.fullPathName).relative_to(path))
                if item.isDirectory:
                    pending.append((item.fullPathName, depth + 1))
                else:
                    result[relative] = self._file(item)
                    if len(result) > 10000:
                        raise BridgeError("单批超过 10000 个文件，请拆分批次", review=True)
        return result

    def move_directory(self, source, destination):
        check_stop(self.stop)
        if self.guard:
            self.guard.before()
        request = self.pb.MoveFileRequest(
            theFilePaths=[source], destPath=destination,
            conflictPolicy=self.pb.MoveFileRequest.Skip,
            moveAcrossClouds=False, handleConflictRecursively=False)
        try:
            result = self.stub.MoveFile(request, metadata=self.metadata, timeout=60)
        except self.grpc.RpcError as error:
            self._rpc_pause(error)
            raise BridgeError("CD2 移动响应未收到，等待核对结果") from None
        expected = child_path(destination, PurePosixPath(source).name)
        paths = [str(PurePosixPath(path)) for path in result.resultFilePaths]
        if not result.success or (paths and paths != [expected]):
            return False
        # Some CD2 builds acknowledge a successful move without resultFilePaths.
        # Skip-on-conflict may also return success: source disappearance is required
        # before accepting that empty-path receipt. Symedia may already consume dest.
        return bool(paths or not self.exists(source))
