import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, conversation_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(conversation_id, set()))
        for queue in queues:
            queue.put_nowait(event)

    async def subscribe(self, conversation_id: str) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[conversation_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers[conversation_id].discard(queue)


event_bus = EventBus()
