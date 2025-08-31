# 👽 Alien News Feed

A customizable, terminal-based news reader that aggregates articles from your favorite subreddits. Stay up-to-date with the latest news, tech, and politics without ever leaving your command line.

## Philosophy & Motivation

I designed this project to fulfill a need that I couldn't get anywhere else in a way I wanted. What's the point? Well one reason is I feel Reddit is uniquely curated in a way RSS feeds are not. Having a steady flow of articles from your favorite subreddits cataloged in a private database, searchable, bookmarkable and exportable was a really appealing idea. Hope you get some use out of what I'm trying to accomplish. Remember to be respectful of the Reddit API and don't poll too often.

\-- Old Lamps

## Features

* **Multi-Profile Management**: You can create, rename, delete, and switch between different user profiles. Each profile can have its own unique list of subreddits and a separate database, keeping your "Work" and "Hobby" news feeds completely separate.
* **Live Reddit Feed**: Fetches the latest articles from any combination of subreddits (e.g., `news+worldnews+technology`).
* **Clean Terminal UI**: A smooth, keyboard-driven interface for browsing articles with multiple themes.
* **Customizable Theming**: The application supports multiple color schemes (like Solarized, Nord, Dracula+) to change the look and feel of the interface.
* **Persistent Storage**: Uses an SQLite database to store articles, keeping track of read, new, and bookmarked items between sessions.
* **In-App Comment Viewer**: Read Reddit comment threads directly within the application in a collapsible tree view.
* **Content Curation**:
  * **Site Filtering**: Block unwanted sites on-the-fly from the action menu.
  * **Blocklist Management**: Manage a persistent list of excluded domains in the settings menu.
* **Filtering and Searching**
   * **Toggle a "bookmarks-only" view**
   * **Search your entire article history for keywords.**
* **Flexible Actions**:
  * Open articles or comment threads in your default web browser.
  * Instantly summarize any article with links to [Perplexity AI](https://www.perplexity.ai/ "null").
  * Archive a page on [archive.is](https://archive.is "null"), copy its URL, or block its domain.
* **Advanced Data Management**:
  * **Full Backups**: Export and import the entire article database.
  * **Bookmark Export**: Export all your bookmarked articles to a clean, styled, and portable HTML file.
  * **Command-Line Tools**: Perform headless backup and restore operations without launching the UI.
* **Article Management**:
  * Bookmark articles to read later and filter the view to show only bookmarks.
  * Live search with a two-stage process for both typing and navigating results.
* **User-Friendly**:
  * An in-app help screen provides a quick reference for all keybindings.
  * Cross-platform support for Windows, macOS, and Linux.

## Installation

You need Python 3.6+ to run this script.

1. **Clone the repository (or download the script):**
   ```
   git clone https://github.com/oldlamps/AlienNewsFeed.git
   cd AlienNewsFeed


   ```
2. **Install the required dependency:** The script requires the `requests` library to fetch data from Reddit's API.
   ```
   pip install requests
   pip install pid


   ```

## ⌨️ Usage

### Interactive Mode

Navigate to the project directory and run the script to launch the interface:

```
python alien.py


```

The first time you run it, a configuration file (`config.ini`) and database (`news_feed.db`) will be created automatically in your system's user config directory:

* **Linux/macOS**: `~/.config/AlienNewsFeed/`
* **Windows**: `%APPDATA%\AlienNewsFeed\`

#### Keybindings

Key(s)

Action

`↑` / `↓`

Move selection up/down one article.

`←` / `→`

Page up/down by 10 articles.

`Enter`

Open the Action Menu for the selected article.

`b`

Bookmark or un-bookmark the selected article.

`c`

View comments for the selected article.

`v`

Toggle the Bookmarks view.

`/`

Enter Search mode to filter articles.

`s`

Open the Settings menu.

`h`

Open the Help/About screen.

`ESC`

Go back, exit a menu, or quit the application.

#### Advanced Search

The search function uses a two-stage process:

1. Press `/` to start typing your query.
2. Press `Enter` to "commit" the search. This shifts focus to the filtered list, allowing you to use all navigation keys (`↑`/`↓`) and action keys (`b`, `c`, `Enter`) on the results.
3. Press `/` again to re-focus the search box to edit your query.

### Command-Line Mode

The application supports headless operations for easy scripting and backups.

* **Export a full backup:**
  ```
  python main.py --export


  ```
  This will save a timestamped backup of your database to the `backups` folder and exit.
* **Import from a backup:**
  ```
  python main.py --import /path/to/your/backup.db


  ```
  This will prompt you with a warning. If you confirm, it will overwrite your current database with the backup file and then launch the application.

## Configuration

You can customize the application by editing the `config.ini` file located in the configuration directory.

* `Theme`: Set your preferred color theme (e.g., `Dracula`).
* `FetchInterval`: Time in seconds between background fetches for new articles.
* `Subreddits`: A `+` separated string of subreddits to pull from.
* `ShowClock`: `true` or `false` to toggle the clock display.
* `BlockedDomains`: A comma-separated list of domains to exclude from the feed (e.g., `badnews.com,another-site.net`).

A restart is required for changes to `FetchInterval` and `Subreddits` to take effect.
