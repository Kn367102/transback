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
    print(f"⚠️ Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# Root route
@app.get("/")
def root():
    return {"message": "Swadeshi Voice Translator backend is running."}

# Language map with improved fallbacks
language_map = {
    "Hindi": ("hi-IN", "hi", "hi"),
    "English": ("en-IN", "en", "en"),
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
    "Bhojpuri": ("bho-IN", "hi", "bho"),
    "Maithili": ("mai-IN", "hi", "mai"),
    "Chhattisgarhi": ("hne-IN", "hi", "hne"),
    "Rajasthani": ("raj-IN", "hi", "raj"),
    "Konkani": ("kok-IN", "kok", "kok"),
    "Dogri": ("doi-IN", "hi", "doi"),
    "Kashmiri": ("ks-IN", "ks", "ks"),
    "Santhali": ("sat-IN", "sat", "sat"),
    "Sindhi": ("sd-IN", "sd", "sd"),
    "Manipuri": ("mni-IN", "mni", "mni"),
    "Bodo": ("brx-IN", "brx", "brx"),
    "Sanskrit": ("sa-IN", "sa", "sa")
}

class ConnectionManager:
    async def connect(self, device_id: str, websocket: WebSocket):
        if device_id in connected_devices:
            await websocket.close(code=1008, reason="Device already connected")
            return False
        await websocket.accept()
        connected_devices[device_id] = websocket
        return True

    def disconnect(self, device_id: str):
        connected_devices.pop(device_id, None)

manager = ConnectionManager()

# WebSocket translation route
@app.websocket("/ws/{src}/{tgt}/{device_id}")
async def translate_ws(websocket: WebSocket, src: str, tgt: str, device_id: str):
    # Validate languages
    if src not in language_map or tgt not in language_map:
        await websocket.close(code=1008, reason="Unsupported language")
        return

    if not await manager.connect(device_id, websocket):
        return

    print(f"🔌 WebSocket connected: {device_id} ({src} → {tgt})")
    
    recognizer = sr.Recognizer()
    src_locale, _, src_code = language_map[src]
    _, tgt_tts_lang, tgt_code = language_map[tgt]

    try:
        while True:
            msg = await websocket.receive()

            if "bytes" in msg:
                try:
                    audio_chunk = msg["bytes"]
                    print(f"📥 Received audio blob: {len(audio_chunk)} bytes")

                    # Create temporary files
                    with tempfile.NamedTemporaryFile(suffix=".webm") as webm_file, \
                         tempfile.NamedTemporaryFile(suffix=".wav") as wav_file:
                        
                        webm_file.write(audio_chunk)
                        webm_file.flush()
                        
                        try:
                            AudioSegment.from_file(webm_file.name).export(wav_file.name, format="wav")
                            print("✅ Converted webm to wav")
                        except Exception as e:
                            await websocket.send_text(f"Audio conversion failed: {str(e)}")
                            continue

                        try:
                            with sr.AudioFile(wav_file.name) as source:
                                audio_data = recognizer.record(source)
                            text = recognizer.recognize_google(audio_data, language=src_locale)
                            print(f"🗣 Recognized: {text}")
                        except sr.UnknownValueError:
                            await websocket.send_text("Could not understand audio")
                            continue
                        except sr.RequestError as e:
                            await websocket.send_text(f"STT service error: {str(e)}")
                            continue
                except Exception as e:
                    await websocket.send_text(f"Audio processing failed: {str(e)}")
                    continue

            elif "text" in msg:
                try:
                    parsed = json.loads(msg["text"])
                    if parsed.get("type") == "text":
                        text = parsed["data"]
                        print(f"📝 Received text for translation: {text}")
                    else:
                        await websocket.send_text("Unsupported message type")
                        continue
                except json.JSONDecodeError:
                    await websocket.send_text("Invalid JSON format")
                    continue
            else:
                await websocket.send_text("Unsupported message format")
                continue

            # Translation
            try:
                translated = translator.translate(text, src=src_code, dest=tgt_code).text
                print(f"🌐 Translated: {translated}")
                await websocket.send_text(json.dumps({
                    "type": "text", 
                    "data": translated,
                    "original": text
                }))
            except Exception as e:
                await websocket.send_text(f"Translation failed: {str(e)}")
                continue

            # TTS
            try:
                tts = gTTS(text=translated, lang=tgt_tts_lang)
                buf = io.BytesIO()
                tts.write_to_fp(buf)
                buf.seek(0)
                
                # Broadcast to other devices
                for other_id, other_ws in connected_devices.items():
                    if other_id != device_id:
                        try:
                            buf.seek(0)
                            await other_ws.send_bytes(buf.read())
                            print(f"🔊 Sent audio to device: {other_id}")
                        except Exception as e:
                            print(f"Failed to send to {other_id}: {str(e)}")
                            manager.disconnect(other_id)
            except Exception as e:
                await websocket.send_text(f"TTS failed: {str(e)}")

    except WebSocketDisconnect:
        print(f"❌ WebSocket disconnected: {device_id}")
        manager.disconnect(device_id)
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        manager.disconnect(device_id)
        await websocket.close(code=1011, reason="Server error")

# REST API for text translation
class TextTranslationRequest(BaseModel):
    text: str
    source_lang: str
    target_lang: str

@app.post("/translate-only/")
async def translate_only(req: TextTranslationRequest):
    if req.source_lang not in language_map or req.target_lang not in language_map:
        return JSONResponse(
            status_code=400,
            content={"error": "Unsupported language"}
        )

    _, _, src_code = language_map[req.source_lang]
    _, tgt_tts_lang, tgt_code = language_map[req.target_lang]

    try:
        translated_text = translator.translate(req.text, src=src_code, dest=tgt_code).text
        print(f"📝 Text-only translated: {translated_text}")
        return {
            "translated_text": translated_text,
            "source_lang": req.source_lang,
            "target_lang": req.target_lang
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Translation failed: {str(e)}"}
        )

# Run the server
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
