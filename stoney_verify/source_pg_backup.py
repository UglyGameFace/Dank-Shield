"""Manual, streaming, encrypted Supabase PostgreSQL backup.

No bot startup hooks. Full pg_dump archive of the confirmed shared *source*
database, encrypted before it touches disk. Nothing uploads or deletes data.
The archive TOC check is NOT a substitute for an independent restore drill.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
from typing import Any, BinaryIO, Iterable
from urllib.parse import unquote, urlsplit

MAGIC = b"DSBK001\n"
SALT_LEN = 16
NONCE_LEN = 12
TAG_LEN = 16
CHUNK_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 2048 * 1024 * 1024
MIN_PASSPHRASE_LEN = 24

# These are fixed, public diagnostic codes. NEVER include raw subprocess
# stderr in exceptions or CI output: pg_dump can echo a source hostname,
# role, and in rare cases a sensitive connection string.
class BackupFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def classify_pg_dump_failure(stderr: bytes) -> str:
    """Return an actionable but credential-free pg_dump failure category."""
    msg = stderr[:32768].decode("utf-8", "replace").lower()
    if any(s in msg for s in (
        "password authentication failed", "no password supplied",
        "authentication failed", "invalid password", "invalid_password",
    )):
        return "database_authentication_rejected"
    if any(s in msg for s in ("tenant or user not found", "unknown tenant")):
        return "pooler_project_or_user_rejected"
    if any(s in msg for s in (
        "could not translate host name", "name or service not known",
        "temporary failure in name resolution", "nodename nor servname",
    )):
        return "database_dns_lookup_failed"
    if any(s in msg for s in (
        "network is unreachable", "no route to host", "connection timed out",
        "operation timed out", "timeout expired",
    )):
        return "database_network_unreachable"
    if "connection refused" in msg:
        return "database_connection_refused"
    if any(s in msg for s in (
        "server does not support ssl", "certificate verify failed",
        "ssl error", "tls handshake",
    )):
        return "database_tls_failed"
    if any(s in msg for s in (
        "too many connections", "too many clients already",
        "remaining connection slots are reserved",
    )):
        return "database_connection_limit"
    if any(s in msg for s in (
        "permission denied", "must be owner of", "must be member of role",
    )):
        return "database_dump_permission_denied"
    if any(s in msg for s in (
        "server version mismatch", "aborting because of server version",
    )):
        return "pg_client_server_version_mismatch"
    if any(s in msg for s in (
        "server closed the connection unexpectedly", "connection to server was lost",
    )):
        return "database_connection_dropped"
    if any(s in msg for s in ("could not connect", "connection to server")):
        return "database_connection_failed_unclassified"
    return "pg_dump_failed_unclassified"



def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        raise ValueError("backup passphrase must be at least 24 characters")
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        passphrase.encode("utf-8")
    )


def _new_encryptor(key: bytes, nonce: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()


def _new_decryptor(key: bytes, nonce: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(key), modes.GCM(nonce)).decryptor()


def validate_source_dsn(dsn: str, project_ref: str) -> dict[str, str]:
    """Require exact Supabase project identity before any pg_dump access.

    Support direct Postgres and the Supabase session pooler, not transaction
    pooling or generic user-supplied hosts. Never emit credentials in errors.
    """
    if not re.fullmatch(r"[a-z0-9]{20}", project_ref or ""):
        raise ValueError("explicit 20-character Supabase source ref required")
    try:
        url = urlsplit(dsn)
        hostname = (url.hostname or "").lower()
        username = unquote(url.username or "")
        password = unquote(url.password or "")
        database = unquote(url.path.lstrip("/"))
        port = url.port or 5432
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid PostgreSQL source connection") from None
    if url.scheme not in ("postgres", "postgresql") or not username or not password:
        raise ValueError("source must be a password-authenticated PostgreSQL URL")
    if database != "postgres" or port != 5432:
        raise ValueError("expected Supabase postgres DB and session port 5432")
    direct = hostname == f"db.{project_ref}.supabase.co" and username == "postgres"
    pooler = (
        hostname.endswith(".pooler.supabase.com")
        and username == f"postgres.{project_ref}"
        and re.fullmatch(r"[a-z0-9.-]+\.pooler\.supabase\.com", hostname)
    )
    if not (direct or pooler):
        raise ValueError("Supabase host or project ref does not match expected source")
    if url.query and any(k.split("=", 1)[0].lower() not in (
        "sslmode", "connect_timeout"
    ) for k in url.query.split("&")):
        raise ValueError("unsupported source URL query option")
    return {
        "PGHOST": hostname,
        "PGPORT": "5432",
        "PGUSER": username,
        "PGPASSWORD": password,
        "PGDATABASE": database,
        "PGSSLMODE": "require",
        "PGCONNECT_TIMEOUT": "10",
    }


def _postgres_tools_are_17() -> None:
    for executable in ("pg_dump", "pg_restore"):
        path = shutil.which(executable)
        if not path:
            raise RuntimeError("PostgreSQL 17 backup/restore binaries required")
        try:
            completed = subprocess.run(
                [path, "--version"], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, check=True, timeout=5
            )
        except (subprocess.SubprocessError, OSError):
            raise RuntimeError("PostgreSQL 17 backup tools unavailable") from None
        version = completed.stdout.decode("ascii", "replace")
        if not re.search(r"\b17(?:\.\d+)?\b", version):
            raise RuntimeError("requires pg_dump and pg_restore major version 17")


def _output_directory(path: Path) -> Path:
    folder = path.expanduser()
    if not folder.is_absolute() or not folder.is_dir() or folder.is_symlink():
        raise ValueError("existing absolute, non-symlink private backup folder required")
    if stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise ValueError("backup folder must be private (owner-only permissions)")
    return folder


def encrypt_stream_to_file(
    plaintext: BinaryIO, target: BinaryIO, passphrase: str, *,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> dict[str, int]:
    """Encrypt pg_dump stdout incrementally, with authenticated AES-256-GCM.

    No temp plaintext file and no whole-database buffering in RAM.
    """
    if not 1 <= max_archive_bytes <= MAX_ARCHIVE_BYTES:
        raise ValueError("archive size limit invalid")
    salt, nonce = os.urandom(SALT_LEN), os.urandom(NONCE_LEN)
    cipher = _new_encryptor(_derive_key(passphrase, salt), nonce)
    target.write(MAGIC + salt + nonce)
    source_bytes = 0
    while True:
        chunk = plaintext.read(CHUNK_BYTES)
        if not chunk:
            break
        source_bytes += len(chunk)
        if source_bytes > max_archive_bytes:
            raise RuntimeError("backup exceeds explicit compressed-byte safety ceiling")
        target.write(cipher.update(chunk))
    target.write(cipher.finalize())
    target.write(cipher.tag)
    if source_bytes < 64:
        raise RuntimeError("pg_dump produced an unexpectedly small archive")
    return {"plaintext_archive_bytes": source_bytes}


def _iter_verified_plaintext(path: Path, passphrase: str) -> Iterable[bytes]:
    """Yield decrypted bytes then authenticate the final GCM tag.

    Only feed this into the NON-EXECUTING pg_restore --list command. Authentication
    is confirmed only at stream end; never restore unauthenticated chunks.
    """
    length = path.stat().st_size
    minimum = len(MAGIC) + SALT_LEN + NONCE_LEN + TAG_LEN + 64
    if length < minimum:
        raise RuntimeError("encrypted backup file is incomplete")
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("unknown encrypted backup version")
        salt = handle.read(SALT_LEN)
        nonce = handle.read(NONCE_LEN)
        decoder = _new_decryptor(_derive_key(passphrase, salt), nonce)
        content_left = length - len(MAGIC) - SALT_LEN - NONCE_LEN - TAG_LEN
        while content_left:
            blob = handle.read(min(CHUNK_BYTES, content_left))
            if not blob:
                raise RuntimeError("truncated encrypted backup")
            content_left -= len(blob)
            plain = decoder.update(blob)
            if plain:
                yield plain
        tag = handle.read(TAG_LEN)
        if len(tag) != TAG_LEN or handle.read(1):
            raise RuntimeError("invalid encrypted archive trailer")
        try:
            decoder.finalize_with_tag(tag)
        except Exception:
            raise RuntimeError("backup authentication failed") from None


def verify_archive_toc(path: Path, passphrase: str) -> dict[str, Any]:
    """Authenticate ALL ciphertext while checking the non-executing dump TOC.

    pg_restore --list is allowed to stop reading stdin once it has consumed
    the archive's table of contents. Large dumps then cause EPIPE when the
    producer writes the remaining encrypted archive payload to its stdin.
    That is *not* a dump verification failure if pg_restore exits cleanly.
    We MUST still decrypt and GCM-authenticate the complete archive after
    pg_restore has closed its input, or a corrupted trailing payload could
    falsely be reported as verified.
    """
    check = subprocess.Popen(
        ["pg_restore", "--list"], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    total = 0
    consumer_closed = False
    try:
        assert check.stdin is not None
        for data in _iter_verified_plaintext(path, passphrase):
            total += len(data)
            if not consumer_closed:
                try:
                    check.stdin.write(data)
                except BrokenPipeError:
                    # pg_restore -l may exit after reading only the TOC.
                    # Keep authenticating the rest; check exit code below.
                    consumer_closed = True
        try:
            check.stdin.close()
        except BrokenPipeError:
            consumer_closed = True
        # An early EOF with exit 0 is normal for non-executing TOC listing.
        # Any nonzero status, even after EPIPE, remains a hard failure.
        if check.wait(timeout=90) != 0:
            raise RuntimeError("pg_restore rejected custom-format archive TOC")
    except Exception:
        if check.poll() is None:
            check.kill()
        check.wait()
        raise
    return {
        "authenticated": True, "archive_toc_readable": True,
        "plaintext_archive_bytes": total, "restore_drill_passed": False
    }


def _sanitized_pg_environment(pg_vars: dict[str, str]) -> dict[str, str]:
    # Preserve only required process settings; do not hand the subprocess
    # application/GitHub/Discord/Supabase API secrets.
    safe = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL")
            if k in os.environ}
    safe.update(pg_vars)
    return safe


def create_backup(
    *, dsn: str, source_ref: str, passphrase: str, output_dir: Path,
) -> dict[str, Any]:
    """One-shot private operator backup, no DB writes or data transfer to Discloud."""
    _postgres_tools_are_17()
    pg_vars = validate_source_dsn(dsn, source_ref)
    _derive_key(passphrase, b"0" * SALT_LEN)  # Refuse weak key before DB access
    folder = _output_directory(output_dir)
    # Explicit disk threshold. This is LOCAL backup destination storage, NOT
    # Discloud PostgreSQL database quota or a guarantee of durable backups.
    if shutil.disk_usage(folder).free < 256 * 1024 * 1024:
        raise RuntimeError("backup destination has insufficient free disk space")
    name = datetime.now(timezone.utc).strftime("supabase-source-%Y%m%dT%H%M%SZ")
    suffix = os.urandom(6).hex()
    destination = folder / f"{name}-{suffix}.dsbak"
    pending = folder / f".{destination.name}.partial"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    proc = None
    try:
        proc = subprocess.Popen(
            ["pg_dump", "--format=custom", "--compress=6",
             "--no-owner", "--no-privileges"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=_sanitized_pg_environment(pg_vars)
        )
        # Drain stderr concurrently. Capture only a bounded prefix in memory,
        # never write raw pg_dump errors (which may contain a DSN) to disk.
        pg_errors = bytearray()
        def _drain_stderr() -> None:
            assert proc is not None and proc.stderr is not None
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                room = max(0, 32768 - len(pg_errors))
                if room:
                    pg_errors.extend(chunk[:room])

        stderr_reader = threading.Thread(
            target=_drain_stderr, name="pg-dump-stderr", daemon=True
        )
        stderr_reader.start()
        with os.fdopen(fd, "wb") as encrypted_file:
            fd = -1
            assert proc.stdout is not None
            try:
                info = encrypt_stream_to_file(
                    proc.stdout, encrypted_file, passphrase
                )
            except RuntimeError as exc:
                # A failed login/host check produces zero stdout, which used
                # to hide the useful pg_dump error behind "too small".
                if str(exc) == "pg_dump produced an unexpectedly small archive":
                    status = proc.wait(timeout=30)
                    stderr_reader.join(timeout=5)
                    if status:
                        raise BackupFailure(
                            classify_pg_dump_failure(bytes(pg_errors))
                        ) from None
                raise
            finally:
                proc.stdout.close()
            status = proc.wait(timeout=180)
            stderr_reader.join(timeout=5)
            if status != 0:
                raise BackupFailure(
                    classify_pg_dump_failure(bytes(pg_errors))
                )
            encrypted_file.flush()
            os.fsync(encrypted_file.fileno())
        try:
            _ = verify_archive_toc(pending, passphrase)
        except RuntimeError:
            raise BackupFailure("encrypted_archive_verification_failed") from None
        os.replace(pending, destination)
        return {
            "status": "encrypted_archive_toc_verified",
            "encrypted_path": str(destination),
            "encrypted_bytes": destination.stat().st_size,
            "encrypted_sha256": _sha256_file(destination),
            "restore_drill_passed": False,
            "warning": (
                "Archive is encrypted and pg_restore can read its TOC, "
                "but independent secure off-host copy and full restore test "
                "are still REQUIRED before cutover."
            ),
        }
    finally:
        if fd >= 0:
            os.close(fd)
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if pending.exists():
            pending.unlink()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as inp:
        while True:
            data = inp.read(CHUNK_BYTES)
            if not data:
                return digest.hexdigest()
            digest.update(data)
