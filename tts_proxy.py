#!/usr/bin/env python3
"""HTTP server that serves static files, proxies Edge TTS, and powers AI media research."""

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request, urllib.parse, os, sys, mimetypes, asyncio, json, re, time

# YouTube transcript fetcher using direct HTTP (no external deps)
def fetch_youtube_transcript(video_id):
    # Method 1: youtubetranscript.com (fast, reliable)
    try:
        url = f"https://youtubetranscript.com/?v={video_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            if isinstance(data, list) and len(data):
                return ' '.join(t.get('text','') for t in data)
    except Exception:
        pass
    # Method 2: try fetching from YouTube page directly
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US'})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode('utf-8', errors='ignore')
            # Extract caption data from ytInitialPlayerResponse
            import re as _re
            match = _re.search(r'"captionTracks"\s*:\s*\[(.*?)\][^]]*?"baseUrl"\s*:\s*"([^"]+)"', html)
            if match:
                caption_url = match.group(2).replace('\u0026', '&')
                req2 = urllib.request.Request(caption_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req2, timeout=10) as r2:
                    xml = r2.read().decode('utf-8', errors='ignore')
                    texts = _re.findall(r'<text[^>]*>([^<]+)</text>', xml)
                    if texts:
                        return ' '.join(texts)
    except Exception:
        pass
    return ''
HAS_YT = True

PORT = int(os.environ.get("PORT", 8080))
DIR = os.path.dirname(os.path.abspath(__file__))

# Default voices: male for both characters
DEFAULT_VOICE_REX = "en-GB-RyanNeural"       # British male — deep adult man
DEFAULT_VOICE_LOU = "en-US-GuyNeural"        # American male — younger, passionate

