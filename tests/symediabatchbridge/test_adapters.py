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
    source_root = Path(config.local_root).parent / "downloads"
    (source_root / "Release").mkdir(parents=True)
    (source_root / "Release/video.mkv").write_bytes(b"video")
    (source_root / "Release/video.zh.srt").write_bytes(b"sub")
    files = [NS(name="Release/video.mkv", size=5, priority=1, progress=1),
             NS(name="Release/video.zh.srt", size=3, priority=1, progress=1)]
    rows = [NS(id=i + 1, download_hash="hash", downloader="qb", status=True, src_storage="local",
               src=(source_root / f.name).as_posix(), dest=str(p), dest_storage="local")
            for i, (f, p) in enumerate(zip(files, [video, subtitle]))]
    downloads = NS(get_files_by_hash=Mock(return_value=[NS(downloader="qb", savepath=source_root.as_posix())]))
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


@pytest.mark.parametrize("content_root", [False, True])
def test_mp_download_fullpath_resolves_torrent_top_directory_once(native, content_root):
    root = PurePosixPath(native.rows[0].src).parent
    native.downloads.get_files_by_hash.return_value = [
        NS(downloader="qb", savepath=str(root if content_root else root.parent), fullpath=r.src)
        for r in native.rows]
    result = native.host.collect(native.job)
    assert [r["source"] for r in result] == [r.src for r in native.rows]


def test_download_root_does_not_match_only_filename(native, modules):
    native.downloads.get_files_by_hash.return_value = [
        NS(downloader="qb", savepath="/downloads/Release", fullpath="/other/Release/video.mkv")]
    with pytest.raises(modules.domain.BridgeError, match="路径与 MP"):
        native.host.collect(native.job)


@pytest.fixture
def existing(native, modules):
    native.job["origin"] = "inventory"
    # Real MP get_by(dest=...) returns [] without a media identity.
    native.transfers.get_by = Mock(return_value=[])
    native.transfers.list_by_date = Mock(side_effect=lambda since: list(native.rows))
    native.job["inventory_files"] = [{"local": r.dest, "history_id": r.id,
        "signature": modules.domain.file_signature(Path(r.dest))} for r in native.rows]
    for row in native.rows:
        Path(row.src).unlink()
    native.chain.torrent_files.side_effect = AssertionError("inventory must not contact downloader")
    native.chain.list_torrents.side_effect = AssertionError("inventory must not contact downloader")
    return native


def test_existing_output_does_not_require_deleted_originals_or_downloader(existing):
    result = existing.host.collect(existing.job)
    assert {r["local"] for r in result} == {str(existing.video), str(existing.subtitle)}
    assert all("source" not in r for r in result)
    existing.downloads.get_files_by_hash.assert_not_called()
    existing.transfers.get_by.assert_not_called()
    existing.transfers.list_by_date.assert_called_once()


@pytest.mark.parametrize("problem", ["removed", "replaced", "new_record", "failed", "different_task"])
def test_inventory_snapshot_cannot_silently_drop_or_replace_files(existing, modules, problem):
    if problem == "removed":
        existing.subtitle.unlink()
    elif problem == "replaced":
        existing.subtitle.write_bytes(b"different subtitle")
    elif problem == "new_record":
        existing.rows[0].id += 100
    elif problem == "failed":
        existing.rows[0].status = False
    else:
        existing.rows[0].download_hash = "other"
    if problem in ('removed', 'replaced'):
        with pytest.raises(modules.domain.BridgeError):
            existing.host.collect(existing.job)
    else:
        # History maintenance does not change accepted bytes or ownership.
        assert len(existing.host.collect(existing.job)) == 2
    existing.chain.torrent_files.assert_not_called()


def test_existing_snapshot_excludes_later_files(existing):
    extra = existing.video.with_suffix(".later.srt")
    extra.write_bytes(b"later")
    existing.rows.append(NS(**{**vars(existing.rows[0]), "id": 100, "dest": str(extra)}))
    assert len(existing.host.collect(existing.job)) == 2


