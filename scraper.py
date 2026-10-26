import trafilatura
from trafilatura import settings
import multiprocessing
from multiprocessing import Process, Queue
from bs4 import BeautifulSoup
import re
import random
from user_agents import build_browser_headers, MODERN_USER_AGENTS

USER_AGENTS = MODERN_USER_AGENTS

# Base headers
HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
}

def _fallback_scraper(html_content):
    """
    A simple fallback scraper using BeautifulSoup if Trafilatura fails.
    It extracts text from common tags like <p>, <h1>, <h2>.
    """
    if not html_content:
        return None, None

    soup = BeautifulSoup(html_content, 'html.parser')

    # Try to find a title from <h1> or <title> tags
    title_tag = soup.find('h1') or soup.find('title')
    title_text = title_tag.get_text(strip=True) if title_tag else "No Title Found"

    # Extract text from main content tags, prioritizing common article containers
    main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|post|body', re.I))
    
    container = main_content if main_content else soup
    tags_to_find = container.find_all(['p', 'h2', 'h3', 'li'])
    
    body_content = [tag.get_text(strip=True) for tag in tags_to_find if tag.get_text(strip=True)]

    if not body_content:
        return None, None # Fallback also failed

    return title_text, '\n\n'.join(body_content)


def _scrape_worker(url, result_queue):
    """
    This function runs in a separate process. It performs the actual scraping
    and puts the result into a queue for the main process to retrieve.
    It now includes a fallback scraping mechanism.
    """
    try:
        config = settings.use_config()
        section = "DOWNLOAD"
        if not config.has_section(section):
            config.add_section(section)
        # Select a random User-Agent
        current_headers = HEADERS.copy()
        current_headers['User-Agent'] = random.choice(USER_AGENTS)
        
        # Convert the headers dict to the string format Trafilatura expects
        config.set(section, "HEADERS", str(current_headers))
        config.set(section, "TIMEOUT", "15") # Set a reasonable timeout for fetching

        # Use Trafilatura to fetch the raw HTML
        downloaded = trafilatura.fetch_url(url, config=config)

        if downloaded is None:
            result_queue.put((None, f"Error: Failed to fetch URL. Site might be down or blocking requests."))
            return

        # Attempt extraction with Trafilatura (Primary Method)
        content = trafilatura.extract(
            downloaded,
            include_comments=False,
            output_format='txt',
            include_tables=False,
            favor_recall=True,
            config=config
        )

        # Check if Trafilatura was successful (check for a reasonable content length)
        if content and len(content) > 150:
            lines = content.split('\n')
            title = lines[0]
            body = '\n'.join(lines[1:])
            result_queue.put((title, body))
        else:
            # If Trafilatura fails, try the fallback scraper
            title, body = _fallback_scraper(downloaded)
            if title and body:
                result_queue.put((title, body))
            else:
                result_queue.put((None, "Error: Primary and fallback scrapers could not extract meaningful content."))

    except Exception as e:
        # If any other error happens inside the process, report it
        result_queue.put((None, f"Error within scraper process: {e}"))


def scrape_direct(url):
    """
    Direct scraping in the current thread with timeout configuration.
    Used as primary or fallback if process spawning is blocked in frozen binaries.
    """
    try:
        config = settings.use_config()
        section = "DOWNLOAD"
        if not config.has_section(section):
            config.add_section(section)
        current_headers = HEADERS.copy()
        current_headers['User-Agent'] = random.choice(USER_AGENTS)
        config.set(section, "HEADERS", str(current_headers))
        config.set(section, "TIMEOUT", "15")

        downloaded = trafilatura.fetch_url(url, config=config)
        if downloaded is None:
            return None, "Error: Failed to fetch URL. Site might be down or blocking requests."

        content = trafilatura.extract(
            downloaded,
            include_comments=False,
            output_format='txt',
            include_tables=False,
            favor_recall=True,
            config=config
        )
        if content and len(content) > 150:
            lines = content.split('\n')
            title = lines[0]
            body = '\n'.join(lines[1:])
            return title, body
        else:
            title, body = _fallback_scraper(downloaded)
            if title and body:
                return title, body
            else:
                return None, "Error: Primary and fallback scrapers could not extract meaningful content."
    except Exception as e:
        return None, f"Error within scraper: {e}"


def scrape_in_subprocess(url, timeout=20):
    """
    Manages the scraping subprocess with fallback to direct execution.
    Uses 'fork' context on Linux/Unix to avoid forkserver connection issues in frozen binaries.
    """
    try:
        # Prefer fork context to avoid forkserver/spawn issues in PyInstaller
        if hasattr(multiprocessing, 'get_context') and 'fork' in multiprocessing.get_all_start_methods():
            ctx = multiprocessing.get_context('fork')
        else:
            ctx = multiprocessing

        result_queue = ctx.Queue()
        p = ctx.Process(target=_scrape_worker, args=(url, result_queue))
        p.start()
        p.join(timeout)

        if p.is_alive():
            p.terminate()
            p.join()
            return None, "Error: Scraping process timed out and was terminated."

        if p.exitcode is not None and p.exitcode != 0:
            # Subprocess failed; fallback to direct scrape
            return scrape_direct(url)

        if result_queue.empty():
            return scrape_direct(url)

        return result_queue.get()
    except Exception:
        # Fallback to direct in-thread execution if subprocess creation fails
        return scrape_direct(url)
