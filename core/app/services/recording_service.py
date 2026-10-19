"""The patient's spoken authorization memo, played to the insurer when the call connects.

Relay voice memos arrive as AAC in an MP4 container; Telnyx plays only MP3 or
WAV, so the memo is converted once with ffmpeg when it is stored. The audio is
PHI: it is served only through a fresh random token per call (only the token's
hash is stored) and only while that case has a live insurer call.
"""

import hashlib
import logging
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from claimshield_contracts.enums import CallStatus
from claimshield_contracts.ids import new_id

from ..audio_store import AudioStore, DbAudioStore
from ..audit import record_audit
from ..errors import InvalidInput, UpstreamError
from ..models_db import AuthorizationRecordingRow, CallRow, CaseRow

log = logging.getLogger("claimshield.recording")

MAX_INPUT_BYTES = 6 * 1024 * 1024
MAX_SECONDS = 45  # a consent memo is one sentence; longer audio is trimmed
PLAYABLE = {"audio/mpeg": "audio/mpeg", "audio/mp3": "audio/mpeg"}
RECORDING_PATH = "/voice/recordings"
_LIVE = (
    CallStatus.INITIATING.value,
    CallStatus.IN_PROGRESS.value,
    CallStatus.PAUSED_FOR_HUMAN.value,
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def to_mp3(audio: bytes, mime_type: str) -> bytes:
    """Convert a voice memo to mono MP3 for phone playback (ffmpeg)."""
    kind = mime_type.split(";")[0].strip().lower()
    if kind in PLAYABLE:
        return audio
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise UpstreamError(
            "ffmpeg is not installed, so the recording can't be converted for the phone.",
            code="recording_conversion_unavailable",
        )
    with tempfile.TemporaryDirectory() as tmp:
        # A file, not a pipe: phone memos often keep the MP4 index at the end.
        source = Path(tmp) / "memo.input"
        target = Path(tmp) / "memo.mp3"
        source.write_bytes(audio)
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-t",
                    str(MAX_SECONDS),
                    "-ac",
                    "1",
                    "-ar",
                    "22050",
                    "-b:a",
                    "48k",
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("recording conversion failed (%s)", type(exc).__name__)
            raise InvalidInput(
                "That recording couldn't be converted for the phone.",
                code="recording_unreadable",
            ) from exc
        return target.read_bytes()


def store_authorization(
    db: Session,
    case: CaseRow,
    audio: bytes,
    mime_type: str,
    *,
    actor: str,
    store: AudioStore | None = None,
) -> AuthorizationRecordingRow:
    """Store (or replace) the case's authorization memo as MP3.

    With an encrypted file store ([2.3]) the bytes go there and the row keeps
    only a storage key: database dumps no longer carry spoken PHI.
    """
    if not audio:
        raise InvalidInput("The recording is empty.", code="recording_empty")
    if len(audio) > MAX_INPUT_BYTES:
        raise InvalidInput("The recording is too large.", code="recording_too_large")
    mp3 = to_mp3(audio, mime_type)
    row = db.scalars(
        select(AuthorizationRecordingRow).where(AuthorizationRecordingRow.case_id == case.id)
    ).first()
    if row is None:
        row = AuthorizationRecordingRow(id=new_id("recording"), case_id=case.id)
        db.add(row)
    if store is not None and not isinstance(store, DbAudioStore):
        old_key = row.storage_key
        store.put(row.id, mp3)
        if old_key and old_key != row.id:
            store.delete(old_key)
        row.audio = None
        row.storage_key = row.id
    else:
        row.audio = mp3
        row.storage_key = None
    row.mime_type = "audio/mpeg"
    row.size_bytes = len(mp3)
    row.token_hash = _hash(secrets.token_urlsafe(32))  # nothing can fetch it until a call
    db.flush()
    record_audit(
        db,
        actor=actor,
        action="case.authorization_recording_stored",
        case_id=case.id,
        resource_type="recording",
        resource_id=row.id,
        details={"size_bytes": len(mp3), "source_type": mime_type.split(";")[0]},
    )
    return row


def has_authorization(db: Session, case_id: str) -> bool:
    return (
        db.scalars(
            select(AuthorizationRecordingRow.id).where(AuthorizationRecordingRow.case_id == case_id)
        ).first()
        is not None
    )


def playback_url(db: Session, case_id: str, public_base_url: str) -> str | None:
    """A fresh URL for this call's playback (any older URL stops working)."""
    row = db.scalars(
        select(AuthorizationRecordingRow).where(AuthorizationRecordingRow.case_id == case_id)
    ).first()
    if row is None or not public_base_url:
        return None
    token = secrets.token_urlsafe(32)
    row.token_hash = _hash(token)
    return f"{public_base_url.rstrip('/')}{RECORDING_PATH}/{token}.mp3"


def audio_for_token(db: Session, token: str, *, store: AudioStore | None = None) -> bytes | None:
    """The MP3 for a playback token, only while that case has a live call.

    [2.6] Every attempt is audited: a denied fetch (bad or expired token, or no
    live call) is the brute-force signal an operator alerts on -- there should
    be roughly one granted fetch per live call, never bursts.
    """
    row = db.scalars(
        select(AuthorizationRecordingRow).where(
            AuthorizationRecordingRow.token_hash == _hash(token)
        )
    ).first()
    case_id = row.case_id if row else None
    live = (
        db.scalars(
            select(CallRow.id).where(
                CallRow.case_id == case_id,
                CallRow.status.in_(_LIVE),
                CallRow.ended_at.is_(None),
            )
        ).first()
        if case_id
        else None
    )
    audio: bytes | None = None
    if row is not None and live is not None:
        audio = store.get(row.storage_key) if row.storage_key and store is not None else row.audio
    if audio is None:
        log.warning(
            "recording playback denied",
            extra={"fields": {"case_id": case_id}},
        )
    record_audit(
        db,
        actor="telephony:playback",
        action="recording.playback",
        case_id=case_id,
        resource_type="recording",
        resource_id=row.id if row else None,
        details={"granted": audio is not None},
    )
    return audio


def silence_wav(seconds: int = 2) -> bytes:
    """Silent 8 kHz WAV used as the hold audio for the AI leg (no hold music)."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x00" * 8000 * seconds)
    return buffer.getvalue()
