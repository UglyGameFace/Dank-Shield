"""Explicit source-vs-PostgreSQL guild-config parity audit.

This module is intentionally a read-only migration *gate*, not a backend
switch, live fallback, sync worker, or source backup. All data is supplied
through explicit page readers. No monkey-patching or startup hooks.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence

from .core_data_stage import canonical_json

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 200
MAX_GUILDS = 500  # First-slice bound. NOT a production-server cap.
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024

PageReader = Callable[[int, int], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class ParityAudit:
    source_rows: int
    postgres_rows: int
    source_digest: str
    postgres_digest: str
    unchanged_source: bool
    matching: bool
    status: str = "read_only_migration_parity"
    cutover_authorized: bool = False

    def public_summary(self) -> dict[str, Any]:
        # Never emit guild identifiers, configuration, secrets or row payloads.
        return {
            "source_rows": self.source_rows,
            "postgres_rows": self.postgres_rows,
            "source_digest": self.source_digest,
            "postgres_digest": self.postgres_digest,
            "unchanged_source": self.unchanged_source,
            "matching": self.matching,
            "status": self.status,
            "cutover_authorized": False,
            "warning": (
                "This is read-only guild-config parity only. Supabase paging "
                "is not a transactional source backup or a write freeze. "
                "Other bot tables and cutover/rollback remain unverified."
            ),
        }


def _identity(guild_id: Any) -> str:
    value = str(guild_id).strip()
    if (not value or not value.isascii() or not value.isdecimal()
            or value[0] == "0" or len(value) > 20):
        raise RuntimeError("invalid guild identity in parity source")
    return value


def _scan(
    reader: PageReader, *, page_size: int, name: str,
) -> dict[str, str]:
    """Bounded scan, refusing duplicate or incomplete pages.

    Readers must impose a stable unique key order. This function never
    assumes a missing result is equivalent to a configured empty server.
    """
    digests: dict[str, str] = {}
    consumed_bytes = 0
    offset = 0
    while True:
        rows = list(reader(offset, page_size))
        if len(rows) > page_size:
            raise RuntimeError("oversized parity page")
        for row in rows:
            if not isinstance(row, Mapping):
                raise RuntimeError("invalid guild row shape")
            gid = _identity(row.get("guild_id"))
            if gid in digests:
                raise RuntimeError("duplicate guild ID in parity scan")
            encoded = canonical_json(row).encode("utf-8")
            if len(encoded) > MAX_ROW_BYTES:
                raise RuntimeError("oversized guild row in parity scan")
            consumed_bytes += len(encoded) + 256
            if consumed_bytes > MAX_TOTAL_BYTES:
                raise RuntimeError("parity scan exceeds bounded memory")
            if len(digests) >= MAX_GUILDS:
                raise RuntimeError("parity scan exceeds first-slice guild limit")
            digests[gid] = sha256(encoded).hexdigest()
        offset += len(rows)
        if len(rows) < page_size:
            break
    return digests


def _digest(items: Mapping[str, str]) -> str:
    joined = "".join(f"{gid}:{items[gid]}\n" for gid in sorted(items))
    return sha256(joined.encode("utf-8")).hexdigest()


def compare_guild_config_sources(
    source: PageReader, postgres: PageReader, *,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> ParityAudit:
    """Compare current rows and re-read source to detect concurrent drift.

    A matching result does not authorize cutover: source can change after
    this audit, and the rest of Dank Shield data is not included.
    """
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError("page size must be 1..200")
    first = _scan(source, page_size=page_size, name="supabase")
    dest = _scan(postgres, page_size=page_size, name="postgres")
    second = _scan(source, page_size=page_size, name="supabase_recheck")
    unchanged = first == second
    same = bool(first) and unchanged and first == dest
    return ParityAudit(
        source_rows=len(first), postgres_rows=len(dest),
        source_digest=_digest(first), postgres_digest=_digest(dest),
        unchanged_source=unchanged, matching=same,
    )


def supabase_page_reader(client: Any) -> PageReader:
    """Known authoritative table only. No generic table name parameter."""
    def fetch(offset: int, size: int) -> Sequence[Mapping[str, Any]]:
        response = (
            client.table("guild_configs").select("*")
            .order("guild_id").order("id")
            .range(offset, offset + size - 1).execute()
        )
        rows = getattr(response, "data", None)
        if not isinstance(rows, list):
            raise RuntimeError("Supabase guild config page unavailable")
        return rows
    return fetch


def postgres_page_reader(connection: Any) -> PageReader:
    """Read original full row payload, never derived/reinterpreted fields."""
    def fetch(offset: int, size: int) -> Sequence[Mapping[str, Any]]:
        with connection.cursor() as cursor:
            cursor.execute(
                "select row_payload from dank_core.guild_config_state "
                "order by guild_id limit %s offset %s", (size, offset),
            )
            records = cursor.fetchall()
        rows = []
        for result in records:
            if not result or not isinstance(result[0], Mapping):
                raise RuntimeError("PostgreSQL guild config payload unavailable")
            rows.append(dict(result[0]))
        return rows
    return fetch
