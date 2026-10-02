from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stoney_verify import share_router_media_remux as remux
from stoney_verify.share_router_media_resolver import MediaResolution


@pytest.fixture(autouse=True)
def _reset_remux_state():
    remux.reset_media_remux_state_for_tests()
    yield
    remux.reset_media_remux_state_for_tests()


def _manifest_resolution(*, provider: str = "vimeo") -> MediaResolution:
    return MediaResolution(
        source_url="https://vimeo.com/123",
        canonical_url="https://vimeo.com/123",
        identity="vimeo:https://vimeo.com/123",
        provider=provider,
        delivery="manifest",
        media_url="https://cdn.example.com/master.m3u8",
        protocol="m3u8_native",
        ext="mp4",
        request_headers=(("Referer", "https://vimeo.com/"),),
        reason="manifest_requires_controlled_transcode_or_player",
    )


def _merge_resolution() -> MediaResolution:
    return MediaResolution(
        source_url="https://www.youtube.com/watch?v=abc",
        canonical_url="https://www.youtube.com/watch?v=abc",
        identity="youtube:abc",
        provider="youtube",
        delivery="merge",
        media_url="https://video.example.com/video.mp4",
        audio_url="https://audio.example.com/audio.m4a",
        protocol="https",
        audio_protocol="https",
        ext="mp4",
        audio_ext="m4a",
        request_headers=(
            ("Referer", "https://www.youtube.com/"),
            ("Cookie", "must-not-forward"),
        ),
        audio_request_headers=(("User-Agent", "Provider UA"),),
        reason="separate_audio_video_requires_merge",
    )


