# news_feed_advanced.py

import os
import sys
import threading
import time
import requests
import json
import subprocess
import sqlite3
import textwrap
import configparser
import webbrowser
import shutil
import argparse
import re
import html
from pathlib import Path
from urllib.parse import urlparse, quote
import pid # Added for single-instance locking

# --- Platform-specific imports for direct keyboard input ---
try:
    import tty
    import termios
    import select

    def getch(timeout=0.1):
        """
        A non-blocking getch for Unix-like systems that correctly handles
        arrow keys and other escape sequences without race conditions.
        """
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            if not select.select([sys.stdin], [], [], timeout)[0]: return None
            ch = sys.stdin.read(1)
            if ch == '\x7f' or ch == '\b': return "BACKSPACE"
            if ch != '\x1b':
                if ch == '\r': return "ENTER"
                return ch

            new_settings = termios.tcgetattr(fd)
            new_settings[6][termios.VMIN], new_settings[6][termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, new_settings)

            extra = sys.stdin.read(3)

            if not extra:
                return "ESC"

            if extra.startswith('[A'): return "UP"
            if extra.startswith('[B'): return "DOWN"
            if extra.startswith('[C'): return "RIGHT"
            if extra.startswith('[D'): return "LEFT"
            if extra == '[3~': return "DELETE"
            if extra == '[5~': return "PGUP"
            if extra == '[6~': return "PGDOWN"
            # ADD THESE FOUR LINES for different terminal emulators
            if extra == '[H' or extra == '[1~': return "HOME"
            if extra == '[F' or extra == '[4~': return "END"

            return None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

except ImportError:
    import msvcrt
    import time as win_time

    def getch(timeout=0.1):
        """
        A non-blocking getch for Windows that correctly handles arrow keys
        and other special characters.
        """
        start_time = win_time.time()
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch == b'\r': return "ENTER"
                if ch == b'\x1b': return "ESC"
                if ch == b'\x08': return "BACKSPACE"
                if ch == b'\xe0':
                    ch2 = msvcrt.getch()
                    # ADD HOME and END to this dictionary
                    return {
                        b'H': "UP", b'P': "DOWN", b'K': "LEFT",
                        b'M': "RIGHT", b'S': "DELETE", b'I': "PGUP",
                        b'Q': "PGDOWN", b'G': "HOME", b'O': "END"
                    }.get(ch2)
                try: return ch.decode('utf-8')
                except UnicodeDecodeError: return None
            elif win_time.time() - start_time > timeout: return None

# --- ANSI color codes for styling the terminal output ---
class Colors:
    RESET, BOLD, CYAN = '\033[0m', '\033[1m', '\033[96m'
    YELLOW, LIGHT_GREY, GREEN = '\033[93m', '\033[38;5;248m', '\033[92m'
    MAGENTA, RED = '\033[95m', '\033[91m'
    BLUE = '\033[94m'
    ITALIC = '\x1b[3m'
    STRIKETHROUGH = '\x1b[9m'
    INLINE_CODE_BG = '\x1b[48;5;239m'
    BOLD_OFF = '\x1b[22m'
    ITALIC_OFF = '\x1b[23m'
    STRIKETHROUGH_OFF = '\x1b[29m'
    UNDERLINE = '\x1b[4m'
    UNDERLINE_OFF = '\x1b[24m'

