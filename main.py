#!/usr/bin/env python3
import uvicorn
import os
import io
import json
import tempfile
from contextlib import suppress
from typing import Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

import speech_recognition as sr
from googletrans import Translator, LANGUAGES
from gtts import gTTS
from pydub import AudioSegment

# Initialize FastAPI app
app = FastAPI()
translator = Translator()
supported_langs = LANGUAGES.keys()

# Global state for connected devices
connected_devices: Dict[str, WebSocket] = {}

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Validation error handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "message": "Validation error"},
    )

@app.get("/")
def root():
    return {"message": "Swadeshi Voice Translator backend is running."}

# Improved language map with better code handling
language_map = {
    "Hindi": {"locale": "hi-IN", "trans_code": "hi", "tts_code": "hi"},
    "English": {"locale": "en-IN", "trans_code": "en", "tts_code": "en"},
    "Tamil": {"locale": "ta-IN", "trans_code": "ta", "tts_code": "ta"},
    "Telugu": {"locale": "te-IN", "trans_code": "te", "tts_code": "te"},
    "Bengali": {"locale": "bn-IN", "trans_code": "bn", "tts_code": "bn"},
    # Add other languages following the same pattern
}

class ConnectionManager:
    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        connected_devices[device_id] = websocket
        return True

    def disconnect(self, device_id: str):
        if device_id in connected_devices:
            del connected_devices[device_id]

manager = ConnectionManager()

@app.websocket("/ws/{src_lang}/{tgt_lang}/{device_id}")
async def websocket_translator(
    websocket: WebSocket,
    src_lang: str,
    tgt_lang: str,
    device_id: str
):
    # Validate languages
    if src_lang not in language_map or tgt_lang not in language_map:
        await websocket.close(code=1008, reason="Unsupported language")
        return

    if not await manager.connect(device_id, websocket):
        return

    recognizer = sr.Recognizer()
    src_config = language_map[src_lang]
    tgt_config = language_map[tgt_lang]

    try:
        while True:
            message = await websocket.receive()

            if "bytes" in message:
                try:
                    # Process audio
                    with tempfile.NamedTemporaryFile(suffix=".webm") as webm_file, \
                         tempfile.NamedTemporaryFile(suffix=".wav") as wav_file:
                        
                        webm_file.write(message["bytes"])
                        webm_file.flush()
                        
                        # Convert to WAV
                        AudioSegment.from_file(webm_file.name).export(wav_file.name, format="wav")
                        
                        # Speech recognition
                        with sr.AudioFile(wav_file.name) as source:
                            audio_data = recognizer.record(source)
                            text = recognizer.recognize_google(audio_data, language=src_config["locale"])
                
                except sr.UnknownValueError:
                    await websocket.send_json({"error": "Could not understand audio"})
                    continue
                except Exception as e:
                    await websocket.send_json({"error": f"Audio processing failed: {str(e)}"})
                    continue

            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    text = data.get("text", "")
                except:
                    await websocket.send_json({"error": "Invalid text format"})
                    continue

            # Translation
            try:
                translation = translator.translate(
                    text,
                    src=src_config["trans_code"],
                    dest=tgt_config["trans_code"]
                ).text
                
                await websocket.send_json({
                    "translation": translation,
                    "original": text
                })

                # Convert to speech
                tts = gTTS(text=translation, lang=tgt_config["tts_code"])
                audio_buffer = io.BytesIO()
                tts.write_to_fp(audio_buffer)
                audio_buffer.seek(0)
                
                # Broadcast to other devices
                for device, ws in connected_devices.items():
                    if device != device_id:
                        try:
                            await ws.send_bytes(audio_buffer.read())
                            audio_buffer.seek(0)
                        except:
                            manager.disconnect(device)

            except Exception as e:
                await websocket.send_json({"error": f"Translation failed: {str(e)}"})

    except WebSocketDisconnect:
        manager.disconnect(device_id)
    except Exception as e:
        manager.disconnect(device_id)
        await websocket.close(code=1011, reason=str(e))

class TranslationRequest(BaseModel):
    text: str
    source_lang: str
    target_lang: str

@app.post("/translate")
async def translate_text(request: TranslationRequest):
    if request.source_lang not in language_map or request.target_lang not in language_map:
        return JSONResponse(
            status_code=400,
            content={"error": "Unsupported language"}
        )

    src_config = language_map[request.source_lang]
    tgt_config = language_map[request.target_lang]

    try:
        translation = translator.translate(
            request.text,
            src=src_config["trans_code"],
            dest=tgt_config["trans_code"]
        ).text
        
        return {
            "translation": translation,
            "original": request.text,
            "source_lang": request.source_lang,
            "target_lang": request.target_lang
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Translation failed: {str(e)}"}
        )

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=10000,
        reload=True,
        workers=2
    )
