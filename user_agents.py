import itertools
import time
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime

# Pool of modern real-world desktop browser User-Agents matching Rust implementation.
# Rotated to prevent fingerprinting and avoid identifying as a custom scraper/bot.
MODERN_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0",
]

_UA_CYCLE = itertools.cycle(MODERN_USER_AGENTS)

def get_masked_user_agent() -> str:
    """Returns the next modern browser User-Agent in round-robin order."""
    return next(_UA_CYCLE)

def build_browser_headers(accept_json: bool = False) -> dict:
    """
    Builds standard modern browser headers to mask requests.
    Includes Sec-Fetch-* headers and appropriate Accept header based on request type.
    """
    ua = get_masked_user_agent()
    if accept_json:
        return {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

def _parse_retry_delay(response: requests.Response, default: float = 2.0, max_delay: float = 10.0) -> float:
    """Parses Retry-After or x-ratelimit-reset headers to determine sleep duration."""
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), max_delay)
        except (ValueError, TypeError):
            pass

    rate_limit_reset = response.headers.get("x-ratelimit-reset")
    if rate_limit_reset:
        try:
            return min(max(float(rate_limit_reset), 1.0), max_delay)
        except (ValueError, TypeError):
            pass

    return default

def reddit_get(url: str, timeout: int = 10, accept_json: bool = True, session: requests.Session = None) -> requests.Response:
    """
    Sends a GET request identifying as a modern desktop browser.
    If HTTP 429 (Too Many Requests) is returned, respects Retry-After /
    x-ratelimit-reset headers or backs off briefly and retries.
    """
    requester = session or requests
    headers = build_browser_headers(accept_json=accept_json)
    response = requester.get(url, headers=headers, timeout=timeout)

    for _ in range(2):
        if response.status_code == 429:
            delay = _parse_retry_delay(response)
            time.sleep(delay)
            new_headers = build_browser_headers(accept_json=accept_json)
            response = requester.get(url, headers=new_headers, timeout=timeout)
        else:
            break

    return response

def parse_reddit_rss_posts(xml_text: str) -> list:
    """
    Parses Reddit Atom RSS feed into article dictionaries.
    Extracts external article link from content HTML to match JSON behavior.
    """
    root = ET.fromstring(xml_text)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    articles = []
    for entry in root.findall('atom:entry', ns):
        title_el = entry.find('atom:title', ns)
        title = title_el.text.strip() if title_el is not None and title_el.text else 'No Title'

        cat_el = entry.find('atom:category', ns)
        subreddit = cat_el.attrib.get('term', '') if cat_el is not None else ''

        link_el = entry.find('atom:link', ns)
        permalink = link_el.attrib.get('href', '') if link_el is not None else ''
        if '/r/' in permalink:
            permalink = permalink[permalink.find('/r/'):]

        pub_el = entry.find('atom:published', ns)
        if pub_el is None:
            pub_el = entry.find('atom:updated', ns)
        created_utc = 0.0
        if pub_el is not None and pub_el.text:
            try:
                created_utc = datetime.fromisoformat(pub_el.text.strip()).timestamp()
            except Exception:
                pass

        content_el = entry.find('atom:content', ns)
        article_url = ''
        if content_el is not None and content_el.text:
            soup = BeautifulSoup(content_el.text, 'html.parser')
            for a in soup.find_all('a'):
                if '[link]' in a.get_text():
                    article_url = a.get('href', '')
                    break

        # Skip self posts without external link
        if not article_url:
            continue

        articles.append({
            'title': title,
            'url': article_url,
            'subreddit': subreddit,
            'permalink': permalink,
            'created_utc': created_utc,
            'score': 0,
            'num_comments': 0,
        })
    return articles

def parse_reddit_rss_comments(xml_text: str) -> list:
    """
    Parses Reddit Atom RSS comments feed into comment dictionaries.
    """
    root = ET.fromstring(xml_text)
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    comments = []
    for entry in root.findall('atom:entry', ns):
        id_el = entry.find('atom:id', ns)
        cid = id_el.text.strip() if id_el is not None and id_el.text else ''
        if cid.startswith('t3_'):
            continue  # The top post submission itself, not a comment
        author_el = entry.find('atom:author/atom:name', ns)
        author = author_el.text.strip() if author_el is not None and author_el.text else '[deleted]'
        if author.startswith('/u/'):
            author = author[3:]

        content_el = entry.find('atom:content', ns)
        body = ''
        if content_el is not None and content_el.text:
            soup = BeautifulSoup(content_el.text, 'html.parser')
            paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if p.get_text(strip=True)]
            if paragraphs:
                body = '\n\n'.join(paragraphs)
            else:
                body = soup.get_text(strip=True)

        if body or cid:
            comments.append({
                'author': author,
                'score': 0,
                'body': body,
            })
    return comments