def test_build_manifest_command_is_fixed_stream_copy_only(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    command = remux.build_ffmpeg_remux_command(
        "/usr/bin/ffmpeg",
        _manifest_resolution(),
        output_path=output,
        max_bytes=25_000_000,
    )

    assert command[0] == "/usr/bin/ffmpeg"
    assert "-nostdin" in command
    assert "-protocol_whitelist" in command
    assert "http,https,tcp,tls,crypto" in command
    assert "-rw_timeout" in command
    assert command.count("-i") == 1
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "copy"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "copy"
    assert "libx264" not in command
    assert "aac" not in command
    assert str(output) == command[-1]


def test_build_merge_command_has_two_bounded_inputs_and_safe_headers(
    tmp_path: Path,
) -> None:
    output = tmp_path / "out.mp4"
    command = remux.build_ffmpeg_remux_command(
        "/usr/bin/ffmpeg",
        _merge_resolution(),
        output_path=output,
        max_bytes=25_000_000,
    )
    assert command.count("-i") == 2
    assert command.count("-protocol_whitelist") == 2
    assert command.count("-rw_timeout") == 2
    assert "0:v:0" in command
    assert "1:a:0" in command
    assert "-shortest" in command
    joined = "\n".join(command)
    assert "Referer: https://www.youtube.com/" in joined
    assert "User-Agent: Provider UA" in joined
    assert "must-not-forward" not in joined


def test_direct_manifest_stays_link_only_without_spawning_ffmpeg(monkeypatch) -> None:
    direct = _manifest_resolution(provider="direct")

    async def should_not_validate(_resolution):
        raise AssertionError("direct manifest must stop before network validation")

    monkeypatch.setattr(remux, "_inputs_are_public", should_not_validate)

    result = asyncio.run(
        remux.remux_media_for_discord(
            direct,
            max_bytes=25_000_000,
        )
    )
    assert result is None
    assert remux.media_remux_snapshot()["unavailable"] == 1


def test_successful_remux_transfers_temp_file_ownership(monkeypatch) -> None:
    async def public(_resolution):
        return True

    async def fake_run(command, *, timeout_seconds):
        assert timeout_seconds >= 8.0
        output = Path(command[-1])
        output.write_bytes(b"x" * 1024)
        return 0, "", False

    monkeypatch.setattr(remux, "_inputs_are_public", public)
    monkeypatch.setattr(remux.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(remux, "_run_ffmpeg", fake_run)

    result = asyncio.run(
        remux.remux_media_for_discord(
            _manifest_resolution(),
            max_bytes=25_000_000,
        )
    )
    assert result is not None
    assert result.path.exists()
    assert result.size_bytes == 1024
    assert remux.media_remux_snapshot()["success"] == 1
    result.cleanup()
    assert not result.path.exists()


def test_failed_remux_removes_temp_file(monkeypatch) -> None:
    attempted: list[Path] = []

    async def public(_resolution):
        return True

    async def fake_run(command, *, timeout_seconds):
        output = Path(command[-1])
        attempted.append(output)
        output.write_bytes(b"partial")
        return 1, "unsupported codec", False

    monkeypatch.setattr(remux, "_inputs_are_public", public)
    monkeypatch.setattr(remux.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(remux, "_run_ffmpeg", fake_run)

    result = asyncio.run(
        remux.remux_media_for_discord(
            _manifest_resolution(),
            max_bytes=25_000_000,
        )
    )
    assert result is None
    assert attempted and not attempted[0].exists()
    assert remux.media_remux_snapshot()["failed"] == 1


def test_oversize_remux_is_rejected_and_cleaned(monkeypatch) -> None:
    attempted: list[Path] = []

    async def public(_resolution):
        return True

    async def fake_run(command, *, timeout_seconds):
        output = Path(command[-1])
        attempted.append(output)
        output.write_bytes(b"x" * 1025)
        return 0, "", False

    monkeypatch.setattr(remux, "_inputs_are_public", public)
    monkeypatch.setattr(remux.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(remux, "_run_ffmpeg", fake_run)

    result = asyncio.run(
        remux.remux_media_for_discord(
            _manifest_resolution(),
            max_bytes=1024,
        )
    )
    assert result is None
    assert attempted and not attempted[0].exists()
    assert remux.media_remux_snapshot()["oversize"] == 1


def test_run_ffmpeg_timeout_kills_process(monkeypatch) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.killed = False
            self.returncode = None

        async def communicate(self):
            if self.killed:
                self.returncode = -9
                return b"", b"timed out"
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    process = FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def scenario():
        return await remux._run_ffmpeg(
            ["/usr/bin/ffmpeg", "-version"],
            timeout_seconds=0.01,
        )

    returncode, detail, timed_out = asyncio.run(scenario())
    assert timed_out is True
    assert process.killed is True
    assert returncode == -9
    assert "timed out" in detail


def test_run_ffmpeg_cancellation_kills_process(monkeypatch) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.killed = False
            self.returncode = None

        async def communicate(self):
            if self.killed:
                self.returncode = -9
                return b"", b"cancelled"
            await asyncio.Event().wait()
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    process = FakeProcess()

    async def fake_create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    async def scenario():
        task = asyncio.create_task(
            remux._run_ffmpeg(
                ["/usr/bin/ffmpeg", "-version"],
                timeout_seconds=30.0,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert process.killed is True
    assert process.returncode == -9


def test_remux_concurrency_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("DANK_SHARE_ROUTER_MEDIA_REMUX_CONCURRENCY", "1")
    remux.reset_media_remux_state_for_tests()

    active = 0
    max_active = 0

    async def public(_resolution):
        return True

    async def fake_run(command, *, timeout_seconds):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.03)
            Path(command[-1]).write_bytes(b"x" * 128)
            return 0, "", False
        finally:
            active -= 1

    monkeypatch.setattr(remux, "_inputs_are_public", public)
    monkeypatch.setattr(remux.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(remux, "_run_ffmpeg", fake_run)

    first = _manifest_resolution()
    second = MediaResolution(
        **{
            **first.__dict__,
            "source_url": "https://vimeo.com/456",
            "canonical_url": "https://vimeo.com/456",
            "identity": "vimeo:https://vimeo.com/456",
            "media_url": "https://cdn.example.com/other.m3u8",
        }
    )

    async def scenario():
        return await asyncio.gather(
            remux.remux_media_for_discord(first, max_bytes=25_000_000),
            remux.remux_media_for_discord(second, max_bytes=25_000_000),
        )

    results = asyncio.run(scenario())
    assert max_active == 1
    assert all(item is not None for item in results)
    for item in results:
        assert item is not None
        item.cleanup()


def test_source_contract_never_uses_shell_and_always_kills_on_timeout() -> None:
    source = Path(remux.__file__).read_text(encoding="utf-8")
    assert "asyncio.create_subprocess_exec(" in source
    assert "create_subprocess_shell" not in source
    assert "process.kill()" in source
    assert '"copy"' in source
    assert "libx264" not in source
