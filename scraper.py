"""
Scraper for 1tamilmv.solar (phpBB forum)
"""

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


BASE_URL = "https://www.1tamilmv.solar"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL + "/",
}


# ============================================================
# FETCH: LIST OF MOVIES (from homepage)
# ============================================================
def fetch_movie_list(limit=None):
    """
    Extract movie topics from homepage.
    Returns [{movie_id, title, url}, ...]
    """
    movies = []
    seen_ids = set()

    try:
        resp = requests.get(BASE_URL, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch homepage: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # Topic URL pattern: /forums/topic/200317-slug/  OR  /index.php?/forums/topic/200317-slug/
    pattern = re.compile(r"/forums/topic/(\d+)[-/]", re.IGNORECASE)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = pattern.search(href)
        if not match:
            continue

        topic_id = match.group(1)
        if topic_id in seen_ids:
            continue

        # ---- Extract title ----
        # phpBB often wraps the title inside the same <a> but sometimes it's plain.
        # Prefer text; if too short, try parent elements.
        title = a.get_text(" ", strip=True)

        # Some phpBB themes put the title in the anchor, others in a <span> around it.
        if len(title) < 15:
            parent = a.find_parent(["div", "li", "span"])
            if parent:
                # Look for a bigger sibling link with same href
                sib = parent.find("a", href=re.compile(re.escape(href)))
                if sib:
                    title = sib.get_text(" ", strip=True)

        # Skip junk like "Languages" / "Login" / short nav links
        if len(title) < 15:
            continue
        if title.lower() in {"languages", "login", "register", "faq", "search"}:
            continue

        # Strip trailing bracket stuff for cleaner titles (keep original for detail page)
        seen_ids.add(topic_id)
        movies.append({
            "movie_id": topic_id,
            "title":    title,
            "url":      urljoin(BASE_URL, href),
        })

        if limit and len(movies) >= limit:
            break

    return movies


# ============================================================
# FETCH: MOVIE DETAILS (magnet, poster, screenshots, quality)
# ============================================================
def fetch_movie_details(movie):
    """
    Visit topic page and extract everything.
    """
    details = []
    magnet = None
    quality = None
    screenshots = []
    poster = None
    poster_url = None

    try:
        resp = requests.get(movie["url"], headers=HEADERS, timeout=25)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # -----------------------------------------------------
        # 1. CLEAN TITLE — use the topic header <h1>
        # -----------------------------------------------------
        h1 = soup.find("h1")
        if h1:
            clean_title = h1.get_text(" ", strip=True)
            if len(clean_title) > 5:
                movie["title"] = clean_title

        # -----------------------------------------------------
        # 2. MAGNET LINKS
        # 1tamilmv shows "MAGNET" as anchor text pointing to magnet:
        # Also raw magnet: may appear in href.
        # -----------------------------------------------------
        magnets = []

        # a) Real magnet: hrefs
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("magnet:"):
                magnets.append(a["href"])

        # b) Plain-text magnet in HTML (rare fallback)
        if not magnets:
            magnets = re.findall(
                r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^"\'<>\s]*',
                html
            )

        if magnets:
            magnet = magnets[0]

        # -----------------------------------------------------
        # 3. SCREENSHOTS
        # Real screenshots are on postimages.org / imgur / ibb / imagebam etc.
        # Thumbnails may also be on the forum itself → skip tiny ones.
        # -----------------------------------------------------
          # -----------------------------------------------------
        # 3. SCREENSHOTS — 3 ways to find them
        # -----------------------------------------------------
        img_hosts = ("postimg", "imgur", "ibb.co", "imagebam", "imgbox",
                     "imagevenue", "pixhost", "turboimagehost", "fastpic",
                     "imgbb", "ibb", "freeimage", "imagehost")

        screenshot_set = set()

        # --- A) Direct <img> tags with external host ---
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            low = src.lower()
            if not any(h in low for h in img_hosts):
                continue
            if src.startswith("//"):
                src = "https:" + src
            elif src.startswith("/"):
                src = BASE_URL + src
            screenshot_set.add(src)

        # --- B) <a href> pointing to image hosts (thumbnails linked to full) ---
        for a in soup.find_all("a", href=True):
            href = a["href"]
            low = href.lower()
            if not any(h in low for h in img_hosts):
                continue
            # Extract the image URL from within the anchor if possible
            inner_img = a.find("img")
            if inner_img:
                src = inner_img.get("src") or inner_img.get("data-src") or ""
                if src:
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = BASE_URL + src
                    screenshot_set.add(src)
            # Also add the href itself if it looks like a direct image
            if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", low):
                screenshot_set.add(href)

        # --- C) Regex fallback on raw HTML ---
        if not screenshot_set:
            raw_matches = re.findall(
                r'https?://[^\s"\'<>]*(?:postimg|imgur|ibb\.co|imagebam|imgbox|'
                r'imagevenue|pixhost|turboimagehost|fastpic|imgbb)[^\s"\'<>]*',
                html,
                re.IGNORECASE
            )
            for url in raw_matches:
                # Strip BBCode artifacts
                url = url.rstrip("[/img]").rstrip(")")
                if re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url.lower()):
                    screenshot_set.add(url)

        screenshots = list(screenshot_set)

        if screenshots:
            poster_url = screenshots[0]

        # -----------------------------------------------------
        # 4. QUALITY — look for 4K / 1080p / 720p in title
        # -----------------------------------------------------
        title_for_quality = movie["title"] + " " + soup.get_text(" ", strip=True)[:3000]
        q_match = re.search(
            r"\b(4K|2160p|1080p|720p|480p)\b",
            title_for_quality,
            re.IGNORECASE
        )
        if q_match:
            quality = q_match.group(1)

        # -----------------------------------------------------
        # 5. BUILD DETAILS LIST
        # -----------------------------------------------------
        if magnet:
            details.append({
                "type":        "magnet",
                "title":       movie["title"],
                "magnet":      magnet,
                "quality":     quality,
                "screenshots": screenshots[:10],
                "screenshot":  poster_url,
            })
        else:
            details.append({
                "type":        "none",
                "title":       movie["title"],
                "quality":     quality,
                "screenshots": screenshots[:10],
                "screenshot":  poster_url,
                "message":     "No magnet link found on topic page",
            })

    except Exception as e:
        details.append({
            "type":    "error",
            "title":   movie["title"],
            "message": f"Error fetching details: {e}",
        })

    movie.update({
        "poster":      poster_url,
        "screenshots": screenshots[:10],
        "magnet":      magnet,
        "quality":     quality,
        "details":     details,
    })
    return movie


# ============================================================
# TEST
# ============================================================
if __name__ == "__main__":
    print("Testing scraper...")
    movies = fetch_movie_list(limit=3)
    print(f"Found {len(movies)} movies\n")

    for m in movies:
        print(f"  ID:    {m['movie_id']}")
        print(f"  Title: {m['title'][:80]}")
        print(f"  URL:   {m['url'][:100]}")
        print()

    if movies:
        print("Fetching details for first movie...")
        enriched = fetch_movie_details(movies[0])
        print(f"  Title:       {enriched['title'][:80]}")
        print(f"  Magnet:      {(enriched['magnet'] or 'NONE')[:80]}")
        print(f"  Quality:     {enriched['quality']}")
        print(f"  Poster:      {(enriched['poster'] or 'NONE')[:80]}")
        print(f"  Screenshots: {len(enriched['screenshots'])}")