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
from supabase import create_client, Client


app = Flask(__name__)
CORS(app)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)

elevenlabs_client = ElevenLabs(
    api_key=os.environ.get("ELEVENLABS_API_KEY")
)

detector = LanguageDetectorBuilder.from_all_languages().build()

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

def detect_field_language(text):
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
        result = supabase.table('audio_cache').select('*').eq('text_hash', text_hash).execute()
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
            voice_id="21m00Tcm4TlvDq8ikWAM",
            text=text,
            model_id="eleven_turbo_v2_5",
            language_code=language
        )

        audio_bytes = b''.join(audio_generator)
        return audio_bytes
    except Exception as e:
        print(f"ElevenLabs error: {e}")
        return None
    

def analyze_deck(apkg_file, native_language):
    """Analyze anki deck to give the user a preview of what will be created"""

    import sqlite3

    print(f"analyze the deck called")
    print(f"Native language: {native_language}")

    native_lang = LANGUAGE_MAP.get(native_language)

    if not native_lang:
        raise ValueError(f"Unsupported native language code: {native_language}")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Created temporary directory: {temp_dir}")

        apkg_path = os.path.join(temp_dir, 'deck.apkg')
        with open(apkg_path, 'wb') as f:
            f.write(apkg_file)

        extract_dir = os.path.join(temp_dir, 'extracted')
        os.makedirs(extract_dir)

        with zipfile.ZipFile(apkg_path, 'r') as zip_ref:
            print(f"files in .apkg: {zip_ref.namelist()}")
            zip_ref.extractall(extract_dir)

        files_in_extract = os.listdir(extract_dir)
        print(f"Extracted files: {files_in_extract}")

        db_path = os.path.join(extract_dir, 'collection.anki21')
        if not os.path.exists(db_path):
            db_path = os.path.join(extract_dir, 'collection.anki2')
            print(f"Using collection.anki2 fallback")
        else:
            print(f"Using collection.anki21")

        print(f"Database path: {db_path}, exists: {os.path.exists(db_path)}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id, flds FROM notes")
        notes = cursor.fetchall()
        print(f"Found {len(notes)} notes in deck")

        conn.close()

        cards_data = []
        uncertain_count = 0

        for note_id, fields_str in notes:
            fields = fields_str.split('\x1f')
            print(f"\nNote {note_id}: {len(fields)} fields")

            field_data = []
            foreign_text = None
            foreign_detected_lang = None
            is_uncertain = False

            for i, field in enumerate(fields):
                clean_text = re.sub('<[^<]+?>', '', field).strip()

                if not clean_text:
                    continue

                detected_lang = detect_field_language(clean_text)

                detected_lang_code = None
                if detected_lang:
                    for code, lang in LANGUAGE_MAP.items():
                        if lang == detected_lang:
                            detected_lang_code = code
                            break
                print(f"Field {i}: '{clean_text[:50]}...' -> detected: {detected_lang}")

                field_data.append({
                    'text': clean_text,
                    'detected_language': detected_lang_code,
                    'is_native': detected_lang == native_lang
                })

                if detected_lang == native_lang:
                    if not native_text:
                        native_text = clean_text
                        print(f"✓ Found native language field ({native_language}) - NO AUDIO")
                else:
                    if not foreign_text:
                        foreign_text = clean_text
                        foreign_detected_lang = detected_lang_code
                        print(f"✓ Found non-native field - WILL GENERATE AUDIO")
            
            if not foreign_text or not native_text:
                is_uncertain = True
                uncertain_count += 1
                print(f "UNCERTAIN: Could not identify clear foreign/native text split")

                if len(field_data) >= 2:
                    foreign_text = field_data[0]['text']
                    foreign_detected_lang = field_data[0]['detected_language']
                    native_text = field_data[1]['text']

            if foreign_text and native_text:
                cards_data.append({
                    'note_id': note_id,
                    'foreign_text': foreign_text,
                    'native_text': native_text,
                    'foreign_language': foreign_detected_lang,
                    'is_uncertain': is_uncertain,
                    'all_fields': field_data
                })

        print(f"\nAnalysis complete: {len(cards_data)} cards, {uncertain_count} uncertain")

        return {
            'total_cards': len(cards_data),
            'uncertain_cards': uncertain_count,
            'cards': cards_data
        }

