"""Tracks open WebSocket connections per user and pushes live updates."""
from collections import defaultdict


class Hub:
    def __init__(self):
        self._conns = defaultdict(set)

    def add(self, user_id, ws):
        self._conns[user_id].add(ws)

    def remove(self, user_id, ws):
        conns = self._conns.get(user_id)
        if conns:
            conns.discard(ws)
            if not conns:
                del self._conns[user_id]

    async def send(self, user_id, message):
        for ws in list(self._conns.get(user_id, ())):
            try:
                await ws.send_json(message)
            except Exception:
                self.remove(user_id, ws)

    async def disconnect(self, user_id):
        for ws in list(self._conns.get(user_id, ())):
            try:
                await ws.close(code=4403)
            except Exception:
                pass
            self.remove(user_id, ws)

    async def send_many(self, user_ids, message):
        for user_id in set(user_ids):
            await self.send(user_id, message)


hub = Hub()
