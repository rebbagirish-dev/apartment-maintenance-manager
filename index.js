const express = require('express');
const app = express();
app.use(express.json({ limit: '10mb' }));

let messages = []; // For production, use a database like Postgres on Railway

// GET /messages?roomId=XYZ&since=123
app.get('/messages', (req, res) => {
    const { roomId, since } = req.query;
    const filtered = messages.filter(m => m.roomId === roomId && m.timestamp > parseInt(since || 0));
    res.json(filtered);
});

// POST /messages
app.post('/messages', (req, res) => {
    const msg = { id: Date.now().toString(), ...req.body };
    messages.push(msg);
    // Optional: Keep only last 1000 messages to save memory
    if (messages.length > 1000) messages.shift(); 
    res.status(201).json(msg);
});

// DELETE /messages/:id
app.delete('/messages/:id', (req, res) => {
    messages = messages.filter(m => m.id !== req.params.id);
    res.sendStatus(204);
});

// DELETE /messages/room/:roomId
app.delete('/messages/room/:roomId', (req, res) => {
    messages = messages.filter(m => m.roomId !== req.params.roomId);
    res.sendStatus(204);
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Chat server running on port ${PORT}`));