def process_deck(apkg_file, target_language, native_language):
    """Process anki deck and create audio practice deck"""

    import sqlite3

    print(f"proces deck called")
    print(f"Target language (for audio): {target_language}")
    print(f"Native language (no audio): {native_language}")
    print(f"Processing {len(cards_data)} cards")

    deck_id = int(hashlib.md5(f"audio_practice_{target_language}_{native_language}".encode()).hexdigest()[:8], 16)
    deck = genanki.Deck(deck_id, f"Audio Practice Deck ({target_language.upper()} - {native_language.upper()})")

    model_id = int(hashlib.md5(f"audio_practice_model_{target_language}_{native_language}".encode()).hexdigest()[:8], 16)
    model = genanki.Model(
        model_id,
        'Audio Practice Model',
        fields=[
            {'name': 'Audio'},
            {'name': 'ForeignText'},
            {'name': 'NativeText'},
        ],
        templates=[
            {
                'name': 'Audio to Native',
                'qfmt': '{{Audio}}',
                'afmt': '{{FrontSide}}<hr id="answer">{{NativeText}}',
            }
        ])
    
    media_files_data = {}
    cards_created = 0

    for card in cards_data:
        foreign_text = card['foreign_text']
        native_text = card['native_text']

        print(f"\nProcessing card: '{foreign_text[:50]}...'")

        text_hash = generate_audio_hash(foreign_text)
        audio_filename = f"{text_hash}.mp3"

        audio_data = get_cached_audio(text_hash)

        if not audio_data:
            print(f" Generating new audio with ElevenLabs in {target_language}...")
            audio_data = generate_audio_elevenlabs(foreign_text, target_language)

    native_lang = LANGUAGE_MAP.get(native_language)

    if not native_lang:
        raise ValueError(f"Unsupported native language code: {native_language}")

    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Created temporary directory: {temp_dir}")

        apkg_path = os.path.join(temp_dir, 'deck.apkg')
        with open(apkg_path, 'wb') as f:
            f.write(apkg_file)

        extract_dir = os.path.join(temp_dir, 'extracted')
        os.makedirs(extract_dir)

        with zipfile.ZipFile(apkg_path, 'r') as zip_ref:
            print(f"files in .apkg: {zip_ref.namelist()}")
            zip_ref.extractall(extract_dir)

        files_in_extract = os.listdir(extract_dir)
        print(f"Extracted files: {files_in_extract}")

        db_path = os.path.join(extract_dir, 'collection.anki21')
        if not os.path.exists(db_path):
            db_path = os.path.join(extract_dir, 'collection.anki2')
            print(f"Using collection.anki2 fallback")
        else:
            print(f"Using collection.anki21 (modern format)")

        print(f"Database path: {db_path}, exists: {os.path.exists(db_path)}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT id, flds FROM notes")
        notes = cursor.fetchall()
        print(f"Found {len(notes)} notes in deck")

        conn.close()

        deck_id = int(hashlib.md5(f"audio_practice_{target_language}_{native_language}".encode()).hexdigest()[:8], 16)
        deck = genanki.Deck(deck_id, f"Audio Practice Deck ({target_language.upper()} - {native_language.upper()})")

        model_id = int(hashlib.md5(f"audio_practice_model_{target_language}_{native_language}".encode()).hexdigest()[:8], 16)
        model = genanki.Model(
            model_id,
            'Audio Practice Model',
            fields=[
                {'name': 'Audio'},
                {'name': 'ForeignText'},
                {'name': 'NativeText'},
            ],
            templates=[
                {
                    'name': 'Audio to Native',
                    'qfmt': '{{Audio}}',
                    'afmt': '{{FrontSide}}<hr id="answer">{{NativeText}}',
                }
            ])
        media_files_data = {}
        cards_created = 0

        for note_id, fields_str in notes:
            fields = fields_str.split('\x1f')
            print(f"\nNote {note_id}: {len(fields)} fields")

            foreign_text = None
            native_text = None

            for i, field in enumerate(fields):
                clean_text = re.sub('<[^<]+?>', '', field).strip()

                if not clean_text:
                    continue

                detected_lang = detect_field_language(clean_text)
                print(f"  Field {i}: '{clean_text[:50]}...' -> detected: {detected_lang}")

                if detected_lang == native_lang:
                    if not native_text:
                        native_text = clean_text
                        print(f"  ✓ Found native language field ({native_language}) - NO AUDIO")

                else:
                    if not foreign_text:
                        foreign_text = clean_text
                        print(f"  ✓ Found non-native field - WILL GENERATE AUDIO")

            if foreign_text and native_text:
                print(f" Creating audio practice card...")
                text_hash = generate_audio_hash(foreign_text)
                audio_filename = f"{text_hash}.mp3"

                audio_data = get_cached_audio(text_hash)

                if not audio_data:
                    print(f"    Generating new audio with ElevenLabs in {target_language}...")
                    audio_data = generate_audio_elevenlabs(foreign_text, target_language)

                    if audio_data:
                        print(f"    Audio generated! Size: {len(audio_data)} bytes")
                        cache_audio(text_hash, foreign_text, audio_data, target_language)
                    else:
                        print(f"    ERROR: Audio generation failed!")
                        continue
                else:
                    print(f"    Using cached audio, size: {len(audio_data)} bytes")

                if audio_data:
                    media_files_data[audio_filename] = audio_data

                    note = genanki.Note(
                        model=model,
                        fields=[f'[sound:{audio_filename}]', foreign_text, native_text]
                    )
                    deck.add_note(note)
                    cards_created += 1
                    print(f"    Card created!")

        print(f"\nTotal cards created: {cards_created}")

        package = genanki.Package(deck)

        media_dir = os.path.join(temp_dir, 'media')
        os.makedirs(media_dir)

        media_file_paths = []
        for filename, data in media_files_data.items():
            file_path = os.path.join(media_dir, filename)
            with open(file_path, 'wb') as f:
                f.write(data)
            media_file_paths.append(file_path)

        package.media_files = media_file_paths

        output_path = os.path.join(temp_dir, 'audio_practice.apkg')
        package.write_to_file(output_path)

        with open(output_path, 'rb') as f:
            output_data = f.read()

        return output_data, cards_created

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "Anki Audio Generator API"})

@app.route('/api/process', methods=['POST'])
def process():
    try:
        print("=== Process request received ===")
        print(f"Request files: {request.files}")
        print(f"Request form: {request.form}")

        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400

        file = request.files['file']
        target_language = request.form.get('language')
        native_language = request.form.get('native_language', 'en')

        print(f"File: {file.filename}")
        print(f"Target Language (audio): {target_language}")
        print(f"Native Language (no audio): {native_language}")

        if not target_language:
            print("ERROR: No target language specified")
            return jsonify({"status": "error", "message": "No target language specified"}), 400

        file_data = file.read()
        print(f"File size: {len(file_data)} bytes")

        print("Processing deck...")
        output_data, cards_created = process_deck(file_data, target_language, native_language)

        print(f"Processing complete! Created {cards_created} cards")

        return send_file(
            BytesIO(output_data),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='audio_practice_deck.apkg'
        )

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
