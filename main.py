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
    "Bengali": ("bn-IN", "bn", "bn"),
    "Urdu": ("ur-IN", "ur", "ur"),
    "Marathi": ("mr-IN", "mr", "mr"),
    "Gujarati": ("gu-IN", "gu", "gu"),
    "Kannada": ("kn-IN", "kn", "kn"),
    "Malayalam": ("ml-IN", "ml", "ml"),
    "Punjabi": ("pa-IN", "pa", "pa"),
    "Assamese": ("as-IN", "as", "as"),
    "Odia": ("or-IN", "or", "or"),
    "Bhojpuri": ("hi-IN", "hi", "hi"),  # Using Hindi as base
    "Maithili": ("hi-IN", "hi", "hi"),   # Using Hindi as base
    "Chhattisgarhi": ("hi-IN", "hi", "hi"),  # Using Hindi as base
    "Rajasthani": ("hi-IN", "hi", "hi"),  # Using Hindi as base
    "Konkani": ("hi-IN", "hi", "hi"),    # Using Hindi as base
    "Dogri": ("hi-IN", "hi", "hi"),      # Using Hindi as base
    "Kashmiri": ("hi-IN", "hi", "hi"),   # Using Hindi as base
    "Santhali": ("hi-IN", "hi", "hi"),   # Using Hindi as base
    "Sindhi": ("hi-IN", "hi", "hi"),     # Using Hindi as base
    "Manipuri": ("mni-IN", "hi", "mni"), # Meitei/Manipuri
    "Bodo": ("brx-IN", "hi", "brx"),     # Bodo
    "Sanskrit": ("sa-IN", "sa", "sa")    # Sanskrit
}

# WebSocket manager
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

# Validation error handler
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )

