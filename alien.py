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
import textwrap
import scraper
import traceback
import ollama_client
from pathlib import Path
from urllib.parse import urlparse, quote
import pid
from user_agents import (
    reddit_get,
    build_browser_headers,
    fetch_reddit_posts,
    fetch_reddit_domain_posts,
    fetch_reddit_comments
)

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
            'FetchInterval': '300',
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
            'FetchInterval': old_settings.get('fetchinterval', '300'),
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
        # Use UPSERT to update score and num_comments if the article already exists
        cursor.execute('''
            INSERT INTO articles (url, title, subreddit, source_domain, permalink, created_utc, score, num_comments, is_new) 
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET 
                score = excluded.score,
                num_comments = excluded.num_comments
        ''', (
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

def mark_all_as_seen_in_db():
    """Sets is_new to 0 for all new articles, leaving is_read untouched."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Only update the is_new column
        cursor.execute("UPDATE articles SET is_new = 0 WHERE is_new = 1")
        rows_affected = cursor.rowcount
        conn.commit()
        return rows_affected

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

            articles = fetch_reddit_posts(SUBREDDITS_STRING)
            CONNECTION_OK = True

            for article_data in articles:
                domain = get_domain_from_url(article_data.get("url"))
                if domain not in BLOCKED_DOMAINS:
                    add_article_to_db(article_data, deleted_urls)

            last_checked_time = time.strftime("%I:%M:%S %p")
            ARTICLES_UPDATED.set()
        except Exception:
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

        self.is_intelligence_view = False
        self.intelligence_data = {
            "article_title": "",
            "article_text": "",
            "ai_summary": "",
            "status_message": "Initializing...",
            "current_article_url": None,
            "wrapped_article_lines": [],
            "wrapped_ai_lines": [] 
        }

        self.intel_active_pane = 'article'  # Can be 'article' or 'ai'
        self.intel_article_scroll_top = 0
        self.intel_ai_scroll_top = 0
        self.is_link_view = False
        self.extracted_links = []
        self.link_selected_index = 0
        
        # --- AI Intelligence Tabs & Chat ---
        self.intel_tabs = ["Summary", "Deep Dive", "Chat"]
        self.intel_active_tab = 0  # 0: Summary, 1: Deep Dive, 2: Chat
        self.intel_chat_history = []  # List of dicts: {"role": "user"|"ai", "text": "..."}
        self.intel_chat_input = ""
        self.intel_chat_scroll_top = 0
        self.intel_chat_auto_scroll = True

        self.page_jump = PAGE_JUMP
        self.scroll_offset = 4
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

        self.is_subreddit_edit_view = False
        self.subreddit_list = []
        self.subreddit_selected_index = 0
        self.subreddit_input_active = False
        self.subreddit_input_query = ""
        self.subreddit_profile_target = None

        self.is_trend_view = False
        self.trend_time_range_index = 1  # Default to Day (0: Hour, 1: Day, 2: Week)
        self.trend_data = {
            "summary": "",
            "wrapped_lines": [],
            "status_message": "Ready to analyze.",
            "scroll_top": 0
        }

        self.is_domain_view = False
        self.domain_articles = []
        self.domain_view_title = ""
        self.domain_view_selected_index = 0
        self.domain_view_scroll_top = 0

        self.is_comment_analysis_view = False
        self.comment_analysis_data = {
            "summary": "",
            "wrapped_lines": [],
            "status_message": "Initializing...",
            "scroll_top": 0
        }

    def _draw_subreddit_editor(self, items_data):
        """Draws the interactive popup for adding/removing subreddits."""
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 60, max(10, len(self.subreddit_list) + 6)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        title = f"Edit Subreddits for '{self.subreddit_profile_target}'"
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, title)
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # Draw the list of subreddits
        for i, name in enumerate(self.subreddit_list):
            row = start_y + 1 + i
            if row >= start_y + pop_h - 3: break # Ensure it doesn't overlap footer
            prefix = "» " if i == self.subreddit_selected_index else "  "
            display_text = f"{prefix}{name}".ljust(pop_w - 4)
            if i == self.subreddit_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{display_text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{display_text}{Colors.RESET}")

        # Draw the input prompt or help text
        prompt_y = start_y + pop_h - 3
        if self.subreddit_input_active:
            input_text = f"Add Subreddit: {self.subreddit_input_query}_"
            sys.stdout.write(f"\x1b[{prompt_y};{start_x + 2}H{pop_bg}{Colors.YELLOW}{input_text.ljust(pop_w - 4)}{Colors.RESET}")
            help_text = "[Enter] Add | [ESC] Cancel"
        else:
            sys.stdout.write(f"\x1b[{prompt_y};{start_x + 2}H{pop_bg}{pop_fg}{' ' * (pop_w - 4)}{Colors.RESET}")
            help_text = "[↑/↓] Nav [a] Add [d] Delete [ESC] Save & Close"

        # Draw the main help footer bar
        footer_y = start_y + pop_h - 2
        help_text_padded = f" {help_text} ".center(pop_w - 2, '─')
        sys.stdout.write(f"\x1b[{footer_y};{start_x}H{pop_bg}{pop_fg}├{help_text_padded}┤{Colors.RESET}")
        sys.stdout.flush()

    def _draw_intelligence_view(self, items_data):
        """Draws the enhanced analysis view with scrolling and active pane indicators."""
        if self.is_domain_view:
            self._draw_domain_view(items_data, is_background=True)
        else:
            self._draw(items_data, is_background=True)

        term_w, term_h = os.get_terminal_size()
        
        start_x, start_y = 1, 1 
        pop_w, pop_h = term_w, term_h
        
        title = f" Intelligence View: {self.intelligence_data['article_title'][:term_w - 25]} "
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, title)
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # --- Vertical Split Calculations (66% / 33%) ---
        available_w = pop_w - 2
        article_w = int(available_w * 0.66)
        ai_w = available_w - article_w - 1 # -1 for separator
        
        content_pane_h = pop_h - 4
        ai_pane_h = pop_h - 4
        
        article_start_x = start_x + 1
        separator_x = start_x + 1 + article_w
        ai_start_x = separator_x + 1
        content_start_y = start_y + 1
        
        # Draw Vertical Separator
        for i in range(pop_h - 2):
            sys.stdout.write(f"\x1b[{start_y + 1 + i};{separator_x}H{pop_bg}{pop_fg}│{Colors.RESET}")
        
        # Headers
        article_pane_color = self.theme['highlight_bg'] if self.intel_active_pane == 'article' else pop_fg
        ai_pane_color = self.theme['highlight_bg'] if self.intel_active_pane == 'ai' else pop_fg
        
        # Article Header
        sys.stdout.write(f"\x1b[{start_y + 1};{article_start_x}H{pop_bg}{article_pane_color} Article Content {'─' * (article_w - 17)}{Colors.RESET}")
        
        # AI Header & Tabs
        # Draw base line first
        sys.stdout.write(f"\x1b[{start_y + 1};{ai_start_x}H{pop_bg}{ai_pane_color}{'─' * ai_w}{Colors.RESET}")
        
        # Draw Tabs
        current_tab_x = ai_start_x + 1
        for i, tab in enumerate(self.intel_tabs):
            # Styling: [ TabName ]
            # Active tab gets highlight color if AI pane is active, or just bright text if inactive
            is_active_tab = (i == self.intel_active_tab)
            
            if is_active_tab:
                tab_text = f" {tab} "
                if self.intel_active_pane == 'ai':
                    # Active Pane + Active Tab = Highlight
                    sys.stdout.write(f"\x1b[{start_y + 1};{current_tab_x}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{tab_text}{Colors.RESET}")
                else:
                    # Inactive Pane + Active Tab = Brighter Text
                    sys.stdout.write(f"\x1b[{start_y + 1};{current_tab_x}H{pop_bg}{Colors.BOLD}{Colors.CYAN}{tab_text}{Colors.RESET}")
            else:
                # Inactive Tab
                tab_text = f" {tab} "
                sys.stdout.write(f"\x1b[{start_y + 1};{current_tab_x}H{pop_bg}{pop_fg}{tab_text}{Colors.RESET}")
            
            current_tab_x += len(tab_text) + 1

        # --- DRAW SCROLLABLE ARTICLE PANE (LEFT) ---
        article_lines = self.intelligence_data.get('wrapped_article_lines', [])
        if article_lines:
            for i in range(content_pane_h - 1): # Reserve top line for header
                line_idx = self.intel_article_scroll_top + i
                if line_idx < len(article_lines):
                    line_text = article_lines[line_idx]
                    line_text = line_text[:article_w - 2]
                    sys.stdout.write(f"\x1b[{content_start_y + 1 + i};{article_start_x + 1}H{pop_bg}{Colors.LIGHT_GREY}{line_text.ljust(article_w - 2)}{Colors.RESET}")
        
            max_scroll = max(0, len(article_lines) - (content_pane_h - 1))
            scroll_percent = self.intel_article_scroll_top / max_scroll if max_scroll > 0 else 1.0
            scrollbar_pos = int(scroll_percent * (content_pane_h - 2))
            for i in range(content_pane_h - 1):
                char = '█' if i == scrollbar_pos else '░'
                sys.stdout.write(f"\x1b[{content_start_y + 1 + i};{article_start_x + article_w - 1}H{pop_bg}{self.theme['highlight_bg']}{char}{Colors.RESET}")

        # --- DRAW CONTENT BASED ON ACTIVE TAB (RIGHT) ---
        tab_name = self.intel_tabs[self.intel_active_tab]
        
        if tab_name == "Chat":
            # --- CHAT UI ---
            chat_h = ai_pane_h - 2 # Reserve space for input box
            
            # 1. Process all chat history into wrapped lines
            chat_lines = []
            max_msg_w = ai_w - 2
            
            for msg in self.intel_chat_history:
                sender = "AI" if msg['role'] == 'ai' else "You"
                color = Colors.CYAN if msg['role'] == 'ai' else Colors.YELLOW
                prefix = f"{sender}: "
                
                # Wrap message content
                # We store tuples of (text_content, color_code) or similar to handle coloring line by line
                # But to keep it simple with existing draw logic, let's pre-format the strings with colors?
                # Actually, standard method is better: just store raw lines and apply color at draw time if possible.
                # BUT, a single message spans multiple lines.
                # Let's store objects or tuples: (line_text, color_of_line)
                
                wrapper = textwrap.TextWrapper(width=max_msg_w, initial_indent=prefix, subsequent_indent="  ")
                wrapped_msg = wrapper.wrap(msg['text'])
                
                for i, line in enumerate(wrapped_msg):
                     # apply color only to the prefix part if it's the first line?
                     # No, the drawing logic below applies color to the whole block usually.
                     # Let's simplify: Just store the string, and maybe a metadata flag for color?
                     # Or just pre-color the prefix in the string? No, length calcs get messed up.
                     is_first = (i == 0)
                     chat_lines.append( {"text": line, "role": msg['role'], "is_first": is_first} )
            
            # 2. Handle Auto-Scrolling
            total_lines = len(chat_lines)
            if self.intel_chat_auto_scroll:
                self.intel_chat_scroll_top = max(0, total_lines - chat_h)
            
            # 3. Render Visible Lines
            current_y = content_start_y + 1
            for i in range(chat_h):
                line_idx = self.intel_chat_scroll_top + i
                if line_idx < total_lines:
                    line_data = chat_lines[line_idx]
                    text = line_data['text']
                    role = line_data['role']
                    is_first = line_data['is_first']
                    
                    color = Colors.CYAN if role == 'ai' else Colors.YELLOW
                    
                    # If it's the first line of a message, highlight the sender
                    # We need to reconstruct the visual string
                    # prefix len is dynamic based on role
                    prefix_str = "AI: " if role == 'ai' else "You: "
                    
                    display_text = ""
                    if is_first:
                         # e.g. "AI: Hello there"
                         # We want AI: to be colored, rest light grey
                         # Slice the text
                         content_part = text[len(prefix_str):]
                         sys.stdout.write(f"\x1b[{current_y};{ai_start_x + 1}H{pop_bg}{color}{prefix_str}{Colors.LIGHT_GREY}{content_part.ljust(max_msg_w - len(prefix_str))}{Colors.RESET}")
                    else:
                         sys.stdout.write(f"\x1b[{current_y};{ai_start_x + 1}H{pop_bg}{Colors.LIGHT_GREY}{text.ljust(max_msg_w)}{Colors.RESET}")
                    
                    current_y += 1

            # 4. Draw Scrollbar for Chat
            if total_lines > chat_h:
                max_scroll = max(0, total_lines - chat_h)
                scroll_percent = self.intel_chat_scroll_top / max_scroll if max_scroll > 0 else 1.0
                scrollbar_pos = int(scroll_percent * (chat_h - 1))
                for i in range(chat_h):
                    char = '█' if i == scrollbar_pos else '░'
                    sys.stdout.write(f"\x1b[{content_start_y + 1 + i};{ai_start_x + ai_w - 1}H{pop_bg}{self.theme['highlight_bg']}{char}{Colors.RESET}")


            # Draw Input Box
            input_y = start_y + pop_h - 3
            sys.stdout.write(f"\x1b[{input_y};{ai_start_x}H{pop_bg}{pop_fg} {'─' * (ai_w - 2)} {Colors.RESET}") # Separator
            
            prompt_marker = "> "
            input_display_w = ai_w - 4
            visible_input = self.intel_chat_input[-(input_display_w):]
            
            # Highlight input if active
            input_color = Colors.YELLOW if self.intel_active_pane == 'ai' else pop_fg
            
            sys.stdout.write(f"\x1b[{input_y + 1};{ai_start_x + 1}H{pop_bg}{input_color}{prompt_marker}{visible_input.ljust(input_display_w)}_ {Colors.RESET}")

        else: 
            # --- SUMMARY / DEEP DIVE (Text View) ---
            # Reuse the existing scrolling text logic but use 'wrapped_ai_lines' 
            # We might want to separate 'Deep Dive' content later, but for now share the buffer
            
            ai_lines = self.intelligence_data.get('wrapped_ai_lines', [])
            if not ai_lines and tab_name == "Summary":
                 sys.stdout.write(f"\x1b[{content_start_y + 2};{ai_start_x + 2}H{pop_bg}{Colors.ITALIC}Press 's' to generate summary...{Colors.RESET}")
            
            if ai_lines:
                for i in range(ai_pane_h - 1): 
                    line_idx = self.intel_ai_scroll_top + i
                    if line_idx < len(ai_lines):
                        line_text = ai_lines[line_idx]
                        line_text = line_text[:ai_w - 2]
                        sys.stdout.write(f"\x1b[{content_start_y + 1 + i};{ai_start_x + 1}H{pop_bg}{Colors.CYAN}{line_text.ljust(ai_w - 2)}{Colors.RESET}")

                max_scroll = max(0, len(ai_lines) - (ai_pane_h - 1))
                scroll_percent = self.intel_ai_scroll_top / max_scroll if max_scroll > 0 else 1.0
                scrollbar_pos = int(scroll_percent * (ai_pane_h - 2))
                for i in range(ai_pane_h - 1):
                    char = '█' if i == scrollbar_pos else '░'
                    sys.stdout.write(f"\x1b[{content_start_y + 1 + i};{ai_start_x + ai_w - 1}H{pop_bg}{self.theme['highlight_bg']}{char}{Colors.RESET}")

        footer_y = pop_h - 2
        status_text = self.intelligence_data.get('status_message', '').ljust(pop_w - 45)
        
        if self.intel_active_pane == 'ai':
             help_text = "[TAB] Switch | [←/→] Tabs | [Enter] Chat".rjust(43)
        else:
             help_text = "[TAB] Switch Panes | [ESC] Back".rjust(43)
             
        footer_content = f" {status_text}{help_text} "
        sys.stdout.write(f"\x1b[{footer_y};{start_x}H{self.theme['bar_bg']}{self.theme['bar_fg']}{footer_content.ljust(pop_w - 2)}{Colors.RESET}")
        sys.stdout.flush()

    def handle_intelligence_view_input(self, key):
        """Handles input for the Intelligence View (scrolling, pane switching, chat)."""
        if key == "ESC":
            self.is_intelligence_view = False
            self.intel_active_pane = 'article'
            self.intel_article_scroll_top = 0
            self.intel_ai_scroll_top = 0
            self.intelligence_data['wrapped_article_lines'] = []
            self.intelligence_data['wrapped_ai_lines'] = []
            self.intel_chat_history = []
            self.intel_chat_input = ""
            self.needs_redraw = True
            return

        if key == '\t': # TAB key - Switch Panes
            self.intel_active_pane = 'ai' if self.intel_active_pane == 'article' else 'article'
            self.needs_redraw = True
            return

        # Global shortcuts for AI actions (only if not typing in chat)
        is_chatting = (self.intel_active_pane == 'ai' and self.intel_tabs[self.intel_active_tab] == "Chat")
        if not is_chatting:
            if key.lower() == 's': self._run_ai_action('summary')
            elif key.lower() == 'k': self._run_ai_action('key_points')

        _, term_h = os.get_terminal_size()
        
        # --- ARTICLE PANE INPUT ---
        if self.intel_active_pane == 'article':
            article_lines = self.intelligence_data.get('wrapped_article_lines', [])
            if article_lines:
                content_pane_h = term_h - 4
                max_scroll = max(0, len(article_lines) - content_pane_h)
                if key == "UP": self.intel_article_scroll_top = max(0, self.intel_article_scroll_top - 1)
                elif key == "DOWN": self.intel_article_scroll_top = min(max_scroll, self.intel_article_scroll_top + 1)
                elif key == "PGUP": self.intel_article_scroll_top = max(0, self.intel_article_scroll_top - content_pane_h)
                elif key == "PGDOWN": self.intel_article_scroll_top = min(max_scroll, self.intel_article_scroll_top + content_pane_h)
                elif key == "HOME": self.intel_article_scroll_top = 0
                elif key == "END": self.intel_article_scroll_top = max_scroll
        
        # --- AI PANE INPUT ---
        elif self.intel_active_pane == 'ai':
            tab_name = self.intel_tabs[self.intel_active_tab]
            
            if tab_name == "Chat":
                if key == "LEFT" and not self.intel_chat_input: 
                    self.intel_active_tab = max(0, self.intel_active_tab - 1)
                elif key == "RIGHT" and not self.intel_chat_input:
                     self.intel_active_tab = min(len(self.intel_tabs) - 1, self.intel_active_tab + 1)
                elif key == "ENTER":
                    if self.intel_chat_input.strip():
                        self.intel_chat_auto_scroll = True # Reset auto-scroll on send
                        self._run_chat_query(self.intel_chat_input.strip())
                        self.intel_chat_input = ""
                elif key == "BACKSPACE":
                    self.intel_chat_input = self.intel_chat_input[:-1]
                elif len(key) == 1 and key.isprintable():
                    self.intel_chat_input += key
                
                # Chat Scrolling
                # Calculate total lines for bounds checking
                # This duplicates logic from draw, but we need it for bounds. 
                # Optimization: Cache total_lines in intelligence_data or calc implicitly?
                # For now, let's just do a rough check or recalc. Recalc is safer.
                _, term_h = os.get_terminal_size()
                ai_pane_h = term_h - 4
                chat_h = ai_pane_h - 2
                
                # We need to know total lines to bound scrolling. 
                # This is a bit expensive to recalc on every keypress but safe.
                # In a real app we'd cache this in the draw loop.
                # Let's verify if intelligence_data has it? No.
                # Lets just do a quick calc:
                total_lines = 0
                term_w, _ = os.get_terminal_size()
                available_w = term_w - 2
                article_w = int(available_w * 0.66)
                ai_w = available_w - article_w - 1
                max_msg_w = ai_w - 2
                
                for msg in self.intel_chat_history:
                    # Quick wrap calc
                    l = len(textwrap.wrap(msg['text'], width=max_msg_w, initial_indent="XX: ", subsequent_indent="  "))
                    total_lines += l
                
                max_scroll = max(0, total_lines - chat_h)

                if key == "UP": 
                    self.intel_chat_scroll_top = max(0, self.intel_chat_scroll_top - 1)
                    self.intel_chat_auto_scroll = False
                elif key == "DOWN": 
                    self.intel_chat_scroll_top = min(max_scroll, self.intel_chat_scroll_top + 1)
                    if self.intel_chat_scroll_top >= max_scroll:
                        self.intel_chat_auto_scroll = True
                elif key == "PGUP": 
                    self.intel_chat_scroll_top = max(0, self.intel_chat_scroll_top - chat_h)
                    self.intel_chat_auto_scroll = False
                elif key == "PGDOWN": 
                    self.intel_chat_scroll_top = min(max_scroll, self.intel_chat_scroll_top + chat_h)
                    if self.intel_chat_scroll_top >= max_scroll:
                        self.intel_chat_auto_scroll = True
                elif key == "HOME":
                    self.intel_chat_scroll_top = 0
                    self.intel_chat_auto_scroll = False
                elif key == "END":
                    self.intel_chat_scroll_top = max_scroll
                    self.intel_chat_auto_scroll = True

            else:
                # Summary or Deep Dive - Navigation + Scrolling
                if key == "LEFT":
                    self.intel_active_tab = max(0, self.intel_active_tab - 1)
                elif key == "RIGHT":
                    self.intel_active_tab = min(len(self.intel_tabs) - 1, self.intel_active_tab + 1)
                elif key == "ENTER":
                    # Trigger Deep Dive Analysis
                    if self.intel_tabs[self.intel_active_tab] == "Deep Dive":
                        self._run_ai_action('deep_dive')
                
                # Scrolling for text views
                ai_lines = self.intelligence_data.get('wrapped_ai_lines', [])
                if ai_lines:
                    ai_pane_h = term_h - 4
                    max_scroll = max(0, len(ai_lines) - (ai_pane_h - 1))
                    if key == "UP": self.intel_ai_scroll_top = max(0, self.intel_ai_scroll_top - 1)
                    elif key == "DOWN": self.intel_ai_scroll_top = min(max_scroll, self.intel_ai_scroll_top + 1)
                    elif key == "PGUP": self.intel_ai_scroll_top = max(0, self.intel_ai_scroll_top - ai_pane_h)
                    elif key == "PGDOWN": self.intel_ai_scroll_top = min(max_scroll, self.intel_ai_scroll_top + ai_pane_h)
                    elif key == "HOME": self.intel_ai_scroll_top = 0
                    elif key == "END": self.intel_ai_scroll_top = max_scroll

        self.needs_redraw = True

    def _run_chat_query(self, user_query):
        """Submits a chat query to Ollama in a background thread."""
        
        # Add user message to history immediately
        self.intel_chat_history.append({"role": "user", "text": user_query})
        self.intel_chat_auto_scroll = True # Ensure we scroll to see user message
        self.needs_redraw = True

        def task(self_ref, query):
            try:
                # Prepare context from article
                article_text = self_ref.intelligence_data.get('article_text', '')
                
                # Construct Prompt
                # We include recent history to allow follow-up questions
                history_text = ""
                for msg in self_ref.intel_chat_history[-6:]: # Limit history context
                    role = "User" if msg['role'] == 'user' else "Assistant"
                    history_text += f"{role}: {msg['text']}\n"
                
                prompt = (
                    f"You are a helpful AI assistant analyzing a news article.\n"
                    f"Article Content:\n{article_text[:6000]}\n\n" # Truncate to avoid context limits
                    f"Chat History:\n{history_text}\n"
                    f"Assistant: "
                )

                self_ref.intelligence_data['status_message'] = "AI is thinking..."
                self_ref.needs_redraw = True

                # Placeholder for AI response
                ai_msg = {"role": "ai", "text": ""}
                self_ref.intel_chat_history.append(ai_msg)
                
                # Stream content into the last message in history
                for chunk in ollama_client.query_ollama(prompt):
                    self_ref.intel_chat_history[-1]['text'] += chunk
                    self_ref.needs_redraw = True
                
                self_ref.intelligence_data['status_message'] = "Chat ready."
                self_ref.needs_redraw = True

            except Exception as e:
                self_ref.intel_chat_history.append({"role": "ai", "text": f"[Error: {str(e)}]"})
                self_ref.intelligence_data['status_message'] = "Chat failed."
                self_ref.needs_redraw = True

        threading.Thread(target=task, args=(self, user_query), daemon=True).start()

    def _draw_trend_view(self, items_data):
        """Draws the Trend Summary popup."""
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        
        start_x, start_y = 1, 1 
        pop_w, pop_h = term_w, term_h
        
        title = " Trend Summary & Analysis "
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, title)
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # --- Draw Time Range Selector ---
        ranges = ["Past Hour", "Past 24 Hours", "Past Week"]
        selector_y = start_y + 2
        sys.stdout.write(f"\x1b[{selector_y};{start_x + 2}H{pop_bg}{pop_fg}Time Range: ")
        
        current_x = start_x + 14
        for i, r in enumerate(ranges):
            if i == self.trend_time_range_index:
                sys.stdout.write(f"\x1b[{selector_y};{current_x}H{self.theme['highlight_bg']}{self.theme['highlight_fg']} {r} {Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{selector_y};{current_x}H{pop_bg}{Colors.LIGHT_GREY} {r} {Colors.RESET}")
            current_x += len(r) + 4

        sys.stdout.write(f"\x1b[{selector_y + 2};{start_x}H{pop_bg}{pop_fg}├{'─' * (pop_w - 2)}┤{Colors.RESET}")

        # --- Draw Summary Content ---
        content_start_y = selector_y + 3
        content_h = pop_h - content_start_y - 3
        
        lines = self.trend_data.get('wrapped_lines', [])
        if lines:
            for i in range(content_h):
                line_idx = self.trend_data['scroll_top'] + i
                if line_idx < len(lines):
                    line_text = lines[line_idx]
                    sys.stdout.write(f"\x1b[{content_start_y + i};{start_x + 2}H{pop_bg}{Colors.CYAN}{line_text.ljust(pop_w - 4)}{Colors.RESET}")
            
            max_scroll = max(0, len(lines) - content_h)
            scroll_percent = self.trend_data['scroll_top'] / max_scroll if max_scroll > 0 else 1.0
            scrollbar_pos = int(scroll_percent * (content_h - 1))
            for i in range(content_h):
                char = '█' if i == scrollbar_pos else '░'
                sys.stdout.write(f"\x1b[{content_start_y + i};{start_x + pop_w - 2}H{pop_bg}{self.theme['highlight_bg']}{char}{Colors.RESET}")

        # --- Footer ---
        footer_y = pop_h - 2
        status_text = self.trend_data.get('status_message', '').ljust(pop_w - 40)
        help_text = "[←/→] Range | [ENTER] Analyze | [ESC] Back".rjust(38)
        footer_content = f" {status_text}{help_text} "
        sys.stdout.write(f"\x1b[{footer_y};{start_x}H{self.theme['bar_bg']}{self.theme['bar_fg']}{footer_content.ljust(pop_w - 2)}{Colors.RESET}")
        sys.stdout.flush()

    def handle_trend_view_input(self, key):
        if key == "ESC":
            self.is_trend_view = False
            self.needs_redraw = True
            return

        if key == "LEFT":
            self.trend_time_range_index = max(0, self.trend_time_range_index - 1)
            self.needs_redraw = True
        elif key == "RIGHT":
            self.trend_time_range_index = min(2, self.trend_time_range_index + 1)
            self.needs_redraw = True
        elif key == "ENTER":
            self._run_trend_analysis()
        
        # Scrolling
        _, term_h = os.get_terminal_size()
        content_h = term_h - 8 # Approximate height of content area
        lines = self.trend_data.get('wrapped_lines', [])
        max_scroll = max(0, len(lines) - content_h)
        
        if key == "UP": self.trend_data['scroll_top'] = max(0, self.trend_data['scroll_top'] - 1)
        elif key == "DOWN": self.trend_data['scroll_top'] = min(max_scroll, self.trend_data['scroll_top'] + 1)
        elif key == "PGUP": self.trend_data['scroll_top'] = max(0, self.trend_data['scroll_top'] - content_h)
        elif key == "PGDOWN": self.trend_data['scroll_top'] = min(max_scroll, self.trend_data['scroll_top'] + content_h)
        elif key == "HOME": self.trend_data['scroll_top'] = 0
        elif key == "END": self.trend_data['scroll_top'] = max_scroll
        
        self.needs_redraw = True

    def _run_trend_analysis(self):
        if "Querying" in self.trend_data['status_message']: return

        def task(self_ref):
            try:
                # 1. Determine cutoff time
                now = time.time()
                if self_ref.trend_time_range_index == 0: cutoff = now - 3600 # 1 Hour
                elif self_ref.trend_time_range_index == 1: cutoff = now - 86400 # 24 Hours
                else: cutoff = now - 604800 # 1 Week

                self_ref.trend_data['status_message'] = "Fetching articles from DB..."
                self_ref.needs_redraw = True

                # 2. Fetch articles
                with sqlite3.connect(DB_FILE) as conn:
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT title, score, subreddit FROM articles WHERE created_utc > ? ORDER BY score DESC LIMIT 50", (cutoff,))
                    rows = cursor.fetchall()
                
                if not rows:
                    self_ref.trend_data['status_message'] = "No articles found in this time range."
                    self_ref.trend_data['summary'] = "No data available to analyze."
                    self_ref.trend_data['wrapped_lines'] = ["No data available."]
                    self_ref.needs_redraw = True
                    return

                # 3. Prepare Prompt
                articles_text = "\n".join([f"- [{row['subreddit']}] {row['title']} (Score: {row['score']})" for row in rows])
                prompt = (
                    f"Analyze the following list of popular news headlines from the past "
                    f"{['hour', 'day', 'week'][self_ref.trend_time_range_index]}.\n"
                    f"Identify the top 3-5 trending topics or themes. "
                    f"For each trend, provide a brief summary of what is happening based on the headlines.\n"
                    f"Format the output as a clear, bulleted list.\n\n"
                    f"Headlines:\n{articles_text}"
                )

                self_ref.trend_data['status_message'] = "Querying Ollama for trends..."
                self_ref.trend_data['summary'] = ""
                self_ref.trend_data['wrapped_lines'] = []
                self_ref.trend_data['scroll_top'] = 0
                self_ref.needs_redraw = True

                term_w, _ = os.get_terminal_size()
                wrap_width = term_w - 6

                # 4. Stream Response
                for chunk in ollama_client.query_ollama(prompt):
                    self_ref.trend_data['summary'] += chunk
                    self_ref.trend_data['wrapped_lines'] = self_ref._format_ai_response(
                        self_ref.trend_data['summary'], wrap_width
                    )
                    self_ref.needs_redraw = True
                
                self_ref.trend_data['status_message'] = "Analysis complete."
                self_ref.needs_redraw = True

            except Exception as e:
                self_ref.trend_data['status_message'] = f"Error: {str(e)}"
                self_ref.trend_data['wrapped_lines'] = [f"Error: {str(e)}"]
                self_ref.needs_redraw = True

        threading.Thread(target=task, args=(self,), daemon=True).start()

    def _format_ai_response(self, text, wrap_width):
        """Processes AI text, handling paragraph breaks and simple Markdown bullets."""
        if not text:
            return []

        formatted_lines = []
        paragraphs = text.split('\n')

        for paragraph in paragraphs:
            if paragraph.strip().startswith('* '):
                line = "  • " + paragraph.strip()[2:]
            else:
                line = paragraph

            if line.strip() == "":
                formatted_lines.append("")
            else:
                formatted_lines.extend(textwrap.wrap(line, width=wrap_width))
        
        return formatted_lines

    def _run_ai_action(self, action_type):
        """Helper to run a specific AI analysis with live text streaming."""
        if "Querying" in self.intelligence_data['status_message']:
            return

        def task(self_ref, action):
            log_file_path = "analysis_error_log.txt"
            try:
                text = self_ref.intelligence_data['article_text']
                if not text:
                    self_ref.intelligence_data['status_message'] = "Error: Article text is empty."
                    self_ref.needs_redraw = True
                    return

                # --- Clear previous response and prepare for streaming ---
                self_ref.intelligence_data['ai_summary'] = ""
                self_ref.intelligence_data['wrapped_ai_lines'] = []
                self_ref.intel_ai_scroll_top = 0
                self_ref.intelligence_data['status_message'] = f"Querying Ollama for {action.replace('_', ' ')}..."
                self_ref.needs_redraw = True

                if action == 'summary':
                    prompt = f"Provide a concise, one-paragraph summary of the following news article:\n\n---\n\n{text[:8000]}"
                elif action == 'key_points':
                    prompt = f"Extract the key takeaways from this article. Present them as a Markdown bulleted list using '* ' for each point.\n\n---\n\n{text[:8000]}"
                elif action == 'deep_dive':
                    prompt = (
                        f"Provide a comprehensive, in-depth analysis of this article. "
                        f"Explore the historical context, potential implications, and multiple perspectives if applicable. "
                        f"Go beyond a simple summary.\n\n---\n\n{text[:8000]}"
                    )
                else:
                    return

                term_w, _ = os.get_terminal_size()
                # 33% width for AI pane
                available_w = term_w - 2
                article_w = int(available_w * 0.66)
                ai_w = available_w - article_w - 1
                wrap_width = ai_w - 3

                # --- Consume the stream from Ollama ---
                for chunk in ollama_client.query_ollama(prompt):
                    self_ref.intelligence_data['ai_summary'] += chunk
                    # Re-wrap the lines with the new, longer text
                    self_ref.intelligence_data['wrapped_ai_lines'] = self_ref._format_ai_response(
                        self_ref.intelligence_data['ai_summary'], wrap_width
                    )
                    # Trigger a redraw for each new chunk to create the "typing" effect
                    self_ref.needs_redraw = True
                
                # --- Sync Result to Chat History ---
                header = action.replace('_', ' ').title()
                self_ref.intel_chat_history.append({"role": "ai", "text": f"**{header}:**\n{self_ref.intelligence_data['ai_summary']}"})

                self.intelligence_data['status_message'] = "Response received. [S]ummary | [K]ey Points"
                self.needs_redraw = True

            except Exception:
                with open(log_file_path, "w") as f:
                    f.write(f"An error occurred during AI action: {action}\n\n")
                    f.write(traceback.format_exc())
                self_ref.intelligence_data['status_message'] = "Error in AI task. See log."
                self_ref.needs_redraw = True

        threading.Thread(target=task, args=(self, action_type), daemon=True).start()

    def _fetch_and_analyze_article(self):
        """
        Background task to scrape, pre-format, and get the initial summary
        with live text streaming.
        """
        log_file_path = "analysis_error_log.txt"
        try:
            url = self.intelligence_data['current_article_url']
            if not url:
                self.intelligence_data['status_message'] = "Error: URL is missing."
                self.needs_redraw = True
                return

            self.intelligence_data['status_message'] = f"Scraping: {url}"
            self.needs_redraw = True

            title, result = scraper.scrape_in_subprocess(url)
            
            if title is None:
                error_message = result
                self.intelligence_data['status_message'] = "Scraping failed."
                term_w, _ = os.get_terminal_size()
                self.intelligence_data['wrapped_ai_lines'] = textwrap.wrap(error_message, width=term_w - 6)
                self.needs_redraw = True
                return
            
            text = result
            self.intelligence_data['article_title'] = title
            self.intelligence_data['article_text'] = text
            self.intelligence_data['status_message'] = "Content extracted. Querying Ollama for initial summary..."

            term_w, _ = os.get_terminal_size()
            
            # Recalculate split widths
            available_w = term_w - 2
            article_w = int(available_w * 0.66)
            article_wrap_width = article_w - 3
            
            ai_w = available_w - article_w - 1
            ai_wrap_width = ai_w - 3

            wrapped_lines = []
            for paragraph in text.split('\n'):
                if paragraph.strip() == "":
                    wrapped_lines.append("")
                else:
                    wrapped_lines.extend(textwrap.wrap(paragraph, width=article_wrap_width))
                    wrapped_lines.append("")
            self.intelligence_data['wrapped_article_lines'] = wrapped_lines
            self.needs_redraw = True
            
            # --- Stream the initial summary from Ollama ---
            prompt = f"You are an expert analyst. Provide a concise, insightful summary of the following article, focusing on key arguments and conclusions.\n\n---\n\n{text[:8000]}"
            
            # Clear previous response data
            self.intelligence_data['ai_summary'] = ""
            self.intelligence_data['wrapped_ai_lines'] = []
            self.intel_ai_scroll_top = 0
            
            for chunk in ollama_client.query_ollama(prompt):
                self.intelligence_data['ai_summary'] += chunk
                self.intelligence_data['wrapped_ai_lines'] = self._format_ai_response(
                    self.intelligence_data['ai_summary'], ai_wrap_width
                )
                self.needs_redraw = True

            # --- Sync Summary to Chat History ---
            self.intel_chat_history.append({"role": "ai", "text": f"**Initial Summary:**\n{self.intelligence_data['ai_summary']}"})
            
            self.intelligence_data['status_message'] = "Summary received. [S]ummary | [K]ey Points"
            self.needs_redraw = True

        except Exception as e:
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write(f"An error occurred while analyzing URL: {self.intelligence_data.get('current_article_url', 'N/A')}\n\n")
                f.write(traceback.format_exc())

            error_message = "An unexpected error occurred. Check 'analysis_error_log.txt'."
            self.intelligence_data['status_message'] = "Analysis Failed!"
            self.intelligence_data['ai_summary'] = textwrap.fill(error_message, width=80)
            self.needs_redraw = True

    def _draw_comment_analysis_view(self, items_data):
        """Draws the Comment Analysis popup."""
        if self.is_domain_view:
            self._draw_domain_view(items_data, is_background=True)
        else:
            self._draw(items_data, is_background=True)
            
        term_w, term_h = os.get_terminal_size()
        
        pop_w, pop_h = int(term_w * 0.9), int(term_h * 0.9)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2
        
        title = " Comment Sentiment & Summary "
        self._draw_popup_border(start_x, start_y, pop_w, pop_h, title)
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        # --- Draw Summary Content ---
        content_start_y = start_y + 2
        content_h = pop_h - 4
        
        lines = self.comment_analysis_data.get('wrapped_lines', [])
        if lines:
            for i in range(content_h):
                line_idx = self.comment_analysis_data['scroll_top'] + i
                if line_idx < len(lines):
                    line_text = lines[line_idx]
                    sys.stdout.write(f"\x1b[{content_start_y + i};{start_x + 2}H{pop_bg}{Colors.CYAN}{line_text.ljust(pop_w - 4)}{Colors.RESET}")
            
            max_scroll = max(0, len(lines) - content_h)
            scroll_percent = self.comment_analysis_data['scroll_top'] / max_scroll if max_scroll > 0 else 1.0
            scrollbar_pos = int(scroll_percent * (content_h - 1))
            for i in range(content_h):
                char = '█' if i == scrollbar_pos else '░'
                sys.stdout.write(f"\x1b[{content_start_y + i};{start_x + pop_w - 2}H{pop_bg}{self.theme['highlight_bg']}{char}{Colors.RESET}")

        # --- Footer ---
        footer_y = pop_h - 2
        status_text = self.comment_analysis_data.get('status_message', '').ljust(pop_w - 20)
        help_text = "[ESC] Back".rjust(18)
        footer_content = f" {status_text}{help_text} "
        sys.stdout.write(f"\x1b[{footer_y};{start_x}H{self.theme['bar_bg']}{self.theme['bar_fg']}{footer_content.ljust(pop_w - 2)}{Colors.RESET}")
        sys.stdout.flush()

    def handle_comment_analysis_input(self, key):
        if key == "ESC":
            self.is_comment_analysis_view = False
            self.needs_redraw = True
            return
        
        # Scrolling
        _, term_h = os.get_terminal_size()
        content_h = term_h - 4
        lines = self.comment_analysis_data.get('wrapped_lines', [])
        max_scroll = max(0, len(lines) - content_h)
        
        if key == "UP": self.comment_analysis_data['scroll_top'] = max(0, self.comment_analysis_data['scroll_top'] - 1)
        elif key == "DOWN": self.comment_analysis_data['scroll_top'] = min(max_scroll, self.comment_analysis_data['scroll_top'] + 1)
        elif key == "PGUP": self.comment_analysis_data['scroll_top'] = max(0, self.comment_analysis_data['scroll_top'] - content_h)
        elif key == "PGDOWN": self.comment_analysis_data['scroll_top'] = min(max_scroll, self.comment_analysis_data['scroll_top'] + content_h)
        elif key == "HOME": self.comment_analysis_data['scroll_top'] = 0
        elif key == "END": self.comment_analysis_data['scroll_top'] = max_scroll
        
        self.needs_redraw = True

    def _run_comment_analysis(self):
        if "Querying" in self.comment_analysis_data['status_message']: return

        def task(self_ref):
            try:
                self_ref.comment_analysis_data['status_message'] = "Extracting comments..."
                self_ref.needs_redraw = True

                # 1. Extract Comments
                comments_text = []
                # Flatten the tree to get a list of comments
                flat_comments = []
                self_ref._flatten_comment_tree(self_ref.comment_tree, flat_comments)
                
                # Take top 30 comments to avoid hitting context limits too hard
                # Prioritize top-level comments (depth 0) and high scores
                sorted_comments = sorted(flat_comments, key=lambda c: (c.depth == 0, c.score), reverse=True)[:30]
                
                for c in sorted_comments:
                    body = c.body
                    # Basic cleanup
                    body = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', r'\1', body) # Remove link markdown
                    comments_text.append(f"- {body[:300]}") # Truncate very long comments

                if not comments_text:
                    self_ref.comment_analysis_data['status_message'] = "No comments found to analyze."
                    self_ref.comment_analysis_data['summary'] = "No comments available."
                    self_ref.comment_analysis_data['wrapped_lines'] = ["No comments available."]
                    self_ref.needs_redraw = True
                    return

                full_text = "\n".join(comments_text)

                # 2. Prepare Prompt
                prompt = (
                    f"Analyze the following comments from a Reddit discussion.\n"
                    f"1. Determine the overall sentiment (Positive, Negative, Mixed, Neutral).\n"
                    f"2. Summarize the main points, arguments, or reactions expressed by the users.\n"
                    f"Format the output clearly with headings.\n\n"
                    f"Comments:\n{full_text}"
                )

                self_ref.comment_analysis_data['status_message'] = "Querying Ollama..."
                self_ref.comment_analysis_data['summary'] = ""
                self_ref.comment_analysis_data['wrapped_lines'] = []
                self_ref.comment_analysis_data['scroll_top'] = 0
                self_ref.needs_redraw = True

                term_w, _ = os.get_terminal_size()
                pop_w = int(term_w * 0.9)
                wrap_width = pop_w - 6

                # 3. Stream Response
                for chunk in ollama_client.query_ollama(prompt):
                    self_ref.comment_analysis_data['summary'] += chunk
                    self_ref.comment_analysis_data['wrapped_lines'] = self_ref._format_ai_response(
                        self_ref.comment_analysis_data['summary'], wrap_width
                    )
                    self_ref.needs_redraw = True
                
                self_ref.comment_analysis_data['status_message'] = "Analysis complete."
                self_ref.needs_redraw = True

            except Exception as e:
                self_ref.comment_analysis_data['status_message'] = f"Error: {str(e)}"
                self_ref.comment_analysis_data['wrapped_lines'] = [f"Error: {str(e)}"]
                self_ref.needs_redraw = True

        threading.Thread(target=task, args=(self,), daemon=True).start()

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
        if key == "ESC" or key == "LEFT":
            self.is_action_menu_view = False
        elif key == "UP":
            self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
        elif key == "DOWN":
            self.action_menu_selected_index = min(max_index, self.action_menu_selected_index + 1)
        elif key == "ENTER" or key == "RIGHT":
            action = actionable_options[actionable_keys[self.action_menu_selected_index]]
            url = self.action_menu_article['url']
            if action == "delete_article":
                self.article_to_delete, self.is_delete_confirm_view = self.action_menu_article, True
            elif action == "open_article":
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif action == "watch_video":
                try:
                    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
                    if sys.platform == "win32":
                        kwargs['creationflags'] = 0x00000200 | 0x00000008
                    else:
                        kwargs['start_new_session'] = True
                    subprocess.Popen([VIDEO_PLAYER_PATH, url], **kwargs)
                    self.status_message, self.status_message_timer = "Launching in Video Player...", 50
                except FileNotFoundError:
                    self.status_message, self.status_message_timer = f"Error: '{VIDEO_PLAYER_PATH}' not found.", 50
            elif action == "open_comments":
                threading.Thread(target=webbrowser.open, args=(f"https://www.reddit.com{self.action_menu_article['permalink']}",)).start()
            elif action == "summarize":
                threading.Thread(target=webbrowser.open, args=(f"https://www.perplexity.ai/?s=o&q={quote(f'summarize {url}')}",)).start()
            elif action == "copy_url":
                self._copy_to_clipboard(url)
                self.status_message, self.status_message_timer = "URL copied to clipboard!", 50
            elif action == "archive":
                threading.Thread(target=webbrowser.open, args=(f"https://archive.is/{quote(url)}",)).start()
            elif action == "find_domain":
                domain = get_domain_from_url(url)
                if domain:
                    self.is_domain_view = True
                    self.domain_view_title = f"Articles from {domain}"
                    self.domain_articles = []
                    self.domain_view_selected_index = 0
                    self.domain_view_scroll_top = 0
                    self.status_message, self.status_message_timer = f"Fetching articles from {domain}...", 50
                    threading.Thread(target=self._fetch_domain_articles_threaded, args=(domain,), daemon=True).start()
                else:
                    self.status_message, self.status_message_timer = "Error: Could not determine domain.", 50

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
            elif idx == 3:  # Video Player Path
                if key == "BACKSPACE": self.video_player_path_setting = self.video_player_path_setting[:-1]
                elif len(key) == 1 and key.isprintable(): self.video_player_path_setting += key
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
            "Open Link in Browser": "open_article",
            "Open Comments in Browser": "open_comments",
            "Summarize with Perplexity": "summarize",
            "---SEPARATOR_1---": "separator",
            "Copy URL to Clipboard": "copy_url",
            "Archive Page (archive.is)": "archive",
            "Find more from this domain": "find_domain",
            "---SEPARATOR_2---": "separator",
            "Exclude this domain": "exclude_domain",
            "---SEPARATOR_3---": "separator",
            "Delete Link": "delete_article"
        }
        if self.action_menu_article:
            domain = get_domain_from_url(self.action_menu_article.get('url', ''))
            if domain in ['youtube.com', 'youtu.be']:
                items = list(options.items())
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
            mode, data = fetch_reddit_comments(permalink)
            if mode == "json":
                if not data: self.comment_view_status = "No comments found."
                else: self.comment_tree, self.comment_view_status = self._parse_comments_to_tree(data), ""
            else:
                if not data: self.comment_view_status = "No comments found."
                else: self.comment_tree, self.comment_view_status = [CommentNode(c, depth=0) for c in data], ""
            self.comment_selected_index, self.comment_scroll_top = 0, 0

            # Prepare the lines for drawing once
            self._prepare_comment_lines()

        except Exception as e: self.comment_view_status = f"Error: {e}"
        self.needs_redraw = True

    def _format_comment_body(self, text):
        text = html.unescape(text)

        def replace_link(match):
            link_text, url = match.group(1), match.group(2)
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
            f"  {key_color}[←/PgUp]{desc_color}  - Page up",
            f"  {key_color}[PgDn]{desc_color}    - Page down",
            f"{header_color}== Actions ==",
            f"  {key_color}[→/Enter]{desc_color} - Open Action Menu for selected article",
            f"  {key_color}[b]{desc_color}       - Bookmark/unbookmark an article",
            f"  {key_color}[m]{desc_color}       - Mark all new articles as seen",
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

        footer_text = " 👽 Alien News Feed v0.40 - Created by Old Lamps "
        # Correctly calculate the display length, accounting for the double-width emoji
        footer_len = len(footer_text) + 1

        content_width = pop_w - 2

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
        if self.is_domain_view:
            self._draw_domain_view(items_data, is_background=True)
        else:
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
            if self.is_domain_view:
                self._draw_domain_view(items_data, is_background=True)
            else:
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

    def _draw_link_popup(self, items_data):
        self._draw_comments(items_data)

        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, min(15, len(self.extracted_links) + 4)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Links in Comment")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        for i, link in enumerate(self.extracted_links):
            row = start_y + 1 + i # Start one line lower for content
            display_text = f" {link['text'][:30]:<30} → {link['url'][:30]}"
            display_text = display_text.ljust(pop_w - 4)

            if i == self.link_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{display_text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x + 2}H{pop_bg}{pop_fg}{display_text}{Colors.RESET}")

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
            max_view = max(1, term_h - 4)
            
            self.selected_index = max(0, min(self.selected_index, len(items_data)-1))
            max_view = max(1, term_h - 4)

            # Define the "scroll offset" (how many lines from the edge to trigger a scroll)
            # Ensure the offset isn't larger than half the screen
            safe_offset = min(self.scroll_offset, max_view // 2)

            # 1. Scrolling Up: If selection is within the top offset
            if self.selected_index < self.scroll_top + safe_offset:
                # Set scroll_top to keep the selection at the offset
                self.scroll_top = self.selected_index - safe_offset
            
            # 2. Scrolling Down: If selection is within the bottom offset
            elif self.selected_index > self.scroll_top + (max_view - 1 - safe_offset):
                # Set scroll_top to keep the selection at the trigger position
                self.scroll_top = self.selected_index - (max_view - 1 - safe_offset)

            # 3. Clamp scroll_top to valid bounds
            self.scroll_top = max(0, self.scroll_top)
            if len(items_data) > max_view:
                self.scroll_top = min(self.scroll_top, len(items_data) - max_view)
            else:
                self.scroll_top = 0 # If all items fit, scroll_top is always 0
            

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

                score = item.get('score', 0)
                display = f"{title_color}{format_time_ago(item.get('created_utc')):<8} {sub} {src}{Colors.RESET} {highlight_icon}{bookmark}{video_icon}{self.theme['new_fg']}[{score}]{Colors.RESET} {item.get('title')}{Colors.RESET}"
                line = f"> {display}" if i == self.selected_index else f"  {display}"

                plain_text_len = len(re.sub(r'\x1b\[[0-9;]*m', '', line))
                if plain_text_len > safe_width:
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
                # A single if/elif chain ensures only one view is drawn per cycle.
                if self.is_intelligence_view:
                    self._draw_intelligence_view(items_data)
                elif self.is_delete_confirm_view:
                    self._draw_confirmation_popup(items_data, "Permanently delete this article? (y/n)")
                elif self.is_exit_confirm_view:
                    self._draw_confirmation_popup(items_data, "Are you sure you want to quit? (y/n)")
                elif self.is_action_menu_view:
                    self._draw_action_menu(items_data)
                elif self.is_settings_view:
                    self._draw_settings(items_data)
                elif self.is_filter_menu_view:
                    self._draw_filter_menu(items_data)
                elif self.is_link_view:
                    self._draw_link_popup(items_data)
                elif self.is_comment_analysis_view:
                    self._draw_comment_analysis_view(items_data)
                elif self.is_comment_view:
                    self._draw_comments(items_data)
                elif self.is_help_view:
                    self._draw_help_menu(items_data)
                elif self.is_import_view:
                    self._draw_import_instructions(items_data)
                elif self.is_profile_view:
                    self._draw_profile_manager(items_data)
                elif self.is_subreddit_edit_view:
                    self._draw_subreddit_editor(items_data)
                elif self.is_trend_view:
                    self._draw_trend_view(items_data)
                elif self.is_domain_view:
                    self._draw_domain_view(items_data)
                else:
                    self._draw(items_data)
                self.needs_redraw = False

            key = getch()
            if not key: continue
            
            # A single if/elif chain ensures only one input handler is used.
            if self.is_intelligence_view:
                self.handle_intelligence_view_input(key)
            elif self.is_delete_confirm_view:
                self.handle_delete_confirm_input(key, items_data)
            elif self.is_exit_confirm_view:
                self.handle_exit_confirm_input(key)
            elif self.is_action_menu_view:
                self.handle_action_menu_input(key)
            elif self.is_settings_view:
                self.handle_settings_input(key)
            elif self.is_filter_menu_view:
                self.handle_filter_menu_input(key)
            elif self.is_link_view:
                self.handle_link_input(key)
            elif self.is_comment_analysis_view:
                self.handle_comment_analysis_input(key)
            elif self.is_comment_view:
                self.handle_comment_view_input(key)
            elif self.is_help_view:
                self.handle_help_view_input(key)
            elif self.is_import_view:
                self.handle_import_view_input(key)
            elif self.is_profile_view:
                self.handle_profile_input(key)
            elif self.is_subreddit_edit_view:
                self.handle_subreddit_edit_input(key)
            elif self.is_trend_view:
                self.handle_trend_view_input(key)
            elif self.is_search_view:
                self.handle_search_view_input(key, items_data)
            elif self.is_domain_view:
                self.handle_domain_view_input(key)
            else:
                self.handle_main_view_input(key, items_data)

    def handle_delete_confirm_input(self, key, items_data):
        if key.lower() == 'y':
            if self.article_to_delete:
                block_and_delete_article(self.article_to_delete['url'])
                self.master_article_list = [a for a in self.master_article_list if a['url'] != self.article_to_delete['url']]
                self.force_regenerate_view = True
                self.status_message, self.status_message_timer = "Article deleted.", 50

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
        
        if key == "ESC" or key == "LEFT":
            self.is_action_menu_view = False
        elif key == "UP":
            self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
        elif key == "DOWN":
            self.action_menu_selected_index = min(max_index, self.action_menu_selected_index + 1)
        elif key == "ENTER" or key == "RIGHT":
            action = actionable_options[actionable_keys[self.action_menu_selected_index]]
            url = self.action_menu_article['url']
            if action == "delete_article":
                self.article_to_delete, self.is_delete_confirm_view = self.action_menu_article, True
            elif action == "open_article":
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif action == "watch_video":
                try:
                    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
                    if sys.platform == "win32":
                        kwargs['creationflags'] = 0x00000200 | 0x00000008
                    else:
                        kwargs['start_new_session'] = True
                    subprocess.Popen([VIDEO_PLAYER_PATH, url], **kwargs)
                    self.status_message, self.status_message_timer = "Launching in Video Player...", 50
                except FileNotFoundError:
                    self.status_message, self.status_message_timer = f"Error: '{VIDEO_PLAYER_PATH}' not found.", 50
            elif action == "open_comments":
                threading.Thread(target=webbrowser.open, args=(f"https://www.reddit.com{self.action_menu_article['permalink']}",)).start()
            elif action == "summarize":
                threading.Thread(target=webbrowser.open, args=(f"https://www.perplexity.ai/?s=o&q={quote(f'summarize {url}')}",)).start()
            elif action == "copy_url":
                self._copy_to_clipboard(url)
                self.status_message, self.status_message_timer = "URL copied to clipboard!", 50
            elif action == "archive":
                threading.Thread(target=webbrowser.open, args=(f"https://archive.is/{quote(url)}",)).start()
            elif action == "exclude_domain":
                domain_to_block = get_domain_from_url(url)
                if domain_to_block and domain_to_block not in BLOCKED_DOMAINS:
                    BLOCKED_DOMAINS.add(domain_to_block)
                    save_general_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting, self.show_clock_setting, BLOCKED_DOMAINS, VIDEO_PLAYER_PATH)
                    self.all_articles = [a for a in self.all_articles if get_domain_from_url(a.get('url')) != domain_to_block]
                    self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))
                    self.status_message, self.status_message_timer = f"Domain '{domain_to_block}' is now hidden.", 50
                    self.force_regenerate_view = True
            elif action == "find_domain":
                domain = get_domain_from_url(url)
                self.is_domain_view = True
                self.domain_view_title = f"Articles from {domain}"
                self.status_message = f"Fetching articles from {domain}..."
                self.status_message_timer = 50
                threading.Thread(target=self._fetch_domain_articles_threaded, args=(domain,), daemon=True).start()
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
            elif key == 'a':
                if self.visible_comments:
                    self.is_comment_analysis_view = True
                    self._run_comment_analysis()
                else:
                    self.status_message = "No comments to analyze."
                    self.status_message_timer = 30

            if original_index != self.comment_selected_index: self.needs_redraw = True

        if key == "ESC":
            self.is_comment_view, self.comment_tree = False, []
        self.needs_redraw = True

    def handle_search_view_input(self, key, items_data):
        if self.search_input_active:
            if key == "ENTER":
                self.search_input_active = False
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

    def _fetch_domain_articles_threaded(self, domain):
        """Fetches articles from a specific domain in a background thread."""
        try:
            articles = fetch_reddit_domain_posts(domain)
            self.domain_articles = articles
            self.status_message = f"Found {len(articles)} articles from {domain}."
            self.status_message_timer = 50
            self.needs_redraw = True

        except Exception as e:
            self.status_message = f"Error fetching domain articles: {str(e)}"
            self.status_message_timer = 50
            self.needs_redraw = True

    def _draw_domain_view(self, items_data, is_background=False):
        """Draws the domain-specific article list using the main draw logic."""
        # Swap state to reuse the main draw function
        main_idx, main_scroll = self.selected_index, self.scroll_top
        self.selected_index, self.scroll_top = self.domain_view_selected_index, self.domain_view_scroll_top
        
        # Override title temporarily
        original_title = self.title
        self.title = self.domain_view_title
        
        # Draw using the domain articles
        self._draw(self.domain_articles, is_background=is_background)
        
        # Save the domain view state (as _draw might have updated scroll_top)
        self.domain_view_selected_index, self.domain_view_scroll_top = self.selected_index, self.scroll_top

        # Restore main state
        self.selected_index, self.scroll_top = main_idx, main_scroll
        self.title = original_title

    def handle_domain_view_input(self, key):
        """Handles input for the domain view."""
        if not self.domain_articles and key != "ESC": return

        if key == "ESC":
            self.is_domain_view = False
            self.needs_redraw = True
            return
        
        # Navigation
        if key == "UP": self.domain_view_selected_index = max(0, self.domain_view_selected_index - 1)
        elif key == "DOWN": self.domain_view_selected_index = min(len(self.domain_articles) - 1, self.domain_view_selected_index + 1)
        elif key == "PGUP": self.domain_view_selected_index = max(0, self.domain_view_selected_index - self.page_jump)
        elif key == "PGDOWN": self.domain_view_selected_index = min(len(self.domain_articles) - 1, self.domain_view_selected_index + self.page_jump)
        elif key == "HOME": self.domain_view_selected_index = 0
        elif key == "END": self.domain_view_selected_index = len(self.domain_articles) - 1
        
        # Actions
        # Actions
        elif key == "ENTER":
            if self.domain_articles:
                self.is_action_menu_view, self.action_menu_article, self.action_menu_selected_index = True, self.domain_articles[self.domain_view_selected_index], 0
        elif key == "c":
            if self.domain_articles:
                self.is_comment_view, self.comment_view_status = True, "Loading comments..."
                threading.Thread(target=self._fetch_comments_threaded, args=(self.domain_articles[self.domain_view_selected_index].get('permalink'),)).start()
        elif key == "a" or key == "RIGHT":
            if self.domain_articles:
                self.is_intelligence_view = True
                selected_article = self.domain_articles[self.domain_view_selected_index]
                self.intelligence_data = {
                    "article_title": selected_article['title'],
                    "article_text": "",
                    "ai_summary": "",
                    "status_message": "Initializing analysis...",
                    "current_article_url": selected_article['url']
                }
                threading.Thread(target=self._fetch_and_analyze_article, daemon=True).start()
                self.needs_redraw = True
        
        self.needs_redraw = True

    def handle_main_view_input(self, key, items_data):
        """Handles all key presses for the main article list view."""
        if not items_data and key not in ["ESC", "s", "v", "/", "h", "p", "t"]: return
        original_index = self.selected_index
        if key == "UP": self.selected_index = max(0, self.selected_index - 1)
        elif key == "DOWN": self.selected_index = min(len(items_data) - 1, self.selected_index + 1)
        elif key == "LEFT" or key == "PGUP": self.selected_index = max(0, self.selected_index - self.page_jump)
        elif key == "PGDOWN": self.selected_index = min(len(items_data) - 1, self.selected_index + self.page_jump)
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
        elif key.lower() == 'm':
            articles_marked = mark_all_as_seen_in_db()
            if articles_marked > 0:
                for article in self.master_article_list:
                    if article['is_new']:
                        article['is_new'] = False
                self.status_message = f"{articles_marked} new articles marked as seen."
                self.force_regenerate_view = True
            else:
                self.status_message = "No new articles to mark as seen."
            self.status_message_timer = 50
        
        elif key == "c":
            if items_data:
                self.is_comment_view, self.comment_view_status = True, "Loading comments..."
                threading.Thread(target=self._fetch_comments_threaded, args=(items_data[self.selected_index].get('permalink'),)).start()
        elif key == "s": self.is_settings_view = True
        elif key == "h": self.is_help_view = True
        elif key == "p": 
            self.is_profile_view = True
            try:
                self.profile_selected_index = self.profiles.index(self.active_profile)
            except ValueError:
                self.profile_selected_index = 0
        elif key == 'v':
            self.is_filter_menu_view = True
            self.filter_menu_selected_index = self.current_view_mode_index
        elif key == '/': self.is_search_view, self.search_query, self.search_input_active = True, "", True
        elif key.lower() == 't':
            self.is_trend_view = True
            self.trend_data = {
                "summary": "",
                "wrapped_lines": [],
                "status_message": "Ready to analyze.",
                "scroll_top": 0
            }
            self.needs_redraw = True
        elif key.lower() == 'a' or key == "RIGHT":
            if items_data:
                self.is_intelligence_view = True
                selected_article = items_data[self.selected_index]
                self.intelligence_data = { # Reset the state
                    "article_title": selected_article['title'],
                    "article_text": "",
                    "ai_summary": "",
                    "status_message": "Initializing analysis...",
                    "current_article_url": selected_article['url']
                }
                # Launch the background task
                threading.Thread(target=self._fetch_and_analyze_article, daemon=True).start()
                self.needs_redraw = True
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
                    # Check if the deleted profile was the one we are currently using
                    if selected_profile == self.active_profile:
                        set_active_profile('Main')
                        self.profile_status_message = "Active profile deleted. Switching to 'Main' and restarting..."
                        NEEDS_RESTART = True
                        self.is_running = False # Exit main loop to trigger restart
                    else:
                        # If it wasn't the active profile, just update the list as normal
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
            # Launch the new subreddit editor UI
            self.is_profile_view = False
            self.is_subreddit_edit_view = True
            self.subreddit_profile_target = self.profiles[self.profile_selected_index]

            # Load current subreddits into the editor list
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE)
            section = f"Profile:{self.subreddit_profile_target}"
            current_subs = config.get(section, 'subreddits', fallback='')
            self.subreddit_list = [s for s in current_subs.split('+') if s] # Filter out empty strings
            self.subreddit_selected_index = 0
        elif key.lower() == 'd':
            if self.profiles[self.profile_selected_index] != 'Main': self.profile_action = 'delete'
            else: self.profile_status_message = "Cannot delete the 'Main' profile."
        self.needs_redraw = True

    def handle_subreddit_edit_input(self, key):
        """Handles key presses for the subreddit editor."""
        global NEEDS_RESTART
        # --- Handle text input mode first ---
        if self.subreddit_input_active:
            if key == "ENTER":
                new_sub = self.subreddit_input_query.strip()
                if new_sub and new_sub not in self.subreddit_list:
                    self.subreddit_list.append(new_sub)
                self.subreddit_input_active = False
                self.subreddit_input_query = ""
            elif key == "ESC":
                self.subreddit_input_active = False
                self.subreddit_input_query = ""
            elif key == "BACKSPACE":
                self.subreddit_input_query = self.subreddit_input_query[:-1]
            elif len(key) == 1 and key.isprintable():
                self.subreddit_input_query += key
            self.needs_redraw = True
            return

        # --- Handle navigation and action mode ---
        if key == "UP":
            self.subreddit_selected_index = max(0, self.subreddit_selected_index - 1)
        elif key == "DOWN":
            self.subreddit_selected_index = min(len(self.subreddit_list) - 1, self.subreddit_selected_index + 1)
        elif key.lower() == 'a':
            self.subreddit_input_active = True
        elif key.lower() == 'd':
            if self.subreddit_list:
                del self.subreddit_list[self.subreddit_selected_index]
                if self.subreddit_selected_index >= len(self.subreddit_list):
                    self.subreddit_selected_index = max(0, len(self.subreddit_list) - 1)
        elif key == "ESC":
            # Save the changes and exit
            updated_subreddits = '+'.join(self.subreddit_list)
            update_profile_subreddits(self.subreddit_profile_target, updated_subreddits)
            self.profile_status_message = f"Subreddits updated for '{self.subreddit_profile_target}'. Restarting..."
            self.is_subreddit_edit_view = False
            self.is_profile_view = True # Go back to the profile manager
            NEEDS_RESTART = True

        self.needs_redraw = True

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

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        if hasattr(multiprocessing, 'set_start_method') and 'fork' in multiprocessing.get_all_start_methods():
            multiprocessing.set_start_method('fork', force=True)
    except (RuntimeError, ValueError):
        pass
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
