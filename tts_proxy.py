#!/usr/bin/env python3
"""HTTP server that serves static files, proxies Edge TTS, and powers AI media research."""

from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request, urllib.parse, os, sys, mimetypes, asyncio, json, re, time

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

                scenes = json.loads(scenes_json)
                results = {}

                for i, scene in enumerate(scenes):
                    text = scene.get('line', '')
                    # Build a search query from topic + scene text
                    keywords = extract_keywords(text, topic)
                    if not keywords:
                        results[str(i)] = []
                        continue

                    query = f"{topic} {keywords}" if topic else keywords
                    # Bias toward official product sources when topic suggests a brand
                    if topic and len(topic.split()) <= 4:
                        query = f"{query} official product photo"
                    images = search_images(query, max_results=max_per)
                    results[str(i)] = images
                    # Brief pause to avoid rate-limiting DuckDuckGo
                    if i < len(scenes) - 1:
                        time.sleep(0.4)

                body = json.dumps({'ok': True, 'scenes': results}).encode('utf-8')
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

def extract_keywords(text, topic):
    """Extract key product search terms from scene text."""
    text_lower = text.lower()
    matched = set()

    for pattern in PRODUCT_PATTERNS:
        found = re.findall(pattern, text_lower)
        matched.update(found)

    # Filter out noise words
    stopwords = {'the','and','for','but','not','its','all','in','to','of','is','it','at','on','be','or','as','an','we','us','no','so','do','go','he','me','my','up','if','by','this','that','with','from','has','can','you','was','are','have','had','been','will','would','could','should','may','just','like','about','also','get','got'}
    specific = [m for m in matched if len(m) > 1 and m not in stopwords]

    if specific:
        # Pick the 3 longest (most specific) terms
        best = sorted(set(specific), key=len, reverse=True)[:3]
        # Always include topic context if available
        if topic:
            return f"{topic} {' '.join(best)}"
        return ' '.join(best)

    # Fallback: extract meaningful words from the scene text itself
    if topic:
        # Combine topic with significant words from the text
        meaningful = [w.strip('.,!?;:"\'()[]{}') for w in text_lower.split() 
                     if len(w) > 2 and w not in stopwords][:5]
        if meaningful:
            return f"{topic} {' '.join(meaningful)}"
        return topic

    return ''


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
