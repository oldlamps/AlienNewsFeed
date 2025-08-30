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
from pathlib import Path
from urllib.parse import urlparse, quote

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

THEMES = {
    "Default": {
        "highlight_bg": '\x1b[47m', "highlight_fg": '\x1b[30m',
        "bar_bg": '\x1b[48;5;235m', "bar_fg": '\x1b[97m',
        "popup_bg": '\x1b[48;5;236m', "popup_fg": '\x1b[97m'
    },
    "Light": {
        "highlight_bg": '\x1b[48;5;235m', "highlight_fg": '\x1b[97m',
        "bar_bg": '\x1b[47m', "bar_fg": '\x1b[30m',
        "popup_bg": '\x1b[47m', "popup_fg": '\x1b[30m'
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

# --- Globals ---
data_lock = threading.Lock()
last_checked_time = "Never"
ARTICLES_UPDATED, HAS_NEW_ARTICLES = threading.Event(), False

# --- Settings Management ---
def load_settings():
    """Loads settings from config.ini, creating it with defaults if it doesn't exist."""
    global FETCH_INTERVAL_SECONDS, SUBREDDITS_STRING
    config = configparser.ConfigParser()
    if not CONFIG_FILE.exists():
        config['Settings'] = {'Theme': 'Default', 'FetchInterval': '60', 'Subreddits': SUBREDDITS_STRING}
        with open(CONFIG_FILE, 'w') as f: config.write(f)
    config.read(CONFIG_FILE)
    FETCH_INTERVAL_SECONDS = config.getint('Settings', 'FetchInterval', fallback=60)
    SUBREDDITS_STRING = config.get('Settings', 'Subreddits', fallback=SUBREDDITS_STRING)
    return config.get('Settings', 'Theme', fallback='Default')

def save_settings(theme_name, fetch_interval, subreddits):
    """Saves the current settings to config.ini."""
    config = configparser.ConfigParser()
    config['Settings'] = {'Theme': theme_name, 'FetchInterval': str(fetch_interval), 'Subreddits': subreddits}
    with open(CONFIG_FILE, 'w') as f: config.write(f)

# --- Database Functions ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY, title TEXT NOT NULL, subreddit TEXT NOT NULL,
                source_domain TEXT, permalink TEXT, created_utc REAL NOT NULL,
                is_read INTEGER DEFAULT 0, is_bookmarked INTEGER DEFAULT 0,
                is_new INTEGER DEFAULT 0 ) ''')
        cursor.execute("PRAGMA table_info(articles)")
        columns = [c[1] for c in cursor.fetchall()]
        if 'source_domain' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN source_domain TEXT")
        if 'permalink' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN permalink TEXT")
        if 'is_new' not in columns: cursor.execute("ALTER TABLE articles ADD COLUMN is_new INTEGER DEFAULT 0")
        conn.commit()

def add_article_to_db(article):
    global HAS_NEW_ARTICLES
    domain = get_domain_from_url(article['url'])
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?,?,?)', (
            article['url'], article['title'], article['subreddit'], domain,
            article['permalink'], article['created_utc'], 0, 0, 1)) # read, bookmarked, new
        if cursor.rowcount > 0:
            with data_lock: HAS_NEW_ARTICLES = True
        conn.commit()

def get_articles_from_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM articles ORDER BY created_utc DESC")
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
            headers = {"User-Agent": "live_news_feed_script/2.3"}
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                if not post_data.get("is_self") and post_data.get("url"):
                    add_article_to_db({k: post_data.get(k) for k in ["title", "url", "subreddit", "created_utc", "permalink"]})
            last_checked_time = time.strftime("%H:%M:%S")
            ARTICLES_UPDATED.set()
        except requests.exceptions.RequestException: pass
        time.sleep(FETCH_INTERVAL_SECONDS)

class NewsFeedMenu:
    def __init__(self, title="Live Reddit News Feed"):
        self.title, self.is_running, self.needs_redraw = title, True, True
        self.all_articles = []

        self.is_comment_view, self.is_settings_view = False, False
        self.is_action_menu_view, self.is_bookmarks_view = False, False
        self.is_search_view, self.search_query = False, ""
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

        self.action_menu_article = None
        self.action_menu_selected_index = 0

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
            url, headers = f"https://www.reddit.com{permalink.rstrip('/')}.json", {"User-Agent": "live_news_feed_script/2.3"}
            response = requests.get(url, headers=headers, timeout=10); response.raise_for_status()
            raw_comments = response.json()[1].get("data", {}).get("children", [])
            if not raw_comments: self.comment_view_status = "No comments found."
            else: self.comment_tree, self.comment_view_status = self._parse_comments_to_tree(raw_comments), ""
            self.comment_selected_index, self.comment_scroll_top = 0, 0
        except (requests.exceptions.RequestException, IndexError, KeyError) as e: self.comment_view_status = f"Error: {e}"
        self.needs_redraw = True

    def _draw_action_menu(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 50, 7
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        sys.stdout.write(f'{pop_bg}'); sys.stdout.write(f'\x1b[{start_y};{start_x}H┌' + '─'*(pop_w-2) + '┐')
        for i in range(pop_h-2): sys.stdout.write(f'\x1b[{start_y+1+i};{start_x}H│{" "*(pop_w-2)}│')
        sys.stdout.write(f'\x1b[{start_y+pop_h-1};{start_x}H└' + '─'*(pop_w-2) + '┘')

        options = ["Open Article in Browser", "Open Comments in Browser", "Summarize with Perplexity"]
        for i, option in enumerate(options):
            row, text = start_y+2+i, option.center(pop_w - 4)
            if i == self.action_menu_selected_index: sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else: sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_settings(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = 70, 9
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        sys.stdout.write(f'{pop_bg}'); sys.stdout.write(f'\x1b[{start_y};{start_x}H┌' + '─'*(pop_w-2) + '┐')
        for i in range(pop_h - 2): sys.stdout.write(f'\x1b[{start_y+1+i};{start_x}H│{" "*(pop_w-2)}│')
        sys.stdout.write(f'\x1b[{start_y+pop_h-1};{start_x}H└' + '─'*(pop_w-2) + '┘')

        sub_text = self.subreddits_setting
        if self.settings_selected_index == 2: sub_text += "_"
        settings = [
            f"Refresh Time (s): < {self.fetch_interval_setting} > (Restart required)",
            f"Color Theme: < {self.theme_names[self.current_theme_index]} >",
            f"Subreddits: {sub_text}",
            "Export Database (Not implemented)", "Import Database (Not implemented)"
        ]
        for i, option in enumerate(settings):
            row, text = start_y+2+i, option.ljust(pop_w - 4)
            if i == self.settings_selected_index: sys.stdout.write(f"\x1b[{row};{start_x+2}H{self.theme['highlight_bg']}{self.theme['highlight_fg']}{text}{Colors.RESET}")
            else: sys.stdout.write(f"\x1b[{row};{start_x+2}H{pop_bg}{pop_fg}{text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw_comments(self, items_data):
        self._draw(items_data, is_background=True)
        term_w, term_h = os.get_terminal_size()
        pop_w, pop_h = int(term_w * 0.9), int(term_h * 0.9)
        start_x, start_y = (term_w - pop_w) // 2, (term_h - pop_h) // 2

        pop_bg, pop_fg = self.theme['popup_bg'], self.theme['popup_fg']
        sys.stdout.write(f'{pop_bg}'); sys.stdout.write(f'\x1b[{start_y};{start_x}H┌' + '─'*(pop_w-2) + '┐')
        for i in range(pop_h - 3): sys.stdout.write(f'\x1b[{start_y+1+i};{start_x}H│{" "*(pop_w-2)}│')
        sys.stdout.write(f'\x1b[{start_y+pop_h-2};{start_x}H├' + '─'*(pop_w-2) + '┤')
        sys.stdout.write(f'\x1b[{start_y+pop_h-1};{start_x}H└' + '─'*(pop_w-2) + '┘')

        if self.comment_view_status:
            sys.stdout.write(f'\x1b[{start_y+2};{start_x+2}H{pop_bg}{pop_fg}{self.comment_view_status.ljust(pop_w-4)}{Colors.RESET}')
        elif self.comment_tree:
            self.visible_comments = []; self._flatten_comment_tree(self.comment_tree, self.visible_comments)
            cont_w, cont_h = pop_w - 4, pop_h - 3
            lines, sel_line = [], -1
            for i, c in enumerate(self.visible_comments):
                if i == self.comment_selected_index: sel_line = len(lines)
                indicator = "[+] " if c.children and c.is_collapsed else "[-] " if c.children else ""
                header = f"{indicator}{Colors.YELLOW}{c.author}{pop_fg} ({c.score}):"
                body = textwrap.wrap(c.body, width=cont_w - len("  "*c.depth))
                lines.append({'text': f"{'  '*c.depth}{header}", 'idx': i})
                for l in body: lines.append({'text': f"{'  '*c.depth}{l}", 'idx': i})
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
        help_text = "[↑/↓] Scroll | [k/j] Top-Level | [↵]Collapse | [ESC]Back".center(pop_w - 2)
        sys.stdout.write(f"\x1b[{start_y+pop_h-2};{start_x+1}H{pop_bg}{pop_fg}{help_text}{Colors.RESET}")
        sys.stdout.flush()

    def _draw(self, items_data, is_background=False):
        if not is_background: sys.stdout.write(Colors.RESET)
        os.system('cls' if os.name == 'nt' else 'clear')
        term_w, term_h = os.get_terminal_size()

        title = self.title
        if self.is_bookmarks_view: title += " [Bookmarks]"
        if self.is_search_view: title += f" [Search: {self.search_query}]"
        sys.stdout.write(f'\x1b[1;1H{title}')

        HL_BG, FG_HL, BG_BAR, FG_BAR = self.theme['highlight_bg'], self.theme['highlight_fg'], self.theme['bar_bg'], self.theme['bar_fg']

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
                if item.get('is_new'): title_color = Colors.YELLOW
                elif item.get('is_read'): title_color = Colors.LIGHT_GREY
                display = f"{title_color}{format_time_ago(item.get('created_utc')):<8} {sub} {src}{Colors.RESET} {bookmark}{item.get('title')}{Colors.RESET}"
                line = f"> {display}" if i == self.selected_index else f"  {display}"
                if i == self.selected_index and not is_background: sys.stdout.write(f'\x1b[{row};1H{HL_BG}{FG_HL}{line.ljust(term_w)}{Colors.RESET}')
                else: sys.stdout.write(f'\x1b[{row};1H{line.ljust(term_w)}{Colors.RESET}')
        footer_row = term_h

        if self.is_search_view:
            help_text = f"Search: {self.search_query}_"
        else:
            help_text = "[v]Bookmarks [/]Search |[b]Mark |[c]Comments |[s]Settings |[↵]Actions |[ESC]Quit"

        last_checked = f"Last checked: {last_checked_time}"
        padding = ' ' * max(0, term_w-len(help_text)-len(last_checked)-2)
        sys.stdout.write(f'\x1b[{footer_row};1H{BG_BAR}{FG_BAR}{(help_text+padding+last_checked).ljust(term_w)}{Colors.RESET}')
        sys.stdout.flush()

    def show(self):
        global HAS_NEW_ARTICLES, FETCH_INTERVAL_SECONDS
        self.all_articles = get_articles_from_db()
        while self.is_running:
            if ARTICLES_UPDATED.is_set():
                if HAS_NEW_ARTICLES:
                    self.all_articles = get_articles_from_db()
                    with data_lock:
                        if HAS_NEW_ARTICLES: self.selected_index, self.scroll_top, HAS_NEW_ARTICLES = 0,0,False
                self.needs_redraw = True; ARTICLES_UPDATED.clear()

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
                else: self._draw(items_data)
                self.needs_redraw = False

            key = getch()
            if not key: continue

            # --- MODIFIED: Refactored input handling to be mutually exclusive ---
            if self.is_action_menu_view:
                if key == "ESC": self.is_action_menu_view = False
                elif key == "UP": self.action_menu_selected_index = max(0, self.action_menu_selected_index - 1)
                elif key == "DOWN": self.action_menu_selected_index = min(2, self.action_menu_selected_index + 1)
                elif key == "ENTER":
                    if self.action_menu_selected_index == 0: webbrowser.open(self.action_menu_article['url'])
                    elif self.action_menu_selected_index == 1: webbrowser.open(f"https://www.reddit.com{self.action_menu_article['permalink']}")
                    elif self.action_menu_selected_index == 2:
                        query = f"summarize {self.action_menu_article['url']}"
                        webbrowser.open(f"https://www.perplexity.ai/?s=o&q={quote(query)}")
                    self.is_action_menu_view = False
                self.needs_redraw = True

            elif self.is_settings_view:
                if key == "ESC":
                    save_settings(self.theme_names[self.current_theme_index], self.fetch_interval_setting, self.subreddits_setting)
                    self.is_settings_view = False
                elif key == "UP": self.settings_selected_index = max(0, self.settings_selected_index - 1)
                elif key == "DOWN": self.settings_selected_index = min(4, self.settings_selected_index + 1)
                elif self.settings_selected_index == 0:
                    if key == "LEFT": self.fetch_interval_setting = max(15, self.fetch_interval_setting - 15)
                    elif key == "RIGHT": self.fetch_interval_setting += 15
                elif self.settings_selected_index == 1:
                    if key == "LEFT" or key == "RIGHT":
                        self.current_theme_index = (self.current_theme_index + 1) % len(self.theme_names)
                        self.theme = THEMES[self.theme_names[self.current_theme_index]]
                elif self.settings_selected_index == 2:
                    if key == "BACKSPACE": self.subreddits_setting = self.subreddits_setting[:-1]
                    elif len(key) == 1 and key.isprintable(): self.subreddits_setting += key
                self.needs_redraw = True

            elif self.is_comment_view:
                if self.visible_comments:
                    original_index = self.comment_selected_index
                    if key == "UP": self.comment_selected_index = max(0, self.comment_selected_index - 1)
                    elif key == "DOWN": self.comment_selected_index = min(len(self.visible_comments) - 1, self.comment_selected_index + 1)
                    elif key == 'j':
                        for i in range(self.comment_selected_index - 1, -1, -1):
                            if self.visible_comments[i].depth == 0: self.comment_selected_index = i; break
                    elif key == 'k':
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

            elif self.is_search_view:
                if key == "ESC":
                    self.is_search_view = False
                    self.search_query = ""
                elif key == "BACKSPACE":
                    self.search_query = self.search_query[:-1]
                elif len(key) == 1 and key.isprintable():
                    self.search_query += key
                else: # Pass through other keys to main handler
                    self.handle_main_view_input(key, items_data)
                self.needs_redraw = True

            else: # Main view handler
                self.handle_main_view_input(key, items_data)

    def handle_main_view_input(self, key, items_data):
        """Handles all key presses for the main article list view."""
        if not items_data and key not in ["ESC", "s", "v", "/"]: return

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
        elif key == 'v':
            self.is_bookmarks_view = not self.is_bookmarks_view
            self.selected_index, self.scroll_top = 0,0
        elif key == '/':
            self.is_search_view, self.search_query = True, ""
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


# --- Main Execution ---
if __name__ == '__main__':
    print(f"Initializing AlienNewsFeed...")
    print(f"Config and database stored in: {CONFIG_DIR}")
    init_db()
    threading.Thread(target=fetch_articles_threaded, daemon=True).start()
    menu = NewsFeedMenu()
    menu.show()
    os.system('cls' if os.name == 'nt' else 'clear')
    print("Exiting.")