# Root route
@app.get("/")
async def root():
    return {
        "message": "Swadeshi Voice Translator API",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "text_translation": "/translate-only/",
            "websocket": "/ws/{src}/{tgt}/{device_id}"
        }
    }

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# WebSocket translation route
@app.websocket("/ws/{src}/{tgt}/{device_id}")
async def translate_ws(websocket: WebSocket, src: str, tgt: str, device_id: str):
    logger.info(f"WebSocket connection attempt: {device_id} ({src} → {tgt})")
    
    # Validate languages
    if src not in language_map or tgt not in language_map:
        await websocket.close(code=1003, reason="Unsupported language")
        return
    
    # Initialize recognizer
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300  # Adjust for better recognition
    
    src_locale, _, src_code = language_map.get(src, ("hi-IN", "hi", "hi"))
    _, tgt_tts_lang, tgt_code = language_map.get(tgt, ("hi-IN", "hi", "hi"))
    
    # Connect the device
    await manager.connect(websocket, device_id)
    
    try:
        while True:
            message = await websocket.receive()
            
            # Handle binary audio data
            if "bytes" in message:
                audio_chunk = message["bytes"]
                logger.info(f"Received audio blob from {device_id}: {len(audio_chunk)} bytes")
                
                # Process audio in a temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as webm_file:
                    webm_file.write(audio_chunk)
                    webm_path = webm_file.name
                
                wav_path = webm_path.replace(".webm", ".wav")
                
                try:
                    # Convert webm to wav
                    AudioSegment.from_file(webm_path).export(wav_path, format="wav")
                    logger.info("Audio conversion successful")
                    
                    # Recognize speech
                    with sr.AudioFile(wav_path) as source:
                        audio_data = recognizer.record(source)
                        text = recognizer.recognize_google(audio_data, language=src_locale)
                        logger.info(f"Recognized text from {device_id}: {text}")
                        
                        # Send recognition confirmation
                        await websocket.send_text(json.dumps({
                            "type": "status",
                            "message": "Speech recognized successfully"
                        }))
                        
                except sr.UnknownValueError:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Could not understand audio"
                    }))
                    continue
                except sr.RequestError as e:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"Speech recognition error: {str(e)}"
                    }))
                    continue
                except Exception as e:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": f"Audio processing error: {str(e)}"
                    }))
                    continue
                finally:
                    # Clean up temporary files
                    with suppress(Exception):
                        os.remove(webm_path)
                        os.remove(wav_path)
            
            # Handle text messages
            elif "text" in message:
                try:
                    data = json.loads(message["text"])
                    if data.get("type") == "text":
                        text = data["data"]
                        logger.info(f"Received text from {device_id}: {text}")
                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Unsupported message type"
                        }))
                        continue
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Invalid JSON format"
                    }))
                    continue
            
            # Check for language support fallback
            fallback_used = False
            if src_code not in supported_langs:
                src_code = "hi"
                fallback_used = True
            if tgt_code not in supported_langs:
                tgt_code = "hi"
                tgt_tts_lang = "hi"
                fallback_used = True
            
            if fallback_used:
                await websocket.send_text(json.dumps({
                    "type": "warning",
                    "message": "Using Hindi as fallback for unsupported language"
                }))
            
            # Perform translation
            try:
                translated = translator.translate(text, src=src_code, dest=tgt_code).text
                logger.info(f"Translated text for {device_id}: {translated}")
                
                # Send translated text back
                await websocket.send_text(json.dumps({
                    "type": "text",
                    "data": translated
                }))
            except Exception as e:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"Translation failed: {str(e)}"
                }))
                continue
            
            # Generate speech from translated text
            try:
                tts = gTTS(text=translated, lang=tgt_tts_lang, slow=False)
                audio_buffer = io.BytesIO()
                tts.write_to_fp(audio_buffer)
                audio_buffer.seek(0)
                
                # Send audio to all connected devices except the sender
                for other_id, other_ws in manager.active_connections.items():
                    if other_id != device_id and other_ws != websocket:
                        try:
                            audio_buffer.seek(0)
                            await other_ws.send_bytes(audio_buffer.read())
                            logger.info(f"Sent audio to device: {other_id}")
                        except Exception as e:
                            logger.error(f"Error sending to {other_id}: {str(e)}")
                            manager.disconnect(other_id)
                
            except Exception as e:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"Speech synthesis failed: {str(e)}"
                }))
    
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {device_id}")
        manager.disconnect(device_id)
    except Exception as e:
        logger.error(f"Unexpected error with {device_id}: {str(e)}")
        manager.disconnect(device_id)
        await websocket.close(code=1011, reason=str(e))

# Text translation request model
class TextTranslationRequest(BaseModel):
    text: str
    source_lang: str
    target_lang: str

# REST API for text translation
@app.post("/translate-only/")
async def translate_only(req: TextTranslationRequest):
    # Validate input languages
    if req.source_lang not in language_map or req.target_lang not in language_map:
        raise HTTPException(
            status_code=400,
            detail="Unsupported language"
        )
    
    _, _, src_code = language_map.get(req.source_lang, ("hi-IN", "hi", "hi"))
    _, tgt_tts_lang, tgt_code = language_map.get(req.target_lang, ("hi-IN", "hi", "hi"))
    
    # Check for language support fallback
    fallback_used = False
    if src_code not in supported_langs:
        src_code = "hi"
        fallback_used = True
    if tgt_code not in supported_langs:
        tgt_code = "hi"
        fallback_used = True
    
    try:
        translated_text = translator.translate(req.text, src=src_code, dest=tgt_code).text
        logger.info(f"Text translated: {req.text[:50]}... → {translated_text[:50]}...")
        
        return {
            "translated_text": translated_text,
            "source_lang": req.source_lang,
            "target_lang": req.target_lang,
            "fallback_used": fallback_used
        }
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Translation failed: {str(e)}"
        )

def start_server():
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").lower() == "true",
        workers=int(os.getenv("WORKERS", "1")),
        log_level="info"
    )

if __name__ == "__main__":
    start_server()
