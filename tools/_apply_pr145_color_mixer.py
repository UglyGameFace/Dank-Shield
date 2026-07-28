from __future__ import annotations

import base64
import hashlib
import os
import traceback
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURE_LOG = ROOT / "tools" / "_pr145_failure.log"
chunks = sorted((ROOT / "tools").glob("_pr145_payload_*.txt"))
if [path.name for path in chunks] != [f"_pr145_payload_{index:02d}.txt" for index in range(8)]:
    raise SystemExit(f"Expected 8 ordered payload chunks, found: {[path.name for path in chunks]}")
payload = "".join(path.read_text(encoding="utf-8").strip() for path in chunks)
if len(payload) != 27288:
    raise SystemExit(f"PR145 payload length mismatch: {len(payload)}")
if hashlib.sha256(payload.encode("ascii")).hexdigest() != "114974c50e7f32e3bf4deb9649bc6abc1de3fed8896f58a748732a968e5c3fb0":
    raise SystemExit("PR145 payload checksum mismatch")
source = zlib.decompress(base64.b64decode(payload)).decode("utf-8")

saved_stdout = os.dup(1)
saved_stderr = os.dup(2)
log_fd = os.open(FAILURE_LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
failed = False
try:
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    exec(
        compile(source, "<pr145-native-profile-correction>", "exec"),
        {"__file__": __file__, "__name__": "__main__"},
    )
except BaseException:
    failed = True
    traceback.print_exc()
finally:
    try:
        os.fsync(log_fd)
    except OSError:
        pass
    os.dup2(saved_stdout, 1)
    os.dup2(saved_stderr, 2)
    os.close(log_fd)
    os.close(saved_stdout)
    os.close(saved_stderr)

captured = FAILURE_LOG.read_text(encoding="utf-8", errors="replace")
if captured:
    print(captured, end="" if captured.endswith("\n") else "\n")
if failed:
    raise SystemExit(1)

FAILURE_LOG.unlink(missing_ok=True)
for path in chunks:
    path.unlink()