THEMES = {
    "Default": {
        "highlight_bg": '\x1b[48;5;238m',
        "highlight_fg": '\x1b[97m',
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[97m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[97m',
        "new_fg": Colors.YELLOW,
        "delete_fg": Colors.RED
    },
    # --- A classic, low-contrast light theme for readability ---
    "Solarized Light": {
        "highlight_bg": '\x1b[48;5;153m', # Light desaturated cyan
        "highlight_fg": '\x1b[38;5;66m',  # Dark slate
        "bar_bg": '\x1b[48;5;231m',       # Off-white (base3)
        "bar_fg": '\x1b[38;5;241m',       # Dark grey (base00)
        "popup_bg": '\x1b[48;5;254m',     # Lighter grey (base2)
        "popup_fg": '\x1b[38;5;241m',     # Dark grey (base00)
        "new_fg": '\x1b[38;5;136m',       # Solarized yellow
        "delete_fg": '\x1b[38;5;160m'     # Solarized red
    },
    # --- A clean, minimalist, high-contrast light theme ---
    "Paper White": {
        "highlight_bg": '\x1b[48;5;235m', # Dark grey
        "highlight_fg": '\x1b[38;5;15m',  # White
        "bar_bg": '\x1b[48;5;15m',        # White
        "bar_fg": '\x1b[38;5;232m',       # Black
        "popup_bg": '\x1b[48;5;254m',     # Off-white
        "popup_fg": '\x1b[38;5;232m',     # Black
        "new_fg": '\x1b[38;5;172m',       # A deep orange for contrast
        "delete_fg": '\x1b[38;5;196m'     # A strong red
    },
    "Solarized Dark": {
        "highlight_bg": '\x1b[48;5;22m', "highlight_fg": '\x1b[38;5;228m',
        "bar_bg": '\x1b[48;5;234m', "bar_fg": '\x1b[38;5;248m',
        "popup_bg": '\x1b[48;5;235m', "popup_fg": '\x1b[38;5;250m',
        "new_fg": '\x1b[38;5;136m',
        "delete_fg": '\x1b[38;5;160m'
    },
    "Nord": {
        "highlight_bg": '\x1b[48;5;24m', "highlight_fg": '\x1b[38;5;229m',
        "bar_bg": '\x1b[48;5;236m', "bar_fg": '\x1b[38;5;111m',
        "popup_bg": '\x1b[48;5;237m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;215m',
        "delete_fg": '\x1b[38;5;196m'
    },
    # --- A retro green-on-black terminal theme ---
    "Matrix": {
        "highlight_bg": '\x1b[48;5;118m', # Bright green
        "highlight_fg": '\x1b[38;5;16m',  # Black
        "bar_bg": '\x1b[48;5;16m',        # Black
        "bar_fg": '\x1b[38;5;46m',        # Bright green
        "popup_bg": '\x1b[48;5;233m',     # Dark grey
        "popup_fg": '\x1b[38;5;40m',      # A more readable, dimmer green
        "new_fg": '\x1b[38;5;178m',       # Contrasting amber/yellow
        "delete_fg": '\x1b[38;5;196m'     # Red
    },
    "Gruvbox Dark": {
        "highlight_bg": '\x1b[48;5;131m', "highlight_fg": '\x1b[38;5;229m',
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[38;5;248m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[38;5;250m',
        "new_fg": '\x1b[38;5;214m',
        "delete_fg": '\x1b[38;5;124m'
    },
    "Monokai": {
        "highlight_bg": '\x1b[48;5;197m', "highlight_fg": '\x1b[38;5;233m',
        "bar_bg": '\x1b[48;5;234m', "bar_fg": '\x1b[38;5;148m',
        "popup_bg": '\x1b[48;5;235m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;118m',
        "delete_fg": '\x1b[38;5;196m'
    },
    "Dracula+": {
        "highlight_bg": '\x1b[48;5;98m', "highlight_fg": '\x1b[38;5;231m',
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[38;5;117m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;208m',
        "delete_fg": '\x1b[38;5;203m'
    },
    # --- A stylish synthwave/outrun theme with neon colors ---
    "Retro Sunset": {
        "highlight_bg": '\x1b[48;5;198m', # Hot pink
        "highlight_fg": '\x1b[38;5;17m',  # Deep blue
        "bar_bg": '\x1b[48;5;17m',        # Deep blue
        "bar_fg": '\x1b[38;5;87m',        # Electric cyan
        "popup_bg": '\x1b[48;5;18m',      # Darker blue
        "popup_fg": '\x1b[38;5;254m',     # Creamy white
        "new_fg": '\x1b[38;5;220m',       # Vibrant gold/yellow
        "delete_fg": '\x1b[38;5;198m'     # Hot pink
    },
     "Cyberpunk": {
        "highlight_bg": '\x1b[48;5;208m', "highlight_fg": '\x1b[38;5;16m',
        "bar_bg": '\x1b[48;5;17m', "bar_fg": '\x1b[38;5;228m',
        "popup_bg": '\x1b[48;5;18m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;198m',
        "delete_fg": '\x1b[38;5;197m'
    }
}

# --- App Configuration & File Paths ---
def get_config_dir():
    """Gets the application's config directory, creating it if necessary."""
    if sys.platform == "win32": config_dir = Path(os.getenv("APPDATA")) / "AlienNewsFeed"
    else: config_dir = Path.home() / ".config" / "AlienNewsFeed"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

CONFIG_DIR = get_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.ini"

# --- Globals that will be set by profile loader ---
DB_FILE = None
SUBREDDITS_STRING = "news+worldnews+politics+technology"
FETCH_INTERVAL_SECONDS = 300
SHOW_CLOCK = True
VIDEO_PLAYER_PATH = "mpv"
BLOCKED_DOMAINS = set()
NEEDS_RESTART = False
PAGE_JUMP = 10
HIGHLIGHT_KEYWORDS = set()
MUTE_KEYWORDS = set()
CONNECTION_OK = True

data_lock = threading.Lock()
last_checked_time = "Never"
ARTICLES_UPDATED, HAS_NEW_ARTICLES = threading.Event(), False
stop_thread_event = threading.Event()

# --- Settings Management ---
def setup_config():
    """Ensures config.ini exists and is in the new profile format."""
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists() or not CONFIG_FILE.read_text().strip():
        # Create a brand new config
        config['Settings'] = {'ActiveProfile': 'Main'}
        config['Profile:Main'] = {
            'Subreddits': 'news+worldnews+politics+technology',
            'DatabaseFile': 'news_feed_main.db',
            'HighlightKeywords': '',
            'MuteKeywords': ''
        }
        config['General'] = {
            'Theme': 'Default',
            'FetchInterval': '300',  # CHANGED
            'ShowClock': 'true',
            'BlockedDomains': ''
        }
        with open(CONFIG_FILE, 'w') as f: config.write(f)
    elif "[Profile:Main]" not in CONFIG_FILE.read_text():
        # Upgrade old config to new profile format
        config.read(CONFIG_FILE)
        old_settings = dict(config['Settings'])

        config.clear() # Clear existing structure

        config['Settings'] = {'ActiveProfile': 'Main'}
        config['Profile:Main'] = {
            'Subreddits': old_settings.get('subreddits', 'news+worldnews+politics+technology'),
            'DatabaseFile': 'news_feed_main.db',
            'HighlightKeywords': '',
            'MuteKeywords': ''
        }
        config['General'] = {
            'Theme': old_settings.get('theme', 'Default'),
            'FetchInterval': old_settings.get('fetchinterval', '300'), # CHANGED
            'ShowClock': old_settings.get('showclock', 'true'),
            'BlockedDomains': old_settings.get('blockeddomains', '')
        }
        with open(CONFIG_FILE, 'w') as f: config.write(f)

def load_profile_settings():
    global DB_FILE, SUBREDDITS_STRING, FETCH_INTERVAL_SECONDS, SHOW_CLOCK, BLOCKED_DOMAINS, HIGHLIGHT_KEYWORDS, MUTE_KEYWORDS, VIDEO_PLAYER_PATH
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    active_profile = config.get('Settings', 'ActiveProfile', fallback='Main')
    profile_section = f"Profile:{active_profile}"
    if not config.has_section(profile_section):
        active_profile = "Main"
        profile_section = "Profile:Main"
    profile_settings = config[profile_section]
    general_settings = config['General']
    SUBREDDITS_STRING = profile_settings.get('Subreddits', 'news+worldnews+politics+technology')
    db_filename = profile_settings.get('DatabaseFile', 'news_feed_main.db')
    DB_FILE = CONFIG_DIR / db_filename
    FETCH_INTERVAL_SECONDS = general_settings.getint('FetchInterval', 60)
    SHOW_CLOCK = general_settings.getboolean('ShowClock', True)
    # --- ADD THIS LINE ---
    VIDEO_PLAYER_PATH = general_settings.get('VideoPlayerPath', 'mpv')
    blocked_str = general_settings.get('BlockedDomains', '')
    BLOCKED_DOMAINS = {domain.strip() for domain in blocked_str.split(',') if domain.strip()}
    highlight_str = profile_settings.get('HighlightKeywords', '')
    mute_str = profile_settings.get('MuteKeywords', '')
    HIGHLIGHT_KEYWORDS = {kw.strip().lower() for kw in highlight_str.split(',') if kw.strip()}
    MUTE_KEYWORDS = {kw.strip().lower() for kw in mute_str.split(',') if kw.strip()}
    return general_settings.get('Theme', 'Default'), active_profile

def save_general_settings(theme_name, fetch_interval, show_clock, blocked_domains, video_player_path):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    blocked_domains_str = ','.join(sorted(list(blocked_domains)))
    config['General'] = {
        'Theme': theme_name,
        'FetchInterval': str(fetch_interval),
        'ShowClock': str(show_clock),
        'BlockedDomains': blocked_domains_str,
        # --- ADD THIS LINE ---
        'VideoPlayerPath': video_player_path
    }
    with open(CONFIG_FILE, 'w') as f: config.write(f)

def save_profile_keywords(profile_name, highlight_keywords, mute_keywords):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    section_name = f"Profile:{profile_name}"
    if config.has_section(section_name):
        config.set(section_name, 'HighlightKeywords', highlight_keywords)
        config.set(section_name, 'MuteKeywords', mute_keywords)
        with open(CONFIG_FILE, 'w') as f:
            config.write(f)

def get_all_profiles():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return sorted([section.split(':')[1] for section in config.sections() if section.startswith('Profile:')])

def set_active_profile(profile_name):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    if not config.has_section('Settings'): config.add_section('Settings')
    config.set('Settings', 'ActiveProfile', profile_name)
    with open(CONFIG_FILE, 'w') as f: config.write(f)

def create_profile(profile_name):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    section_name = f"Profile:{profile_name}"
    if config.has_section(section_name): return False
    config.add_section(section_name)
    config.set(section_name, 'Subreddits', 'news+worldnews')
    db_filename = f"news_feed_{profile_name.lower().replace(' ', '_')}.db"
    config.set(section_name, 'DatabaseFile', db_filename)
    config.set(section_name, 'HighlightKeywords', '')
    config.set(section_name, 'MuteKeywords', '')
    with open(CONFIG_FILE, 'w') as f: config.write(f)
    return True

def delete_profile(profile_name):
    if profile_name == 'Main': return False
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    section_name = f"Profile:{profile_name}"
    if config.has_section(section_name):
        db_filename = config.get(section_name, 'DatabaseFile', fallback=None)
        config.remove_section(section_name)
        with open(CONFIG_FILE, 'w') as f: config.write(f)
        if db_filename:
            db_path = CONFIG_DIR / db_filename
            if db_path.exists(): db_path.unlink()
        return True
    return False

def rename_profile(old_name, new_name):
    if old_name == 'Main' or not new_name.strip(): return False
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    old_section, new_section = f"Profile:{old_name}", f"Profile:{new_name}"
    if not config.has_section(old_section) or config.has_section(new_section): return False
    items = dict(config.items(old_section))
    old_db = CONFIG_DIR / items.get('databasefile', '')
    new_db_filename = f"news_feed_{new_name.lower().replace(' ', '_')}.db"
    items['databasefile'] = new_db_filename
    config.add_section(new_section)
    for key, value in items.items(): config.set(new_section, key, value)
    config.remove_section(old_section)
    if config.get('Settings', 'ActiveProfile') == old_name: config.set('Settings', 'ActiveProfile', new_name)
    with open(CONFIG_FILE, 'w') as f: config.write(f)
    new_db_path = CONFIG_DIR / new_db_filename
    if old_db.exists(): old_db.rename(new_db_path)
    return True

def update_profile_subreddits(profile_name, subreddits):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    section_name = f"Profile:{profile_name}"
    if config.has_section(section_name) and subreddits.strip():
        config.set(section_name, 'Subreddits', subreddits)
        with open(CONFIG_FILE, 'w') as f: config.write(f)
        return True
    return False

# --- Database Functions ---
def init_db(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY, title TEXT NOT NULL, subreddit TEXT NOT NULL,
                source_domain TEXT, permalink TEXT, created_utc REAL NOT NULL,
                is_read INTEGER DEFAULT 0, is_bookmarked INTEGER DEFAULT 0,
                is_new INTEGER DEFAULT 0, score INTEGER DEFAULT 0, num_comments INTEGER DEFAULT 0 ) ''')
        cursor.execute("CREATE TABLE IF NOT EXISTS deleted_articles (url TEXT PRIMARY KEY)")
        cursor.execute("PRAGMA table_info(articles)")
        columns = [c[1] for c in cursor.fetchall()]
        if 'score' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN score INTEGER DEFAULT 0")
        if 'num_comments' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN num_comments INTEGER DEFAULT 0")
        conn.commit()

def add_article_to_db(article, deleted_urls):
    global HAS_NEW_ARTICLES
    url = article.get('url')
    if not url or url in deleted_urls: return
    domain = get_domain_from_url(url)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO articles (url, title, subreddit, source_domain, permalink, created_utc, score, num_comments, is_new) VALUES (?,?,?,?,?,?,?,?,?)', (
            url, article.get('title'), article.get('subreddit'), domain,
            article.get('permalink'), article.get('created_utc'), article.get('score', 0),
            article.get('num_comments', 0), 1))
        if cursor.rowcount > 0:
            with data_lock: HAS_NEW_ARTICLES = True
        conn.commit()

def get_articles_from_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if not BLOCKED_DOMAINS:
            cursor.execute("SELECT * FROM articles ORDER BY created_utc DESC")
        else:
            placeholders = ','.join('?' for _ in BLOCKED_DOMAINS)
            cursor.execute(f"SELECT * FROM articles WHERE source_domain NOT IN ({placeholders}) ORDER BY created_utc DESC", tuple(BLOCKED_DOMAINS))
        return [dict(row) for row in cursor.fetchall()]

def update_article_status(url, is_read=None, is_bookmarked=None, is_new=None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        if is_read is not None: cursor.execute("UPDATE articles SET is_read = ? WHERE url = ?", (int(is_read), url))
        if is_bookmarked is not None: cursor.execute("UPDATE articles SET is_bookmarked = ? WHERE url = ?", (int(is_bookmarked), url))
        if is_new is not None: cursor.execute("UPDATE articles SET is_new = ? WHERE url = ?", (int(is_new), url))
        conn.commit()

def block_and_delete_article(url):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO deleted_articles (url) VALUES (?)", (url,))
        cursor.execute("DELETE FROM articles WHERE url = ?", (url,))
        conn.commit()

# --- Utility Functions ---
def get_domain_from_url(url):
    if not url: return ""
    try:
        netloc = urlparse(url).netloc
        return netloc[4:] if netloc.startswith('www.') else netloc
    except Exception: return ""

def format_time_ago(utc_timestamp):
    delta = time.time() - utc_timestamp
    if delta < 60: return f"{int(delta)}s ago"
    if delta < 3600: return f"{int(delta / 60)}m ago"
    if delta < 86400: return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"

# --- Comment Data Structure ---
class CommentNode:
    def __init__(self, data, depth=0):
        self.author, self.score, self.body, self.depth = data.get('author','[d]'), data.get('score',0), data.get('body',''), depth
        self.children, self.is_collapsed = [], False

# --- Core Application Logic ---
def fetch_articles_threaded():
    global last_checked_time, HAS_NEW_ARTICLES, CONNECTION_OK
    while not stop_thread_event.is_set():
        HAS_NEW_ARTICLES = False
        try:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT url FROM deleted_articles")
                deleted_urls = {row[0] for row in cursor.fetchall()}

            url = f"https://www.reddit.com/r/{SUBREDDITS_STRING}/new.json?limit=50"
            headers = {"User-Agent": "live_news_feed_script/2.6"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            # If the above lines succeed, the connection is OK
            CONNECTION_OK = True

            data = response.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                domain = get_domain_from_url(post_data.get("url"))
                if not post_data.get("is_self") and post_data.get("url") and domain not in BLOCKED_DOMAINS:
                    article_data = {k: post_data.get(k) for k in ["title", "url", "subreddit", "created_utc", "permalink", "score", "num_comments"]}
                    add_article_to_db(article_data, deleted_urls)

            last_checked_time = time.strftime("%I:%M:%S %p")
            ARTICLES_UPDATED.set()
        except requests.exceptions.RequestException:
            # If any network error occurs, set the status to False
            CONNECTION_OK = False
            # Trigger a redraw to show the red indicator immediately
            ARTICLES_UPDATED.set()
            pass

        stop_thread_event.wait(timeout=FETCH_INTERVAL_SECONDS)

class NewsFeedMenu:
    def __init__(self, active_profile, title="👽 Alien News Feed"):
        self.title, self.is_running, self.needs_redraw = title, True, True
        self.active_profile = active_profile
        self.all_articles = []
        self.force_regenerate_view = True

        self.is_comment_view, self.is_settings_view = False, False
        self.is_action_menu_view, self.is_filter_menu_view = False, False
        self.is_help_view, self.is_import_view, self.is_profile_view = False, False, False
        self.is_delete_confirm_view, self.is_exit_confirm_view = False, False
        self.is_search_view, self.search_query = False, ""
        self.search_input_active = False

        self.is_link_view = False
        self.extracted_links = []
        self.link_selected_index = 0

        self.page_jump = PAGE_JUMP
        self.selected_index, self.scroll_top = 0, 0

        self.comment_tree, self.visible_comments = [], []
        self.comment_view_status, self.comment_selected_index, self.comment_scroll_top = "", 0, 0

        self.profile_selected_index = 0
        self.profile_input_active = False
        self.profile_input_query = ""
        self.profile_action = None
        self.profiles = get_all_profiles()
        self.profile_status_message = ""

        self.view_modes = ["All", "Unseen", "Highlights", "Bookmarks", "Video", "Read"]
        self.current_view_mode_index = 0
        self.filter_menu_selected_index = 0

        self.settings_selected_index = 0
        self.theme_names = list(THEMES.keys())
        theme_name, _ = load_profile_settings()
        self.current_theme_index = self.theme_names.index(theme_name) if theme_name in self.theme_names else 0
        self.theme = THEMES[self.theme_names[self.current_theme_index]]
        self.fetch_interval_setting = FETCH_INTERVAL_SECONDS
        self.show_clock_setting = SHOW_CLOCK
        # --- ADD THIS LINE ---
        self.video_player_path_setting = VIDEO_PLAYER_PATH
        self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))
        self.highlight_keywords_setting = ','.join(sorted(list(HIGHLIGHT_KEYWORDS)))
        self.mute_keywords_setting = ','.join(sorted(list(MUTE_KEYWORDS)))
        self.last_displayed_minute = -1
        self.last_known_width, self.last_known_height = os.get_terminal_size()

        self.status_message = ""
        self.status_message_timer = 0

        self.action_menu_article = None
        self.action_menu_selected_index = 0
        self.article_to_delete = None
        self.master_article_list = []

    def _draw_settings(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, 17
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Settings")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        video_path_text = self.video_player_path_setting
        blocked_text = self.blocked_domains_setting
        highlight_text = self.highlight_keywords_setting
        mute_text = self.mute_keywords_setting

        if self.settings_selected_index == 3: video_path_text += "_"
        if self.settings_selected_index == 4: blocked_text += "_"
        if self.settings_selected_index == 5: highlight_text += "_"
        if self.settings_selected_index == 6: mute_text += "_"

        clock_status = "< Enabled >" if self.show_clock_setting else "< Disabled >"
        options = [
            f"Refresh Time (s): < {self.fetch_interval_setting} >",
            f"Color Theme: < {self.theme_names[self.current_theme_index]} >",
            f"Show Clock: {clock_status}",
            f"Video Player Path: {video_path_text}",
            f"Blocked Domains: {blocked_text}",
            f"Highlight Words: {highlight_text}",
            f"Mute Words: {mute_text}",
            "SEPARATOR",
            "Export Bookmarks to HTML",
            "Export Full Backup",
            "Import from Backup"
        ]
        divider_pos = 7
        for i, option in enumerate(options):
            row = start_y + 2 + i
            if i > divider_pos: row += 1
            if i == divider_pos:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")
                continue
            text = option.ljust(pop_w - 4)
            logical_index = i - 1 if i > divider_pos else i
            if logical_index == self.settings_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")

        help_footer_y = start_y + pop_h - 2
        sys.stdout.write(f"\x1b[{help_footer_y -1};{start_x}H{pop_bg}{pop_fg}├{'─'*(pop_w-2)}┤")

        # --- This dictionary contains the corrected, shorter help text ---
        help_strings = {
            3: "Enter the path to your video player",
            4: "Enter comma-separated domains (e.g., site.com,another.org)",
            5: "Enter comma-separated words to highlight article titles",
            6: "Enter comma-separated words to hide articles from the feed"
        }
        help_text = help_strings.get(self.settings_selected_index, "")
        sys.stdout.write(f"\x1b[{help_footer_y};{start_x+2}H{pop_bg}{Colors.CYAN}{help_text.ljust(pop_w - 4)}")
        sys.stdout.write(Colors.RESET)
        sys.stdout.flush()

    def handle_action_menu_input(self, key):
        options_dict = self._get_action_menu_options()
        actionable_options = {k: v for k, v in options_dict.items() if v != 'separator'}
        actionable_keys = list(actionable_options.keys())
        max_index = len(actionable_keys) - 1
        if key == "ESC": self.is_action_menu_view = False
        elif key == "UP": self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
        elif key == "DOWN": self.action_menu_selected_index = min(max_index, self.action_menu_selected_index + 1)
        elif key == "ENTER":
            action = actionable_options[actionable_keys[self.action_menu_selected_index]]
            url = self.action_menu_article['url']
            if action == "delete_article":
                self.article_to_delete, self.is_delete_confirm_view = self.action_menu_article, True
            elif action == "open_article": threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif action == "watch_mpv":
                try:
                    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
                    if sys.platform == "win32": kwargs['creationflags'] = 0x00000200 | 0x00000008
                    else: kwargs['start_new_session'] = True
                    # --- USE THE GLOBAL VARIABLE ---
                    subprocess.Popen([VIDEO_PLAYER_PATH, url], **kwargs)
                    self.status_message, self.status_message_timer = "Launching video in player...", 50
                except FileNotFoundError: self.status_message, self.status_message_timer = f"Error: '{VIDEO_PLAYER_PATH}' not found.", 50
            elif action == "open_comments": threading.Thread(target=webbrowser.open, args=(f"https://www.reddit.com{self.action_menu_article['permalink']}",)).start()
            elif action == "summarize": threading.Thread(target=webbrowser.open, args=(f"https://www.perplexity.ai/?s=o&q={quote(f'summarize {url}')}",)).start()
            elif action == "copy_url":
                self._copy_to_clipboard(url)
                self.status_message, self.status_message_timer = "URL copied to clipboard!", 50
            elif action == "archive": threading.Thread(target=webbrowser.open, args=(f"https://archive.is/{quote(url)}",)).start()
            elif action == "exclude_domain":
                domain_to_block = get_domain_from_url(url)
                if domain_to_block and domain_to_block not in BLOCKED_DOMAINS:
                    BLOCKED_DOMAINS.add(domain_to_block)
                    # --- UPDATE THIS FUNCTION CALL ---
                    save_general_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting, self.show_clock_setting, BLOCKED_DOMAINS, VIDEO_PLAYER_PATH)
                    self.all_articles = [a for a in self.all_articles if get_domain_from_url(a.get('url')) != domain_to_block]
                    self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))
                    self.status_message, self.status_message_timer = f"Domain '{domain_to_block}' is now hidden.", 50
                    self.force_regenerate_view = True
            self.is_action_menu_view = False
        self.needs_redraw = True

    def handle_settings_input(self, key):
        global BLOCKED_DOMAINS, HIGHLIGHT_KEYWORDS, MUTE_KEYWORDS, VIDEO_PLAYER_PATH

        if key == "ESC":
            self.blocked_domains_setting = self.blocked_domains_setting.strip(',')
            BLOCKED_DOMAINS = {d.strip() for d in self.blocked_domains_setting.split(',') if d.strip()}
            # --- UPDATE THIS VARIABLE BEFORE SAVING ---
            VIDEO_PLAYER_PATH = self.video_player_path_setting.strip()
            # --- UPDATE THIS FUNCTION CALL ---
            save_general_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting,
                                 self.show_clock_setting, BLOCKED_DOMAINS, VIDEO_PLAYER_PATH)

            self.highlight_keywords_setting = self.highlight_keywords_setting.strip(',')
            self.mute_keywords_setting = self.mute_keywords_setting.strip(',')
            HIGHLIGHT_KEYWORDS = {kw.strip().lower() for kw in self.highlight_keywords_setting.split(',') if kw.strip()}
            MUTE_KEYWORDS = {kw.strip().lower() for kw in self.mute_keywords_setting.split(',') if kw.strip()}
            save_profile_keywords(self.active_profile, self.highlight_keywords_setting, self.mute_keywords_setting)

            self.is_settings_view = False
            self.force_regenerate_view = True
            self.needs_redraw = True
            return

        if key == "UP":
            self.settings_selected_index = max(0, self.settings_selected_index - 1)
        elif key == "DOWN":
            # --- UPDATE MAX INDEX ---
            self.settings_selected_index = min(9, self.settings_selected_index + 1)
        else:
            idx = self.settings_selected_index
            if idx == 0:  # Refresh Time
                if key == "LEFT": self.fetch_interval_setting = max(15, self.fetch_interval_setting - 15)
                elif key == "RIGHT": self.fetch_interval_setting += 15
            elif idx == 1:  # Theme
                if key == "RIGHT": self.current_theme_index = (self.current_theme_index + 1) % len(self.theme_names)
                elif key == "LEFT": self.current_theme_index = (self.current_theme_index - 1 + len(self.theme_names)) % len(self.theme_names)
                self.theme = THEMES[self.theme_names[self.current_theme_index]]
            elif idx == 2:  # Show Clock
                if key == "LEFT" or key == "RIGHT": self.show_clock_setting = not self.show_clock_setting
            # --- ADD THIS NEW BLOCK ---
            elif idx == 3:  # Video Player Path
                if key == "BACKSPACE": self.video_player_path_setting = self.video_player_path_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.video_player_path_setting += key
            # --- UPDATE INDICES FOR THE FOLLOWING BLOCKS ---
            elif idx == 4:  # Blocked Domains
                if key == "BACKSPACE": self.blocked_domains_setting = self.blocked_domains_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.blocked_domains_setting += key
            elif idx == 5:  # Highlight Keywords
                if key == "BACKSPACE": self.highlight_keywords_setting = self.highlight_keywords_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.highlight_keywords_setting += key
            elif idx == 6:  # Mute Keywords
                if key == "BACKSPACE": self.mute_keywords_setting = self.mute_keywords_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.mute_keywords_setting += key
            elif key == "ENTER": # Handle action items at the bottom
                # --- UPDATE INDICES ---
                if idx == 7:  # Export Bookmarks
                    export_bookmarks_to_html()
                    self.status_message, self.status_message_timer, self.is_settings_view = "Bookmarks exported to backups folder!", 50, False
                elif idx == 8:  # Export Full Backup
                    backups_dir = CONFIG_DIR / "backups"
                    backups_dir.mkdir(exist_ok=True)
                    dest_path = backups_dir / f"backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
                    shutil.copy(DB_FILE, dest_path)
                    self.status_message, self.status_message_timer, self.is_settings_view = f"Backup saved to backups folder!", 50, False
                elif idx == 9:  # Import from Backup
                    self.is_settings_view = False
                    self.is_import_view = True
        self.needs_redraw = True

    def _get_action_menu_options(self):
        options = {
            "Open Article in Browser": "open_article",
            "Open Comments in Browser": "open_comments",
            "Summarize with Perplexity": "summarize",
            "---SEPARATOR_1---": "separator",
            "Copy URL to Clipboard": "copy_url",
            "Archive Page (archive.is)": "archive",
            "---SEPARATOR_2---": "separator",
            "Exclude this domain": "exclude_domain",
            "---SEPARATOR_3---": "separator",
            "Delete Article": "delete_article"
        }
        if self.action_menu_article:
            domain = get_domain_from_url(self.action_menu_article.get('url', ''))
            if domain in ['youtube.com', 'youtu.be']:
                items = list(options.items())
                # --- This line is updated for the new text and action name ---
                items.insert(1, ("Launch in Video Player", "watch_video"))
                options = dict(items)
        return options

    def _copy_to_clipboard(self, text):
        try:
            if sys.platform == "win32":
                subprocess.run(["clip"], input=text.strip().encode('utf-8'), check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.strip().encode('utf-8'), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.strip().encode('utf-8'), check=True)
        except (FileNotFoundError, subprocess.CalledProcessError): pass

    def _parse_comments_to_tree(self, comments_json, depth=0):
        tree = []
        for cmt in comments_json:
            if cmt['kind'] == 't1':
                node = CommentNode(cmt['data'], depth)
                if 'replies' in cmt['data'] and cmt['data'].get('replies'):
                    node.children = self._parse_comments_to_tree(cmt['data']['replies']['data']['children'], depth + 1)
                tree.append(node)
        return tree

    def _flatten_comment_tree(self, nodes, result):
        for node in nodes:
            result.append(node)
            if not node.is_collapsed and node.children: self._flatten_comment_tree(node.children, result)

    def _fetch_comments_threaded(self, permalink):
        if not permalink: self.comment_view_status, self.needs_redraw = "Error: No permalink.", True; return
        try:
            url, headers = f"https://www.reddit.com{permalink.rstrip('/')}.json", {"User-Agent": "live_news_feed_script/2.6"}
            response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
            raw_comments = response.json()[1].get("data", {}).get("children", [])
            if not raw_comments: self.comment_view_status = "No comments found."
            else: self.comment_tree, self.comment_view_status = self._parse_comments_to_tree(raw_comments), ""
            self.comment_selected_index, self.comment_scroll_top = 0, 0

            # Prepare the lines for drawing once
            self._prepare_comment_lines()

        except (requests.exceptions.RequestException, IndexError, KeyError) as e: self.comment_view_status = f"Error: {e}"
        self.needs_redraw = True

    def _format_comment_body(self, text):
        text = html.unescape(text)

        def replace_link(match):
            link_text, url = match.group(1), match.group(2)
            # FIX: Use specific "off" codes and reset only the foreground color.
            # This no longer uses the aggressive RESET and will not affect the background.
            return (
                f"{Colors.UNDERLINE}{Colors.BLUE}{link_text}{Colors.UNDERLINE_OFF}"
                f"{self.theme['popup_fg']}"
            )

        # Use a robust regex that handles special characters in links
        text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', replace_link, text)

        # Use specific "off" codes for other styles
        text = re.sub(r'\*\*(.*?)\*\*', f'{Colors.BOLD}\\1{Colors.BOLD_OFF}', text)
        text = re.sub(r'~~(.*?)~~', f'{Colors.STRIKETHROUGH}\\1{Colors.STRIKETHROUGH_OFF}', text)
        text = re.sub(r'\*(.*?)\*', f'{Colors.ITALIC}\\1{Colors.ITALIC_OFF}', text)

        text = re.sub(r'`(.*?)`', f'{Colors.INLINE_CODE_BG}\\1{Colors.RESET}', text)
        text = re.sub(r'>!(.*?)!<', f'\x1b[30;40m\\1{Colors.RESET}', text)

        return text

    # --- NEW: Method to extract links from a comment body ---
    def _extract_links_from_comment(self, comment_body):
        """Parses a comment body and returns a list of found links."""
        # Regex for Markdown links: [text](url)
        markdown_regex = r'\[([^\]]+)\]\((https?:\/\/[^\)]+)\)'
        # Regex for raw http/https links in the text, avoiding those already in markdown
        raw_link_regex = r'(?<!\]\()(https?:\/\/[^\s<>"\'`]+)'

        found_links = []
        # Find Markdown links first
        for match in re.finditer(markdown_regex, comment_body):
            text, url = match.groups()
            found_links.append({"text": text.strip(), "url": url.strip()})

        # Find raw links, avoiding duplicates already found in Markdown links
        existing_urls = {link['url'] for link in found_links}
        for match in re.finditer(raw_link_regex, comment_body):
            url = match.group(1).strip()
            if url not in existing_urls:
                # For raw links, create a short, clean text representation
                display_text = url.replace("https://", "").replace("http://", "")
                if len(display_text) > 50:
                    display_text = display_text[:47] + "..."
                found_links.append({"text": display_text, "url": url})

        return found_links

    def _draw_popup_border(self, start_x, start_y, pop_w, pop_h, title=""):
        pop_bg = self.theme['popup_bg']
        border_color = self.theme['highlight_bg']
        sys.stdout.write(border_color)
        if title:
            title_text = f" {title} "
            content_width = pop_w - 2
            remaining_width = content_width - len(title_text)
            left_dashes, right_dashes = remaining_width // 2, remaining_width - (remaining_width // 2)
            top_border = f"┌{'─' * left_dashes}{title_text}{'─' * right_dashes}┐"
        else: top_border = f"┌{'─' * (pop_w - 2)}┐"
        sys.stdout.write(f'\x1b[{start_y};{start_x}H{top_border}')
        for i in range(pop_h - 2):
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x}H│')
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x + pop_w - 1}H│')
        sys.stdout.write(f'\x1b[{start_y + pop_h - 1};{start_x}H└' + '─' * (pop_w - 2) + '┘')
        sys.stdout.write(pop_bg)
        for i in range(pop_h - 2):
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x + 1}H{" " * (pop_w - 2)}')

    def _prepare_comment_lines(self):
        """Processes the comment tree into a list of drawable lines."""
        if not self.comment_tree:
            self.comment_lines_to_draw = []
            return

        self.visible_comments = []
        self._flatten_comment_tree(self.comment_tree, self.visible_comments)

        pop_w, term_h = os.get_terminal_size()
        cont_w = int(pop_w * 0.9) - 4
        pop_fg = self.theme['popup_fg']

        lines = []
        for c in self.visible_comments:
            comment_index = self.visible_comments.index(c)
            header = f"{'[-] ' if c.children and not c.is_collapsed else '[+] ' if c.children else ''}{Colors.YELLOW}{c.author}{pop_fg} ({c.score}):"
            lines.append({'text': f"{'  '*c.depth}{header}", 'idx': comment_index})

            formatted_body = self._format_comment_body(c.body)
            for line in formatted_body.split('\n'):
                prefix, quote_offset = "", 0
                is_quote = line.startswith('>')

                if is_quote:
                    line = line[1:].lstrip()
                    prefix = f"{Colors.GREEN}┃ {Colors.RESET}"
                    quote_offset = 2

                wrapped_lines = textwrap.wrap(line, width=cont_w - len("  "*c.depth) - quote_offset)
                for wrapped_line in wrapped_lines:
                    # If it's a quote, color the text grey. Otherwise, use the default.
                    if is_quote:
                        styled_line = f"{Colors.LIGHT_GREY}{wrapped_line}{pop_fg}"
                    else:
                        styled_line = wrapped_line
                    lines.append({'text': f"{'  '*c.depth}{prefix}{styled_line}", 'idx': comment_index})

        self.comment_lines_to_draw = lines

    def _draw_confirmation_popup(self, items_data, prompt):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = len(prompt) + 6, 5
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h)
        sys.stdout.write(f"\x1b[{start_y + 2};{start_x + 3}H{self.theme['popup_bg']}{Colors.YELLOW}{prompt}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_import_instructions(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        # Adjusted height for the new lines of text
        pop_w, pop_h = 70, 19
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Import from Backup")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        executable_name = "AlienNewsFeed.exe" if sys.platform == "win32" else "./AlienNewsFeed"

        # Updated content to show both import command examples
        content = [
            f"{Colors.YELLOW}There are two ways to import a backup:",
            "",
            f"{Colors.BOLD}A) Manually Replace The File:{Colors.BOLD_OFF}",
            "   1. Close this application.",
            "   2. Replace the database file in the config folder below",
            "      with your backup file.",
            "",
            f"{Colors.BOLD}B) Use the Command-Line on Next Launch:{Colors.BOLD_OFF}",
            f"   > {Colors.CYAN}{executable_name} --import /path/to/backup.db{pop_fg}",
            f"     {Colors.LIGHT_GREY}(Imports to the currently active profile){pop_fg}",
            "",
            f"   > {Colors.CYAN}{executable_name} --import /path/to/backup.db --profile <NAME>{pop_fg}",
            "",
            f"{Colors.YELLOW}Your config folder path is:",
            f"{Colors.CYAN}{CONFIG_DIR.resolve()}",
            "",
            f"{Colors.LIGHT_GREY}Press [ESC] to dismiss this message."
        ]

        for i, line in enumerate(content):
            row = start_y + 1 + i
            plain_text_len = len(re.sub(r'\x1b\[[0-9;]*m', '', str(line)))
            padding = ' ' * max(0, (pop_w - 4) - plain_text_len)

            sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{line}{padding}{Colors.RESET}")

        sys.stdout.flush()

    def _draw_help_menu(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, 17
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Help / About")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        key_color, desc_color, header_color = Colors.YELLOW, pop_fg, Colors.CYAN

        content = [
            f"{header_color}== Navigation ==",
            f"  {key_color}[↑/↓]{desc_color}     - Navigate articles",
            f"  {key_color}[←/→]{desc_color}     - Page up/down",
            f"{header_color}== Actions ==",
            f"  {key_color}[Enter]{desc_color}   - Open Action Menu for selected article",
            f"  {key_color}[b]{desc_color}       - Bookmark/unbookmark an article",
            f"  {key_color}[c]{desc_color}       - View article comments in-app",
            f"  {key_color}[Del]{desc_color}     - Delete an article permanently",
            f"{header_color}== Views & Modes ==",
            f"  {key_color}[/]{desc_color}       - Enter search mode (Press Enter to browse results)",
            f"  {key_color}[v]{desc_color}       - Open Filter Menu",
            f"  {key_color}[p]{desc_color}       - Open Profile Manager",
            f"  {key_color}[s]{desc_color}       - Open settings",
            f"  {key_color}[h]{desc_color}       - Show this help screen",
            f"  {key_color}[ESC]{desc_color}     - Go back, clear search, or show quit confirmation",
        ]
        for i, line in enumerate(content):
            sys.stdout.write(f"\x1b[{start_y + 1 + i};{start_x + 2}H{pop_bg}{line.ljust(pop_w - 4)}{Colors.RESET}")

        # --- Create and draw the custom footer ---
        footer_text = " 👽 Alien News Feed v0.2 - Created by Old Lamps "
        # Correctly calculate the display length, accounting for the double-width emoji
        footer_len = len(footer_text) + 1

        content_width = pop_w - 2

        # The typo was in this calculation block in the previous version
        remaining_width = content_width - footer_len
        left_dashes = remaining_width // 2
        right_dashes = remaining_width - left_dashes

        border_bg = self.theme['highlight_bg']
        text_color = self.theme['highlight_fg']
        border_element_color = self.theme['popup_fg']

        bottom_border_str = (
            f"{border_element_color}└{'─' * left_dashes}"
            f"{text_color}{footer_text}"
            f"{border_element_color}{'─' * right_dashes}┘"
        )

        sys.stdout.write(f"\x1b[{start_y + pop_h - 1};{start_x}H{border_bg}{bottom_border_str}")
        sys.stdout.write(Colors.RESET)

        sys.stdout.flush()

    def _draw_action_menu(self, items_data):
        self._draw(items_data, is_background=True)
        options_dict = self._get_action_menu_options()
        options_list = list(options_dict.keys())
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 50, len(options_list) + 3
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Actions")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        current_row, action_idx = start_y + 1, 0
        for option_text in options_list:
            current_row += 1
            if option_text.startswith("---"):
                sys.stdout.write(f"\x1b[{current_row};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")
                continue
            text = option_text.center(pop_w - 4)
            color = self.theme.get('delete_fg', Colors.RED) if options_dict[option_text] == 'delete_article' else pop_fg
            if action_idx == self.action_menu_selected_index:
                sys.stdout.write(f"\x1b[{current_row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{current_row};{start_x+2}H{pop_bg}{color}{text}{Colors.RESET}")
            action_idx += 1
        sys.stdout.flush()

    def _draw_filter_menu(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 30, len(self.view_modes) + 4
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Filter View")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        for i, mode in enumerate(self.view_modes):
            row = start_y + 2 + i
            prefix = "✓ " if i == self.current_view_mode_index else "  "
            display_text = f"  {prefix}{mode}".ljust(pop_w - 4)
            if i == self.filter_menu_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{display_text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{display_text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_settings(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        # --- Make sure popup height is increased to 17 ---
        pop_w, pop_h = 70, 17
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Settings")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # --- Variables to hold text being edited ---
        video_path_text = self.video_player_path_setting
        blocked_text = self.blocked_domains_setting
        highlight_text = self.highlight_keywords_setting
        mute_text = self.mute_keywords_setting

        # --- Ensure the cursor logic indices are correct ---
        if self.settings_selected_index == 3: video_path_text += "_"
        if self.settings_selected_index == 4: blocked_text += "_"
        if self.settings_selected_index == 5: highlight_text += "_"
        if self.settings_selected_index == 6: mute_text += "_"

        clock_status = "< Enabled >" if self.show_clock_setting else "< Disabled >"
        options = [
            f"Refresh Time (s): < {self.fetch_interval_setting} >",
            f"Color Theme: < {self.theme_names[self.current_theme_index]} >",
            f"Show Clock: {clock_status}",
            # --- This is the new option that should appear ---
            f"Video Player Path: {video_path_text}",
            f"Blocked Domains: {blocked_text}",
            f"Highlight Words: {highlight_text}",
            f"Mute Words: {mute_text}",
            "SEPARATOR",
            "Export Bookmarks to HTML",
            "Export Full Backup",
            "Import from Backup"
        ]
        # --- Divider position must be updated to 7 ---
        divider_pos = 7
        for i, option in enumerate(options):
            row = start_y + 2 + i
            if i > divider_pos: row += 1
            if i == divider_pos:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")
                continue
            text = option.ljust(pop_w - 4)
            logical_index = i - 1 if i > divider_pos else i
            if logical_index == self.settings_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")

        help_footer_y = start_y + pop_h - 2
        sys.stdout.write(f"\x1b[{help_footer_y -1};{start_x}H{pop_bg}{pop_fg}├{'─'*(pop_w-2)}┤")

        # --- The help string indices must also be updated ---
        help_strings = {
            3: "Enter the full path to your video player executable (e.g., /usr/bin/mpv)",
            4: "Enter comma-separated domains (e.g., site.com,another.org)",
            5: "Enter comma-separated words to highlight article titles",
            6: "Enter comma-separated words to hide articles from the feed"
        }
        help_text = help_strings.get(self.settings_selected_index, "")
        sys.stdout.write(f"\x1b[{help_footer_y};{start_x+2}H{pop_bg}{Colors.CYAN}{help_text.ljust(pop_w - 4)}")
        sys.stdout.write(Colors.RESET)
        sys.stdout.flush()

    def _draw_comments(self, items_data):
        # Allow this method to be called for background drawing without clearing the whole screen
        if not self.is_link_view:
            self._draw(items_data, is_background=True)

        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = int(term_w * 0.9), int(term_h * 0.9)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Comments")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        sys.stdout.write(f"\x1b[{start_y+pop_h-2};{start_x}H{pop_bg}{pop_fg}├{'─'*(pop_w-2)}┤")

        if self.comment_view_status:
            sys.stdout.write(f'\x1b[{start_y+2};{start_x+2}H{pop_bg}{pop_fg}{self.comment_view_status.ljust(pop_w-4)}{Colors.RESET}')
        elif self.comment_tree:
            lines = self.comment_lines_to_draw
            cont_w, cont_h = pop_w - 4, pop_h - 4

            sel_line = next((i for i, line in enumerate(lines) if line['idx'] == self.comment_selected_index), -1)

            if sel_line != -1:
                if sel_line < self.comment_scroll_top: self.comment_scroll_top = sel_line
                elif sel_line >= self.comment_scroll_top + cont_h:
                    self.comment_scroll_top = min(sel_line - cont_h + 2, max(0, len(lines) - cont_h))

            for i in range(cont_h):
                line_idx = self.comment_scroll_top + i
                if line_idx >= len(lines): break
                line_data, row = lines[line_idx], start_y+1+i
                is_sel = line_data['idx'] == self.comment_selected_index

                text_to_draw = line_data['text']
                plain_text_len = len(re.sub(r'\x1b\[[0-9;]*m', '', text_to_draw))
                padding = ' ' * max(0, cont_w - plain_text_len)

                if is_sel:
                    sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text_to_draw}{padding}{Colors.RESET}")
                else:
                    sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text_to_draw}{padding}{Colors.RESET}")

        help_text = "[↑/↓]Scroll [←/→]Top-Lvl [↵]Collapse [l]Links [ESC]Back".center(pop_w - 2)
        sys.stdout.write(f"\x1b[{start_y+pop_h-2};{start_x+1}H{pop_bg}{pop_fg}{help_text}{Colors.RESET}")
        sys.stdout.flush()

    # --- NEW: Method to draw the link extraction popup ---
    def _draw_link_popup(self, items_data):
        # 1. Draw the background (the comment view)
        self._draw_comments(items_data)

        # 2. Define popup dimensions
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, min(15, len(self.extracted_links) + 4)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        # 3. Draw the border and title
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Links in Comment")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # 4. List the links
        for i, link in enumerate(self.extracted_links):
            row = start_y + 1 + i # Start one line lower for content
            # Truncate text and url to fit the popup width
            display_text = f" {link['text'][:30]:<30} → {link['url'][:30]}"
            display_text = display_text.ljust(pop_w - 4)

            if i == self.link_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{display_text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{display_text}{Colors.RESET}")

        # 5. Add a help footer
        help_text = "[↑/↓] Select | [↵] Open | [ESC] Back".center(pop_w - 2)
        sys.stdout.write(f"\x1b[{start_y + pop_h - 2};{start_x + 1}H{pop_bg}{pop_fg}{help_text}{Colors.RESET}")
        sys.stdout.flush()


    def _draw(self, items_data, is_background=False):
        if not is_background: sys.stdout.write(Colors.RESET)
        os.system('cls' if os.name == 'nt' else 'clear')
        term_w, term_h = os.get_terminal_size()

        safe_width = term_w - 1

        title = f"  {self.title} [{self.active_profile}]"

        current_mode = self.view_modes[self.current_view_mode_index]
        title += f" [{current_mode}]"

        if self.is_search_view: title += f" [Search: {self.search_query}]"
        BG_BAR, FG_BAR = self.theme['bar_bg'], self.theme['bar_fg']

        if self.show_clock_setting:
            current_time = time.strftime("%A, %B %d, %Y %I:%M %p")
            plain_title_len = len(re.sub(r'\x1b\[[0-9;]*m', '', title))
            padding = ' ' * max(0, safe_width - plain_title_len - len(current_time))
            header_text = f"{title}{padding}{current_time}"
        else:
            header_text = title.ljust(safe_width)

        sys.stdout.write(f'\x1b[1;1H{BG_BAR}{FG_BAR}{header_text}{Colors.RESET}')

        HL_BG, FG_HL = self.theme['highlight_bg'], self.theme['highlight_fg']
        if not items_data:
            sys.stdout.write(f'\x1b[3;1HNo articles found...{Colors.RESET}')
        else:
            self.selected_index = max(0, min(self.selected_index, len(items_data)-1))
            max_view = max(1, term_h - 5)
            if self.selected_index < self.scroll_top: self.scroll_top = self.selected_index
            if self.selected_index >= self.scroll_top + max_view: self.scroll_top = self.selected_index-max_view+1
            for i in range(self.scroll_top, min(self.scroll_top+max_view, len(items_data))):
                item, row = items_data[i], i-self.scroll_top+3
                is_highlighted = any(kw in item['title'].lower() for kw in HIGHLIGHT_KEYWORDS)
                highlight_icon = f"{Colors.YELLOW}★ {Colors.RESET}" if is_highlighted else ""

                sub, src = f"{Colors.GREEN}[{item.get('subreddit')}]", f"{Colors.CYAN}[{item.get('source_domain','')}]"
                bookmark = "🔖 " if item.get('is_bookmarked') else ""
                video_icon = "🎬 " if item.get('source_domain') in ['youtube.com', 'youtu.be'] else ""
                title_color = ""
                if item.get('is_new'): title_color = self.theme['new_fg']
                elif item.get('is_read'): title_color = Colors.LIGHT_GREY

                display = f"{title_color}{format_time_ago(item.get('created_utc')):<8} {sub} {src}{Colors.RESET} {highlight_icon}{bookmark}{video_icon}{item.get('title')}{Colors.RESET}"
                line = f"> {display}" if i == self.selected_index else f"  {display}"

                # FIX: Replace the simple ljust with our robust padding logic
                plain_text_len = len(re.sub(r'\x1b\[[0-9;]*m', '', line))
                # Truncate if too long, pad with spaces if too short
                if plain_text_len > safe_width:
                    # This is a complex problem; for now, we just prevent wrapping
                    line_to_draw = line
                else:
                    padding = ' ' * (safe_width - plain_text_len)
                    line_to_draw = line + padding

                if i == self.selected_index and not is_background:
                    sys.stdout.write(f'\x1b[{row};1H{self.theme["highlight_bg"]}{self.theme["highlight_fg"]}{line_to_draw}{Colors.RESET}')
                else: sys.stdout.write(f'\x1b[{row};1H{line_to_draw}{Colors.RESET}')

        footer_row = term_h
        if self.status_message_timer > 0: help_text = self.status_message
        elif self.search_input_active: help_text = f"Search: {self.search_query}_"
        elif self.is_search_view: help_text = f"Browsing search. [/] Edit | [ESC] Clear"
        else:
            help_text = "[v]Views [/]Srch [p]Profiles |[b]Mark [c]Cmnts |[s]Settings [h]Help |[ESC]Quit"

        status_indicator = "🟢" if CONNECTION_OK else "🔴"
        last_checked = f"{status_indicator} Last checked: {last_checked_time}"

        padding = ' ' * max(0, safe_width - len(help_text) - len(last_checked))
        footer_text = f"{help_text}{padding}{last_checked}"

        sys.stdout.write(f'\x1b[{footer_row};1H{BG_BAR}{FG_BAR}{footer_text}{Colors.RESET}')
        sys.stdout.flush()

    def show(self):
        global HAS_NEW_ARTICLES, NEEDS_RESTART
        self.master_article_list = get_articles_from_db()
        items_data = []
        while self.is_running:
            # Check for terminal resize
            current_width, current_height = os.get_terminal_size()
            if (current_width, current_height) != (self.last_known_width, self.last_known_height):
                self.needs_redraw = True
                self.last_known_width, self.last_known_height = current_width, current_height

            if NEEDS_RESTART:
                self.is_running = False
                continue
            if self.status_message_timer > 0:
                self.status_message_timer -= 1
                if self.status_message_timer == 0: self.status_message, self.needs_redraw = "", True
            if ARTICLES_UPDATED.is_set():
                if HAS_NEW_ARTICLES:
                    self.master_article_list = get_articles_from_db()
                    with data_lock:
                        if HAS_NEW_ARTICLES: self.selected_index, self.scroll_top, HAS_NEW_ARTICLES = 0,0,False
                self.force_regenerate_view = True
                ARTICLES_UPDATED.clear()
            if self.show_clock_setting:
                current_minute = time.localtime().tm_min
                if current_minute != self.last_displayed_minute:
                    self.last_displayed_minute, self.needs_redraw = current_minute, True

            if self.force_regenerate_view:
                self.all_articles = [a for a in self.master_article_list if not any(kw in a['title'].lower() for kw in MUTE_KEYWORDS)] if MUTE_KEYWORDS else self.master_article_list
                current_mode = self.view_modes[self.current_view_mode_index]
                if current_mode == "Bookmarks": items_data = [a for a in self.all_articles if a['is_bookmarked']]
                elif current_mode == "Highlights": items_data = [a for a in self.all_articles if any(kw in a['title'].lower() for kw in HIGHLIGHT_KEYWORDS)]
                elif current_mode == "Unseen": items_data = [a for a in self.all_articles if a['is_new']]
                elif current_mode == "Read": items_data = [a for a in self.all_articles if a['is_read']]
                elif current_mode == "Video": items_data = [a for a in self.all_articles if get_domain_from_url(a.get('url')) in ['youtube.com', 'youtu.be', 'vimeo.com']]
                else: items_data = self.all_articles

                if self.is_search_view:
                    q = self.search_query.lower()
                    items_data = [
                        a for a in items_data if
                        q in a['title'].lower()
                        or q in a.get('source_domain','').lower()
                        or q in a.get('subreddit', '').lower()
                    ]

                self.force_regenerate_view = False
                self.needs_redraw = True

            if self.needs_redraw:
                if self.is_delete_confirm_view: self._draw_confirmation_popup(items_data, "Permanently delete this article? (y/n)")
                elif self.is_exit_confirm_view: self._draw_confirmation_popup(items_data, "Are you sure you want to quit? (y/n)")
                elif self.is_action_menu_view: self._draw_action_menu(items_data)
                elif self.is_settings_view: self._draw_settings(items_data)
                elif self.is_filter_menu_view: self._draw_filter_menu(items_data)
                elif self.is_link_view: self._draw_link_popup(items_data)
                elif self.is_comment_view: self._draw_comments(items_data)
                elif self.is_help_view: self._draw_help_menu(items_data)
                elif self.is_import_view: self._draw_import_instructions(items_data)
                elif self.is_profile_view: self._draw_profile_manager(items_data)
                else: self._draw(items_data)
                self.needs_redraw = False

            key = getch()
            if not key: continue
            if self.is_delete_confirm_view: self.handle_delete_confirm_input(key, items_data)
            elif self.is_exit_confirm_view: self.handle_exit_confirm_input(key)
            elif self.is_action_menu_view: self.handle_action_menu_input(key)
            elif self.is_settings_view: self.handle_settings_input(key)
            elif self.is_filter_menu_view: self.handle_filter_menu_input(key)
            elif self.is_link_view: self.handle_link_input(key)
            elif self.is_comment_view: self.handle_comment_view_input(key)
            elif self.is_help_view: self.handle_help_view_input(key)
            elif self.is_import_view: self.handle_import_view_input(key)
            elif self.is_profile_view: self.handle_profile_input(key)
            elif self.is_search_view: self.handle_search_view_input(key, items_data)
            else: self.handle_main_view_input(key, items_data)

    def handle_delete_confirm_input(self, key, items_data):
        # If 'y' is pressed, perform the deletion.
        if key.lower() == 'y':
            if self.article_to_delete:
                # Delete from the database
                block_and_delete_article(self.article_to_delete['url'])

                # Delete from the in-memory master list to ensure the UI updates
                self.master_article_list = [a for a in self.master_article_list if a['url'] != self.article_to_delete['url']]

                # Trigger a full view regeneration and show a confirmation message
                self.force_regenerate_view = True
                self.status_message, self.status_message_timer = "Article deleted.", 50

        # After any key press ('y' or any other key to cancel),
        # reset the state to exit the confirmation view.
        self.is_delete_confirm_view, self.article_to_delete = False, None
        self.needs_redraw = True

    def handle_exit_confirm_input(self, key):
        if key.lower() == 'y': self.is_running = False
        else: self.is_exit_confirm_view, self.needs_redraw = False, True

    def handle_import_view_input(self, key):
        if key == "ESC": self.is_import_view, self.needs_redraw = False, True

    def handle_help_view_input(self, key):
        if key == "ESC" or key == "h": self.is_help_view, self.needs_redraw = False, True

    def handle_filter_menu_input(self, key):
        if key == "ESC": self.is_filter_menu_view = False
        elif key == "UP": self.filter_menu_selected_index = max(0, self.filter_menu_selected_index - 1)
        elif key == "DOWN": self.filter_menu_selected_index = min(len(self.view_modes) - 1, self.filter_menu_selected_index + 1)
        elif key == "ENTER":
            self.current_view_mode_index = self.filter_menu_selected_index
            self.is_filter_menu_view = False
            self.selected_index, self.scroll_top = 0, 0
            self.force_regenerate_view = True
        self.needs_redraw = True

    def handle_action_menu_input(self, key):
        options_dict = self._get_action_menu_options()
        actionable_options = {k: v for k, v in options_dict.items() if v != 'separator'}
        actionable_keys = list(actionable_options.keys())
        max_index = len(actionable_keys) - 1
        if key == "ESC": self.is_action_menu_view = False
        elif key == "UP": self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
        elif key == "DOWN": self.action_menu_selected_index = min(max_index, self.action_menu_selected_index + 1)
        elif key == "ENTER":
            action = actionable_options[actionable_keys[self.action_menu_selected_index]]
            url = self.action_menu_article['url']
            if action == "delete_article":
                self.article_to_delete, self.is_delete_confirm_view = self.action_menu_article, True
            elif action == "open_article": threading.Thread(target=webbrowser.open, args=(url,)).start()
            # --- This block is updated to match the new action and messages ---
            elif action == "watch_video":
                try:
                    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
                    if sys.platform == "win32": kwargs['creationflags'] = 0x00000200 | 0x00000008
                    else: kwargs['start_new_session'] = True
                    # It correctly uses the global variable for the path
                    subprocess.Popen([VIDEO_PLAYER_PATH, url], **kwargs)
                    # The feedback message is now generic
                    self.status_message, self.status_message_timer = "Launching in Video Player...", 50
                except FileNotFoundError: self.status_message, self.status_message_timer = f"Error: '{VIDEO_PLAYER_PATH}' not found.", 50
            elif action == "open_comments": threading.Thread(target=webbrowser.open, args=(f"https://www.reddit.com{self.action_menu_article['permalink']}",)).start()
            elif action == "summarize": threading.Thread(target=webbrowser.open, args=(f"https://www.perplexity.ai/?s=o&q={quote(f'summarize {url}')}",)).start()
            elif action == "copy_url":
                self._copy_to_clipboard(url)
                self.status_message, self.status_message_timer = "URL copied to clipboard!", 50
            elif action == "archive": threading.Thread(target=webbrowser.open, args=(f"https://archive.is/{quote(url)}",)).start()
            elif action == "exclude_domain":
                domain_to_block = get_domain_from_url(url)
                if domain_to_block and domain_to_block not in BLOCKED_DOMAINS:
                    BLOCKED_DOMAINS.add(domain_to_block)
                    save_general_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting, self.show_clock_setting, BLOCKED_DOMAINS, VIDEO_PLAYER_PATH)
                    self.all_articles = [a for a in self.all_articles if get_domain_from_url(a.get('url')) != domain_to_block]
                    self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))
                    self.status_message, self.status_message_timer = f"Domain '{domain_to_block}' is now hidden.", 50
                    self.force_regenerate_view = True
            self.is_action_menu_view = False
        self.needs_redraw = True

    def handle_settings_input(self, key):
        global BLOCKED_DOMAINS, HIGHLIGHT_KEYWORDS, MUTE_KEYWORDS, VIDEO_PLAYER_PATH

        if key == "ESC":
            self.blocked_domains_setting = self.blocked_domains_setting.strip(',')
            BLOCKED_DOMAINS = {d.strip() for d in self.blocked_domains_setting.split(',') if d.strip()}
            VIDEO_PLAYER_PATH = self.video_player_path_setting.strip()
            save_general_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting,
                                 self.show_clock_setting, BLOCKED_DOMAINS, VIDEO_PLAYER_PATH)

            self.highlight_keywords_setting = self.highlight_keywords_setting.strip(',')
            self.mute_keywords_setting = self.mute_keywords_setting.strip(',')
            HIGHLIGHT_KEYWORDS = {kw.strip().lower() for kw in self.highlight_keywords_setting.split(',') if kw.strip()}
            MUTE_KEYWORDS = {kw.strip().lower() for kw in self.mute_keywords_setting.split(',') if kw.strip()}
            save_profile_keywords(self.active_profile, self.highlight_keywords_setting, self.mute_keywords_setting)

            self.is_settings_view = False
            self.force_regenerate_view = True
            self.needs_redraw = True
            return

        if key == "UP":
            self.settings_selected_index = max(0, self.settings_selected_index - 1)
        elif key == "DOWN":
            self.settings_selected_index = min(9, self.settings_selected_index + 1)
        else:
            idx = self.settings_selected_index
            if idx == 0:  # Refresh Time
                if key == "LEFT": self.fetch_interval_setting = max(15, self.fetch_interval_setting - 15)
                elif key == "RIGHT": self.fetch_interval_setting += 15
            elif idx == 1:  # Theme
                if key == "RIGHT": self.current_theme_index = (self.current_theme_index + 1) % len(self.theme_names)
                elif key == "LEFT": self.current_theme_index = (self.current_theme_index - 1 + len(self.theme_names)) % len(self.theme_names)
                self.theme = THEMES[self.theme_names[self.current_theme_index]]
            elif idx == 2:  # Show Clock
                if key == "LEFT" or key == "RIGHT": self.show_clock_setting = not self.show_clock_setting
            # --- This block at index 3 correctly targets the video player setting ---
            elif idx == 3:  # Video Player Path
                if key == "BACKSPACE": self.video_player_path_setting = self.video_player_path_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.video_player_path_setting += key
            # --- The following indices are now correct ---
            elif idx == 4:  # Blocked Domains
                if key == "BACKSPACE": self.blocked_domains_setting = self.blocked_domains_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.blocked_domains_setting += key
            elif idx == 5:  # Highlight Keywords
                if key == "BACKSPACE": self.highlight_keywords_setting = self.highlight_keywords_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.highlight_keywords_setting += key
            elif idx == 6:  # Mute Keywords
                if key == "BACKSPACE": self.mute_keywords_setting = self.mute_keywords_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.mute_keywords_setting += key
            elif key == "ENTER":
                if idx == 7:  # Export Bookmarks
                    export_bookmarks_to_html()
                    self.status_message, self.status_message_timer, self.is_settings_view = "Bookmarks exported!", 50, False
                elif idx == 8:  # Export Full Backup
                    backups_dir = CONFIG_DIR / "backups"
                    backups_dir.mkdir(exist_ok=True)
                    dest_path = backups_dir / f"backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
                    shutil.copy(DB_FILE, dest_path)
                    self.status_message, self.status_message_timer, self.is_settings_view = f"Backup saved!", 50, False
                elif idx == 9:  # Import from Backup
                    self.is_settings_view = False
                    self.is_import_view = True
        self.needs_redraw = True

    # --- NEW: Handler for the link extraction popup ---
    def handle_link_input(self, key):
        if key == "ESC":
            self.is_link_view = False
            self.extracted_links = []
        elif key == "UP":
            self.link_selected_index = max(0, self.link_selected_index - 1)
        elif key == "DOWN":
            self.link_selected_index = min(len(self.extracted_links) - 1, self.link_selected_index + 1)
        elif key == "ENTER":
            if self.extracted_links:
                url_to_open = self.extracted_links[self.link_selected_index]['url']
                threading.Thread(target=webbrowser.open, args=(url_to_open,)).start()
                # Close the popup after opening the link
                self.is_link_view = False
                self.extracted_links = []
        self.needs_redraw = True

    def handle_comment_view_input(self, key):
        if self.visible_comments:
            original_index = self.comment_selected_index
            if key == "UP": self.comment_selected_index = max(0, self.comment_selected_index - 1)
            elif key == "DOWN": self.comment_selected_index = min(len(self.visible_comments) - 1, self.comment_selected_index + 1)
            elif key == 'LEFT':
                for i in range(self.comment_selected_index - 1, -1, -1):
                    if self.visible_comments[i].depth == 0: self.comment_selected_index = i; break
            elif key == 'RIGHT':
                for i in range(self.comment_selected_index + 1, len(self.visible_comments)):
                    if self.visible_comments[i].depth == 0: self.comment_selected_index = i; break
            elif key == "ENTER":
                selected_comment = self.visible_comments[self.comment_selected_index]
                if selected_comment.children:
                    selected_comment.is_collapsed = not selected_comment.is_collapsed
                    self._prepare_comment_lines() # Re-prepare lines after collapsing/expanding
            # --- NEW: Trigger for link extraction ---
            elif key == 'l':
                if self.visible_comments:
                    selected_comment = self.visible_comments[self.comment_selected_index]
                    links = self._extract_links_from_comment(selected_comment.body)
                    if links:
                        self.extracted_links = links
                        self.link_selected_index = 0
                        self.is_link_view = True
                    else:
                        self.status_message = "No links found in this comment."
                        self.status_message_timer = 30 # Show for ~3 seconds

            if original_index != self.comment_selected_index: self.needs_redraw = True

        if key == "ESC":
            self.is_comment_view, self.comment_tree = False, []
        self.needs_redraw = True

    def handle_search_view_input(self, key, items_data):
        if self.search_input_active:
            if key == "ENTER":
                self.search_input_active = False
                # No need to force regen here, as the view won't change
            elif key == "ESC":
                self.is_search_view, self.search_query, self.search_input_active = False, "", False
                self.force_regenerate_view = True # Clear search
            elif key == "BACKSPACE":
                self.search_query = self.search_query[:-1]
                self.force_regenerate_view = True # Force regen on change
            elif len(key) == 1 and key.isprintable():
                self.search_query += key
                self.force_regenerate_view = True # Force regen on change
        else:
            if key == '/': self.search_input_active = True
            elif key == "ESC":
                self.is_search_view, self.search_query = False, ""
                self.force_regenerate_view = True # Clear search
            else: self.handle_main_view_input(key, items_data)
        self.needs_redraw = True

    def handle_main_view_input(self, key, items_data):
        """Handles all key presses for the main article list view."""
        if not items_data and key not in ["ESC", "s", "v", "/", "h", "p"]: return
        original_index = self.selected_index
        if key == "UP": self.selected_index = max(0, self.selected_index - 1)
        elif key == "DOWN": self.selected_index = min(len(items_data) - 1, self.selected_index + 1)
        elif key == "LEFT" or key == "PGUP": self.selected_index = max(0, self.selected_index - self.page_jump)
        elif key == "RIGHT" or key == "PGDOWN": self.selected_index = min(len(items_data) - 1, self.selected_index + self.page_jump)
        elif key == "HOME": self.selected_index = 0
        elif key == "END": self.selected_index = len(items_data) - 1
        elif key == "DELETE":
            if items_data: self.article_to_delete, self.is_delete_confirm_view = items_data[self.selected_index], True
        elif key == "b":
            if items_data:
                selected = items_data[self.selected_index]
                new_status = not selected.get('is_bookmarked')
                update_article_status(url=selected['url'], is_bookmarked=new_status)
                selected['is_bookmarked'] = new_status
        elif key == "c":
            if items_data:
                self.is_comment_view, self.comment_view_status = True, "Loading comments..."
                threading.Thread(target=self._fetch_comments_threaded, args=(items_data[self.selected_index].get('permalink'),)).start()
        elif key == "s": self.is_settings_view = True
        elif key == "h": self.is_help_view = True
        elif key == "p": self.is_profile_view = True
        elif key == 'v':
            self.is_filter_menu_view = True
            self.filter_menu_selected_index = self.current_view_mode_index
        elif key == '/': self.is_search_view, self.search_query, self.search_input_active = True, "", True
        elif key == "ENTER":
            if items_data:
                self.is_action_menu_view, self.action_menu_article, self.action_menu_selected_index = True, items_data[self.selected_index], 0
                update_article_status(url=self.action_menu_article['url'], is_read=True, is_new=False)
                items_data[self.selected_index]['is_read'], items_data[self.selected_index]['is_new'] = True, False
        elif key == "ESC":
            if self.current_view_mode_index != 0:
                # If in a filtered view, Esc returns to the "All" view
                self.current_view_mode_index = 0
                self.selected_index, self.scroll_top = 0, 0
                self.force_regenerate_view = True
            else:
                # If already in the "All" view, then show quit confirmation
                self.is_exit_confirm_view = True

        if original_index != self.selected_index:
            if items_data:
                newly_selected = items_data[self.selected_index]
                if newly_selected.get('is_new'):
                    newly_selected['is_new'] = False
                    update_article_status(url=newly_selected['url'], is_new=False)
        self.needs_redraw = True

    def _draw_profile_manager(self, items_data):
        """Draws the fully functional Profile Manager UI."""
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 74, max(12, len(self.profiles) + 8)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Profile Manager")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        for i, name in enumerate(self.profiles):
            row = start_y + 1 + i
            # Adjusted to leave space for a 2-line footer (prompt + help bar)
            if row >= start_y + pop_h - 3: break
            prefix = "» " if i == self.profile_selected_index else "  "
            suffix = " (Active)" if name == self.active_profile else ""
            display_text = f"{prefix}{name}{suffix}".ljust(pop_w - 4)
            if i == self.profile_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{display_text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{display_text}{Colors.RESET}")

        prompt_y = start_y + pop_h - 3

        if self.profile_input_active:
            prompt_map = {'create': "Enter new profile name: ", 'rename': "Enter new name for profile: ", 'edit': "Enter subreddits (e.g. news+world): "}
            prompt = prompt_map.get(self.profile_action, "")

            available_width = (pop_w - 4) - len(prompt) - 1
            display_query = self.profile_input_query
            if len(display_query) > available_width:
                display_query = display_query[-available_width:]

            input_text = f"{prompt}{display_query}_"
            sys.stdout.write(f"\x1b[{prompt_y};{start_x + 2}H{pop_bg}{Colors.YELLOW}{input_text.ljust(pop_w - 4)}{Colors.RESET}")
            help_text = "[Enter] Confirm | [ESC] Cancel"
        elif self.profile_action == 'delete':
            selected_profile = self.profiles[self.profile_selected_index]
            prompt = f"Delete '{selected_profile}' and its DB? This is permanent. (y/n)"
            sys.stdout.write(f"\x1b[{prompt_y};{start_x + 2}H{pop_bg}{Colors.YELLOW}{prompt.ljust(pop_w - 4)}{Colors.RESET}")
            help_text = "[y] Confirm | [Any other key] Cancel"
        else:
            status_text = self.profile_status_message.ljust(pop_w - 4)
            sys.stdout.write(f"\x1b[{prompt_y};{start_x + 2}H{pop_bg}{Colors.GREEN}{status_text}{Colors.RESET}")
            help_text = "[↵]Switch [n]New [r]Rename [e]Edit [d]Delete [ESC]Back"

        # --- FIX: Consolidated Help Bar Drawing ---
        footer_y = start_y + pop_h - 2
        help_text_padded = f" {help_text} "

        content_width = pop_w - 2
        remaining_width = content_width - len(help_text_padded)
        left_dashes = remaining_width // 2
        right_dashes = remaining_width - left_dashes

        full_footer_bar = f"├{'─' * left_dashes}{help_text_padded}{'─' * right_dashes}┤"

        sys.stdout.write(f"\x1b[{footer_y};{start_x}H{pop_bg}{pop_fg}{full_footer_bar}{Colors.RESET}")
        sys.stdout.flush()

    def handle_profile_input(self, key):
        """Handles key presses for the functional Profile Manager."""
        global NEEDS_RESTART
        self.profile_status_message = ""
        if self.profile_input_active:
            if key == "ENTER":
                query = self.profile_input_query.strip()
                selected_profile = self.profiles[self.profile_selected_index]
                if self.profile_action == 'create' and query:
                    self.profile_status_message = f"Profile '{query}' created." if create_profile(query) else f"Error: Profile '{query}' already exists."
                elif self.profile_action == 'rename' and query:
                    self.profile_status_message = f"Renamed to '{query}'." if rename_profile(selected_profile, query) else "Error: Could not rename."
                elif self.profile_action == 'edit' and query:
                    self.profile_status_message = f"Updated subreddits for '{selected_profile}'." if update_profile_subreddits(selected_profile, query) else "Error: Could not update."
                self.profile_input_active, self.profile_action, self.profile_input_query = False, None, ""
                self.profiles = get_all_profiles()
            elif key == "ESC": self.profile_input_active, self.profile_action, self.profile_input_query = False, None, ""
            elif key == "BACKSPACE": self.profile_input_query = self.profile_input_query[:-1]
            elif len(key) == 1 and key.isprintable(): self.profile_input_query += key
            self.needs_redraw = True
            return
        if self.profile_action == 'delete':
            selected_profile = self.profiles[self.profile_selected_index]
            if key.lower() == 'y':
                if delete_profile(selected_profile):
                    self.profile_status_message = f"Profile '{selected_profile}' deleted."
                    self.profile_selected_index = max(0, self.profile_selected_index - 1)
                    self.profiles = get_all_profiles()
                else: self.profile_status_message = "Error: Cannot delete 'Main' profile."
            else: self.profile_status_message = "Deletion cancelled."
            self.profile_action = None
            self.needs_redraw = True
            return
        if key == "UP": self.profile_selected_index = max(0, self.profile_selected_index - 1)
        elif key == "DOWN": self.profile_selected_index = min(len(self.profiles) - 1, self.profile_selected_index + 1)
        elif key == "ESC": self.is_profile_view = False
        elif key == "ENTER":
            selected_profile = self.profiles[self.profile_selected_index]
            set_active_profile(selected_profile)
            self.profile_status_message = f"'{selected_profile}' is now active. Restarting..."
            NEEDS_RESTART, self.is_running = True, False
        elif key.lower() == 'n': self.profile_action, self.profile_input_active = 'create', True
        elif key.lower() == 'r':
            self.profile_action, self.profile_input_active = 'rename', True
            self.profile_input_query = self.profiles[self.profile_selected_index]
        elif key.lower() == 'e':
            self.profile_action, self.profile_input_active = 'edit', True
            config = configparser.ConfigParser(); config.read(CONFIG_FILE)
            section = f"Profile:{self.profiles[self.profile_selected_index]}"
            self.profile_input_query = config.get(section, 'subreddits', fallback='')
        elif key.lower() == 'd':
            if self.profiles[self.profile_selected_index] != 'Main': self.profile_action = 'delete'
            else: self.profile_status_message = "Cannot delete the 'Main' profile."
        self.needs_redraw = True

# --- Command-line and Utility Functions ---
def export_bookmarks_to_html():
    backups_dir = CONFIG_DIR / "backups"
    backups_dir.mkdir(exist_ok=True)
    dest_path = backups_dir / f"bookmarks-{time.strftime('%Y%m%d')}.html"
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE is_bookmarked = 1 ORDER BY created_utc DESC")
        bookmarks = [dict(row) for row in cursor.fetchall()]
    li_items = "<li>No bookmarks found.</li>" if not bookmarks else "\n".join([f'<li><a href="{b["url"]}">{b["title"]}</a> <span class="meta">({b.get("source_domain", "N/A")})</span></li>' for b in bookmarks])
    html_template = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Alien News Feed Bookmarks</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;background-color:#1e1e1e;color:#d4d4d4;line-height:1.6;margin:0;padding:2em;}}.container{{max-width:800px;margin:0 auto;}}h1{{color:#569cd6;border-bottom:1px solid #444;padding-bottom:.5em;}}p{{color:#999;}}ul{{list-style-type:none;padding:0;}}li{{margin-bottom:1em;padding:1em;background-color:#252526;border-left:3px solid #569cd6;}}a{{color:#9cdcfe;text-decoration:none;}}a:hover{{text-decoration:underline;}}.meta{{font-size:.8em;color:#888;margin-left:.5em;}}</style></head><body><div class="container"><h1>👽 Alien News Feed Bookmarks</h1><p>Exported on: {time.strftime('%Y-%m-%d %H:%M:%S')}</p><ul>{li_items}</ul></div></body></html>"""
    with open(dest_path, 'w', encoding='utf-8') as f: f.write(html_template)
    return dest_path

def export_database():
    backups_dir = CONFIG_DIR / "backups"
    backups_dir.mkdir(exist_ok=True)
    dest_path = backups_dir / f"backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
    try:
        shutil.copy(DB_FILE, dest_path)
        print(f"Success! Backup saved to:\n{dest_path}")
    except FileNotFoundError:
        print(f"Error: Database file not found at {DB_FILE}")
        sys.exit(1)

def import_database(path_str, profile_name):
    """Headless import of a database file to a specific profile with confirmation."""
    backup_path = Path(path_str)
    if not backup_path.is_file():
        print(f"Error: Backup file not found at '{backup_path}'")
        sys.exit(1)

    # Find the target database file based on the profile name
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    section_name = f"Profile:{profile_name}"

    if not config.has_section(section_name):
        print(f"Error: Profile '{profile_name}' not found in config file.")
        sys.exit(1)

    db_filename = config.get(section_name, 'DatabaseFile', fallback=None)
    if not db_filename:
        print(f"Error: DatabaseFile not configured for profile '{profile_name}'.")
        sys.exit(1)

    target_db_path = CONFIG_DIR / db_filename

    # Display a specific and clear warning message
    print("--- ⚠️ WARNING ---")
    print(f"This will permanently overwrite the database for the '{profile_name}' profile.")
    print(f"Target DB: {target_db_path}")
    print(f"Importing from: {backup_path}")
    confirm = input("Are you sure you want to continue? (y/n): ").lower().strip()

    if confirm in ['y', 'yes']:
        shutil.copy(backup_path, target_db_path)
        print("Import successful. Starting application...")
    else:
        print("Import cancelled.")
        sys.exit(0)

# --- Main Execution ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="A terminal-based news feed reader.")
    parser.add_argument('--export', action='store_true', help="Export a full backup of the database and exit.")
    parser.add_argument('--import', dest='import_path', metavar='PATH', help="Import a database from the specified path and start the app.")
    parser.add_argument('--profile', dest='profile_name', metavar='NAME', help="Specify a profile to import the database into (defaults to active profile).")
    args = parser.parse_args()
    pid_file = pid.PidFile(pidname='aliennewsfeed', piddir=CONFIG_DIR)

    NEEDS_RESTART = True
    fetch_thread = None

    while NEEDS_RESTART:
        NEEDS_RESTART = False
        if fetch_thread:
            stop_thread_event.set()
            fetch_thread.join()
            stop_thread_event.clear()

        setup_config()
        theme_name, active_profile = load_profile_settings()
        init_db(DB_FILE)

        if args.export:
            export_database()
            sys.exit(0)
        if args.import_path:
            # If --profile is specified, use it. Otherwise, use the active profile.
            target_profile = args.profile_name if args.profile_name else active_profile
            import_database(args.import_path, target_profile)
            # Prevent the import from running again if the app restarts
            args.import_path = None

        print(f"Initializing AlienNewsFeed...")
        print(f"Config and database stored in: {CONFIG_DIR}")

        fetch_thread = threading.Thread(target=fetch_articles_threaded, daemon=True)
        fetch_thread.start()

        time.sleep(1)
        menu = NewsFeedMenu(active_profile)

        try:
            with pid_file:
                menu.show()
        except pid.PidFileAlreadyLockedError:
            print("Another instance of Alien News Feed is already running. Exiting.")
            sys.exit(1)
        finally:
            if not NEEDS_RESTART:
                stop_thread_event.set()
                if fetch_thread:
                    fetch_thread.join()
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Exiting.")
                os._exit(0)
