"""Websocket fan-out. Every server->browser message is a typed JSON event:
  snapshot, session_state, session_removed, message_delta, tool_activity, speak,
  permission_request, select_session, tts_status
"""
import asyncio
import json

from fastapi import WebSocket


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def add(self, ws: WebSocket):
        self.clients.add(ws)

    def remove(self, ws: WebSocket):
        self.clients.discard(ws)

    async def emit(self, type_: str, **payload):
        if not self.clients:
            return
        data = json.dumps({"type": type_, **payload})
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=2)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


hub = Hub()
