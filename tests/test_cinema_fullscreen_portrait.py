from __future__ import annotations

"""The shared Theater must recover the portrait player without resetting media."""

from stoney_verify import movie_night_web


def _theater_html() -> str:
    return movie_night_web._watch_html(
        "room-fullscreen", 42, "uid=42&exp=9999999999&sig=test",
    )


def test_fullscreen_returns_to_css_16_by_9_portrait_without_stale_pixel_height() -> None:
    page = _theater_html()
    assert '.video-stage[data-cinema-layout="inline"]' in page
    assert "aspect-ratio:16/9!important;" in page
    assert "height:auto!important;" in page
    assert "max-height:none!important;" in page
    assert 'videoStage.dataset.cinemaLayout=stageFullscreen?"fullscreen":"inline";' in page
    assert "document.fullscreenElement===videoStage" in page
    assert "document.webkitFullscreenElement===videoStage" in page
    assert "videoStage.style.removeProperty(property)" in page
    assert 'videoStage.style.height=height+"px"' not in page
    assert 'Math.round(width*9/16)' not in page


def test_fullscreen_uses_stage_dom_state_not_stale_native_compositor_signal() -> None:
    page = _theater_html()
    layout = page.split("function stabilizePlayerLayout()", 1)[1].split(
        "function recoverPlayerFromViewportChange()", 1,
    )[0]
    assert 'video.webkitDisplayingFullscreen' not in layout
    assert 'nativeVideoFullscreen' not in layout
    assert 'document.fullscreenElement===videoStage' in layout
    assert 'videoStage.style.removeProperty(property)' in layout
    assert '.video-stage:fullscreen' in page
    assert 'height:100vh;' in page


def test_fullscreen_exit_rechecks_viewport_without_restarting_video_or_audio() -> None:
    page = _theater_html()
    recovery = page.split("function recoverPlayerFromViewportChange()", 1)[1].split(
        "if(typeof ResizeObserver", 1,
    )[0]
    assert "[0,80,240,600,1200]" in recovery
    assert "stabilizePlayerLayout();" in recovery
    assert "video.load(" not in recovery
    assert "video.src=" not in recovery
    assert "attachStream(" not in recovery
    assert "seek(" not in recovery
    assert "compatAudio.load(" not in recovery
    assert 'cinemaResizeObserver.observe(videoStage.parentElement)' in page
    assert 'screen.orientation?.addEventListener?.("change",recoverPlayerFromViewportChange)' in page
    assert 'document.addEventListener("fullscreenchange",handleFullscreenChange)' in page
    assert 'video.addEventListener("webkitendfullscreen"' in page
