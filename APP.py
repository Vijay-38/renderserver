const { WebSocketServer } = require('ws');

const wss = new WebSocketServer({ port: 8080 });

const rooms = new Map();

function broadcast(room, message, excludeId = null) {
  room.forEach((player) => {
    if (player.id !== excludeId && player.ws.readyState === 1) {
      player.ws.send(JSON.stringify(message));
    }
  });
}

wss.on('connection', (ws) => {
  let playerId = null;
  let currentRoom = null;
  let roomName = null;

  console.log('Player connected');

  ws.on('message', (data) => {
    try {
      const message = JSON.parse(data);
      
      switch (message.type) {
        case 'welcome':
          playerId = message.playerId;
          console.log('Player ID assigned:', playerId);
          break;
          
        case 'join':
          roomName = message.roomName || 'default';
          
          if (!rooms.has(roomName)) {
            rooms.set(roomName, new Map());
          }
          currentRoom = rooms.get(roomName);
          currentRoom.set(playerId, { ws, id: playerId });
          
          // Send room state to new player
          const players = {};
          currentRoom.forEach((p, id) => {
            if (id !== playerId) players[id] = p.state;
          });
          ws.send(JSON.stringify({ type: 'room-state', players }));
          
          // Notify others
          broadcast(currentRoom, { 
            type: 'player-joined', 
            playerId, 
            state: { pos: { x: 0, y: 150, z: 0 }, heading: 0, pitch: 0, roll: 0, speed: 0 }
          }, playerId);
          
          console.log(`Player ${playerId} joined room ${roomName}`);
          break;
          
        case 'update':
          if (currentRoom && playerId) {
            const player = currentRoom.get(playerId);
            if (player) {
              player.state = message.state;
              broadcast(currentRoom, { 
                type: 'player-update', 
                playerId, 
                state: message.state 
              }, playerId);
            }
          }
          break;
          
        case 'leave':
          if (currentRoom && playerId) {
            currentRoom.delete(playerId);
            broadcast(currentRoom, { type: 'player-left', playerId });
            
            if (currentRoom.size === 0) {
              rooms.delete(roomName);
            }
          }
          break;
      }
    } catch (e) {
      console.error('Error processing message:', e);
    }
  });

  ws.on('close', () => {
    if (currentRoom && playerId) {
      currentRoom.delete(playerId);
      broadcast(currentRoom, { type: 'player-left', playerId });
      console.log(`Player ${playerId} disconnected`);
    }
  });
});

console.log('WebSocket server running on port 8080');