def fetch_reddit_posts(subreddits: str) -> list:
    """
    Dual-strategy fetch matching Rust implementation:
    1. Tries the official .json endpoint with modern browser headers.
    2. If blocked or non-JSON, falls back to the .rss feed.
    """
    json_url = f"https://www.reddit.com/r/{subreddits}/new.json?limit=50"
    try:
        res = reddit_get(json_url, timeout=10, accept_json=True)
        if res.status_code == 200 and "json" in res.headers.get("content-type", "").lower():
            data = res.json()
            articles = []
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                if not post_data.get("is_self") and "crosspost_parent_list" not in post_data and post_data.get("url"):
                    articles.append({
                        "title": post_data.get("title"),
                        "url": post_data.get("url"),
                        "subreddit": post_data.get("subreddit"),
                        "created_utc": post_data.get("created_utc"),
                        "permalink": post_data.get("permalink"),
                        "score": post_data.get("score", 0),
                        "num_comments": post_data.get("num_comments", 0),
                    })
            if articles:
                return articles
    except Exception:
        pass

    # Fallback to RSS feed
    rss_url = f"https://www.reddit.com/r/{subreddits}/new.rss"
    res = reddit_get(rss_url, timeout=10, accept_json=False)
    res.raise_for_status()
    return parse_reddit_rss_posts(res.text)

def fetch_reddit_domain_posts(domain: str) -> list:
    """
    Fetches articles from a domain with dual-strategy JSON + RSS fallback.
    """
    json_url = f"https://www.reddit.com/domain/{domain}/new.json?limit=50"
    try:
        res = reddit_get(json_url, timeout=10, accept_json=True)
        if res.status_code == 200 and "json" in res.headers.get("content-type", "").lower():
            data = res.json()
            articles = []
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                if not post_data.get("is_self") and post_data.get("url"):
                    articles.append({
                        "title": post_data.get("title"),
                        "url": post_data.get("url"),
                        "subreddit": post_data.get("subreddit"),
                        "source_domain": domain,
                        "permalink": post_data.get("permalink"),
                        "created_utc": post_data.get("created_utc"),
                        "score": post_data.get("score", 0),
                        "num_comments": post_data.get("num_comments", 0),
                        "is_read": False,
                        "is_bookmarked": False,
                        "is_new": False,
                    })
            if articles:
                return articles
    except Exception:
        pass

    # Fallback to RSS feed
    rss_url = f"https://www.reddit.com/domain/{domain}/new.rss"
    res = reddit_get(rss_url, timeout=10, accept_json=False)
    res.raise_for_status()
    articles = parse_reddit_rss_posts(res.text)
    for a in articles:
        a["source_domain"] = domain
        a["is_read"] = False
        a["is_bookmarked"] = False
        a["is_new"] = False
    return articles

def fetch_reddit_comments(permalink: str):
    """
    Fetches comments with dual-strategy JSON + RSS fallback.
    Returns (mode, data) where mode is 'json' or 'rss'.
    """
    clean = permalink.rstrip('/').rstrip('.json').rstrip('.rss')
    base_url = clean if clean.startswith('http') else f"https://www.reddit.com{clean}"

    # 1. Try JSON
    try:
        json_url = f"{base_url}.json"
        res = reddit_get(json_url, timeout=10, accept_json=True)
        if res.status_code == 200 and "json" in res.headers.get("content-type", "").lower():
            raw_comments = res.json()[1].get("data", {}).get("children", [])
            if raw_comments:
                return ("json", raw_comments)
    except Exception:
        pass

    # 2. Fallback to RSS
    rss_url = f"{base_url}.rss"
    res = reddit_get(rss_url, timeout=10, accept_json=False)
    res.raise_for_status()
    comments = parse_reddit_rss_comments(res.text)
    return ("rss", comments)
