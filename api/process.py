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

        extract_dir = os.path.join(temp_dir, 'extracted')
        os.makedirs(extract_dir)

        with zipfile.ZipFile(apkg_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        import sqlite3

        db_path = os.path.join(extract_dir, 'collection.anki2')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id, flds FROM notes")
        notes = cursor.fetchall()

        audio_files = {}
        updated_count = 0

        for note_id, fields_str in notes:
            fields = fields_str.split('\x1f')

            updated_fields = []
            modified = False 

            for field in fields:
                if has_audio_tag(field):
                    updated_fields.append(field)
                    continue

                detected_lang = detect_field_language(field)

                if detected_lang == target_lang: 
                    import re
                    clean_text = re.sub('<[^<]+?>', '', field).strip()

                    if clean_text:
                        text_hash = generate_audio_hash(clean_text)

                        audio_data = get_cached_audio(text_hash)

                        if not audio_data:
                            audio_data = generate_audio_elevenlabs(clean_text, target_language)

                            if audio_data:
                                cache_audio(text_hash, clean_text, audio_data, target_language)
                        
                        if audio_data:
                            audio_filename = f"{text_hash}.mp3"
                            audio_files[audio_filename] = audio_data

                            updated_field = field + f" [sound:{audio_filename}]"
                            updated_fields.append(updated_field)
                            modified = True
                        else: 
                            updated_fields.append(field)
                    else:
                        updated_fields.append(field)
                else: 
                    updated_fields.append(field)
            if modified: 
                new_fields_str = '\x1f'.join(updated_fields)
                cursor.execute("UPDATE notes SET flds = ? WHERE id = ?", (new_fields_str, note_id))
                updated_count += 1
        conn.commit()
        conn.close()

        media_dir = extract_dir
        for filename, audio_data in audio_files.items():
            with open(os.path.join(media_dir, filename), 'wb') as f:
                f.write(audio_data)

        output_path = os.path.join(temp_dir, 'output.apkg')
        with zipfile.ZipFile(output_path, 'w') as zip_out:
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, extract_dir)
                    zip_out.write(file_path, arcname)

        with open(output_path, 'rb') as f:
            output_data = f.read()

            return output_data, updated_count 
        
    class handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                content_type = self.headers.get('Content-Type')

                if 'multipart/form-data' not in content_type:
                    raise ValueError("expected multipart/form-data")
                
                form = cgi.FieldStorage(
                    fp=self.rfile,
                    headers=self.headers,
                    environ={'REQUEST_METHOD': 'POST'}
                )

                file_item = form['file']
                language = form.getvalue('language')

                if not file_item.file:
                    raise ValueError("No file uploaded")
                
                file_data = file_item.file.read()

                output_data, updated_count = process_deck(file_data, language)

                self.send_response(200)
                self.send_header('Content-type', 'application/octet-stream')
                self.end_headers()
                self.wfile.write(output_data)

            except Exception as e:
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = {'status': 'error', 'message': str(e)}