class ProxyHandler(BaseHTTPRequestHandler):

    def add_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')

    def do_OPTIONS(self):
        self.send_response(200)
        self.add_cors()
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]

        # --- EDGE TTS PROXY ENDPOINT ---
        if path == '/tts-proxy':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                text = params.get('text', [''])[0]
                voice = params.get('voice', [DEFAULT_VOICE_REX])[0]

                if not text:
                    self.send_error(400, 'Missing text parameter')
                    return

                # Generate audio using Edge TTS
                try:
                    from edge_tts import Communicate
                except ImportError:
                    self.send_error(500, 'edge-tts not installed. Run: pip3 install edge-tts')
                    return

                async def generate():
                    data = b''
                    comm = Communicate(text[:500], voice)
                    async for chunk in comm.stream():
                        if chunk['type'] == 'audio':
                            data += chunk['data']
                    return data

                loop = asyncio.new_event_loop()
                try:
                    audio_data = loop.run_until_complete(generate())
                finally:
                    loop.close()

                if not audio_data:
                    self.send_error(502, 'Empty audio from Edge TTS')
                    return

                self.send_response(200)
                self.send_header('Content-Type', 'audio/mpeg')
                self.send_header('Content-Length', str(len(audio_data)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(audio_data)
                return

            except Exception as e:
                print(f'[proxy] error: {e}', file=sys.stderr)
                self.send_error(502, str(e))
                return

        # --- MEDIA RESEARCH: per-scene image search ---
        if path == '/media-research-scenes':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                topic = params.get('topic', [''])[0]
                scenes_json = params.get('scenes', ['[]'])[0]
                max_per = int(params.get('max_per', ['3'])[0])
                notes = params.get('notes', [''])[0]

                scenes = json.loads(scenes_json)
                results = {}

                # Build full script context for better keyword extraction
                full_text = ' '.join(s.get('line','') for s in scenes)
                product = identify_product(topic, full_text, notes)

                for i, scene in enumerate(scenes):
                    text = scene.get('line', '')
                    # Get adjacent scene context for better queries
                    prev_text = scenes[i-1].get('line','') if i > 0 else ''
                    next_text = scenes[i+1].get('line','') if i < len(scenes)-1 else ''
                    keywords = extract_keywords(text, topic, product, prev_text, next_text)
                    if not keywords:
                        results[str(i)] = []
                        continue

                    query = f"{product} {keywords}" if product else f"{topic} {keywords}"
                    # Bias toward official product sources when topic suggests a brand
                    if product and len(product.split()) <= 4:
                        query = f"{query} official product photo"
                    images = search_images(query, max_results=max_per)
                    results[str(i)] = images
                    # Brief pause to avoid rate-limiting DuckDuckGo
                    if i < len(scenes) - 1:
                        time.sleep(0.4)

                body = json.dumps({'ok': True, 'scenes': results, 'product': product}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            except Exception as e:
                print(f'[media-research-scenes] error: {e}', file=sys.stderr)
                body = json.dumps({'ok': False, 'error': str(e)}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

        # --- YOUTUBE TOPIC EXTRACTION ---
        if path == '/youtube-topics':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                url = params.get('url', [''])[0]
                api_key = params.get('api_key', [''])[0]

                if not url:
                    body = json.dumps({'ok': False, 'error': 'Missing YouTube URL'}).encode('utf-8')
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # Extract video ID
                import re as _re
                vid_match = _re.search(r'(?:v=|/)([\w-]{11})', url)
                if not vid_match:
                    body = json.dumps({'ok': False, 'error': 'Invalid YouTube URL'}).encode('utf-8')
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                video_id = vid_match.group(1)

                if not HAS_YT:
                    body = json.dumps({'ok': False, 'error': 'youtube-transcript-api not installed. Run: pip3 install youtube-transcript-api'}).encode('utf-8')
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # Get transcript using built-in HTTP fetcher
                full_text = fetch_youtube_transcript(video_id)
                if not full_text or len(full_text) < 50:
                    body = json.dumps({'ok': False, 'error': 'No transcript available — video may have captions disabled or is too short'}).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # Use Claude to extract topics if API key provided
                if api_key and len(full_text) > 100:
                    topics = extract_topics_with_claude(full_text, api_key)
                else:
                    topics = extract_topics_fallback(full_text)

                body = json.dumps({'ok': True, 'topics': topics, 'title': video_id}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            except Exception as e:
                print(f'[youtube-topics] error: {e}', file=sys.stderr)
                body = json.dumps({'ok': False, 'error': str(e)}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

        # --- IMAGE PROXY: fetch remote images with CORS headers ---
        if path == '/image-proxy':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                url = params.get('url', [''])[0]

                if not url:
                    self.send_error(400, 'Missing url parameter')
                    return

                # Fetch the image server-side
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                    content_type = resp.headers.get('Content-Type', 'image/jpeg')

                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'public, max-age=3600')
                self.add_cors()
                self.end_headers()
                self.wfile.write(data)
                return

            except Exception as e:
                print(f'[image-proxy] error: {e}', file=sys.stderr)
                self.send_error(502, str(e))
                return

        # --- MEDIA SEARCH: simple query (for search bar, no keyword extraction) ---
        if path == '/media-search':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                query = params.get('query', [''])[0]
                max_results = int(params.get('max_results', ['8'])[0])

                if not query:
                    body = json.dumps({'ok': False, 'error': 'Missing query'}).encode('utf-8')
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # Simple direct search — use query as-is, no keyword extraction
                images = search_images(query, max_results=max_results)
                body = json.dumps({'ok': True, 'results': images}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            except Exception as e:
                print(f'[media-search] error: {e}', file=sys.stderr)
                body = json.dumps({'ok': False, 'error': str(e)}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

        # --- MEDIA RESEARCH: single query image search ---
        if path == '/media-research':
            try:
                qs = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                query = params.get('query', [''])[0]
                max_results = int(params.get('max_results', ['6'])[0])

                if not query:
                    body = json.dumps({'ok': False, 'error': 'Missing query'}).encode('utf-8')
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.add_cors()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                images = search_images(query, max_results=max_results)
                body = json.dumps({'ok': True, 'results': images}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

            except Exception as e:
                print(f'[media-research] error: {e}', file=sys.stderr)
                body = json.dumps({'ok': False, 'error': str(e)}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.add_cors()
                self.end_headers()
                self.wfile.write(body)
                return

        # --- STATIC FILE SERVING ---
        filepath = os.path.join(DIR, path.lstrip('/'))
        if path == '/' or not os.path.isfile(filepath):
            filepath = os.path.join(DIR, 'rex_lou_studio_merged.html')

        if not os.path.isfile(filepath):
            self.send_error(404)
            return

        try:
            with open(filepath, 'rb') as f:
                data = f.read()

            mime, _ = mimetypes.guess_type(filepath)
            if not mime:
                mime = 'application/octet-stream'

            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.add_cors()
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self.send_error(500, str(e))


# ── IMAGE SEARCH UTILITIES ──

# Product detail keywords that suggest we should search
PRODUCT_PATTERNS = [
    # Pricing
    r'\b(price|pricing|cost|discount|sale|deal|bundle|bargain|expensive|cheap|affordable|budget|premium)\b',
    r'\$\d+(?:[.,]\d+)?[KMB]?',                           # $999, $1.2K, $5M
    r'\b\d+\s*dollars?\b',                                # "1200 dollars"
    # Specs & tech
    r'\b(specs?|specification|storage|\d+GB|\d+TB|RAM|battery|mAh|processor|chip|core|GPU|CPU|camera|\d+MP|megapixel|display|screen|resolution|refresh|\d+Hz|OLED|LCD|QLED|LED|port|USB-C?|HDMI|bluetooth|wi.?fi|airplay|chromecast|streaming|weight|dimension|size|inches?|hours?)\b',
    r'\b\d+\s*inch(?:es)?\b',                             # "27 inch"
    # Product categories
    r'\b(suitcase|portable|foldable|folding|collapsible|briefcase|carry.?on|luggage|rolling|wheeled|travel|backpack|stand|tripod|mount|case|enclosure)\b',
    r'\b(tv|television|monitor|projector|speaker|headphone|earbud|watch|tablet|laptop|phone|smartphone|console|controller|cable|charger|adapter|dongle|dock|hub)\b',
    # Features & states
    r'\b(colors?|colou?rs?|variant|model|edition|version|series|generation|gen)\b',
    r'\b(available|availability|release|shipping|launch|stock|pre.?order|back.?order|unbox(?:ing)?|review|hands.?on|first look)\b',
    r'\b(feature|design|build|material|aluminum|titanium|glass|plastic|carbon|leather|fabric|mesh|metal|wood)\b',
    r'\b(comparison|versus|compared|alternative|competitor|rival)\b',
    r'\b(review|rating|score|benchmark|performance)\b',
    r'\b(photo|video|image quality|low light|zoom|wide angle|ultra.?wide|telephoto|portrait|macro)\b',
    # Unique / weird / viral products
    r'\b(bizarre|weird|strange|unusual|unique|innovative|interesting|cool|wild|insane|unbelievable|next.?level|viral)\b',
]

def identify_product(topic, full_text, notes):
    """Identify the main product from topic, script text, and notes."""
    candidates = []

    # Check for known product indicators in topic
    if topic:
        candidates.append(topic)

    # Look for product names in notes (often the most specific)
    if notes:
        # Extract capitalized phrases that look like product names
        caps = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b', notes)
        for c in caps:
            if len(c) > 5 and c.lower() not in {'This', 'The', 'That', 'These', 'Those', 'Great', 'Unusual', 'Battery', 'Airplay'}:
                candidates.append(c)

    # Fallback: use topic as product
    if candidates:
        # Return the most specific (shortest non-generic) candidate
        best = min(candidates, key=lambda x: len(x))
        return best if len(best) > 2 else topic

    return topic

def extract_keywords(text, topic, product='', prev_text='', next_text=''):
    """Extract key product search terms from scene text and context."""
    text_lower = text.lower()
    matched = set()

    # Also check adjacent scenes for context
    context_lower = (prev_text + ' ' + next_text).lower()

    for pattern in PRODUCT_PATTERNS:
        found = re.findall(pattern, text_lower)
        matched.update(found)
        # Also check context for product-related terms
        ctx_found = re.findall(pattern, context_lower)
        matched.update(ctx_found)

    # Filter out noise words
    stopwords = {'the','and','for','but','not','its','all','in','to','of','is','it','at','on','be','or','as','an','we','us','no','so','do','go','he','me','my','up','if','by','this','that','with','from','has','can','you','was','are','have','had','been','will','would','could','should','may','just','like','about','also','get','got'}
    specific = [m for m in matched if len(m) > 1 and m not in stopwords]

    if specific:
        # Pick the 3 longest (most specific) terms
        best = sorted(set(specific), key=len, reverse=True)[:3]
        # Always include product context if available
        if product:
            return f"{product} {' '.join(best)}"
        if topic:
            return f"{topic} {' '.join(best)}"
        return ' '.join(best)

    # Fallback: extract meaningful words from the scene text itself
    if product:
        meaningful = [w.strip('.,!?;:\"\'()[]{}') for w in text_lower.split()
                     if len(w) > 2 and w not in stopwords][:5]
        if meaningful:
            return f"{product} {' '.join(meaningful)}"
        return product
    if topic:
        meaningful = [w.strip('.,!?;:\"\'()[]{}') for w in text_lower.split()
                     if len(w) > 2 and w not in stopwords][:5]
        if meaningful:
            return f"{topic} {' '.join(meaningful)}"
        return topic

    return ''


def extract_topics_with_claude(transcript_text, api_key):
    """Use Claude to extract topics from a YouTube transcript."""
    try:
        prompt = f"""Extract the main topics discussed in this video transcript. Return ONLY a JSON array of topic strings. Max 10 topics, each 2-6 words. Example: ["Dropshipping Business", "AI Tools", "Content Creation"]

Transcript (first 3000 chars):
{transcript_text[:3000]}"""

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps({
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 500,
                'messages': [{'role': 'user', 'content': prompt}]
            }).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01'
            }
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            raw = data.get('content', [{}])[0].get('text', '[]')
            # Extract JSON array
            match = re.search(r'\[[\s\S]*\]', raw)
            if match:
                topics = json.loads(match.group(0))
                return topics[:10]
            return []
    except Exception as e:
        print(f'[claude-topics] error: {e}', file=sys.stderr)
        return extract_topics_fallback(transcript_text)


def extract_topics_fallback(transcript_text):
    """Fallback: use keyword frequency to guess topics."""
    # Look for capitalized phrases that repeat
    words = transcript_text.split()
    # Get common bigrams and trigrams with capital letters
    topics = set()
    topic_indicators = [
        r'(?:how to|guide to|tips for|best|top \d+|ways to|secrets of|truth about)\s+([A-Z][a-z]+(?:\s+[A-Z]?[a-z]+){0,4})',
        r'(?:side hustle|business idea|income stream|make money|earn|profit)(?:s|ing)?\s*(?:with|from|using|as|in)?\s*([A-Z][a-z]+(?:\s+[A-Z]?[a-z]+){0,4})',
        r'\b([A-Z][a-z]+ (?:Automation|Marketing|Design|Services?|Creation|Writing|Editing|Management|Consulting|Development|Trading|Investing|Products?|Courses?|Apps?))\b',
    ]

    for pattern in topic_indicators:
        found = re.findall(pattern, transcript_text[:3000])
        topics.update(f[:60] for f in found if len(f) > 5)

    if topics:
        return list(topics)[:10]

    # Ultimate fallback: return section headers
    return ['Featured Topics'] if not topics else list(topics)[:10]


def search_images(query, max_results=6):
    """Search for images using DuckDuckGo."""
    if not query or not query.strip():
        return []
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = []
            for r in ddgs.images(query, max_results=max_results):
                thumb = r.get('thumbnail', '') or r.get('image', '')
                image_url = r.get('image', '') or thumb
                if not image_url:
                    continue
                results.append({
                    'url': image_url,
                    'thumbnail': thumb or image_url,
                    'title': (r.get('title', '') or '')[:120],
                    'source': (r.get('source', '') or '')[:80],
                })
            return results
    except ImportError:
        print('[search] ddgs not installed. Run: pip3 install ddgs', file=sys.stderr)
        return []
    except Exception as e:
        print(f'[search] error for "{query[:60]}": {e}', file=sys.stderr)
        return []


if __name__ == '__main__':
    os.system('lsof -ti:8080 | xargs kill -9 2>/dev/null')
    print(f'✅ Edge TTS + Media Research Server → http://localhost:{PORT}', flush=True)
    httpd = HTTPServer(('0.0.0.0', PORT), ProxyHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
