"""Opt-in supervised TorrServer sidecar launcher for Dank Cinema.

Not installed as the Discloud START command by default. Production activation
requires an operator-verified, pinned binary and a staged memory/canary review.
The Discord bot always remains the primary child; torrent-worker crashes or
memory pressure never trigger a restart of the bot or bypass signed routes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MIB = 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CinemaWorkerConfigurationError(RuntimeError):
    pass


def _int_env(env: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(env.get(name, "")).strip()
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise CinemaWorkerConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise CinemaWorkerConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _rss_bytes(pid: int) -> int | None:
    """Linux resident memory, not virtual Go address space."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines()
        for line in lines:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return None
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fd:
        for chunk in iter(lambda: fd.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class WorkerConfiguration:
    binary: Path
    expected_sha256: str
    port: int
    state_dir: Path
    ram_limit_bytes: int
    process_limit_bytes: int
    process_reserve_bytes: int
    piece_cache_bytes: int
    settings_memory_budget_bytes: int
    max_torrents: int

    @classmethod
    def from_environment(cls, env: dict[str, str]) -> "WorkerConfiguration":
        raw_binary = str(env.get("DANK_CINEMA_WORKER_BINARY", "")).strip()
        expected = str(env.get("DANK_CINEMA_WORKER_SHA256", "")).lower().strip()
        if not raw_binary or not SHA256.fullmatch(expected):
            raise CinemaWorkerConfigurationError(
                "Rolling worker requires an explicit binary path and SHA-256 fingerprint."
            )
        binary = Path(raw_binary).resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise CinemaWorkerConfigurationError("Pinned Cinema worker binary must be an executable file.")
        if _sha256(binary) != expected:
            raise CinemaWorkerConfigurationError("Cinema worker SHA-256 differs from pinned release.")
        raw_address = str(env.get("DANK_CINEMA_ROLLING_ENDPOINT", "")).strip()
        address = urlsplit(raw_address)
        if (address.scheme != "http" or address.hostname != "127.0.0.1"
                or not address.port or address.port == 8080
                or address.username or address.password or address.path not in ("", "/")
                or address.query or address.fragment):
            raise CinemaWorkerConfigurationError(
                "Rolling endpoint must be a dedicated 127.0.0.1 HTTP port other than 8080."
            )
        budget = _int_env(env, "DANK_CINEMA_ROLLING_MEMORY_BUDGET_BYTES", 128 * MIB, 64 * MIB, 512 * MIB)
        sessions = _int_env(env, "DANK_TORRENT_MAX_SESSIONS", 4, 1, 16)
        if 4 * MIB * sessions * 4 > budget:
            raise CinemaWorkerConfigurationError("Rolling per-torrent reservation exceeds worker budget.")
        global_limit = _int_env(env, "DANK_PROCESS_MEMORY_LIMIT_MB", 1495, 512, 32768) * MIB
        reserve = _int_env(env, "DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB", 350, 128, 16384) * MIB
        worker_limit = _int_env(env, "DANK_CINEMA_WORKER_RSS_LIMIT_MB", 192, 96, 1024) * MIB
        if worker_limit + reserve + 256 * MIB > global_limit:
            raise CinemaWorkerConfigurationError(
                "Worker hard-stop threshold leaves insufficient guaranteed headroom for the core bot."
            )
        base = Path(str(env.get("DANK_CINEMA_WORKER_STATE_DIR", "data/cinema_worker_runtime")))
        state_dir = base.resolve()
        if not state_dir.is_relative_to(Path.cwd().resolve()):
            raise CinemaWorkerConfigurationError("Worker state directory must remain inside the app checkout.")
        return cls(
            binary=binary, expected_sha256=expected, port=address.port,
            state_dir=state_dir, ram_limit_bytes=worker_limit,
            process_limit_bytes=global_limit, process_reserve_bytes=reserve,
            piece_cache_bytes=max(4 * MIB, budget // (sessions * 4)),
            settings_memory_budget_bytes=budget, max_torrents=sessions,
        )


def _settings_request(port: int, action: str, sets: dict | None = None) -> dict | None:
    data = {"action": action}
    if sets is not None:
        data["sets"] = sets
    request = Request(
        f"http://127.0.0.1:{port}/settings",
        data=json.dumps(data, separators=(",", ":")).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    # Never follow redirects to other hosts, even during configuration.
    class NoRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise CinemaWorkerConfigurationError("Worker settings endpoint redirected unexpectedly.")
    opener = __import__("urllib.request", fromlist=["build_opener"]).build_opener(NoRedirect)
    with opener.open(request, timeout=2.0) as response:
        if response.status != 200:
            raise CinemaWorkerConfigurationError("Worker settings rejected operator configuration.")
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise CinemaWorkerConfigurationError("Worker settings response exceeded its limit.")
        return json.loads(raw) if raw else None


def _verified_settings(cfg: WorkerConfiguration) -> None:
    initial = _settings_request(cfg.port, "get")
    if not isinstance(initial, dict):
        raise CinemaWorkerConfigurationError("Worker did not provide a settings document.")
    updated = dict(initial)
    updated.update({
        "UseDisk": False,
        "TorrentsSavePath": "",
        "CacheSize": cfg.piece_cache_bytes,
        "RemoveCacheOnDrop": True,
        "DisableUPNP": True,
        "StoreSettingsInJson": False,
        "ConnectionsLimit": 25,
        "PreloadCache": 0,
        "ReaderReadAHead": 25,
    })
    _settings_request(cfg.port, "set", updated)
    final = _settings_request(cfg.port, "get")
    if not isinstance(final, dict) or not (
        final.get("UseDisk") is False
        and final.get("TorrentsSavePath") in ("", None)
        and final.get("RemoveCacheOnDrop") is True
        and final.get("DisableUPNP") is True
        and final.get("CacheSize") == cfg.piece_cache_bytes
    ):
        raise CinemaWorkerConfigurationError("Worker rejected required zero-disk and resource settings.")


def _halt(proc: subprocess.Popen, timeout: float = 4.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3.0)


def _worker_command(cfg: WorkerConfiguration) -> list[str]:
    # HTTP admin API must never bind to the public site interface.
    # The torrent peer socket can accept legitimate internet peers; neither
    # its peer port nor the admin port is the Discloud public site port.
    return [
        str(cfg.binary), "--ip", "127.0.0.1", "--port", str(cfg.port),
        "--path", str(cfg.state_dir), "--torrentaddr", "0.0.0.0:0",
    ]


def start_worker(cfg: WorkerConfiguration) -> subprocess.Popen:
    cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    process_env = dict(os.environ)
    process_env["GOMEMLIMIT"] = str(max(32 * MIB, cfg.ram_limit_bytes - 64 * MIB))
    process_env["GOGC"] = "65"
    child = subprocess.Popen(
        _worker_command(cfg), env=process_env, stdin=subprocess.DEVNULL,
    )
    try:
        until = time.monotonic() + 12
        while time.monotonic() < until:
            if child.poll() is not None:
                raise CinemaWorkerConfigurationError("Cinema rolling worker exited before health verification.")
            if (_rss_bytes(child.pid) or cfg.ram_limit_bytes + 1) >= cfg.ram_limit_bytes:
                raise CinemaWorkerConfigurationError("Cinema rolling worker exceeded preflight RSS limit.")
            try:
                _verified_settings(cfg)
                print("CINEMA_WORKER_READY mode=memory_only http=loopback", flush=True)
                return child
            except (HTTPError, URLError, TimeoutError, ConnectionError, OSError):
                time.sleep(0.2)
        raise CinemaWorkerConfigurationError("Cinema rolling worker failed its 12-second preflight.")
    except BaseException:
        _halt(child)
        raise


def supervise(
    bot_command: list[str],
    cfg: WorkerConfiguration,
    *,
    env: dict[str, str] | None = None,
    sample_seconds: float = 0.5,
) -> int:
    """Run the Discord child independently; stop ONLY the Cinema worker on pressure."""
    worker = start_worker(cfg)
    bot = None
    stopped = False

    def handle_signal(signum, frame):
        nonlocal stopped
        stopped = True
        if bot is not None and bot.poll() is None:
            bot.send_signal(signum)

    old = {name: signal.getsignal(name) for name in (signal.SIGTERM, signal.SIGINT)}
    try:
        for name in old:
            signal.signal(name, handle_signal)
        bot = subprocess.Popen(bot_command, env=env or dict(os.environ))
        while bot.poll() is None:
            if stopped:
                break
            if worker is not None:
                worker_rss = _rss_bytes(worker.pid)
                bot_rss = _rss_bytes(bot.pid)
                parent_rss = _rss_bytes(os.getpid())
                if worker.poll() is not None:
                    print("CINEMA_WORKER_EXITED; bot stays online; rolling HTTP requests fail closed", flush=True)
                    worker = None
                elif (worker_rss is None or bot_rss is None or parent_rss is None
                        or worker_rss >= cfg.ram_limit_bytes
                        or worker_rss + bot_rss + parent_rss >=
                           cfg.process_limit_bytes - cfg.process_reserve_bytes):
                    print("CINEMA_WORKER_STOPPED_FOR_MEMORY; bot stays online; no restart storm", flush=True)
                    _halt(worker)
                    worker = None
            time.sleep(sample_seconds)
        if stopped and bot.poll() is None:
            _halt(bot)
        return int(bot.wait())
    finally:
        if bot is not None:
            _halt(bot)
        if worker is not None:
            _halt(worker)
        for name, old_handler in old.items():
            signal.signal(name, old_handler)


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    bot_cmd = [sys.executable, "-u", "main.py"]
    if str(os.getenv("DANK_CINEMA_ROLLING_ENABLED", "")).casefold() != "true":
        os.execv(sys.executable, bot_cmd)
        raise AssertionError("execv must not return")
    try:
        cfg = WorkerConfiguration.from_environment(dict(os.environ))
        return supervise(bot_cmd, cfg)
    except (CinemaWorkerConfigurationError, OSError) as exc:
        # An explicit failure is safer than quietly switching an opt-in
        # large-file stream back to a full-file native downloader.
        print(f"CINEMA_WORKER_PREFLIGHT_BLOCKED: {type(exc).__name__}: {exc}", flush=True)
        print("CORE_BOT_NOT_STARTED because rolling mode explicitly requested an unavailable worker.", flush=True)
        return 78


if __name__ == "__main__":
    raise SystemExit(main())
