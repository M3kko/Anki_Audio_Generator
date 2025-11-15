from http.server import BaseHTTPRequestHandler
import json
import os
import tempfile
import hashlib
from io import BytesIO
import genkanki
from lingua import Language, LanguageDetectorBuilder
from elevenlabs.client import ElevenLabs
from supabase import create_client, Client

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)

elevenlabs_client = ElevenLabs(
    api_key=os.environ.get("ELEVENLABS_API_KEY")
)

detector = LanguageDetectorBuilder.from_all_languages().build()

LANGUAGE_MAP = {
    'en-US': Language.ENGLISH,
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
    'uk': Language.UKRAINIAN,
    'ru': Language.RUSSIAN,
    'hu': Language.HUNGARIAN,
    'no': Language.NORWEGIAN,
    'vi': Language.VIETNAMESE,
}

def detect_field_language(text):
    """Detect the language of a given text field."""
    if not text or len(text.strip()) == 0:
        return None
    import re
    clean_text = re.sub(r'<[^<]+?>', '', text)

    if len(clean_text.strip()) == 0:
        return None
    
    detected = detector.detect_language_of(clean_text)
    return detected

def has_audio_tag(text):
    """Check if field alrady has an audio tag"""
    import re 
    return bool(re.search(r'\[sound:[^\]]+\]', text))

def generate_audio_hash(text):
    """Generate hash for text to use as a cache key"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def get_cached_audio(text_hash):
    """Check if audio exists in Supabase cache"""
    try: 
        result = supabase.table('audio_cache').select('*').eq('text_hash', text_hash).execute()
        if result.data and len(result.data) > 0:

            file_path = result.data[0]['file_path']
            audio_data = supabase.storage.from_('audio-files').download_(file_path)
            return audio_data
        return None
    except Exception as e:
        print(f"Cache lookup error: {e}")
        return None
    
def cache_audio(text_hash, text, audio_data, language):
    """Store audio in Supabase cache"""
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
        print(f"Cache store error: {e}")

def generate_audio_elevenlabs(text, language_code):
    """Generate audio files using ElevenLabs """
    try:
        audio_generrator = elevenlabs_client.generate(
            text=text,
            voice="Adam",
            model="eleven_turbo_v2.5"
        )

        audio_bytes = b''.join(audio_generator)
        return audio_bytes
    except Exception as e:
        print(f"ElevenLabs generation error: {e}")
        return None
    
def process_deck(apkg_file, target_language):
    """Process Anki decka and add audio"""

    target_lang = LANGUAGE_MAP.get(target_language)
    if not target_lang:
        raise ValueError(f"unsupported language: {target_language}")
    
    with tempfile.TemporaryDirector() as temp_dir:
        apkg_path = os.path.join(temp_dir, 'deck.apkg')
        with open(apkg_path, 'wb') as f:
            f.write(apkg_file)

        