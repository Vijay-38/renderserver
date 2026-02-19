import asyncio
import os
import websockets

PORT = int(os.environ.get("PORT", 10000))

async def handler(websocket):
    print("Client connected")

    try:
        async for message in websocket:
            print("Received:", message)
            await websocket.send(f"Echo: {message}")
    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")

async def main():
    async with websockets.serve(handler, "0.0.0.0", PORT):
        print(f"WebSocket server running on port {PORT}")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
