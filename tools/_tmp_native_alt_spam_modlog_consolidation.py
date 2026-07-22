from __future__ import annotations

import base64
import hashlib
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = tuple(
    ROOT / f"tools/_tmp_alt_payload_{index:02d}.txt"
    for index in range(8)
)
EXPECTED_SHA256 = "882e6088d624eecc8e0f0cc5a960929730af4afed994c2a472df727165f22fcd"

missing = [str(path) for path in CHUNKS if not path.exists()]
if missing:
    raise RuntimeError(f"missing staged payload chunks: {missing}")

payload = "".join(
    path.read_text(encoding="utf-8").strip()
    for path in CHUNKS
)
source = zlib.decompress(
    base64.b64decode(payload)
).decode("utf-8")
actual = hashlib.sha256(source.encode("utf-8")).hexdigest()
if actual != EXPECTED_SHA256:
    raise RuntimeError(
        f"staged helper checksum mismatch: expected={EXPECTED_SHA256} actual={actual}"
    )

code = compile(source, __file__, "exec")
exec(code, globals(), globals())

for path in CHUNKS:
    path.unlink()
