# 👽 Alien News Feed

A customizable, terminal-based news reader that aggregates articles from your favorite subreddits. Stay up-to-date with the latest news, tech, and politics without ever leaving your command line.

---

## Features

* **Live Reddit Feed**: Fetches the latest articles from any combination of subreddits (e.g., `news+worldnews+technology`).
* **Clean Terminal UI**: A smooth, keyboard-driven interface for browsing articles.
* **Persistent Storage**: Uses an SQLite database to store articles, keeping track of read, new, and bookmarked items between sessions.
* **In-App Comment Viewer**: Read Reddit comment threads directly within the application in a collapsible tree view.
* **Powerful Actions**:
    * Open articles or comment threads in your default web browser.
    * Instantly summarize any article with with links to [Perplexity AI](https://www.perplexity.ai/).
* **Customization & Settings**:
    * **Themes**: Choose from several built-in color schemes (Default, Light, Solarized Dark, Dracula, Paper).
    * **Subreddits**: Easily change the list of subreddits to fetch news from. 
* **Article Management**:
    * Bookmark articles to read later.
    * Filter by bookmarked articles.
    * Live search to filter articles by title or domain.
* **Cross-Platform**: Works on Windows, macOS, and Linux.

---

## Installation

You need Python 3.6+ to run this script.

1.  **Clone the repository (or download the script):**
    ```bash
    git clone https://github.com/oldlamps/AlienNewsFeed.git
    cd AlienNewsFeed
    ```

2.  **Install the required dependency:**
    The script requires the `requests` library to fetch data from Reddit's API.
    ```bash
    pip install requests
    ```

---

##  Usage

1.  **Run the script:**
    Navigate to the project directory and run:
    ```bash
    python main.py
    ```
    The first time you run it, a configuration file (`config.ini`) and database (`news_feed.db`) will be created automatically in your system's user config directory:
    * **Linux/macOS**: `~/.config/AlienNewsFeed/`
    * **Windows**: `%APPDATA%\AlienNewsFeed\`

2.  **Navigate the Interface:**
    Use your keyboard to navigate and interact with the news feed.

    | Key(s)         | Action                                       |
    |----------------|----------------------------------------------|
    | `↑` / `↓`      | Move selection up/down one article.          |
    | `←` / `→`      | Page up/down by 10 articles.                 |
    | `Enter`        | Open the Action Menu for the selected article. |
    | `b`            | Bookmark or un-bookmark the selected article.  |
    | `c`            | View comments for the selected article.      |
    | `v`            | Toggle the Bookmarks view.                   |
    | `/`            | Enter Search mode to filter articles.        |
    | `s`            | Open the Settings menu.                      |
    | `ESC`          | Go back, exit a menu, or quit the application. |

---

## ⚙️ Configuration

You can customize the application by editing the `config.ini` file located in the configuration directory mentioned above.

* `Theme`: Set your preferred color theme (e.g., `Dracula`).
* `FetchInterval`: Time in seconds between background fetches for new articles.
* `Subreddits`: A `+` separated string of subreddits to pull from.
* `ShowClock`: `true` or `false` to toggle the clock display.

A restart is required for changes to `FetchInterval` and `Subreddits` to take effect.
