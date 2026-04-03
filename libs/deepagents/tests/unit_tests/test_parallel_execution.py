"""Tests for ParallelToolExecutor."""

from __future__ import annotations

import asyncio
import time

from deepagents.execution.parallel import ParallelToolExecutor, ToolCall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delayed_read(path: str, *, delay: float = 0.05) -> str:
    """Simulate a read that takes *delay* seconds."""
    await asyncio.sleep(delay)
    return f"content of {path}"


async def _delayed_write(path: str, content: str, *, delay: float = 0.05) -> str:
    """Simulate a write that takes *delay* seconds."""
    await asyncio.sleep(delay)
    return f"wrote {content} to {path}"


async def _failing_read(path: str) -> str:
    """Always raises."""
    msg = f"boom reading {path}"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_independent_reads_execute_concurrently() -> None:
    """Three reads on different files should run in parallel."""
    executor = ParallelToolExecutor()
    delay = 0.1
    calls = [
        ToolCall(
            name="read_file",
            args={"path": f"/a/{i}.txt"},
            fn=lambda path, d=delay: _delayed_read(path, delay=d),
        )
        for i in range(3)
    ]

    start = time.monotonic()
    results = await executor.execute(calls)
    elapsed = time.monotonic() - start

    assert len(results) == 3
    assert all(r.ok for r in results)
    # Parallel execution: total time should be close to one delay, not three.
    assert elapsed < delay * 2


async def test_write_then_read_same_file_sequential() -> None:
    """A write followed by a read on the same path must be sequential."""
    executor = ParallelToolExecutor()
    order: list[str] = []

    async def tracked_write(path: str, content: str) -> str:  # noqa: ARG001
        order.append("write")
        await asyncio.sleep(0.02)
        return f"wrote {content}"

    async def tracked_read(path: str) -> str:
        order.append("read")
        return f"content of {path}"

    calls = [
        ToolCall(name="write_file", args={"path": "/data/f.txt", "content": "hi"}, fn=tracked_write),
        ToolCall(name="read_file", args={"path": "/data/f.txt"}, fn=tracked_read),
    ]

    results = await executor.execute(calls)

    assert results[0].ok
    assert results[1].ok
    # Write must complete before read starts.
    assert order == ["write", "read"]


async def test_mixed_independent_and_dependent() -> None:
    """Independent calls run concurrently while dependent ones are sequential."""
    executor = ParallelToolExecutor()
    order: list[str] = []

    async def track(label: str) -> str:
        order.append(label)
        await asyncio.sleep(0.02)
        return label

    calls = [
        # Group 1: write to /a and independent read of /b can be concurrent.
        ToolCall(
            name="write_file",
            args={"path": "/a/file.txt", "content": "x"},
            fn=lambda path, content: track("write-a"),  # noqa: ARG005
        ),
        ToolCall(
            name="read_file",
            args={"path": "/b/file.txt"},
            fn=lambda path: track("read-b"),  # noqa: ARG005
        ),
        # Group 2: read /a depends on write /a above, so new group.
        ToolCall(
            name="read_file",
            args={"path": "/a/file.txt"},
            fn=lambda path: track("read-a"),  # noqa: ARG005
        ),
    ]

    results = await executor.execute(calls)

    assert len(results) == 3
    assert all(r.ok for r in results)
    # read-a must come after write-a.
    assert order.index("write-a") < order.index("read-a")


async def test_one_failure_does_not_block_others() -> None:
    """A failing tool should not prevent other parallel calls from completing."""
    executor = ParallelToolExecutor()
    calls = [
        ToolCall(name="read_file", args={"path": "/ok.txt"}, fn=_delayed_read),
        ToolCall(name="read_file", args={"path": "/fail.txt"}, fn=_failing_read),
        ToolCall(name="read_file", args={"path": "/ok2.txt"}, fn=_delayed_read),
    ]

    results = await executor.execute(calls)

    assert results[0].ok
    assert not results[1].ok
    assert isinstance(results[1].error, RuntimeError)
    assert results[2].ok


async def test_results_in_original_order() -> None:
    """Results must match the submission order, not execution order."""
    executor = ParallelToolExecutor()

    async def slow(path: str) -> str:  # noqa: ARG001
        await asyncio.sleep(0.08)
        return "slow"

    async def fast(path: str) -> str:  # noqa: ARG001
        await asyncio.sleep(0.01)
        return "fast"

    calls = [
        ToolCall(name="read_file", args={"path": "/slow.txt"}, fn=slow),
        ToolCall(name="read_file", args={"path": "/fast.txt"}, fn=fast),
    ]

    results = await executor.execute(calls)

    assert results[0].value == "slow"
    assert results[0].index == 0
    assert results[1].value == "fast"
    assert results[1].index == 1


async def test_disabled_runs_sequentially() -> None:
    """When disabled, all calls run one at a time regardless of dependencies."""
    executor = ParallelToolExecutor(enabled=False)
    delay = 0.05
    calls = [
        ToolCall(
            name="read_file",
            args={"path": f"/file{i}.txt"},
            fn=lambda path, d=delay: _delayed_read(path, delay=d),
        )
        for i in range(3)
    ]

    start = time.monotonic()
    results = await executor.execute(calls)
    elapsed = time.monotonic() - start

    assert len(results) == 3
    assert all(r.ok for r in results)
    # Sequential: total time >= 3 * delay.
    assert elapsed >= delay * 3 * 0.9  # small tolerance


async def test_is_concurrency_safe_classmethod() -> None:
    """Read-only tools are concurrency-safe; write tools are not."""
    assert ParallelToolExecutor.is_concurrency_safe("read_file")
    assert ParallelToolExecutor.is_concurrency_safe("glob")
    assert ParallelToolExecutor.is_concurrency_safe("grep")
    assert ParallelToolExecutor.is_concurrency_safe("web_search")
    assert not ParallelToolExecutor.is_concurrency_safe("write_file")
    assert not ParallelToolExecutor.is_concurrency_safe("bash")


async def test_empty_calls_returns_empty() -> None:
    """Passing an empty list returns an empty list."""
    executor = ParallelToolExecutor()
    results = await executor.execute([])
    assert results == []
