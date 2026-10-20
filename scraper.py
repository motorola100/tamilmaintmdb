"""TamilMV listing/detail scraper with multiple-magnet extraction.

This module stores only metadata. It does not download torrent payloads.
"""
import html
import re
from urllib.parse import urljoin, unquote, parse_qs, urlparse
from difflib import SequenceMatcher
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import requests
from bs4 import BeautifulSoup

DEFAULT_BASE_URL = "https://www.1tamilmv.capital/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


SESSION = requests.Session()

retry = Retry(
    total=3,
    connect=3,
    read=2,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

MAGNET_RE = re.compile(r"magnet:\?[^\"'<>\\\s]+", re.IGNORECASE)
TOPIC_ID_RE = re.compile(r"/(?:forums/)?topic/(\d+)[-/]", re.IGNORECASE)


def _get(url, timeout=(8, 25)):
    response = SESSION.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def fetch_movie_list(base_url=DEFAULT_BASE_URL, limit=30):
    base_url = base_url.rstrip("/") + "/"
    response = _get(base_url)
    soup = BeautifulSoup(response.text, "html.parser")
    listings, seen = [], set()

    # Prefer topic-like links, regardless of the site's current wrapper classes.
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        match = TOPIC_ID_RE.search(href)
        if not match:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if title.casefold() in {
            "languages",
            "login",
            "register",
            "search",
            "home",
            "faq",
        }:
            continue
        if len(title) < 8:
            parent = anchor.find_parent(["article", "li", "div"])
            if parent:
                candidate = parent.find("a", href=True)
                if candidate:
                    title = " ".join(candidate.get_text(" ", strip=True).split())
                    href = candidate.get("href", href)
                    match = TOPIC_ID_RE.search(href) or match
        source_id = match.group(1)
        if source_id in seen or len(title) < 8:
            continue
        if title.lower() in {"login", "register", "search", "home", "faq"}:
            continue
        seen.add(source_id)
        listings.append({
            "source_id": source_id,
            "movie_id": source_id,
            "title": title,
            "url": urljoin(base_url, href),
        })
        if limit and len(listings) >= limit:
            break
    return listings


def _extract_magnets(raw_html, soup):
    candidates = []
    for anchor in soup.find_all("a", href=True):
        href = html.unescape(anchor.get("href", "").strip())
        if href.lower().startswith("magnet:?"):
            candidates.append(href)
        # Some pages put magnet URI in data attributes or clipboard controls.
        for attr in ("data-magnet", "data-clipboard-text", "data-href", "data-url"):
            value = anchor.get(attr)
            if value and "magnet:?" in value.lower():
                candidates.extend(MAGNET_RE.findall(html.unescape(value)))
    # Encoded/plain text fallback. Decode a few layers without assuming a short
    # info hash can be expanded to a longer magnet URI.
    for source in (raw_html, unquote(raw_html)):
        for match in MAGNET_RE.findall(html.unescape(source)):
            candidates.append(match.rstrip("),;"))
    seen, results = set(), []
    for magnet in candidates:
        magnet = html.unescape(magnet).replace("&amp;", "&")
        if not magnet.lower().startswith("magnet:?"):
            continue
        if magnet not in seen:
            seen.add(magnet)
            results.append(magnet)
    return results


def _magnet_info(magnet, title):
    query = parse_qs(urlparse(magnet).query)
    xt = (query.get("xt") or [""])[0]
    info_hash = xt.split(":")[-1].lower() if "btih:" in xt.lower() else None
    dn = (query.get("dn") or [""])[0]
    search_text = f"{dn} {title}"
    quality_match = re.search(r"\b(2160p|4k|1080p|780p|720p|480p|360p|uhd|fhd|hd)\b", search_text, re.I)
    size = None
    try:
        size = int((query.get("xl") or [None])[0])
    except (TypeError, ValueError):
        pass
    return {
        "url": magnet,
        "info_hash": info_hash,
        "quality": quality_match.group(1).upper() if quality_match else None,
        "file_size_bytes": size,
    }


def _extract_images(soup, page_url):
    """Extract actual artwork from the post body, excluding site/torrent UI images.

    The forum's og:image can be a generic magnet/torrent icon, so it is not
    trusted as a poster. Prefer real images embedded in the topic post.
    Returns (poster_url, screenshots). Poster is chosen from portrait artwork;
    landscape images are kept as screenshots.
    """
    screenshots = []
    poster = None

    def normalize_image_url(value):
        if not value:
            return None
        value = html.unescape(str(value).strip())
        if value.startswith('//'):
            value = 'https:' + value
        absolute = urljoin(page_url, value)
        parsed = urlparse(absolute)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return None
        return absolute

    content = (
        soup.select_one('.ipsType_richText')
        or soup.select_one("[data-role='commentContent']")
        or soup.select_one('.cPost_contentWrap')
        or soup.select_one('.ipsComment_content')
        or soup.select_one('.postbody')
        or soup.select_one('.entry-content')
        or soup.select_one('.post-content')
        or soup.select_one('article')
    )
    if content is None:
        content = soup.body or soup

    image_hosts = (
        'postimg', 'imgur', 'ibb.co', 'imagebam', 'imgbox', 'imagevenue',
        'pixhost', 'turboimagehost', 'fastpic', 'imgbb', 'imgchest',
        'imgrock', 'imagezilla', 'imagehost', 'pbs.twimg.com',
    )
    image_extensions = re.compile(r'\.(?:jpe?g|png|webp|gif|bmp|avif)(?:$|[?#])', re.I)
    ignored_words = (
        'avatar', 'logo', 'emoji', 'emoticon', 'reaction', 'smilie', 'icon',
        'magnet', 'torrent', 'direct-link', 'direct_link', 'download',
        'button', 'separator', 'spacer', 'blank', 'transparent', 'pixel',
        'line', 'border', 'banner', 'header', 'footer',
    )
    candidates = []
    seen = set()

    for img in content.find_all('img'):
        values = []
        for attr in ('data-src', 'data-original', 'data-lazy-src', 'data-url',
                     'data-image', 'data-lazyload', 'src'):
            if img.get(attr):
                values.append(img.get(attr))
        srcset = img.get('srcset') or img.get('data-srcset')
        if srcset:
            entries = []
            for entry in srcset.split(','):
                bits = entry.strip().split()
                if bits:
                    width_match = re.search(r'(\d+)w$', bits[1]) if len(bits) > 1 else None
                    entries.append((int(width_match.group(1)) if width_match else 0, bits[0]))
            if entries:
                values.insert(0, max(entries)[1])

        for value in values:
            url = normalize_image_url(value)
            if not url or url in seen:
                continue
            parsed = urlparse(url)
            descriptor = ' '.join([
                ' '.join(img.get('class') or []), img.get('alt') or '',
                img.get('title') or '', parsed.path, parsed.query,
            ]).lower()
            if any(word in descriptor for word in ignored_words):
                continue
            if not (image_extensions.search(parsed.path) or
                    ('format=' in parsed.query.lower() and any(fmt in parsed.query.lower() for fmt in ('format=webp', 'format=jpg', 'format=jpeg', 'format=png'))) or
                    any(host in parsed.netloc.lower() for host in image_hosts) or
                    any(token in parsed.path.lower() for token in ('/uploads/', '/screenshots/', '/images/'))):
                continue

            # Ignore obvious thin separators and tiny icons when dimensions exist.
            try:
                width = int(img.get('width', 0) or 0)
                height = int(img.get('height', 0) or 0)
            except (TypeError, ValueError):
                width = height = 0
            if width and height:
                ratio = width / height
                if height <= 35 or width <= 35 or (width >= 250 and height <= 70):
                    continue
            else:
                ratio = None

            # Images from the forum's own static/theme assets are usually UI,
            # not the artwork hosted in the release post.
            host = parsed.netloc.lower()
            own_site = any(token in host for token in ('1tamilmv', 'tamilmv'))
            if own_site and not any(token in parsed.path.lower() for token in ('/uploads/', '/screenshots/', '/images/')):
                continue

            seen.add(url)
            candidates.append({'url': url, 'ratio': ratio, 'width': width, 'height': height,
                               'descriptor': descriptor})

    # A portrait image is the strongest poster candidate. Landscape images are
    # the original screenshots and must remain in the screenshots array.
    for item in candidates:
        ratio = item['ratio']
        descriptor = item['descriptor']
        poster_hint = any(word in descriptor for word in ('poster', 'cover', 'movie-art', 'movie_art'))
        if poster_hint or (ratio is not None and 0.30 <= ratio <= 0.85):
            poster = item['url']
            break

    # Fallback: if the post has no clearly identifiable portrait poster,
    # promote the first valid image from the post body to the poster.
    # It will be removed from screenshots below to avoid showing it twice.
    if poster is None and candidates:
        poster = candidates[0]['url']

    for item in candidates:
        url = item['url']
        if url == poster:
            continue
        # Do not treat a second portrait as a screenshot unless no poster was
        # identified; keep landscape/still images as the original screenshots.
        ratio = item['ratio']
        if ratio is None or ratio >= 1.05:
            if url not in screenshots:
                screenshots.append(url)

    # If the page only exposes a generic icon/UI or no clear artwork, return no
    # poster rather than saving the wrong image as the movie poster.
    return poster, screenshots[:20]


def fetch_movie_details(movie, base_url=DEFAULT_BASE_URL):
    response = _get(movie["url"])
    raw_html = response.text
    soup = BeautifulSoup(raw_html, "html.parser")
    h1 = soup.find("h1")
    title = " ".join(h1.get_text(" ", strip=True).split()) if h1 else movie.get("title", "")
    magnets = [_magnet_info(m, title) for m in _extract_magnets(raw_html, soup)]
    poster_url, screenshots = _extract_images(soup, movie.get("url") or base_url)
    return {
        "title": title,
        "magnets": magnets,
        "poster_url": poster_url,
        "screenshots": screenshots,
    }


def clean_title_for_tmdb(title):
    value = title or ""
    # Remove common release tags without deleting meaningful title words.
    value = re.sub(r"\[[^\]]*\]|\([^)]*(?:1080p|720p|480p|4k|hdrip|web[- ]?dl|bluray)[^)]*\)", " ", value, flags=re.I)
    value = re.sub(r"\b(2160p|1080p|780p|720p|480p|360p|4k|uhd|fhd|hdrip|webrip|web[- ]?dl|bluray|brrip|x264|x265|h264|h265|aac|hevc|proper|repack|multi audio|dual audio|uncut)\b", " ", value, flags=re.I)
    value = re.sub(r"\bS(?:EASON)?\s*0?\d{1,2}\s*E(?:P(?:ISODE)?)?\s*0?\d{1,3}(?:\s*[-–]\s*(?:S\d{1,2}\s*)?E?P?0?\d{1,3})?\b", " ", value, flags=re.I)
    value = re.sub(r"\b(?:complete|full)?\s*season\s*0?\d{1,2}(?:\s*(?:complete|full))?\b", " ", value, flags=re.I)
    value = re.sub(r"\bepisodes?\s*0?\d{1,3}(?:\s*[-–]\s*0?\d{1,3})?\b", " ", value, flags=re.I)
    value = re.sub(r"\b(tamil|telugu|hindi|english|malayalam|kannada|bengali|dubbed|org)\b", " ", value, flags=re.I)
    value = re.sub(r"\b(19|20)\d{2}\b", " ", value)
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split()).strip()


