"""Bounded CD2 RPCs. No dependency on the MoviePilot CloudDrive storage plugin."""

from pathlib import PurePosixPath
from urllib.parse import urlsplit
import re

from .domain import Awaiting, BridgeError, child_path, check_stop


class CD2:
    def __init__(self, config, stop, stub=None):
        import grpc
        from clouddrive2_client.proto import clouddrive_pb2, clouddrive_pb2_grpc
        self.grpc, self.pb = grpc, clouddrive_pb2
        self.config, self.stop, self.channel = config, stop, None
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

    def _children(self, path):
        check_stop(self.stop)
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
            raise BridgeError("CD2 目录读取失败，请检查令牌、权限和连接") from None

    def _lookup(self, path):
        prefix = self.config.cd2_prefix
        if not path.startswith(prefix + "/"):
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

    def require_inbox(self, path):
        item = self._lookup(path)
        if item is None or not item.isDirectory:
            raise Awaiting("请先在 CD2 创建配置的 Symedia 待归档目录")

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
        request = self.pb.MoveFileRequest(
            theFilePaths=[source], destPath=destination,
            conflictPolicy=self.pb.MoveFileRequest.Skip,
            moveAcrossClouds=False, handleConflictRecursively=False)
        try:
            result = self.stub.MoveFile(request, metadata=self.metadata, timeout=60)
        except self.grpc.RpcError:
            raise BridgeError("CD2 移动响应未收到，等待核对结果") from None
        expected = child_path(destination, PurePosixPath(source).name)
        return bool(result.success and list(result.resultFilePaths) == [expected])
