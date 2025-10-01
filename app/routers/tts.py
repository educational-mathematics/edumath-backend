# app/routers/ai_tts.py (o donde tengas tu router /ai/tts)
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from google.cloud import texttospeech
import io, wave, os

router = APIRouter(prefix="/ai", tags=["ai"])

# Alias populares (Gemini) → voces reales de Google Cloud TTS
VOICE_ALIASES = {
    "kore": ("es-ES", "es-ES-Neural2-A"),
    "puck": ("es-ES", "es-ES-Neural2-A"),
    "aoede": ("en-US", "en-US-Standard-C"),
}

def _lang_from_name(voice_name: str, fallback: str = "es-PE") -> str:
    # ej: "es-PE-Standard-A" -> "es-PE"
    parts = voice_name.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return fallback

@router.post("/tts")
def tts(body: dict):
    text = (body.get("text") or "").strip()
    req_voice = (body.get("voice") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text requerido")

    # Voz por defecto (puedes cambiarla por env var TTS_VOICE si prefieres)
    default_voice = os.getenv("TTS_VOICE", "es-PE-Standard-A")

    # Normaliza voz solicitada
    voice_key = req_voice.lower()
    if voice_key in VOICE_ALIASES:
        language_code, voice_name = VOICE_ALIASES[voice_key]
    elif req_voice:
        # El usuario pasó un nombre de voz “real” de GC TTS
        voice_name = req_voice
        language_code = _lang_from_name(voice_name, "es-PE")
    else:
        voice_name = default_voice
        language_code = _lang_from_name(voice_name, "es-PE")

    # Intenta sintetizar
    try:
        client = texttospeech.TextToSpeechClient()
    except Exception as e:
        # Problema de credenciales (ADC)
        raise HTTPException(status_code=503, detail=f"TTS no disponible: {e}")

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code="es-ES", name=voice_name)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=24000,
    )

    try:
        res = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
    except Exception as e:
        # Si el nombre de voz no existe, GC TTS devuelve 400 → probamos con la default
        if voice_name != default_voice:
            fallback_lang = _lang_from_name(default_voice, "es-PE")
            fallback_voice = texttospeech.VoiceSelectionParams(language_code=fallback_lang, name=default_voice)
            try:
                res = client.synthesize_speech(input=synthesis_input, voice=fallback_voice, audio_config=audio_config)
            except Exception as e2:
                raise HTTPException(status_code=502, detail=f"TTS error (fallback): {e2}")
        else:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=502, detail=f"TTS error: {e}")

    # Empaqueta PCM en WAV 24kHz mono s16le
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(res.audio_content)
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/wav")