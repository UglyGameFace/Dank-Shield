from __future__ import annotations

import pytest

from tools.dankshield_scale_capacity import estimate


def test_500k_envelope_is_a_budget_not_a_benchmark() -> None:
    result = estimate(500_000)
    assert result.illustrative_shards_at_1000_guilds == 500
    assert result.batched_heartbeat_requests_per_minute == 2_000
    assert result.batched_heartbeat_requests_per_second == pytest.approx(33.3333333333)
    assert result.individual_heartbeat_operations_per_second == pytest.approx(8_333.3333333333)
    assert result.underlying_heartbeat_row_writes_per_second == result.individual_heartbeat_operations_per_second
    assert result.periodic_metrics_guild_passes_per_second == pytest.approx(1_666.66666667)
    assert result.existing_token_retention_capacity_per_day == 12_000


def test_smaller_workloads_and_partial_batches() -> None:
    assert estimate(5).batched_heartbeat_requests_per_minute == 1
    assert estimate(30).batched_heartbeat_requests_per_minute == 1
    assert estimate(5).individual_heartbeat_operations_per_second == pytest.approx(5/60)
    assert estimate(30).individual_heartbeat_operations_per_second == pytest.approx(30/60)
    assert estimate(12).batched_heartbeat_requests_per_minute == 1
    assert estimate(251).batched_heartbeat_requests_per_minute == 2
    assert estimate(10_000).illustrative_shards_at_1000_guilds == 10


@pytest.mark.parametrize("inputs", [
    {"guild_count": 0},
    {"guild_count": -10},
    {"guild_count": 50, "heartbeat_seconds": 0},
    {"guild_count": 50, "heartbeat_batch_size": 251},
    {"guild_count": 50, "metrics_seconds": 0},
    {"guild_count": 50, "cleanup_tokens_per_hour": -1},
])
def test_invalid_envelopes_rejected(inputs: dict) -> None:
    with pytest.raises(ValueError):
        estimate(**inputs)
