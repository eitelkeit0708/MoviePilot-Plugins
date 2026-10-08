from hashlib import sha1
from pathlib import Path, PurePosixPath
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import grpc
import pytest
from clouddrive2_client.proto import clouddrive_pb2 as pb


@pytest.fixture
def native(modules, config_values):
    config = modules.domain.Config.parse(config_values)
    video = Path(config.local_root) / "电影" / "作品 (2026)" / "作品.mkv"
    subtitle = video.with_suffix(".zh.srt")
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    subtitle.write_bytes(b"sub")
    files = [NS(name="Release/video.mkv", size=5, priority=1, progress=1),
             NS(name="Release/video.zh.srt", size=3, priority=1, progress=1)]
    rows = [NS(id=i + 1, download_hash="hash", downloader="qb", status=True, src_storage="local",
               src="/downloads/" + f.name, dest=str(p), dest_storage="local")
            for i, (f, p) in enumerate(zip(files, [video, subtitle]))]
    downloads = NS(get_files_by_hash=Mock(return_value=[NS(downloader="qb", savepath="/downloads")]))
    transfers = NS(list_by_hash=Mock(return_value=rows))
    chain = NS(torrent_files=Mock(return_value=files), list_torrents=Mock(return_value=[NS(progress=100)]))
    storage = NS(get_folder=Mock(), upload_file=Mock())
    host = modules.host.MPHost(config, chain=chain, storage=storage, downloads=downloads,
                               transfers=transfers, extensions={".mkv", ".srt"})
    return NS(host=host, config=config, files=files, rows=rows, downloads=downloads, transfers=transfers,
              chain=chain, storage=storage, video=video, subtitle=subtitle,
              job={"download_hash": "hash", "downloader": "qb", "files": []})


def test_native_manifest_includes_subtitle_omitted_from_mp_download_db(native):
    results = native.host.collect(native.job)
    assert {r["local"] for r in results} == {str(native.video), str(native.subtitle)}
    assert len(native.job["download_manifest"]) == 2
    native.chain.torrent_files.assert_called_once_with(tid="hash", downloader="qb")


@pytest.mark.parametrize("problem", ["download_incomplete", "transfer_missing", "transfer_failed", "different_downloader"])
def test_incomplete_native_subtitle_holds_whole_task(native, modules, problem):
    if problem == "download_incomplete":
        native.files[1].progress = .9
    elif problem == "transfer_missing":
        native.rows.pop()
    elif problem == "transfer_failed":
        native.rows[1].status = False
    else:
        native.rows[1].downloader = "another-qb"
    with pytest.raises(modules.domain.Awaiting):
        native.host.collect(native.job)


def test_transmission_selected_and_completed_contract(native):
    native.chain.torrent_files.return_value = [
        NS(name="Release/video.mkv", size=5, completed=5, selected=True),
        NS(name="Release/video.zh.srt", size=3, completed=3, selected=True),
        NS(name="Release/Sample.mkv", size=10, completed=0, selected=False)]
    assert len(native.host.collect(native.job)) == 2


def test_deselected_video_not_added_and_late_selection_after_seal_held(native, modules):
    native.host.collect(native.job)
    native.job["files"] = [{"relative": "sealed"}]
    native.files[1].priority = 0
    with pytest.raises(modules.domain.BridgeError, match="选择发生变化"):
        native.host.collect(native.job)


def test_missing_downloader_does_not_guess_batch_complete(native, modules):
    native.chain.torrent_files.return_value = []
    with pytest.raises(modules.domain.Awaiting):
        native.host.collect(native.job)


def test_deselected_file_is_not_reintroduced_by_transfer_history(native):
    native.files[1].priority = 0
    assert native.host.collect(native.job) == [{"local": str(native.video), "history_id": 1}]


def test_duplicate_downloader_paths_require_review(native, modules):
    native.files.append(native.files[0])
    with pytest.raises(modules.domain.BridgeError, match="重复文件"):
        native.host.collect(native.job)


def test_mixed_output_roots_require_review(native, modules, tmp_path):
    native.rows[1].dest = str(tmp_path / "elsewhere.srt")
    with pytest.raises(modules.domain.BridgeError, match="本批部分文件") as caught:
        native.host.collect(native.job)
    assert caught.value.review


def test_native_public_storage_contract(native):
    remote = "/MP暂存/batch/电影/作品 (2026)/作品.mkv"
    parent = NS(type="dir", path=str(Path(remote).parent), fileid="123")
    native.storage.get_folder.return_value = parent
    native.storage.upload_file.return_value = NS(path=remote, size=5, fileid="456")
    assert native.host.upload(native.video, remote) == {"size": 5, "fileid": "456"}
    native.storage.get_folder.assert_called_once_with(storage="115网盘Plus", path=Path(remote).parent)
    native.storage.upload_file.assert_called_once_with(fileitem=parent, path=native.video, new_name=native.video.name)


def test_wrong_upload_folder_never_writes(native, modules):
    native.storage.get_folder.return_value = NS(type="dir", path="/old/cache/path")
    with pytest.raises(modules.domain.BridgeError):
        native.host.upload(native.video, "/MP暂存/batch/作品.mkv")
    native.storage.upload_file.assert_not_called()


def cloud_file(path, directory=False, size=0, digest=""):
    return pb.CloudDriveFile(id="id-" + path, name=path.split("/")[-1], fullPathName=path,
                             isDirectory=directory, size=size, fileHashes={2: digest} if digest else {})


