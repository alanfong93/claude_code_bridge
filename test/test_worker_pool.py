from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from worker_pool import BaseSessionWorker, PerSessionWorkerPool


class _NoopThread(threading.Thread):
    def __init__(self, session_key: str, started: list[str]):
        super().__init__(daemon=True)
        self.session_key = session_key
        self._track = started  # avoid overriding threading.Thread._started

    def start(self) -> None:  # type: ignore[override]
        self._track.append(self.session_key)
        # Mark as "alive" by setting _started event (required for is_alive() check)
        self._started.set()


def test_per_session_worker_pool_reuses_same_key() -> None:
    started: list[str] = []
    pool: PerSessionWorkerPool[_NoopThread] = PerSessionWorkerPool()
    w1 = pool.get_or_create("k1", lambda k: _NoopThread(k, started))
    w2 = pool.get_or_create("k1", lambda k: _NoopThread(k, started))
    w3 = pool.get_or_create("k2", lambda k: _NoopThread(k, started))

    assert w1 is w2
    assert w1 is not w3
    assert started.count("k1") == 1
    assert started.count("k2") == 1


@dataclass
class _Task:
    req_id: str
    done_event: threading.Event
    result: Optional[str] = None


class _EchoWorker(BaseSessionWorker[_Task, str]):
    def _handle_task(self, task: _Task) -> str:
        return f"ok:{task.req_id}"

    def _handle_exception(self, exc: Exception, task: _Task) -> str:
        return f"err:{task.req_id}:{exc}"


class _FailWorker(_EchoWorker):
    def _handle_task(self, task: _Task) -> str:
        raise RuntimeError("boom")


def test_base_session_worker_processes_task_and_sets_event() -> None:
    worker = _EchoWorker("s1")
    worker.start()
    try:
        task = _Task(req_id="r1", done_event=threading.Event())
        worker.enqueue(task)
        assert task.done_event.wait(timeout=2.0) is True
        assert task.result == "ok:r1"
    finally:
        worker.stop()
        worker.join(timeout=2.0)


def test_base_session_worker_exception_path() -> None:
    worker = _FailWorker("s1")
    worker.start()
    try:
        task = _Task(req_id="r2", done_event=threading.Event())
        worker.enqueue(task)
        assert task.done_event.wait(timeout=2.0) is True
        assert task.result is not None
        assert task.result.startswith("err:r2:")
    finally:
        worker.stop()
        worker.join(timeout=2.0)

