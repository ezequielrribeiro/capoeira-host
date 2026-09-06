from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from .bridge import BridgeServer
from .errors import QueueFullError


@dataclass
class Job:
    request_id: str
    provider: str
    events: asyncio.Queue = field(default_factory=asyncio.Queue)


class RequestScheduler:
    def __init__(self, bridge: BridgeServer, queue_size: int = 10, timeout: float = 180.0) -> None:
        self.bridge = bridge
        self.queue_size = queue_size
        self.timeout = timeout
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._busy: set[str] = set()

    def _queue(self, provider: str) -> asyncio.Queue:
        if provider not in self._queues:
            self._queues[provider] = asyncio.Queue(maxsize=self.queue_size)
        return self._queues[provider]

    def submit(self, provider: str, payload: dict) -> Job:
        queue = self._queue(provider)
        job = Job(request_id=str(uuid.uuid4()), provider=provider)
        try:
            queue.put_nowait((job, payload))
        except asyncio.QueueFull as exc:
            raise QueueFullError(f"bridge queue full ({self.queue_size})") from exc
        self._ensure_worker(provider)
        return job

    def is_busy(self, provider: str) -> bool:
        return provider in self._busy

    def _ensure_worker(self, provider: str) -> None:
        worker = self._workers.get(provider)
        if worker is None or worker.done():
            self._workers[provider] = asyncio.create_task(self._worker(provider))

    async def _worker(self, provider: str) -> None:
        queue = self._queue(provider)
        while True:
            job, payload = await queue.get()
            self._busy.add(provider)
            try:
                await self._process(job, payload)
            finally:
                self._busy.discard(provider)

    async def _process(self, job: Job, payload: dict) -> None:
        session = self.bridge.get_session(job.provider)
        if session is None:
            await job.events.put({"type": "error", "message": f"no bridge available for provider '{job.provider}'"})
            return

        pending = await self.bridge.send_prompt(job.request_id, session, payload)
        done_wait = asyncio.ensure_future(pending.done)
        deadline = time.monotonic() + self.timeout
        try:
            while not done_wait.done():
                remain = deadline - time.monotonic()
                if remain <= 0:
                    done_wait.cancel()
                    await job.events.put({"type": "error", "message": "bridge timeout"})
                    return
                try:
                    partial = await asyncio.wait_for(pending.partials.get(), timeout=min(0.1, remain))
                except asyncio.TimeoutError:
                    continue
                await job.events.put({"type": "text", "text": partial})
        except asyncio.CancelledError:
            done_wait.cancel()
            raise

        if done_wait.cancelled():
            await job.events.put({"type": "error", "message": "bridge timeout"})
            return
        if done_wait.exception() is not None:
            exc = done_wait.exception()
            await job.events.put({"type": "error", "message": getattr(exc, "message", str(exc))})
            return
        await job.events.put({"type": "done", "payload": done_wait.result()})