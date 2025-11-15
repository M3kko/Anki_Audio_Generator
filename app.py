from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import os
import tempfile
import hashlib
import zipfile
import sqlite3
import re
from io import BytesIO
import genanki
from lingua import Language, LanguageDetectorBuilder
from elevenlabs.client import ElevenLabs
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

# Initialize clients
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_KEY")
)

elevenlabs_client = ElevenLabs(
    api_key=os.environ.get("ELEVENLABS_API_KEY")
)

# Language detector
detector = LanguageDetectorBuilder.from_all_languages().build()

# Map language codes to Lingua
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
    """Detect the language of a text field"""
    if not text or len(text.strip()) == 0:
        return None

    # Remove HTML tags for better detection
    clean_text = re.sub('<[^<]+?>', '', text)

    if len(clean_text.strip()) == 0:
        return None

    detected = detector.detect_language_of(clean_text)
    return detected


def has_audio_tag(text):
    """Check if field already has [sound:...] tag"""
    return bool(re.search(r'\[sound:[^\]]+\]', text))


def generate_audio_hash(text):
    """Generate hash for text to use as cache key"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def get_cached_audio(text_hash):
    """Check if audio exists in Supabase cache"""
    try:
        result = supabase.table('audio_cache').select('*').eq('text_hash', text_hash).execute()
        if result.data and len(result.data) > 0:
            # Download audio from storage
            file_path = result.data[0]['file_path']
            audio_data = supabase.storage.from_('audio-files').download(file_path)
            return audio_data
        return None
    except Exception as e:
        print(f"Cache lookup error: {e}")
        return None


def cache_audio(text_hash, text, audio_data, language):
    """Store audio in Supabase cache"""
    try:
        filename = f"{text_hash}.mp3"

        # Upload to storage
        supabase.storage.from_('audio-files').upload(
            filename,
            audio_data,
            {'content-type': 'audio/mpeg'}
        )

        # Store metadata in table
        supabase.table('audio_cache').insert({
            'text_hash': text_hash,
            'text': text[:500],  # Store truncated text for reference
            'file_path': filename,
            'language': language
        }).execute()

    except Exception as e:
        print(f"Cache storage error: {e}")


def generate_audio_elevenlabs(text, language):
    """Generate audio using ElevenLabs API"""
    try:
        # Use default voice (you can customize per language)
        audio_generator = elevenlabs_client.generate(
            text=text,
            voice="Adam",  # Default voice
            model="eleven_multilingual_v2"
        )

        # Convert generator to bytes
        audio_bytes = b''.join(audio_generator)
        return audio_bytes

    except Exception as e:
        print(f"ElevenLabs error: {e}")
        return None


def process_deck(apkg_file, target_language):
    """Process Anki deck and add audio"""
    print(f"=== process_deck called with language: {target_language} ===")

    target_lang = LANGUAGE_MAP.get(target_language)
    if not target_lang:
        raise ValueError(f"Unsupported language: {target_language}")

    print(f"Target language mapped to: {target_lang}")

    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Created temp directory: {temp_dir}")
        # Extract .apkg file
        apkg_path = os.path.join(temp_dir, 'deck.apkg')
        with open(apkg_path, 'wb') as f:
            f.write(apkg_file)

        # Unzip the .apkg
        extract_dir = os.path.join(temp_dir, 'extracted')
        os.makedirs(extract_dir)

        with zipfile.ZipFile(apkg_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        # Read deck using SQLite
        db_path = os.path.join(extract_dir, 'collection.anki2')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all notes
        cursor.execute("SELECT id, flds FROM notes")
        notes = cursor.fetchall()
        print(f"Found {len(notes)} notes in deck")

        audio_files = {}
        updated_count = 0

        for note_id, fields_str in notes:
            fields = fields_str.split('\x1f')  # Anki uses \x1f as field separator
            print(f"\nNote {note_id}: {len(fields)} fields")

            updated_fields = []
            modified = False

            for i, field in enumerate(fields):
                # Skip if already has audio
                if has_audio_tag(field):
                    print(f"  Field {i}: Already has audio, skipping")
                    updated_fields.append(field)
                    continue

                # Detect language
                detected_lang = detect_field_language(field)
                clean_text = re.sub('<[^<]+?>', '', field).strip()
                print(f"  Field {i}: '{clean_text[:50]}...' -> detected: {detected_lang}, target: {target_lang}")

                if detected_lang == target_lang:
                    # This field matches target language, generate audio
                    print(f"  ✓ MATCH! Generating audio for this field")
                    clean_text = re.sub('<[^<]+?>', '', field).strip()

                    if clean_text:
                        text_hash = generate_audio_hash(clean_text)
                        print(f"    Text hash: {text_hash}")

                        # Check cache
                        audio_data = get_cached_audio(text_hash)

                        if not audio_data:
                            # Generate new audio
                            print(f"    Generating new audio with ElevenLabs...")
                            audio_data = generate_audio_elevenlabs(clean_text, target_language)

                            if audio_data:
                                print(f"    Audio generated! Size: {len(audio_data)} bytes")
                                cache_audio(text_hash, clean_text, audio_data, target_language)
                            else:
                                print(f"    ERROR: Audio generation failed!")
                        else:
                            print(f"    Using cached audio, size: {len(audio_data)} bytes")

                        if audio_data:
                            # Save audio file
                            audio_filename = f"{text_hash}.mp3"
                            audio_files[audio_filename] = audio_data

                            # Add sound tag to field
                            updated_field = field + f" [sound:{audio_filename}]"
                            updated_fields.append(updated_field)
                            modified = True
                            print(f"    Added sound tag to field")
                        else:
                            updated_fields.append(field)
                    else:
                        updated_fields.append(field)
                else:
                    updated_fields.append(field)

            if modified:
                # Update note in database
                new_fields_str = '\x1f'.join(updated_fields)
                cursor.execute("UPDATE notes SET flds = ? WHERE id = ?", (new_fields_str, note_id))
                updated_count += 1

        conn.commit()
        conn.close()

        # Add audio files to media
        media_dir = extract_dir
        for filename, audio_data in audio_files.items():
            with open(os.path.join(media_dir, filename), 'wb') as f:
                f.write(audio_data)

        # Repackage as .apkg
        output_path = os.path.join(temp_dir, 'output.apkg')
        with zipfile.ZipFile(output_path, 'w') as zip_out:
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, extract_dir)
                    zip_out.write(file_path, arcname)

        # Read output file
        with open(output_path, 'rb') as f:
            output_data = f.read()

        return output_data, updated_count


@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "Anki Audio Generator API"})


@app.route('/api/process', methods=['POST'])
def process():
    try:
        print("=== Process request received ===")
        print(f"Request files: {request.files}")
        print(f"Request form: {request.form}")

        # Get file and language from request
        if 'file' not in request.files:
            print("ERROR: No file in request")
            return jsonify({"status": "error", "message": "No file uploaded"}), 400

        file = request.files['file']
        language = request.form.get('language')

        print(f"File: {file.filename}, Language: {language}")

        if not language:
            print("ERROR: No language specified")
            return jsonify({"status": "error", "message": "No language specified"}), 400

        # Read file data
        file_data = file.read()
        print(f"File size: {len(file_data)} bytes")

        # Process the deck
        print(f"Starting deck processing...")
        output_data, updated_count = process_deck(file_data, language)

        print(f"Processing complete! Updated {updated_count} cards")

        # Return the modified deck
        return send_file(
            BytesIO(output_data),
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name='deck_with_audio.apkg'
        )

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
