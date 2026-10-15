import os
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import lru_cache
import re

import requests
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# ============================================================
# ENV
# ============================================================

load_dotenv()

TAMILMV_URL = os.getenv("TAMILMV_URL", "https://www.1tamilmv.lease/")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
PORT = int(os.getenv("PORT", "5000"))

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__)

# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)

adapter = HTTPAdapter(
    max_retries=retry,
    pool_connections=20,
    pool_maxsize=20
)

session.mount("https://", adapter)
session.mount("http://", adapter)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

# ============================================================
# CACHE
# ============================================================

class Cache:
    def __init__(self, ttl_seconds=CACHE_TTL):
        self.cache = {}
        self.ttl = ttl_seconds
    
    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now())
    
    def clear(self):
        self.cache.clear()

cache = Cache()

# ============================================================
# GLOBAL MOVIE DATA
# ============================================================

movie_list = []
real_dict = {}
is_loading = False
last_update = None

# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    """Render the main UI page"""
    return render_template("index.html")

@app.route("/api/start")
def start_movies():
    """Start fetching movies"""
    global movie_list, real_dict, is_loading, last_update
    
    # Check cache first
    cached_data = cache.get("movie_data")
    if cached_data:
        movie_list, real_dict = cached_data
        last_update = datetime.now()
        return jsonify({
            "status": "success",
            "movies": movie_list,
            "total": len(movie_list),
            "from_cache": True
        })
    
    if is_loading:
        return jsonify({
            "status": "loading",
            "message": "Movies are being fetched... Please wait."
        })
    
    # Start background fetch
    is_loading = True
    
    def fetch_in_background():
        global movie_list, real_dict, is_loading, last_update
        try:
            movies, details = get_movies_parallel()
            if movies:
                cache.set("movie_data", (movies, details))
                movie_list = movies
                real_dict = details
                last_update = datetime.now()
            else:
                logger.warning("No movies found")
        except Exception as e:
            logger.exception("Background fetch failed: %s", e)
        finally:
            is_loading = False
    
    thread = threading.Thread(target=fetch_in_background, daemon=True)
    thread.start()
    
    return jsonify({
        "status": "fetching",
        "message": "Movies are being fetched... Please wait."
    })

@app.route("/api/movies")
def get_movies():
    """Get the list of movies"""
    global movie_list, real_dict, last_update
    
    if not movie_list:
        # Try to load from cache
        cached_data = cache.get("movie_data")
        if cached_data:
            movie_list, real_dict = cached_data
            last_update = datetime.now()
    
    return jsonify({
        "movies": movie_list,
        "total": len(movie_list),
        "last_update": last_update.isoformat() if last_update else None
    })

@app.route("/api/movie/<int:index>")
def get_movie_details(index):
    """Get details for a specific movie"""
    global movie_list, real_dict
    
    if index < 0 or index >= len(movie_list):
        return jsonify({"error": "Movie not found"}), 404
    
    title = movie_list[index]
    details = real_dict.get(title, [])
    
    return jsonify({
        "title": title,
        "details": details
    })

@app.route("/api/status")
def get_status():
    """Get the current status"""
    global is_loading, last_update
    
    return jsonify({
        "is_loading": is_loading,
        "movies_count": len(movie_list),
        "last_update": last_update.isoformat() if last_update else None
    })

@app.route("/api/refresh")
def refresh_movies():
    """Force refresh the movie list"""
    global movie_list, real_dict, is_loading
    
    cache.clear()
    movie_list = []
    real_dict = {}
    is_loading = False
    
    return jsonify({
        "status": "success",
        "message": "Cache cleared. Please fetch movies again."
    })

# ============================================================
# SCRAPER FUNCTIONS
# ============================================================

