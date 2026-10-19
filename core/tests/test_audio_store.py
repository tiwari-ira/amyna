"""[2.3] Authorization audio: encrypted files outside the DB (prod) vs blob (dev).

The database row keeps only a storage key in file mode: database dumps and
backups no longer carry spoken PHI, and the bytes on disk are Fernet ciphertext.
"""

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from app.audio_store import DbAudioStore, EncryptedFileStore, build_audio_store
from app.demo_support import build_isolated_app, create_demo_visit, service_headers
from app.models_db import AuthorizationRecordingRow, CaseRow
from app.services import recording_service
from claimshield_contracts.enums import ServiceName

MP3 = b"ID3fake-mp3-bytes"
FERNET_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()


def _case(client):  # noqa: ANN001, ANN202
    relay = service_headers(client.app.state.container.settings, ServiceName.RELAY)
    setup = create_demo_visit(client, relay)
    container = client.app.state.container
    with container.session_scope() as db:
        case = db.get(CaseRow, setup["case_id"])
    return container, case


def test_file_mode_stores_ciphertext_outside_the_db(tmp_path: Path) -> None:
    app = build_isolated_app(
        tmp_path / "core.db",
        authorization_audio_store="file",
        authorization_audio_dir=str(tmp_path / "recordings"),
        recording_encryption_key=FERNET_KEY,
    )
    from fastapi.testclient import TestClient

    container, case = _case(TestClient(app))
    store = container.audio_store
    assert isinstance(store, EncryptedFileStore)

    with container.session_scope() as db:
        recording_service.store_authorization(db, case, MP3, "audio/mpeg", actor="t", store=store)
        row = db.scalars(select(AuthorizationRecordingRow)).first()
        assert row.audio is None and row.storage_key == row.id
        assert store.get(row.storage_key) == MP3

    files = list((tmp_path / "recordings").glob("*.bin"))
    assert len(files) == 1
    on_disk = files[0].read_bytes()
    assert MP3 not in on_disk  # ciphertext, not plaintext


def test_db_mode_is_unchanged(tmp_path: Path) -> None:
    app = build_isolated_app(tmp_path / "core.db")
    from fastapi.testclient import TestClient

    container, case = _case(TestClient(app))
    assert isinstance(container.audio_store, DbAudioStore)
    with container.session_scope() as db:
        row = recording_service.store_authorization(db, case, MP3, "audio/mpeg", actor="t")
        assert row.audio == MP3 and row.storage_key is None


def test_a_wrong_key_cannot_decrypt(tmp_path: Path) -> None:
    store = EncryptedFileStore(tmp_path / "rec", FERNET_KEY)
    store.put("rec_1", MP3)
    other = EncryptedFileStore(tmp_path / "rec", base64.urlsafe_b64encode(b"x" * 32).decode())
    assert other.get("rec_1") is None  # logged + denied, never raises


def test_staging_refuses_file_mode_without_a_key(tmp_path: Path) -> None:
    from app.config import ConfigError

    with pytest.raises(ConfigError, match="RECORDING_ENCRYPTION_KEY"):
        build_audio_store(
            mode="file",
            root=tmp_path / "rec",
            encryption_key="",
            app_env="staging",
        )
    # Dev/test gets an ephemeral key instead (recordings do not survive restarts).
    store, mode = build_audio_store(
        mode="file", root=tmp_path / "rec2", encryption_key="", app_env="test"
    )
    assert mode == "file"
    store.put("rec_1", MP3)
    assert store.get("rec_1") == MP3
