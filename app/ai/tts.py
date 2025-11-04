import os
from google.cloud import texttospeech

def synthesize_mp3(text: str, voice: str | None = None) -> bytes:
    voice_name = voice or os.getenv("TTS_VOICE", "es-ES-Standard-A")
    client = texttospeech.TextToSpeechClient()
    input_ = texttospeech.SynthesisInput(text=text)
    voice_ = texttospeech.VoiceSelectionParams(language_code="es-ES", name=voice_name)
    config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    res = client.synthesize_speech(input=input_, voice=voice_, audio_config=config)
    return res.audio_content