def get_movies_parallel():
    """Scrape movies using parallel processing - Only Magnet, Screenshot & Title"""
    if not TAMILMV_URL:
        logger.warning("TAMILMV_URL is not configured.")
        return [], {}

    mainUrl = TAMILMV_URL
    movie_list = []
    real_dict = {}
    
    try:
        # Step 1: Fetch main page
        response = session.get(
            mainUrl,
            headers=HEADERS,
            timeout=(10, 20)
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find movie containers
        temps = soup.find_all('div', {'class': 'ipsType_break ipsContained'})
        
        if len(temps) < 13:
            logger.warning("Not enough movies found on the page")
            return [], {}
        
        # Collect movie links
        movie_data = []
        for i in range(min(30, len(temps))):
            try:
                title = temps[i].findAll('a')[0].text.strip()
                link = temps[i].find('a')['href']
                if not link.startswith('http'):
                    link = f'{TAMILMV_URL}{link}'
                movie_data.append((title, link))
            except Exception as e:
                logger.error(f"Error parsing movie {i}: {e}")
                continue
        
        # Step 2: Fetch details in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_movie = {
                executor.submit(get_movie_details_parallel, title, link): (title, link)
                for title, link in movie_data
            }
            
            for future in as_completed(future_to_movie):
                title, link = future_to_movie[future]
                try:
                    details = future.result(timeout=15)
                    movie_list.append(title)
                    real_dict[title] = details
                except Exception as e:
                    logger.error(f"Error fetching details for {title}: {e}")
                    movie_list.append(title)
                    real_dict[title] = [{
                        "title": title,
                        "magnet": None,
                        "screenshot": None,
                        "type": "error",
                        "message": f"Error loading details: {str(e)[:100]}"
                    }]
        
        return movie_list, real_dict
        
    except Exception as e:
        logger.error(f"Error in get_movies_parallel: {e}")
        return [], {}

def get_movie_details_parallel(title, url):
    """
    Get movie details for a single movie (for parallel execution)
    Fetches: Title, Magnet Links, Screenshots
    """
    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=(10, 15)
        )
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Get movie title
        movie_title = soup.find('h1')
        movie_title = movie_title.text.strip() if movie_title else title
        
        # Find magnet links
        mag_links = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('magnet:'):
                mag_links.append(href)
        
        # Find screenshot images - Look for images in the post content
        screenshot_urls = []
        
        # Method 1: Find images inside post content
        post_content = soup.find('div', {'class': 'ipsContained'})
        if post_content:
            images = post_content.find_all('img')
            for img in images:
                src = img.get('src', '')
                # Filter out icons, avatars, etc. - keep actual screenshot images
                if src and not src.endswith('svg') and not 'icon' in src.lower() and not 'avatar' in src.lower():
                    if not src.startswith('http'):
                        src = f'{TAMILMV_URL}{src}'
                    screenshot_urls.append(src)
        
        # Method 2: If no images found, look for attachments
        if not screenshot_urls:
            attachment_divs = soup.find_all('div', {'class': 'ipsAttachment'})
            for div in attachment_divs:
                img = div.find('img')
                if img:
                    src = img.get('src', '')
                    if src:
                        if not src.startswith('http'):
                            src = f'{TAMILMV_URL}{src}'
                        screenshot_urls.append(src)
        
        # Method 3: Look for images in gallery or lightbox
        if not screenshot_urls:
            gallery_images = soup.find_all('img', {'data-ipslightbox': True})
            for img in gallery_images:
                src = img.get('src', '')
                if src:
                    if not src.startswith('http'):
                        src = f'{TAMILMV_URL}{src}'
                    screenshot_urls.append(src)
        
        # Method 4: Find any large images in the page
        if not screenshot_urls:
            all_images = soup.find_all('img')
            for img in all_images:
                src = img.get('src', '')
                # Check if it's a screenshot (usually larger images)
                if src and 'thumbnail' not in src.lower() and 'avatar' not in src.lower():
                    if src.startswith('http') and '1tamilmv' in src:
                        screenshot_urls.append(src)
        
        # Limit screenshots to max 5
        screenshot_urls = screenshot_urls[:5]
        
        # Create movie details with only required fields
        movie_details = []
        
        if mag_links:
            for i, magnet in enumerate(mag_links):
                detail = {
                    "title": movie_title,
                    "magnet": magnet,
                    "screenshot": screenshot_urls[0] if screenshot_urls else None,
                    "screenshots": screenshot_urls,  # All screenshots
                    "type": "magnet",
                    "quality": extract_quality_from_magnet(magnet)
                }
                movie_details.append(detail)
        else:
            # No magnet links found
            movie_details.append({
                "title": movie_title,
                "magnet": None,
                "screenshot": screenshot_urls[0] if screenshot_urls else None,
                "screenshots": screenshot_urls,
                "type": "none",
                "message": "No magnet links available"
            })
        
        return movie_details
        
    except Exception as e:
        logger.error(f"Error retrieving details for {title}: {e}")
        return [{
            "title": title,
            "magnet": None,
            "screenshot": None,
            "screenshots": [],
            "type": "error",
            "message": f"Error loading details: {str(e)[:100]}"
        }]

def extract_quality_from_magnet(magnet):
    """Extract quality (1080p, 720p, etc.) from magnet link or title"""
    qualities = ['2160p', '1080p', '720p', '480p', '4K', 'HD', 'FHD', 'UHD']
    for q in qualities:
        if q.lower() in magnet.lower():
            return q
    return 'Unknown'

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )