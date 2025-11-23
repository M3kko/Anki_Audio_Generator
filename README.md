# Features

- Automatic language detection for cards
- AI-powered audio generation (ElevenLabs)
- Audio caching via Supabase
- Two-step preview and processing
- Multi-language support (60+ languages)

# Tech Stack

**Backend**: Flask (Python) REST API with CORS support

**Language Detection**: Lingua library for automatic field language identification

**Audio Generation**: ElevenLabs API (eleven_turbo_v2_5 model) for text-to-speech

**Caching**: Supabase (PostgreSQL + Storage) for audio file caching and deduplication

**Anki Integration**: genanki library for programmatic deck creation

**Deployment**: Gunicorn WSGI server, Railway/Vercel compatible

# Deployment Instructions

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd Anki_Audio_Generator
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set environment variables**
   Create a `.env` file with:
   ```
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   SUPABASE_URL=your_supabase_project_url
   SUPABASE_KEY=your_supabase_anon_key
   PORT=5000
   ```

4. **Set up Supabase**
   - Create a Supabase project
   - Create a storage bucket named `audio-files`
   - Create a table named `audio_cache` with columns:
     - `text_hash` (text, primary key)
     - `text` (text)
     - `file_path` (text)
     - `language` (text)
     - `created_at` (timestamp)

5. **Run locally**
   ```bash
   python app.py
   ```
   Access at `http://localhost:5000`

6. **Deploy to Railway/Vercel**
   - Connect your GitHub repository
   - Add environment variables in dashboard
   - Deploy automatically on push
