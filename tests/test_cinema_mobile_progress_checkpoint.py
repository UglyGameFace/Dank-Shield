"""Exercise the rendered mobile Cinema checkpoint logic in real JavaScript."""
import re
import shutil
import subprocess

import pytest

from stoney_verify.movie_night_web import _watch_html


def test_mobile_resume_checkpoint_is_scoped_to_member_and_media():
    if not shutil.which("node"):
        pytest.skip("Node is required for browser JS checkpoint regression")
    html = _watch_html("synthetic-room", 141, "exp=12345")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
    assert scripts
    js = scripts[-1]
    start = js.index("function progressLocalKey(key)")
    end = js.index("async function maybeRestoreWatchProgress(s)", start)
    checkpoint = js[start:end]
    code = """
        const BOOT={uid:141};
        const rows=new Map();
        const localStorage={
          getItem:(k)=>rows.has(k)?rows.get(k):null,
          setItem:(k,v)=>rows.set(k,v),
        };
        let lastLocalProgressAt=0;
    """ + checkpoint + """
        checkpointLocalProgress("movie:42:0:0",125,2000,false,true);
        const entry=readLocalProgress("movie:42:0:0");
        if (!entry || entry.seconds!==125 || entry.duration!==2000)
          throw new Error("Missing crash-resumable local progress");
        if (readLocalProgress("movie:43:0:0")!==null)
          throw new Error("Different title leaked progress");
        rows.set(progressLocalKey("movie:42:0:0"),
          JSON.stringify({...entry,savedAt:Date.now()-8*86400000}));
        if (readLocalProgress("movie:42:0:0")!==null)
          throw new Error("Expired checkpoint was restored");
        if (!progressLocalKey("movie:42:0:0").includes(":141:"))
          throw new Error("Checkpoint is not keyed by authenticated member");
        checkpointLocalProgress("movie:42:0:0",1999,2000,true,true);
        if (readLocalProgress("movie:42:0:0")!==null)
          throw new Error("Completed title must not resume");
    """
    result = subprocess.run(
        ["node", "-e", code], capture_output=True, text=True, timeout=12,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_browser_js_parses_after_progress_recovery_changes(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required for browser JS syntax regression")
    html = _watch_html("synthetic-room", 141, "")
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
    assert scripts
    filename = tmp_path / "cinema-watch-inline.js"
    filename.write_text(scripts[-1], encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(filename)],
        capture_output=True, text=True, timeout=12, check=False,
    )
    assert result.returncode == 0, result.stderr
