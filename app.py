from flask import Flask, request, send_file, jsonify
from flask_cors import CORS 
import os
import tempfile
import hashlib
import zipfile
import json
from io import BytesIO
import genanki
import re
from lingua import Language, LanguageDetectorBuilder
from elevenlabs.client import ElevenLabs
from supabase import create_client, CLient


app = Flask(__name__)
CORS(app)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)

elevenlabs_client = ElevenLabs(
    api_key=os.environ.get("ELEVENLABS_API_KEY")
)

detector = LanguageDetectorBuilder.from_all_langauges().build()

LANGUAGE_MAP = {
    'en': Language.ENGLISH,
    'es': Language.SPANISH,
    'fr': Language.FRENCH,
    'pt': Language.PORTUGUESE,
    'ar': Language.ARABIC,
    'ja': Language.JAPANESE,
    'zh': Language.CHINESE,
    'de': Language.GERMAN,
    'hi': Language.HINDI,
    'ko': Language.KOREAN,
    'it': Language.ITALIAN,
    'id': Language.INDONESIAN,
    'nl': Language.DUTCH,
    'tr': Language.TURKISH,
    'fil': Language.TAGALOG,
    'pl': Language.POLISH,
    'sv': Language.SWEDISH,
    'bg': Language.BULGARIAN,
    'ro': Language.ROMANIAN,
    'cs': Language.CZECH,
    'el': Language.GREEK,
    'fi': Language.FINNISH,
    'hr': Language.CROATIAN,
    'ms': Language.MALAY,
    'sk': Language.SLOVAK,
    'da': Language.DANISH,
    'ta': Language.TAMIL,
    'uk': Language.UKRAINIAN,
    'ru': Language.RUSSIAN,
    'hu': Language.HUNGARIAN,
    'no': Language.BOKMAL,
    'vi': Language.VIETNAMESE,
}

def detect_field_langauge(text):
    """Detects the language of a text field"""
    if not text or len(text.strip()) == 0:
        return None 
    
    clean_text = re.sub('<[^<]+?>', '', text)

    if len(clean_text.strip()) == 0:
        return None
    
    detected = detector.detect_language_of(clean_text)
    return detected

def generate_audio_hash(text):
    """Generate hash for text to use as cache key"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def get_cached_audio(text_hash):
    """Check if audio exists in Supabase Storage"""
    try: 
        result = supabase.table('audio-cache').select('*').eq('text_hash', text_hash, text_hash).execute()
        if result.data and len(result.data) > 0:
            file_path = result.data[0]['file_path']
            audio_data = supabase.storage.from_('audio-files').download(file_path)
            return audio_data
        return None
    except Exception as e:
        print(f'Cache lookup error: {e}')
        return None
    
def cache_audio(text_hash, text, audio_data, language):
    """Store audio in supabase storage"""
    try:
        filename = f"{text_hash}.mp3"

        supabase.storage.from_('audio-files').upload(
            filename,
            audio_data,
            {'content-type': 'audio/mpeg'}
        )

        supabase.table('audio_cache').insert({
            'text_hash': text_hash,
            'text': text[:500],
            'file_path': filename,
            'language': language
        }).execute()

    except Exception as e:
        print(f'Cache storage error: {e}')

def generate_audio_elevenlabs(text, language):
    """Generate audio using eleven labs API"""

    try:
        audio_generator = elevenlabs_client.text_to_speech.convert_as_stream(
            void_id="21m00Tcm4TlvDq8ikWAM"
            text=text,
            model_id="eleven_turbo_v2_5",
            language_code=language
        )

        audio_bytes = b''.join(audio_generator)
        return audio_bytes
    except Exception as e:
        print(f"ElevenLabs error: {e}")
        return None
    
def process_deck(apkg_file, target_langauge, native_language):
    """Process anki deck and create audio practice deck"""

    import sqlite3

    print(f"proces deck called")
    print(f"Target language (for audio): {target_langauge}")
    print(f"Native language (no audio): {native_language}")

    native_lang = LANGUAGE_MAP.get(native_language)

    if not native_lang:
        raise ValueError(f"Unsupported native language code: {native_language}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Created temporary directory: {temp_dir}")

        apkg_path = os.path.join(temp_dir, 'deck.apkg')
        with open(apkg_path, 'wb') as f:
            f.write(apkg_file.read())

            