def test_cd2_move_real_protobuf_is_single_directory_and_no_merge(modules, config_values):
    stub = NS(MoveFile=Mock(return_value=pb.FileOperationResult(success=True, resultFilePaths=["/115/Symedia待归档/batch"])))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    assert cloud.move_directory("/115/MP暂存/batch", "/115/Symedia待归档")
    args, kwargs = stub.MoveFile.call_args
    request = args[0]
    assert list(request.theFilePaths) == ["/115/MP暂存/batch"]
    assert request.conflictPolicy == pb.MoveFileRequest.Skip
    assert request.moveAcrossClouds is False and request.handleConflictRecursively is False
    assert kwargs == {"metadata": (("authorization", "Bearer test-secret"),), "timeout": 60}
    stub.MoveFile.return_value = pb.FileOperationResult(success=True, resultFilePaths=["/115/Symedia待归档/batch (1)"])
    assert not cloud.move_directory("/115/MP暂存/batch", "/115/Symedia待归档")


def test_cd2_refreshes_ancestors_and_all_batch_children(modules, config_values):
    tree = {"/115": [cloud_file("/115/MP暂存", True)],
            "/115/MP暂存": [cloud_file("/115/MP暂存/batch", True)],
            "/115/MP暂存/batch": [cloud_file("/115/MP暂存/batch/Season 01", True)],
            "/115/MP暂存/batch/Season 01": [cloud_file("/115/MP暂存/batch/Season 01/a.mkv", size=5, digest="a" * 40),
                                               cloud_file("/115/MP暂存/batch/Season 01/a.srt", size=3)]}
    requests = []
    def listing(request, **kwargs):
        requests.append(request)
        assert kwargs["timeout"] == 30
        return iter([pb.SubFilesReply(subFiles=tree[request.path])])
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=NS(GetSubFiles=listing))
    result = cloud.tree("/115/MP暂存/batch")
    assert set(result) == {"Season 01/a.mkv", "Season 01/a.srt"}
    assert all(request.forceRefresh for request in requests)
    assert len(requests) == 4
    assert result["Season 01/a.mkv"]["sha1"] == "a" * 40


def test_cd2_rpc_errors_never_leak_credentials(modules, config_values):
    class Error(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAUTHENTICATED
        def __str__(self):
            return "test-secret"
    stub = NS(GetSubFiles=Mock(side_effect=Error()))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    with pytest.raises(modules.domain.BridgeError) as caught:
        cloud.exists("/115/MP暂存")
    assert "test-secret" not in str(caught.value)


def test_cd2_duplicate_or_escaped_paths_block_traversal(modules, config_values):
    duplicate = cloud_file("/115/x", True)
    stub = NS(GetSubFiles=Mock(return_value=iter([pb.SubFilesReply(subFiles=[duplicate, duplicate])])))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    with pytest.raises(modules.domain.BridgeError, match="重复名称"):
        cloud.exists("/115/x")
    stub.GetSubFiles.return_value = iter([pb.SubFilesReply(subFiles=[cloud_file("/elsewhere/y", True)])])
    with pytest.raises(modules.domain.BridgeError, match="路径不匹配"):
        cloud.exists("/115/x")


def test_batch_through_both_real_adapters_with_offline_services(native, modules, tmp_path):
    """Compose the real engine, MP adapter, SQLite store, CD2 adapter and protobufs.

    Only the download/database/storage service calls and CD2 server are simulated.
    """
    dirs = {"/115", native.config.inbox}
    files, moves = {}, []

    def get_folder(storage, path):
        assert storage == "115网盘Plus"
        full = PurePosixPath("/115" + path.as_posix())
        dirs.update(str(p) for p in [full, *full.parents] if str(p).startswith("/115"))
        return NS(type="dir", path=path.as_posix(), fileid="parent")

    def upload_file(fileitem, path, new_name):
        remote = fileitem.path + "/" + new_name
        data = path.read_bytes()
        files["/115" + remote] = cloud_file("/115" + remote, size=len(data), digest=sha1(data).hexdigest())
        return NS(path=remote, size=len(data), fileid="uploaded")

    def listing(request, **kwargs):
        assert request.forceRefresh and kwargs["timeout"] == 30
        children = [cloud_file(p, True) for p in dirs if str(PurePosixPath(p).parent) == request.path]
        children += [v for p, v in files.items() if str(PurePosixPath(p).parent) == request.path]
        return iter([pb.SubFilesReply(subFiles=children)])

    def move(request, **kwargs):
        assert len(request.theFilePaths) == 1
        source = request.theFilePaths[0]
        assert source in dirs
        destination = request.destPath + "/" + PurePosixPath(source).name
        assert destination not in dirs
        moves.append((source, destination))
        for p in list(dirs):
            if p == source or p.startswith(source + "/"):
                dirs.remove(p)
                dirs.add(destination + p[len(source):])
        for p in list(files):
            if p.startswith(source + "/"):
                item = files.pop(p)
                item.fullPathName = destination + p[len(source):]
                files[item.fullPathName] = item
        return pb.FileOperationResult(success=True, resultFilePaths=[destination])

    native.storage.get_folder.side_effect = get_folder
    native.storage.upload_file.side_effect = upload_file
    store = modules.store.Store(tmp_path / "ledger")
    store.observe(instance="SymediaBatchBridge", download_hash="hash", downloader="qb", title="作品", history_id=1,
                  routing=native.config.routing())
    stop = Event()
    cloud = modules.cd2.CD2(native.config, stop, stub=NS(GetSubFiles=listing, MoveFile=move))
    job = store.jobs()[0]
    modules.engine.Engine(store, native.config, native.host, cloud, stop).process(job)
    result = store.get(job["id"])
    assert result["state"] == "handed_off", result["message"]
    assert result["history_ids"] == [1, 2]
    assert len(moves) == 1 and len(files) == 2
    assert set(cloud.tree(moves[0][1])) == {"电影/作品 (2026)/作品.mkv", "电影/作品 (2026)/作品.zh.srt"}
