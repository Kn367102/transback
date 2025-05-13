import uvicorn
import os
import io
import json
import tempfile
import logging
import asyncio
from contextlib import suppress
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

import speech_recognition as sr
from googletrans import Translator, LANGUAGES
from gtts import gTTS
from pydub import AudioSegment

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('translator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Swadeshi Voice Translator API",
    description="Real-time voice and text translation for Indian languages",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

translator = Translator()
supported_langs = LANGUAGES.keys()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Language map with enhanced support
language_map = {
    "Hindi": ("hi-IN", "hi", "hi"),
    "English": ("en-US", "en", "en"),
    "Tamil": ("ta-IN", "ta", "ta"),
    "Telugu": ("te-IN", "te", "te"),
    # ... rest of your language map ...
}

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}

    async def connect(self, websocket: WebSocket, device_id: str):
        await websocket.accept()
        self.active_connections[device_id] = websocket
        logger.info(f"Device connected: {device_id}")
        return websocket

    def disconnect(self, device_id: str):
        if device_id in self.active_connections:
            del self.active_connections[device_id]
            logger.info(f"Device disconnected: {device_id}")

    async def send_message(self, device_id: str, message: str):
        if device_id in self.active_connections:
            await self.active_connections[device_id].send_text(message)

manager = ConnectionManager()

# ... [Keep all your existing route handlers] ...

def start_server():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=os.environ.get("RELOAD", "false").lower() == "true",
        workers=int(os.environ.get("WORKERS", "1"))
    )

if __name__ == "__main__":
    start_server()
