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

rooms = defaultdict(dict)  # room_name -> {player_id: player_data}


async def handle_client(websocket, path):
    player_id = str(uuid.uuid4())[:8]
    current_room = None
    player_state = None
    
    print(f"Player {player_id} connected")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get('type')

                if msg_type == 'join':
                    room_name = data.get('roomName', 'default')
                    current_room = room_name
                    
                    rooms[room_name][player_id] = {
                        'websocket': websocket,
                        'state': {'pos': {'x': 0, 'y': 150, 'z': 0}, 'heading': 0, 'pitch': 0, 'roll': 0, 'speed': 0}
                    }

                    # Send room state to new player
                    players = {pid: p['state'] for pid, p in rooms[room_name].items() if pid != player_id}
                    await websocket.send(json.dumps({
                        'type': 'room-state',
                        'players': players
                    }))

                    # Notify others
                    broadcast(room_name, {
                        'type': 'player-joined',
                        'playerId': player_id,
                        'state': {'pos': {'x': 0, 'y': 150, 'z': 0}, 'heading': 0, 'pitch': 0, 'roll': 0, 'speed': 0}
                    }, exclude_id=player_id)

                    print(f"Player {player_id} joined room {room_name}")

                elif msg_type == 'update':
                    if current_room and player_id in rooms[current_room]:
                        player_state = data.get('state')
                        rooms[current_room][player_id]['state'] = player_state
                        broadcast(current_room, {
                            'type': 'player-update',
                            'playerId': player_id,
                            'state': player_state
                        }, exclude_id=player_id)

                elif msg_type == 'leave':
                    if current_room and player_id in rooms[current_room]:
                        del rooms[current_room][player_id]
                        broadcast(current_room, {'type': 'player-left', 'playerId': player_id})
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
            broadcast(current_room, {'type': 'player-left', 'playerId': player_id})
            if not rooms[current_room]:
                del rooms[current_room]


def broadcast(room_name, message, exclude_id=None):
    """Broadcast message to all players in a room except excluded player"""
    if room_name not in rooms:
        return
    
    msg_str = json.dumps(message)
    
    for pid, player_data in rooms[room_name].items():
        if pid != exclude_id:
            try:
                asyncio.create_task(player_data['websocket'].send(msg_str))
            except:
                pass


async def main():
    print("Starting WebSocket server on port 8080...")
    async with websockets.serve(handle_client, "0.0.0.0", 8080):
        print("Server running. Press Ctrl+C to stop.")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")
