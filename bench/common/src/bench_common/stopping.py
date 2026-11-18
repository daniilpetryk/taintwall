# Copyright 2026 Daniil Petryk
# SPDX-License-Identifier: Apache-2.0
"""Why a benchmark run must stop: the cost ceiling or a provider failure.

Both are raised from inside the benchmark's model call, where its retry
decorator (tenacity) wraps them in ``RetryError``; ``stop_reason`` finds
them again so the runner can stop cleanly and still write its summary.
"""


class CostCeilingError(RuntimeError):
    """The run would spend more than its ceiling."""

    def __init__(self, spent: float, ceiling: float) -> None:
        super().__init__(f"spent ${spent:.4f}, ceiling ${ceiling:.2f}")


class ProviderError(RuntimeError):
    """The model provider failed; the run stops instead of scoring the pair."""

    def __init__(self, error: Exception) -> None:
        super().__init__(f"provider error: {type(error).__name__}")


def stop_reason(error: BaseException) -> str | None:
    """Why a run must stop, if ``error`` is or wraps a ceiling or provider error.

    Looks through ``__cause__``/``__context__`` and tenacity's ``RetryError``
    (``last_attempt``), which the benchmarks' retry decorators add.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, CostCeilingError | ProviderError):
            return str(current)
        attempt = getattr(current, "last_attempt", None)
        inner = attempt.exception() if attempt is not None else None
        pending.extend([current.__cause__, current.__context__, inner])
    return None