def test_inventory_ignores_unrelated_history_rewrite_if_file_unchanged(existing, modules):
    existing.rows.append(NS(**{**vars(existing.rows[0]), "id": 100, "download_hash": "other"}))
    assert len(existing.host.collect(existing.job)) == 2


def test_legacy_import_builds_snapshot_from_remaining_output(existing):
    existing.job.pop("inventory_files")
    existing.transfers.list_by_hash.return_value = existing.rows
    assert len(existing.host.collect(existing.job)) == 2
    assert len(existing.job["inventory_files"]) == 2


def test_inventory_subtitles_without_media_are_held(existing, modules):
    existing.job["inventory_files"] = existing.job["inventory_files"][1:]
    with pytest.raises(modules.domain.BridgeError, match="只有字幕"):
        existing.host.collect(existing.job)


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


def test_late_downloader_selection_does_not_change_owned_scope(native, modules):
    native.host.collect(native.job)
    native.files[1].priority = 0
    assert len(native.host.collect(native.job)) == 2
    native.chain.torrent_files.assert_called_once()


def test_missing_downloader_does_not_guess_batch_complete(native, modules):
    native.chain.torrent_files.return_value = []
    with pytest.raises(modules.domain.Awaiting):
        native.host.collect(native.job)


def test_deselected_file_is_not_reintroduced_by_transfer_history(native):
    native.files[1].priority = 0
    result = native.host.collect(native.job)
    assert len(result) == 1 and result[0]["local"] == str(native.video)
    assert result[0]["source"] == native.rows[0].src and result[0]["history_id"] == 1


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
    native.storage.get_folder.assert_called_once_with(storage="u115", path=Path(remote).parent)
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


def test_cd2_empty_path_success_requires_source_disappearance(modules, config_values, monkeypatch):
    stub = NS(MoveFile=Mock(return_value=pb.FileOperationResult(success=True)))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    exists = Mock(return_value=True)
    monkeypatch.setattr(cloud, "exists", exists)
    assert not cloud.move_directory("/115/MP暂存/batch", "/115/Symedia待归档")
    exists.return_value = False
    assert cloud.move_directory("/115/MP暂存/batch", "/115/Symedia待归档")
    exists.assert_called_with("/115/MP暂存/batch")
    stub.MoveFile.return_value = pb.FileOperationResult(success=False)
    assert not cloud.move_directory("/115/MP暂存/batch", "/115/Symedia待归档")


def test_cd2_drive_root_scoped_token_uses_single_slash(modules, config_values):
    config = modules.domain.Config.parse({**config_values,"cd2_prefix":"/","inbox":"/transfer/LYZ"})
    assert config.cd2_staging == "/MP暂存"
    tree = {"/": [cloud_file("/MP暂存", True)],
            "/MP暂存": [cloud_file("/MP暂存/batch",True)],
            "/MP暂存/batch": [cloud_file("/MP暂存/batch/a.srt",size=3)]}
    requests=[]
    def listing(request, **kwargs):
        requests.append(request.path)
        return iter([pb.SubFilesReply(subFiles=tree[request.path])])
    cloud = modules.cd2.CD2(config,Event(),stub=NS(GetSubFiles=listing))
    assert cloud.tree("/MP暂存/batch")["a.srt"]["size"] == 3
    assert requests == ["/","/MP暂存","/MP暂存/batch"]


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


