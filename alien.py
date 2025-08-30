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

            extra = sys.stdin.read(2)
            if extra == '[A': return "UP"
            if extra == '[B': return "DOWN"
            if extra == '[C': return "RIGHT"
            if extra == '[D': return "LEFT"
            return "ESC"
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
                    return {b'H': "UP", b'P': "DOWN", b'K': "LEFT", b'M': "RIGHT"}.get(ch2)
                try: return ch.decode('utf-8')
                except UnicodeDecodeError: return None
            elif win_time.time() - start_time > timeout: return None

# --- ANSI color codes for styling the terminal output ---
class Colors:
    RESET, BOLD, CYAN = '\033[0m', '\033[1m', '\033[96m'
    YELLOW, LIGHT_GREY, GREEN = '\033[93m', '\033[38;5;248m', '\033[92m'
    MAGENTA = '\033[95m'
    BLUE = '\033[94m'

THEMES = {
    "Default": {
        "highlight_bg": '\x1b[47m', "highlight_fg": '\x1b[30m',
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[97m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[97m',
        "new_fg": Colors.YELLOW
    },
    "Solarized Dark": {
        "highlight_bg": '\x1b[48;5;22m', "highlight_fg": '\x1b[38;5;228m',
        "bar_bg": '\x1b[48;5;234m', "bar_fg": '\x1b[38;5;248m',
        "popup_bg": '\x1b[48;5;235m', "popup_fg": '\x1b[38;5;250m',
        "new_fg": '\x1b[38;5;136m' # A muted yellow/gold
    },
    "Nord": {
        "highlight_bg": '\x1b[48;5;24m', "highlight_fg": '\x1b[38;5;229m', # Dark blue bg, light cream fg
        "bar_bg": '\x1b[48;5;236m', "bar_fg": '\x1b[38;5;111m',      # Darker grey bg, muted blue fg
        "popup_bg": '\x1b[48;5;237m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;215m' # Muted orange
    },
    "Gruvbox Dark": {
        "highlight_bg": '\x1b[48;5;131m', "highlight_fg": '\x1b[38;5;229m', # Muted red bg, light cream fg
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[38;5;248m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[38;5;250m',
        "new_fg": '\x1b[38;5;214m' # Gold
    },
    "Monokai": {
        "highlight_bg": '\x1b[48;5;197m', "highlight_fg": '\x1b[38;5;233m', # Bright pink bg, dark grey fg
        "bar_bg": '\x1b[48;5;234m', "bar_fg": '\x1b[38;5;148m',      # Dark grey bg, yellow fg
        "popup_bg": '\x1b[48;5;235m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;118m' # Bright green
    },
    "Dracula+": {
        "highlight_bg": '\x1b[48;5;98m', "highlight_fg": '\x1b[38;5;231m', # Purple bg, bright white fg
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[38;5;117m',      # Dark grey bg, cyan fg
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;208m' # Orange
    },
     "Cyberpunk": {
        "highlight_bg": '\x1b[48;5;208m', "highlight_fg": '\x1b[38;5;16m', # Bright yellow/orange bg, black fg
        "bar_bg": '\x1b[48;5;17m', "bar_fg": '\x1b[38;5;228m',       # Dark blue bg, bright yellow fg
        "popup_bg": '\x1b[48;5;18m', "popup_fg": '\x1b[38;5;252m',
        "new_fg": '\x1b[38;5;198m' # Hot pink
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
DB_FILE = CONFIG_DIR / "news_feed.db"
CONFIG_FILE = CONFIG_DIR / "config.ini"

PAGE_JUMP = 10
SUBREDDITS_STRING = "news+worldnews+politics+technology"
FETCH_INTERVAL_SECONDS = 60
SHOW_CLOCK = True

# --- Globals ---
data_lock = threading.Lock()
last_checked_time = "Never"
ARTICLES_UPDATED, HAS_NEW_ARTICLES = threading.Event(), False
BLOCKED_DOMAINS = set()

# --- Settings Management ---
def load_settings():
    """Loads settings from config.ini, creating it with defaults if it doesn't exist."""
    global FETCH_INTERVAL_SECONDS, SUBREDDITS_STRING, SHOW_CLOCK, BLOCKED_DOMAINS
    config = configparser.ConfigParser()
    defaults = {
        'Theme': 'Default', 'FetchInterval': '60', 'Subreddits': SUBREDDITS_STRING,
        'ShowClock': 'true', 'BlockedDomains': ''
    }
    if not CONFIG_FILE.exists():
        config['Settings'] = defaults
        with open(CONFIG_FILE, 'w') as f: config.write(f)
    config.read(CONFIG_FILE)
    settings = config['Settings']
    FETCH_INTERVAL_SECONDS = settings.getint('FetchInterval', 60)
    SUBREDDITS_STRING = settings.get('Subreddits', SUBREDDITS_STRING)
    SHOW_CLOCK = settings.getboolean('ShowClock', True)

    blocked_str = settings.get('BlockedDomains', '')
    BLOCKED_DOMAINS = {domain.strip() for domain in blocked_str.split(',') if domain.strip()}

    return settings.get('Theme', 'Default')

def save_settings(theme_name, fetch_interval, subreddits, show_clock, blocked_domains):
    """Saves the current settings to config.ini."""
    config = configparser.ConfigParser()
    blocked_domains_str = ','.join(sorted(list(blocked_domains)))
    config['Settings'] = {
        'Theme': theme_name, 'FetchInterval': str(fetch_interval),
        'Subreddits': subreddits, 'ShowClock': str(show_clock),
        'BlockedDomains': blocked_domains_str
    }
    with open(CONFIG_FILE, 'w') as f: config.write(f)

# --- Database Functions ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;") # Enable Write-Ahead Logging
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY, title TEXT NOT NULL, subreddit TEXT NOT NULL,
                source_domain TEXT, permalink TEXT, created_utc REAL NOT NULL,
                is_read INTEGER DEFAULT 0, is_bookmarked INTEGER DEFAULT 0,
                is_new INTEGER DEFAULT 0, score INTEGER DEFAULT 0, num_comments INTEGER DEFAULT 0 ) ''')
        cursor.execute("PRAGMA table_info(articles)")
        columns = [c[1] for c in cursor.fetchall()]
        if 'score' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN score INTEGER DEFAULT 0")
        if 'num_comments' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN num_comments INTEGER DEFAULT 0")
        conn.commit()

def add_article_to_db(article):
    global HAS_NEW_ARTICLES
    domain = get_domain_from_url(article.get('url'))
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO articles (url, title, subreddit, source_domain, permalink, created_utc, score, num_comments, is_new) VALUES (?,?,?,?,?,?,?,?,?)', (
            article.get('url'), article.get('title'), article.get('subreddit'), domain,
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
            query = "SELECT * FROM articles ORDER BY created_utc DESC"
            cursor.execute(query)
        else:
            placeholders = ','.join('?' for domain in BLOCKED_DOMAINS)
            query = f"SELECT * FROM articles WHERE source_domain NOT IN ({placeholders}) ORDER BY created_utc DESC"
            cursor.execute(query, tuple(BLOCKED_DOMAINS))
        return [dict(row) for row in cursor.fetchall()]

def update_article_status(url, is_read=None, is_bookmarked=None, is_new=None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        if is_read is not None: cursor.execute("UPDATE articles SET is_read = ? WHERE url = ?", (int(is_read), url))
        if is_bookmarked is not None: cursor.execute("UPDATE articles SET is_bookmarked = ? WHERE url = ?", (int(is_bookmarked), url))
        if is_new is not None: cursor.execute("UPDATE articles SET is_new = ? WHERE url = ?", (int(is_new), url))
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
    global last_checked_time, HAS_NEW_ARTICLES
    while True:
        HAS_NEW_ARTICLES = False
        try:
            url = f"https://www.reddit.com/r/{SUBREDDITS_STRING}/new.json?limit=50"
            headers = {"User-Agent": "live_news_feed_script/2.6"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                domain = get_domain_from_url(post_data.get("url"))

                if not post_data.get("is_self") and post_data.get("url") and domain not in BLOCKED_DOMAINS:
                    add_article_to_db({
                        k: post_data.get(k) for k in
                        ["title", "url", "subreddit", "created_utc", "permalink", "score", "num_comments"]
                    })

            last_checked_time = time.strftime("%H:%M:%S")
            ARTICLES_UPDATED.set()
        except requests.exceptions.RequestException: pass
        time.sleep(FETCH_INTERVAL_SECONDS)

class NewsFeedMenu:
    def __init__(self, title="👽 Alien News Feed"):
        self.title, self.is_running, self.needs_redraw = title, True, True
        self.all_articles = []

        self.is_comment_view, self.is_settings_view = False, False
        self.is_action_menu_view, self.is_bookmarks_view = False, False
        self.is_help_view, self.is_import_view = False, False
        self.is_search_view, self.search_query = False, ""
        self.search_input_active = False
        self.selected_index, self.scroll_top = 0, 0

        self.comment_tree, self.visible_comments = [], []
        self.comment_view_status, self.comment_selected_index, self.comment_scroll_top = "", 0, 0

        self.settings_selected_index = 0
        self.theme_names = list(THEMES.keys())
        initial_theme = load_settings()
        self.current_theme_index = self.theme_names.index(initial_theme) if initial_theme in self.theme_names else 0
        self.theme = THEMES[self.theme_names[self.current_theme_index]]
        self.fetch_interval_setting = FETCH_INTERVAL_SECONDS
        self.subreddits_setting = SUBREDDITS_STRING
        self.show_clock_setting = SHOW_CLOCK
        self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))
        self.last_displayed_minute = -1

        self.status_message = ""
        self.status_message_timer = 0

        self.action_menu_article = None
        self.action_menu_selected_index = 0

    def _copy_to_clipboard(self, text):
        try:
            if sys.platform == "win32":
                subprocess.run(["clip"], input=text.strip().encode('utf-8'), check=True)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text.strip().encode('utf-8'), check=True)
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.strip().encode('utf-8'), check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

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
        except (requests.exceptions.RequestException, IndexError, KeyError) as e: self.comment_view_status = f"Error: {e}"
        self.needs_redraw = True

    def _format_comment_body(self, text):
        text = html.unescape(text)

        # Hyperlinks: [text](url) -> text [domain]
        def replace_link(match):
            text, url = match.group(1), match.group(2)
            domain = get_domain_from_url(url)
            return f'{text} {Colors.BLUE}[{domain}]{self.theme["popup_fg"]}'
        text = re.sub(r'\[(.*?)\]\((.*?)\)', replace_link, text)

        # Bold and italics
        text = re.sub(r'\*\*(.*?)\*\*', f'{Colors.BOLD}\\1{Colors.RESET}{self.theme["popup_fg"]}', text)
        text = re.sub(r'\*(.*?)\*', f'{Colors.BOLD}\\1{Colors.RESET}{self.theme["popup_fg"]}', text)

        return text

    def _draw_popup_border(self, start_x, start_y, pop_w, pop_h, title=""):
        """Helper function to draw a titled border for a popup."""
        pop_bg = self.theme['popup_bg']
        border_color = self.theme['highlight_bg']
        sys.stdout.write(border_color)

        if title:
            title_text = f" {title} "
            content_width = pop_w - 2
            remaining_width = content_width - len(title_text)
            left_dashes = remaining_width // 2
            right_dashes = remaining_width - left_dashes
            top_border = f"┌{'─' * left_dashes}{title_text}{'─' * right_dashes}┐"
        else:
            top_border = f"┌{'─' * (pop_w - 2)}┐"
        sys.stdout.write(f'\x1b[{start_y};{start_x}H{top_border}')

        for i in range(pop_h - 2):
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x}H│')
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x + pop_w - 1}H│')

        sys.stdout.write(f'\x1b[{start_y + pop_h - 1};{start_x}H└' + '─' * (pop_w - 2) + '┘')

        sys.stdout.write(pop_bg)
        for i in range(pop_h - 2):
            sys.stdout.write(f'\x1b[{start_y + 1 + i};{start_x + 1}H{" " * (pop_w - 2)}')

    def _draw_import_instructions(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 74, 11
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Import Instructions")

        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        content = [
            f"{Colors.YELLOW}Your config folder has been opened.",
            f"{pop_fg}To restore a backup, you can either:",
            f"{Colors.BOLD}A) Manually replace the database file:{pop_fg}",
            f"  1. Close this application.",
            f"  2. In the folder that opened, replace 'news_feed.db' with your backup.",
            f"{Colors.BOLD}B) Use the command-line option on next launch:{pop_fg}",
            f"  > python main.py --import /path/to/your/backup.db",
            f"",
            f"{Colors.CYAN}Press [ESC] to dismiss this message.{pop_fg}"
        ]
        for i, line in enumerate(content):
            sys.stdout.write(f"\x1b[{start_y + 1 + i};{start_x + 2}H{pop_bg}{line.ljust(pop_w - 4)}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_help_menu(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, 16
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
            f"{header_color}== Views & Modes ==",
            f"  {key_color}[/]{desc_color}       - Enter search mode (Press Enter to browse results)",
            f"  {key_color}[v]{desc_color}       - Toggle bookmarks-only view",
            f"  {key_color}[s]{desc_color}       - Open settings",
            f"  {key_color}[h]{desc_color}       - Show this help screen",
            f"  {key_color}[ESC]{desc_color}     - Go back, clear search, or quit",
            f"",
            f"{Colors.LIGHT_GREY}      Alien News Feed v3.0 - Created by You!"
        ]

        for i, line in enumerate(content):
            sys.stdout.write(f"\x1b[{start_y + 1 + i};{start_x + 2}H{pop_bg}{line}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_action_menu(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 50, 11
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Actions")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        options = [
            "Open Article in Browser", "Open Comments in Browser", "Summarize with Perplexity",
            "Copy URL to Clipboard", "Archive Page (archive.is)", "Exclude this domain"
        ]

        sep1_pos, sep2_pos = 3, 5

        for i, option in enumerate(options):
            row_offset = 0
            if i >= sep1_pos: row_offset += 1
            if i >= sep2_pos: row_offset += 1
            row = start_y + 2 + i + row_offset

            if i == sep1_pos:
                sys.stdout.write(f"\x1b[{start_y+2+i};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")
            if i == sep2_pos:
                sys.stdout.write(f"\x1b[{start_y+2+i+1};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")

            text = option.center(pop_w - 4)
            if i == self.action_menu_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_settings(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, 13
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Settings")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        sub_text = self.subreddits_setting
        if self.settings_selected_index == 3: sub_text += "_"
        blocked_text = self.blocked_domains_setting
        if self.settings_selected_index == 4: blocked_text += "_"
        clock_status = "< Enabled >" if self.show_clock_setting else "< Disabled >"

        options = [
            f"Refresh Time (s): < {self.fetch_interval_setting} >",
            f"Color Theme: < {self.theme_names[self.current_theme_index]} >",
            f"Show Clock: {clock_status}",
            f"Subreddits: {sub_text}",
            f"Blocked Domains: {blocked_text}",
            "SEPARATOR",
            "Export Bookmarks to HTML",
            "Export Full Backup",
            "Import from Backup"
        ]

        divider_pos = 5

        for i, option in enumerate(options):
            row = start_y + 2 + i
            if i > divider_pos: row += 1

            if i == divider_pos:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{'─'*(pop_w-4)}{Colors.RESET}")
                continue

            text = option.ljust(pop_w - 4)

            logical_index = i
            if i > divider_pos: logical_index -= 1

            if logical_index == self.settings_selected_index:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else:
                sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_comments(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = int(term_w * 0.9), int(term_h * 0.9)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        self._draw_popup_border(start_x, start_y, pop_w, pop_h, "Comments")
        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']

        sys.stdout.write(f'\x1b[{start_y+pop_h-2};{start_x}H├' + '─'*(pop_w-2) + '┤')

        if self.comment_view_status:
            sys.stdout.write(f'\x1b[{start_y+2};{start_x+2}H{pop_bg}{pop_fg}{self.comment_view_status.ljust(pop_w-4)}{Colors.RESET}')
        elif self.comment_tree:
            self.visible_comments = []; self._flatten_comment_tree(self.comment_tree, self.visible_comments)
            cont_w, cont_h = pop_w - 4, pop_h - 4
            lines, sel_line = [], -1
            for i, c in enumerate(self.visible_comments):
                if i == self.comment_selected_index: sel_line = len(lines)
                indicator = "[+] " if c.children and c.is_collapsed else "[-] " if c.children else ""
                header = f"{indicator}{Colors.YELLOW}{c.author}{pop_fg} ({c.score}):"
                lines.append({'text': f"{'  '*c.depth}{header}", 'idx': i})

                formatted_body = self._format_comment_body(c.body)
                for line in formatted_body.split('\n'):
                    prefix, quote_offset = "", 0
                    if line.startswith('>'):
                        line = line[1:].lstrip()
                        prefix = f"{Colors.GREEN}┃ {Colors.RESET}{pop_fg}"
                        quote_offset = 2

                    wrapped_lines = textwrap.wrap(line, width=cont_w - len("  "*c.depth) - quote_offset)
                    for wrapped_line in wrapped_lines:
                        lines.append({'text': f"{'  '*c.depth}{prefix}{wrapped_line}", 'idx': i})

            if sel_line != -1:
                if sel_line < self.comment_scroll_top: self.comment_scroll_top = sel_line
                if sel_line >= self.comment_scroll_top+cont_h: self.comment_scroll_top = sel_line-cont_h+1
            for i in range(cont_h):
                line_idx = self.comment_scroll_top + i
                if line_idx >= len(lines): break
                line, row = lines[line_idx], start_y+1+i
                is_sel = line['idx'] == self.comment_selected_index
                output = line['text'].ljust(cont_w)
                if is_sel: sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{output}{Colors.RESET}")
                else: sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{output}{Colors.RESET}")
        help_text = "[↑/↓] Scroll | [←/→] Top-Level | [↵]Collapse | [ESC]Back".center(pop_w - 2)
        sys.stdout.write(f"\x1b[{start_y+pop_h-2};{start_x+1}H{pop_bg}{pop_fg}{help_text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw(self, items_data, is_background=False):
        if not is_background: sys.stdout.write(Colors.RESET)
        os.system('cls' if os.name == 'nt' else 'clear')
        term_w, term_h = os.get_terminal_size()

        title = self.title
        if self.is_bookmarks_view: title += " [Bookmarks]"
        if self.is_search_view: title += f" [Search: {self.search_query}]"

        BG_BAR, FG_BAR = self.theme['bar_bg'], self.theme['bar_fg']

        if self.show_clock_setting:
            current_time = time.strftime("%A, %B %d, %Y %I:%M %p")
            padding = ' ' * max(0, term_w - (len(title) + 1) - len(current_time))
            full_title_bar = f"{title}{padding}{current_time}"
        else:
            full_title_bar = title
        sys.stdout.write(f'\x1b[1;1H{BG_BAR}{FG_BAR}{full_title_bar.ljust(term_w)}{Colors.RESET}')

        HL_BG, FG_HL = self.theme['highlight_bg'], self.theme['highlight_fg']

        if not items_data: sys.stdout.write(f'\x1b[3;1HNo articles found...{Colors.RESET}')
        else:
            self.selected_index = max(0, min(self.selected_index, len(items_data)-1))
            max_view = max(1, term_h - 5)
            if self.selected_index < self.scroll_top: self.scroll_top = self.selected_index
            if self.selected_index >= self.scroll_top + max_view: self.scroll_top = self.selected_index-max_view+1
            for i in range(self.scroll_top, min(self.scroll_top+max_view, len(items_data))):
                item, row = items_data[i], i-self.scroll_top+3
                sub, src = f"{Colors.GREEN}[{item.get('subreddit')}]", f"{Colors.CYAN}[{item.get('source_domain','')}]"
                bookmark, title_color = ("🔖 " if item.get('is_bookmarked') else ""), ""
                if item.get('is_new'): title_color = self.theme['new_fg']
                elif item.get('is_read'): title_color = Colors.LIGHT_GREY
                display = f"{title_color}{format_time_ago(item.get('created_utc')):<8} {sub} {src}{Colors.RESET} {bookmark}{item.get('title')}{Colors.RESET}"
                line = f"> {display}" if i == self.selected_index else f"  {display}"
                if i == self.selected_index and not is_background: sys.stdout.write(f'\x1b[{row};1H{HL_BG}{FG_HL}{line.ljust(term_w)}{Colors.RESET}')
                else: sys.stdout.write(f'\x1b[{row};1H{line.ljust(term_w)}{Colors.RESET}')
        footer_row = term_h

        if self.status_message_timer > 0:
             help_text = self.status_message
        elif self.is_search_view:
            if self.search_input_active:
                help_text = f"Search: {self.search_query}_"
            else:
                help_text = f"Browsing search. [/] Edit | [ESC] Clear"
        else:
            help_text = "[v]Bkmrks [/]Srch |[b]Mark [c]Cmnts |[s]Settings [h]Help |[↵]Actions |[ESC]Quit"

        last_checked = f"Last checked: {last_checked_time}"
        padding = ' ' * max(0, term_w-len(help_text)-len(last_checked)-2)
        sys.stdout.write(f'\x1b[{footer_row};1H{BG_BAR}{FG_BAR}{(help_text+padding+last_checked).ljust(term_w)}{Colors.RESET}')
        sys.stdout.flush()

    def show(self):
        global HAS_NEW_ARTICLES
        self.all_articles = get_articles_from_db()
        while self.is_running:
            if self.status_message_timer > 0:
                self.status_message_timer -= 1
                if self.status_message_timer == 0:
                    self.status_message = ""
                    self.needs_redraw = True

            if ARTICLES_UPDATED.is_set():
                if HAS_NEW_ARTICLES:
                    self.all_articles = get_articles_from_db()
                    with data_lock:
                        if HAS_NEW_ARTICLES: self.selected_index, self.scroll_top, HAS_NEW_ARTICLES = 0,0,False
                self.needs_redraw = True; ARTICLES_UPDATED.clear()

            if self.show_clock_setting:
                current_minute = time.localtime().tm_min
                if current_minute != self.last_displayed_minute:
                    self.last_displayed_minute = current_minute
                    self.needs_redraw = True

            items_data = self.all_articles
            if self.is_bookmarks_view:
                items_data = [a for a in self.all_articles if a['is_bookmarked']]
            if self.is_search_view:
                q = self.search_query.lower()
                items_data = [a for a in items_data if q in a['title'].lower() or q in a.get('source_domain','').lower()]

            if self.needs_redraw:
                if self.is_action_menu_view: self._draw_action_menu(items_data)
                elif self.is_settings_view: self._draw_settings(items_data)
                elif self.is_comment_view: self._draw_comments(items_data)
                elif self.is_help_view: self._draw_help_menu(items_data)
                elif self.is_import_view: self._draw_import_instructions(items_data)
                else: self._draw(items_data)
                self.needs_redraw = False

            key = getch()
            if not key: continue

            if self.is_action_menu_view: self.handle_action_menu_input(key)
            elif self.is_settings_view: self.handle_settings_input(key)
            elif self.is_comment_view: self.handle_comment_view_input(key)
            elif self.is_help_view: self.handle_help_view_input(key)
            elif self.is_import_view: self.handle_import_view_input(key)
            elif self.is_search_view: self.handle_search_view_input(key, items_data)
            else: self.handle_main_view_input(key, items_data)

    def handle_import_view_input(self, key):
        if key == "ESC":
            self.is_import_view = False
        self.needs_redraw = True

    def handle_help_view_input(self, key):
        if key == "ESC" or key == "h":
            self.is_help_view = False
        self.needs_redraw = True

    def handle_action_menu_input(self, key):
        if key == "ESC": self.is_action_menu_view = False
        elif key == "UP": self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
        elif key == "DOWN": self.action_menu_selected_index = min(5, self.action_menu_selected_index + 1)
        elif key == "ENTER":
            idx = self.action_menu_selected_index
            if idx == 0:
                url = self.action_menu_article['url']
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif idx == 1:
                url = f"https://www.reddit.com{self.action_menu_article['permalink']}"
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif idx == 2:
                query = f"summarize {self.action_menu_article['url']}"
                url = f"https://www.perplexity.ai/?s=o&q={quote(query)}"
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif idx == 3:
                self._copy_to_clipboard(self.action_menu_article['url'])
            elif idx == 4:
                url = f"https://archive.is/{quote(self.action_menu_article['url'])}"
                threading.Thread(target=webbrowser.open, args=(url,)).start()
            elif idx == 5:
                domain_to_block = get_domain_from_url(self.action_menu_article['url'])
                if domain_to_block and domain_to_block not in BLOCKED_DOMAINS:
                    BLOCKED_DOMAINS.add(domain_to_block)
                    save_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting,
                                  self.subreddits_setting, self.show_clock_setting, BLOCKED_DOMAINS)
                    self.all_articles = [a for a in self.all_articles if get_domain_from_url(a.get('url')) != domain_to_block]
                    self.blocked_domains_setting = ','.join(sorted(list(BLOCKED_DOMAINS)))

            self.is_action_menu_view = False
        self.needs_redraw = True

    def handle_settings_input(self, key):
        global BLOCKED_DOMAINS
        if key == "ESC":
            self.blocked_domains_setting = self.blocked_domains_setting.strip(',')
            BLOCKED_DOMAINS = {d.strip() for d in self.blocked_domains_setting.split(',') if d.strip()}
            save_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting, self.subreddits_setting, self.show_clock_setting, BLOCKED_DOMAINS)
            self.is_settings_view = False
        elif key == "UP": self.settings_selected_index = max(0, self.settings_selected_index - 1)
        elif key == "DOWN": self.settings_selected_index = min(7, self.settings_selected_index + 1)
        elif key == "ENTER":
            idx = self.settings_selected_index
            if idx == 5: # Export Bookmarks
                path = export_bookmarks_to_html()
                self.status_message = f"Bookmarks exported to backups folder!"
                self.status_message_timer = 50
                self.is_settings_view = False
            elif idx == 6: # Export Full Backup
                backups_dir = CONFIG_DIR / "backups"
                backups_dir.mkdir(exist_ok=True)
                backup_filename = f"backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
                dest_path = backups_dir / backup_filename
                shutil.copy(DB_FILE, dest_path)
                self.status_message = f"Backup saved to backups folder!"
                self.status_message_timer = 50
                self.is_settings_view = False
            elif idx == 7: # Import from Backup
                webbrowser.open(CONFIG_DIR.resolve().as_uri())
                self.is_settings_view = False
                self.is_import_view = True
        elif self.settings_selected_index == 0: # Refresh Time
            if key == "LEFT": self.fetch_interval_setting = max(15, self.fetch_interval_setting - 15)
            elif key == "RIGHT": self.fetch_interval_setting += 15
        elif self.settings_selected_index == 1: # Theme
            if key == "RIGHT":
                self.current_theme_index = (self.current_theme_index + 1) % len(self.theme_names)
            elif key == "LEFT":
                self.current_theme_index = (self.current_theme_index - 1 + len(self.theme_names)) % len(self.theme_names)
            self.theme = THEMES[self.theme_names[self.current_theme_index]]
        elif self.settings_selected_index == 2: # Show Clock
            if key == "LEFT" or key == "RIGHT": self.show_clock_setting = not self.show_clock_setting
        elif self.settings_selected_index == 3: # Subreddits
            if key == "BACKSPACE": self.subreddits_setting = self.subreddits_setting[:-1]
            elif len(key) == 1 and key.isprintable(): self.subreddits_setting += key
        elif self.settings_selected_index == 4: # Blocked Domains
            if key == "BACKSPACE": self.blocked_domains_setting = self.blocked_domains_setting[:-1]
            elif len(key) == 1 and key.isprintable(): self.blocked_domains_setting += key

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
                    self.needs_redraw = True
            if original_index != self.comment_selected_index: self.needs_redraw = True
        if key == "ESC":
            self.is_comment_view, self.comment_tree = False, []
            self.needs_redraw = True

    def handle_search_view_input(self, key, items_data):
        if self.search_input_active:
            if key == "ENTER":
                self.search_input_active = False
            elif key == "ESC":
                self.is_search_view, self.search_query = False, ""
                self.search_input_active = False
            elif key == "BACKSPACE":
                self.search_query = self.search_query[:-1]
            elif len(key) == 1 and key.isprintable():
                self.search_query += key
        else:
            if key == '/':
                self.search_input_active = True
            elif key == "ESC":
                self.is_search_view, self.search_query = False, ""
            else:
                self.handle_main_view_input(key, items_data)
        self.needs_redraw = True

    def handle_main_view_input(self, key, items_data):
        """Handles all key presses for the main article list view."""
        if not items_data and key not in ["ESC", "s", "v", "/", "h"]: return

        original_index = self.selected_index
        if key == "UP": self.selected_index = max(0, self.selected_index - 1)
        elif key == "DOWN": self.selected_index = min(len(items_data) - 1, self.selected_index + 1)
        elif key == "LEFT": self.selected_index = max(0, self.selected_index - PAGE_JUMP)
        elif key == "RIGHT": self.selected_index = min(len(items_data) - 1, self.selected_index + PAGE_JUMP)
        elif key == "b":
            if items_data:
                selected = items_data[self.selected_index]
                new_status = not selected.get('is_bookmarked')
                update_article_status(url=selected['url'], is_bookmarked=new_status)
                selected['is_bookmarked'] = new_status
        elif key == "c":
            if items_data:
                self.is_comment_view, self.comment_view_status = True, "Loading comments..."
                permalink = items_data[self.selected_index].get('permalink')
                threading.Thread(target=self._fetch_comments_threaded, args=(permalink,)).start()
        elif key == "s": self.is_settings_view = True
        elif key == "h": self.is_help_view = True
        elif key == 'v':
            self.is_bookmarks_view = not self.is_bookmarks_view
            self.selected_index, self.scroll_top = 0,0
        elif key == '/':
            self.is_search_view, self.search_query, self.search_input_active = True, "", True
        elif key == "ENTER":
            if items_data:
                self.is_action_menu_view = True
                self.action_menu_article = items_data[self.selected_index]
                self.action_menu_selected_index = 0
                update_article_status(url=self.action_menu_article['url'], is_read=True)
                items_data[self.selected_index]['is_read'] = True
        elif key == "ESC": self.is_running = False

        if original_index != self.selected_index:
            if items_data:
                newly_selected = items_data[self.selected_index]
                if newly_selected.get('is_new'):
                    newly_selected['is_new'] = False
                    update_article_status(url=newly_selected['url'], is_new=False)
        self.needs_redraw = True

# --- Command-line and Utility Functions ---
def export_bookmarks_to_html():
    """Queries the DB for bookmarks and exports them to a styled HTML file."""
    backups_dir = CONFIG_DIR / "backups"
    backups_dir.mkdir(exist_ok=True)
    html_filename = f"bookmarks-{time.strftime('%Y%m%d')}.html"
    dest_path = backups_dir / html_filename

    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles WHERE is_bookmarked = 1 ORDER BY created_utc DESC")
        bookmarks = [dict(row) for row in cursor.fetchall()]

    if not bookmarks:
        li_items = "<li>No bookmarks found.</li>"
    else:
        li_items_list = []
        for b in bookmarks:
            domain = b.get('source_domain', 'N/A')
            li_items_list.append(f'<li><a href="{b["url"]}">{b["title"]}</a> <span class="meta">({domain})</span></li>')
        li_items = "\n".join(li_items_list)

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Alien News Feed Bookmarks</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
               background-color: #1e1e1e; color: #d4d4d4; line-height: 1.6; margin: 0; padding: 2em; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ color: #569cd6; border-bottom: 1px solid #444; padding-bottom: 0.5em; }}
        p {{ color: #999; }}
        ul {{ list-style-type: none; padding: 0; }}
        li {{ margin-bottom: 1em; padding: 1em; background-color: #252526; border-left: 3px solid #569cd6; }}
        a {{ color: #9cdcfe; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .meta {{ font-size: 0.8em; color: #888; margin-left: 0.5em; }}
    </style>
</head>
<body><div class="container">
    <h1>👽 Alien News Feed Bookmarks</h1>
    <p>Exported on: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
    <ul>{li_items}</ul>
</div></body></html>"""

    with open(dest_path, 'w', encoding='utf-8') as f:
        f.write(html_template)
    return dest_path

def export_database():
    """Headless export of the database."""
    backups_dir = CONFIG_DIR / "backups"
    backups_dir.mkdir(exist_ok=True)
    backup_filename = f"backup-{time.strftime('%Y%m%d-%H%M%S')}.db"
    dest_path = backups_dir / backup_filename
    try:
        shutil.copy(DB_FILE, dest_path)
        print(f"Success! Backup saved to:\n{dest_path}")
    except FileNotFoundError:
        print(f"Error: Database file not found at {DB_FILE}")
        sys.exit(1)

def import_database(path_str):
    """Headless import of a database file with confirmation."""
    backup_path = Path(path_str)
    if not backup_path.is_file():
        print(f"Error: Backup file not found at '{backup_path}'")
        sys.exit(1)

    print("--- WARNING ---")
    print("This will permanently overwrite your current database.")
    print(f"Current DB: {DB_FILE}")
    print(f"Importing from: {backup_path}")

    confirm = input("Are you sure you want to continue? (y/n): ").lower().strip()

    if confirm in ['y', 'yes']:
        shutil.copy(backup_path, DB_FILE)
        print("Import successful. Starting application...")
    else:
        print("Import cancelled.")
        sys.exit(0)

# --- Main Execution ---
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="A terminal-based news feed reader.")
    parser.add_argument('--export', action='store_true', help="Export a full backup of the database and exit.")
    parser.add_argument('--import', dest='import_path', metavar='PATH', help="Import a database from the specified path and start the app.")
    args = parser.parse_args()

    pid_file = pid.PidFile(pidname='aliennewsfeed', piddir=CONFIG_DIR)

    try:
        with pid_file: # Enforces single instance
            init_db()

            if args.export:
                export_database()
                sys.exit(0)

            if args.import_path:
                import_database(args.import_path)

            print(f"Initializing AlienNewsFeed...")
            print(f"Config and database stored in: {CONFIG_DIR}")

            # Start the background thread for fetching articles
            threading.Thread(target=fetch_articles_threaded, daemon=True).start()

            time.sleep(1)

            menu = NewsFeedMenu()
            try:
                menu.show()
            finally:
                os.system('cls' if os.name == 'nt' else 'clear')
                print("Exiting.")
    except pid.PidFileAlreadyLockedError:
        print("Another instance of Alien News Feed is already running. Exiting.")
        sys.exit(1)

