"""Planning-only upper bounds for Dank Shield guild-scale DB traffic.

This does not benchmark or certify a Supabase/Discord deployment. It assumes
one guild heartbeat each 60 seconds and guild metrics every 300 seconds,
matching the inspected bot worker defaults (2026-10-20).
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from math import ceil


@dataclass(frozen=True)
class ScaleEnvelope:
    guild_count: int
    illustrative_shards_at_1000_guilds: int
    individual_heartbeat_operations_per_second: float
    batched_heartbeat_requests_per_minute: int
    batched_heartbeat_requests_per_second: float
    underlying_heartbeat_row_writes_per_second: float
    periodic_metrics_guild_passes_per_second: float
    existing_token_retention_capacity_per_day: int


def estimate(
    guild_count: int,
    *,
    heartbeat_seconds: int = 60,
    heartbeat_batch_size: int = 250,
    metrics_seconds: int = 300,
    cleanup_tokens_per_hour: int = 500,
) -> ScaleEnvelope:
    if guild_count < 1:
        raise ValueError("guild_count must be positive")
    if heartbeat_seconds < 1 or metrics_seconds < 1:
        raise ValueError("cadences must be positive")
    if not 1 <= heartbeat_batch_size <= 250:
        raise ValueError("heartbeat_batch_size must be 1..250")
    if cleanup_tokens_per_hour < 0:
        raise ValueError("cleanup_tokens_per_hour must be nonnegative")

    return ScaleEnvelope(
        guild_count=guild_count,
        illustrative_shards_at_1000_guilds=ceil(guild_count / 1000),
        individual_heartbeat_operations_per_second=guild_count / heartbeat_seconds,
        batched_heartbeat_requests_per_minute=ceil(guild_count / heartbeat_batch_size),
        batched_heartbeat_requests_per_second=ceil(guild_count / heartbeat_batch_size) / heartbeat_seconds,
        underlying_heartbeat_row_writes_per_second=guild_count / heartbeat_seconds,
        periodic_metrics_guild_passes_per_second=guild_count / metrics_seconds,
        existing_token_retention_capacity_per_day=cleanup_tokens_per_hour * 24,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Dank Shield architecture planning, not a load test")
    parser.add_argument("--guilds", type=int, required=True)
    parser.add_argument("--heartbeat-seconds", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--metrics-seconds", type=int, default=300)
    args = parser.parse_args()
    envelope = estimate(
        args.guilds,
        heartbeat_seconds=args.heartbeat_seconds,
        heartbeat_batch_size=args.batch_size,
        metrics_seconds=args.metrics_seconds,
    )
    print(json.dumps(asdict(envelope), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