def test_recovery_browser_is_read_only_scoped_and_fresh(modules, config_values):
    tree = {"/115": [cloud_file("/115/归档", True), cloud_file("/115/file.mkv", size=5)],
            "/115/归档": [cloud_file("/115/归档/作品", True)]}
    requests = []
    def listing(request, **kwargs):
        requests.append(request)
        return iter([pb.SubFilesReply(subFiles=tree[request.path])])
    stub = NS(GetSubFiles=listing, MoveFile=Mock())
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    assert cloud.directories("/115") == ["/115/归档"]
    assert cloud.directories("/115/归档") == ["/115/归档/作品"]
    assert all(r.forceRefresh for r in requests)
    with pytest.raises(modules.domain.BridgeError):
        cloud.directories("/other")
    stub.MoveFile.assert_not_called()


@pytest.mark.parametrize("identifier,expected", [("3535498838548678168", "3535498838548678168"),
                                               ("", ""), ("123/name", ""), ("/115/folder", ""), ("0", "")])
def test_directory_identity_accepts_only_native_115_folder_ids(modules, config_values, identifier, expected):
    item = cloud_file("/115/batch", True)
    item.id = identifier
    stub = NS(GetSubFiles=Mock(side_effect=lambda *a, **k: iter([pb.SubFilesReply(subFiles=[item])])))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    assert cloud.directory_id("/115/batch") == expected
    assert stub.GetSubFiles.call_args.args[0].forceRefresh


def test_missing_or_file_shaped_directory_identity_cannot_confirm_handoff(modules, config_values):
    stub = NS(GetSubFiles=Mock(side_effect=lambda *a, **k: iter([pb.SubFilesReply()])))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    with pytest.raises(modules.domain.Awaiting):
        cloud.directory_id("/115/batch")
    item = cloud_file("/115/batch", False, size=3)
    item.id = "123"
    stub.GetSubFiles.side_effect = lambda *a, **k: iter([pb.SubFilesReply(subFiles=[item])])
    with pytest.raises(modules.domain.BridgeError, match="被文件占用"):
        cloud.directory_id("/115/batch")


def test_cd2_duplicate_or_escaped_paths_block_traversal(modules, config_values):
    duplicate = cloud_file("/115/x", True)
    stub = NS(GetSubFiles=Mock(return_value=iter([pb.SubFilesReply(subFiles=[duplicate, duplicate])])))
    cloud = modules.cd2.CD2(modules.domain.Config.parse(config_values), Event(), stub=stub)
    with pytest.raises(modules.domain.BridgeError, match="重复名称"):
        cloud.exists("/115/x")
    stub.GetSubFiles.return_value = iter([pb.SubFilesReply(subFiles=[cloud_file("/elsewhere/y", True)])])
    with pytest.raises(modules.domain.BridgeError, match="路径不匹配"):
        cloud.exists("/115/x")


def test_batch_through_both_real_adapters_with_offline_services(native, modules, tmp_path, monkeypatch):
    """Compose the real engine, MP adapter, SQLite store, CD2 adapter and protobufs.

    Only the download/database/storage service calls and CD2 server are simulated.
    """
    dirs = {"/115", native.config.inbox}
    files, moves = {}, []

    def get_folder(storage, path):
        assert storage == "u115"
        full = PurePosixPath("/115" + path.as_posix())
        dirs.update(str(p) for p in [full, *full.parents] if str(p).startswith("/115"))
        return NS(type="dir", path=path.as_posix(), fileid="123")

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
    def initialize(method, endpoint, data, retry_limit):
        assert method == "POST" and endpoint == "/open/upload/init" and retry_limit == 0
        filename, filesize, filesha1 = data["file_name"], data["file_size"], data["fileid"]
        local = next(p for p in (native.video, native.subtitle) if p.name == filename)
        # The fake 115 service materializes the file on a successful hash reuse.
        parent = native.storage.get_folder.call_args.kwargs["path"].as_posix()
        remote = "/115" + parent + "/" + filename
        files[remote] = cloud_file(remote, size=filesize, digest=filesha1)
        assert filesha1 == sha1(local.read_bytes()).hexdigest()
        return {"state": True, "data": {"status": 2}}
    monkeypatch.setattr(modules.host, "native_provider", lambda: NS(_request_api=initialize))
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
