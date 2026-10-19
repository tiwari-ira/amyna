"""[2.3] Where the patient's authorization audio lives.

Two backends behind one interface:

* ``db`` (default, dev/test): the MP3 bytes in the authorization_recordings row.
* ``file`` (staging/prod): the MP3 encrypted with Fernet (AES-128-CBC + HMAC,
  authenticated encryption) under a dedicated directory. The row keeps only a
  storage key, so database dumps and backups no longer carry spoken PHI in the
  clear. An S3 backend slots in behind the same protocol later (put/get/delete
  with a storage key); the callers never change.

The key comes from RECORDING_ENCRYPTION_KEY (a Fernet key; generate with
``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).
Dev/test may run without one: an ephemeral process-local key is generated with
a warning, which means test audio never persists usefully across restarts --
exactly what dev/test needs.
"""

import logging
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("claimshield.audio_store")


class AudioStore(Protocol):
    """Byte storage for one case's authorization recording, keyed by recording id."""

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...


class DbAudioStore:
    """The legacy backend: bytes in the row (wired by recording_service)."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}  # unused; recording_service keeps the row

    def put(self, key: str, data: bytes) -> None:
        self._data[key] = data

    def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class EncryptedFileStore:
    """Fernet-encrypted files under a dedicated directory (staging/prod)."""

    def __init__(self, root: Path, fernet_key: str) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)

    def _path(self, key: str) -> Path:
        # Storage keys are ours (recording ids): still, never trust a separator.
        safe = key.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self._root / f"{safe}.bin"

    def put(self, key: str, data: bytes) -> None:
        self._path(key).write_bytes(self._fernet.encrypt(data))

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return self._fernet.decrypt(path.read_bytes())
        except InvalidToken:
            log.warning("authorization recording could not be decrypted (wrong key?)")
            return None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def build_audio_store(
    *, mode: str, root: Path, encryption_key: str, app_env: str
) -> tuple[AudioStore, str]:
    """Returns (store, effective mode) and warns about the dev/test ephemeral key."""
    if mode != "file":
        return DbAudioStore(), "db"
    key = encryption_key
    if not key:
        if app_env in ("staging", "prod"):
            from .config import ConfigError

            raise ConfigError(
                "AUTHORIZATION_AUDIO_STORE=file requires RECORDING_ENCRYPTION_KEY outside dev/test."
            )
        key = Fernet.generate_key().decode()
        log.warning(
            "RECORDING_ENCRYPTION_KEY is empty; using an ephemeral process-local key "
            "(recordings will not decrypt after a restart)"
        )
    return EncryptedFileStore(root, key), "file"
