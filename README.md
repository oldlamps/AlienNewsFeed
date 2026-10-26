# Alien News Feed

A customizable, terminal-based news reader that aggregates articles from your favorite subreddits. Stay up-to-date with the latest news without ever leaving your terminal.

<img width="90%" height="90%" alt="alien help" src="https://github.com/user-attachments/assets/b223e9d6-cc94-4b20-a71c-cbbb3e3296fc" />


## Features

* **Multi-Profile Management**: Create, rename, delete, and switch between different user profiles. Each profile can have its own unique list of subreddits and a separate database, keeping your "Work" and "Hobby" news feeds completely separate. <img width="90%" height="90%" alt="alien profile" src="https://github.com/user-attachments/assets/d8b302fd-f84e-45b9-874b-4af624ad6b3f" />

* **Live Reddit Feed**: Fetches the latest articles from any combination of subreddits (e.g., `news+worldnews+technology`) with automatic fallback to RSS feeds and modern desktop browser identification to prevent connection blocks.
* **Clean Terminal UI**: A smooth, keyboard-driven interface with multiple color schemes (Default, Dark, Solarized, Nord, Dracula).
* **Persistent Storage**: Uses an SQLite database to store articles, keeping track of read, new, and bookmarked items between sessions.
* **In-App Comment Viewer**: Read Reddit comment threads directly within the application in a collapsible tree view.
* **Content Curation & Filtering**:
  * **Site Filtering**: Block unwanted sites on-the-fly from the action menu.
  * **Blocklist Management**: Manage a persistent list of excluded domains in the settings menu.
  * **View Filter Menu**: Filter between all articles, bookmarked items, or unread articles.
  * **Mark All as Seen**: Clear unread indicators across the entire feed with a single keypress (`m`).