def infer_series_coverage(title):
    """Represent a release pack as a season and episode range.

    Examples:
      S01E02 / Season 1 Episode 2 -> single_episode, S=1 E=2..2
      S01E01-E04 -> episode_pack, S=1 E=1..4
      S01 Complete Season / Season 1 Complete -> season_pack, S=1 E range unknown
      Movie title (2026) -> movie
    """
    text = (title or "").upper()
    season_start = season_end = episode_start = episode_end = None
    media_type = "movie"
    release_kind = "movie"

    # Multi-season bundle e.g. S01-S03 Complete
    multi = re.search(r"\bS(?:EASON)?\s*0?(\d{1,2})\s*[-–]\s*S(?:EASON)?\s*0?(\d{1,2})\b", text)
    if multi:
        media_type = "tv"
        season_start, season_end = int(multi.group(1)), int(multi.group(2))
        release_kind = "multi_season_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # Common explicit episode ranges: S01E01-E04, S01E01-04, S01 EP 1-4
    match = re.search(r"\bS(?:EASON)?\s*0?(\d{1,2})\s*E(?:P(?:ISODE)?)?\s*0?(\d{1,3})(?:\s*[-–]\s*(?:S(?:\d{1,2})\s*)?E?P?0?(\d{1,3}))?", text)
    if match:
        media_type = "tv"
        season_start = season_end = int(match.group(1))
        episode_start = int(match.group(2))
        episode_end = int(match.group(3) or match.group(2))
        release_kind = "single_episode" if episode_start == episode_end else "episode_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # E01-E04 plus season marker in other title styles
    match = re.search(r"\bSEASON\s*0?(\d{1,2}).{0,20}\b(?:EP(?:ISODE)?S?\.?\s*)?0?(\d{1,3})\s*[-–]\s*0?(\d{1,3})\b", text)
    if match:
        media_type = "tv"
        season_start = season_end = int(match.group(1))
        episode_start, episode_end = int(match.group(2)), int(match.group(3))
        release_kind = "single_episode" if episode_start == episode_end else "episode_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # Season pack (episode coverage unknown or full season)
    match = re.search(r"\bS(?:EASON)?\s*0?(\d{1,2})\b", text)
    if match and re.search(r"\b(COMPLETE|FULL|ALL\s+EPISODES|SEASON\s+PACK|COMPLETE\s+SEASON)\b", text):
        media_type = "tv"
        season_start = season_end = int(match.group(1))
        release_kind = "season_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # Explicit "Season 1" is enough to classify as series, but not assume complete.
    match = re.search(r"\bSEASON\s*0?(\d{1,2})\b", text)
    if match:
        media_type = "tv"
        season_start = season_end = int(match.group(1))
        release_kind = "season_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # Explicit episode range without a season number, e.g. "Episodes 1-4".
    match = re.search(r"\bEPISODES?\.?\s*0?(\d{1,3})\s*[-–]\s*0?(\d{1,3})\b", text)
    if match:
        media_type = "tv"
        episode_start, episode_end = int(match.group(1)), int(match.group(2))
        release_kind = "single_episode" if episode_start == episode_end else "episode_pack"
        return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)

    # Episode code with no season marker, e.g. EP01, Episode 4
    match = re.search(r"\b(?:EP(?:ISODE)?\.?\s*)0?(\d{1,3})\b", text)
    if match:
        media_type = "tv"
        episode_start = episode_end = int(match.group(1))
        release_kind = "single_episode"

    return locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end)


def locals_subset(media_type, release_kind, season_start, season_end, episode_start, episode_end):
    return {
        "media_type": media_type,
        "release_kind": release_kind,
        "season_start": season_start,
        "season_end": season_end,
        "episode_start": episode_start,
        "episode_end": episode_end,
    }


if __name__ == "__main__":
    found = fetch_movie_list(DEFAULT_BASE_URL, limit=5)
    print(f"Found {len(found)} topic links")
    for item in found:
        detail = fetch_movie_details(item, DEFAULT_BASE_URL)
        print(item["title"], "magnets:", len(detail["magnets"]))
