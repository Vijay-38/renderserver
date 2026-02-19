#!/usr/bin/env python3
"""
WebSocket Server for Multiplayer Jet Game
Install: pip install websockets
Run: python server.py
"""

import asyncio
import websockets
import json
import uuid
from collections import defaultdict
from aiohttp import web

rooms = defaultdict(dict)  # room_name -> {player_id: player_data}

# HTTP health check endpoint for Render
async def health_check(request):
    return web.Response(text="OK")

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    player_id = str(uuid.uuid4())[:8]
    current_room = None
    
    print(f"Player {player_id} connected")

    try:
        async for msg in ws:
            if msg.type == websockets.TextMessage:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

                    if msg_type == 'join':
                        room_name = data.get('roomName', 'default')
                        current_room = room_name
                        
                        rooms[room_name][player_id] = {
                            'ws': ws,
                            'state': {'pos': {'x': 0, 'y': 150, 'z': 0}, 'heading': 0, 'pitch': 0, 'roll': 0, 'speed': 0}
                        }

                        # Send welcome message with player ID
                        await ws.send_json({
                            'type': 'welcome',
                            'playerId': player_id
                        })

                        # Send room state to new player
                        players = {pid: p['state'] for pid, p in rooms[room_name].items() if pid != player_id}
                        await ws.send_json({
                            'type': 'room-state',
                            'players': players
                        })

                        # Notify others
                        await broadcast(room_name, {
                            'type': 'player-joined',
                            'playerId': player_id,
                            'state': {'pos': {'x': 0, 'y': 150, 'z': 0}, 'heading': 0, 'pitch': 0, 'roll': 0, 'speed': 0}
                        }, exclude_id=player_id)

                        print(f"Player {player_id} joined room {room_name}")

                    elif msg_type == 'update':
                        if current_room and player_id in rooms[current_room]:
                            player_state = data.get('state')
                            rooms[current_room][player_id]['state'] = player_state
                            await broadcast(current_room, {
                                'type': 'player-update',
                                'playerId': player_id,
                                'state': player_state
                            }, exclude_id=player_id)

                    elif msg_type == 'leave':
                        if current_room and player_id in rooms[current_room]:
                            del rooms[current_room][player_id]
                            await broadcast(current_room, {'type': 'player-left', 'playerId': player_id})
                            if not rooms[current_room]:
                                del rooms[current_room]
                            current_room = None

                except json.JSONDecodeError:
                    print(f"Invalid JSON from {player_id}")

    except websockets.exceptions.ConnectionClosed:
        print(f"Player {player_id} disconnected")
    finally:
        if current_room and player_id in rooms[current_room]:
            del rooms[current_room][player_id]
            await broadcast(current_room, {'type': 'player-left', 'playerId': player_id})
            if not rooms[current_room]:
                del rooms[current_room]

    return ws


async def broadcast(room_name, message, exclude_id=None):
    """Broadcast message to all players in a room except excluded player"""
    if room_name not in rooms:
        return
    
    for pid, player_data in list(rooms[room_name].items()):
        if pid != exclude_id:
            try:
                await player_data['ws'].send_json(message)
            except:
                pass


async def init_app():
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_get('/', health_check)
    return app


async def main():
    print("Starting server on port 8080...")
    app = await init_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("Server running on http://0.0.0.0:8080")
    print("WebSocket available at ws://0.0.0.0:8080/ws")
    
    try:
        await asyncio.Future()  # Run forever
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