* **Search**: Live keyword search across your entire article history with a two-stage process for typing and navigating results.
* **Flexible Actions**:
  * Open articles or comment threads in your default web browser.
  * Launch video links directly in an external video player (such as mpv).
  * Archive pages on [archive.is](https://archive.is) or copy URLs to clipboard.
* **Data Management**:
  * **Full Backups**: Export and import complete SQLite database backups.
  * **Bookmark Export**: Export all your bookmarked articles to a styled HTML document.
  * **Command-Line Tools**: Perform headless backup and restore operations via CLI flags.
* **User-Friendly**: An in-app help screen provides a quick reference for all keybindings by pressing `h`.
* **Intelligence View (Article Reader & Optional Local AI)**:
  Press `a` or `→` on any article in the feed to open the Intelligence View:
  * **Full Article Reader**: Extracts and renders the clean, distraction-free text of the linked article directly in your terminal, stripping out ads and website clutter.
  * **Optional AI Integration**: The AI summary and analysis features in this view are completely optional. The article reader works standalone without any AI dependencies. If you optionally choose to run a local Ollama instance (`llama3`), you can generate streaming article summaries, extract key bulleted takeaways, run deep dives, and ask custom questions via an interactive chat pane.
  * **Optional AI Comment & Trend Analysis**: Also includes optional community sentiment summaries in comment threads (`a`) and headline trend analysis across your subreddits (`t`) when Ollama is running.

<img width="90%" height="90%" alt="alien comments" src="https://github.com/user-attachments/assets/2c5068e9-e563-4d79-a5dc-1725b91a8119" />


## Installation

### Pre-built Binary
Download the compiled release binary from the [Releases page](https://github.com/oldlamps/AlienNewsFeed/releases). No Python installation or dependencies required.

### Running from Source
Requires Python 3.10+.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/oldlamps/AlienNewsFeed.git
   cd AlienNewsFeed
   ```

2. **Install dependencies:**
   ```bash
   pip install requests pid beautifulsoup4 trafilatura
   ```

3. **(Optional) Local AI Setup:**
   If you wish to use the optional AI summaries in the Intelligence View, install [Ollama](https://ollama.com/) and pull a model (the app connects to `http://localhost:11434` with `llama3:latest` by default):
   ```bash
   ollama run llama3
   ```
   *Note: This step is entirely optional. Alien News Feed and the Intelligence View article reader work fully without Ollama.*


## Usage

### Interactive Mode

Run the application:

```bash
python alien.py
# or using the binary:
./AlienNewsFeed
```

Configuration files (`config.ini`) and SQLite databases (`news_feed.db`) are saved automatically:

* **Linux/macOS**: `~/.config/AlienNewsFeed/`
* **Windows**: `%APPDATA%\AlienNewsFeed\`


### Keybindings

#### Main Feed

| Key | Action |
| :--- | :--- |
| `↑` / `↓` | Move selection up / down one article |
| `←` / `PgUp` | Jump up by one page (10 articles) |
| `PgDn` | Jump down by one page (10 articles) |
| `Home` / `End` | Jump to top / bottom of feed |
| `a` or `→` | Open Intelligence View (Article reader + optional AI) |
| `Enter` | Open the Action Menu for the selected article |
| `b` | Toggle bookmark on selected article |
| `m` | Mark all new articles as seen |
| `c` | View Reddit comments in-app |
| `Del` | Delete article permanently and add domain/URL to blocked list |
| `/` | Enter search mode (press `Enter` to navigate results) |
| `v` | Open View Filter menu (All Articles, Bookmarks, Unread) |
| `p` | Open Profile Manager |
| `s` | Open Settings menu |
| `h` | Open Help / About screen |
| `t` | Open Trend Analysis view (optional AI) |
| `ESC` | Back, clear search, or show quit confirmation |

#### Intelligence View (`a` or `→`)

| Key | Action |
| :--- | :--- |
| `Tab` | Switch active focus between the **Article Reader** and the **AI Pane** |
| `←` / `→` | Switch AI tabs (**Summary** / **Deep Dive** / **Chat**) |
| `s` | Run concise article summary (optional AI) |
| `k` | Extract key points as a bulleted list (optional AI) |
| `Enter` | Trigger analysis (on Deep Dive tab) or Send question (on Chat tab) |
| `↑` / `↓` | Scroll the active pane line by line |
| `PgUp` / `PgDn` | Scroll the active pane by page |
| `Home` / `End` | Jump to beginning or end of pane content |
| `ESC` | Exit Intelligence View back to the main feed |

#### In-App Comment Viewer (`c`)

| Key | Action |
| :--- | :--- |
| `↑` / `↓` | Navigate comment list |
| `Enter` | Collapse or expand comment thread |
| `l` | Show popup list of external links in the selected comment |
| `a` | Run comment sentiment/discussion analysis (optional AI) |
| `ESC` | Exit back to main feed |

#### Advanced Search

1. Press `/` to begin typing a search query.
2. Press `Enter` to commit the search. Focus shifts to the filtered results, allowing full navigation (`↑`/`↓`) and actions (`b`, `c`, `Enter`).
3. Press `/` again to re-focus the search bar and update the query, or press `ESC` to clear.


### Command-Line Mode

Headless operations for scripts and scheduled cron jobs:

* **Export full database backup:**
  ```bash
  python alien.py --export
  ```
  Saves a timestamped `.db` backup to the `backups` directory and exits.

* **Import database from backup:**
  ```bash
  python alien.py --import /path/to/backup.db [--profile ProfileName]
  ```


## Configuration

Edit `config.ini` in your configuration directory to customize settings:

* `Theme`: Color scheme (`Default`, `Dark`, `Solarized`, `Nord`, `Dracula`).
* `FetchInterval`: Background refresh interval in seconds (default `300`).
* `Subreddits`: Plus-separated list of subreddits to pull from (e.g. `news+worldnews+politics+technology`).
* `ShowClock`: Set `true` or `false` to toggle clock display.
* `BlockedDomains`: Comma-separated list of domains to exclude from feeds.
* `VideoPlayerPath`: Optional executable path for playing video links (e.g. `/usr/bin/mpv`).
* `OllamaModel`: Local model name for optional AI analysis (e.g. `llama3:latest`, `mistral`).


### Clipboard Support

The "Copy URL to Clipboard" action utilizes system utilities:

* **macOS** (`pbcopy`) and **Windows** (`clip.exe`): Included with the operating system.
* **Linux** (`xclip`): Install via your distribution package manager if not present:
  * Debian / Ubuntu / Mint: `sudo apt-get install xclip`
  * Fedora / RHEL: `sudo dnf install xclip`
  * Arch Linux: `sudo pacman -S xclip`


## Building from Source

To build a standalone executable:

```bash
pip install pyinstaller
pyinstaller AlienNewsFeed.spec --clean
```

The compiled binary will be placed in `dist/AlienNewsFeed`.
