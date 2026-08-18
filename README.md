# GitHub Follower Toolkit (`ghf-toolkit`)

`ghf-toolkit` is a command-line interface (CLI) and terminal user interface (TUI) tool to manage GitHub followers. It automates follow and unfollow tasks, monitors API rate limits, and displays account metrics in the terminal.

## Features

- **Follow Target Users:** Follow followers of a target account (skips users who already follow you, accounts you already follow, and previous session history).
- **Unfollow Non-Followers:** Unfollow accounts that do not follow you back.
- **Mass Unfollow:** Unfollow all accounts you currently follow.
- **Unfollow Specific Users:** Unfollow individual users by username.
- **Protect Accounts (Whitelist):** Prevent specific users from being unfollowed.
- **Simulate Operations (`--dry-run`):** Test follow or unfollow actions without making account changes.

---

## Installation & Setup

### Step 1: Install

**Option A: Using pipx (Recommended)**

```bash
pipx install git+https://github.com/ragibalasad/github-follower-toolkit.git
```

**Option B: Using pip (If pipx is not installed)**

```bash
pip install git+https://github.com/ragibalasad/github-follower-toolkit.git
```

### Step 2: Create a GitHub Token

Create a Personal Access Token (PAT) with follow permissions:

- **[Classic Token (Recommended)](https://github.com/settings/tokens/new):** Enter a note, select the `user:follow` scope checkbox, and click **Generate token**.
- **[Fine-Grained Token](https://github.com/settings/personal-access-tokens/new):** Under **Account permissions** > **Followers**, set **Access** to **Read and write**, and click **Generate token**.

### Step 3: Launch and Connect Token

Start the application:

```bash
ghf-toolkit
```

Paste your token when prompted. The application saves it securely to `~/.config/ghf-toolkit/config.json` (`0600` file permissions).

> **Alternative Token Methods:**
> - In the interactive shell: Run `set token <TOKEN>`
> - In configuration file: Save `{"github_token": "<TOKEN>"}` in `~/.config/ghf-toolkit/config.json` (Windows: `%APPDATA%/ghf-toolkit/config.json`)

---

## Usage

### 1. Interactive Mode

Run `ghf-toolkit` without arguments or with `-i`:

```bash
ghf-toolkit
```

#### Available Commands

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `run` | `[-v]` | Start the follow process for the current target. |
| `ufollow` | `[-n \| -a] [-l <N>] [-d <min> [max]] [-s] [-f] [-v]` | Start the unfollow process. |
| `ufollow` | `<username> [-s]` | Unfollow a specific user. |
| `wl add` | `<user1> [user2...]` | Add users to the protected whitelist. |
| `wl rm` | `<user1> [user2...]` | Remove users from the whitelist. |
| `wl list` | | Show all whitelisted users. |
| `wl clear` | | Remove all users from the whitelist. |
| `set target` | `<username>` | Set target GitHub user. |
| `set limit` | `<number>` | Set session follow/unfollow limit. |
| `set delay` | `<min> [max]` | Set delay range in seconds. |
| `set dry-run` | `<on \| off>` | Enable or disable simulation mode. |
| `set token` | `<token>` | Update the GitHub token. |
| `show` | | Refresh metrics and API quota. |
| `clear-state` | | Clear local follow history (`follow_state.json`). |
| `help` | | Show command reference manual. |
| `exit` | | Exit the application. |

#### `ufollow` Options

- `-n, --non-followers`: Target accounts that do not follow back (default).
- `-a, --all`: Target all followed accounts.
- `-l, --limit <N>`: Maximum number of accounts to unfollow.
- `-d, --delay <min> [max]`: Pacing delay range in seconds.
- `-s, --dry-run`: Simulate operations without sending API requests.
- `-f, --force`: Skip confirmation prompt for `--all` mode.
- `-v, --verbose`: Print detailed logs during execution.

---

### 2. Direct CLI Mode

Run operations directly without entering the interactive shell:

#### Auto-Follow

```bash
# Follow up to 50 users from target account
ghf-toolkit --target torvalds --max-follows 50

# Simulate follow operation with custom delay
ghf-toolkit --target torvalds --dry-run --delay-min 3.0 --delay-max 5.0 -v
```

#### Auto-Unfollow

```bash
# Unfollow up to 30 non-followers
ghf-toolkit ufollow -n -l 30

# Unfollow all accounts (skip confirmation prompt)
ghf-toolkit ufollow -a -f

# Unfollow a specific user in simulation mode
ghf-toolkit ufollow octocat --dry-run
```

#### Whitelist Management

```bash
# Add users to whitelist
ghf-toolkit wl add torvalds octocat

# List whitelisted users
ghf-toolkit wl list

# Remove user from whitelist
ghf-toolkit wl rm octocat

# Clear whitelist
ghf-toolkit wl clear
```

---

## Command Line Arguments Reference

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `-t, --target` | string | `None` | Target GitHub username. |
| `--token` | string | `None` | GitHub Personal Access Token. |
| `-m, --max-follows` | integer | `50` | Maximum accounts to follow in the session. |
| `--delay-min` | float | `2.0` | Minimum delay in seconds between mutations. |
| `--delay-max` | float | `4.0` | Maximum delay in seconds between mutations. |
| `--dry-run` | flag | `False` | Simulate actions without making API mutations. |
| `-i, --interactive` | flag | `False` | Start in interactive REPL mode. |
| `--state-file` | string | `None` | Path to custom follow state JSON file. |
| `--clear-state` | flag | `False` | Clear local follow state before execution. |
| `-v, --verbose` | flag | `False` | Enable verbose log output. |
| `-V, --version` | flag | | Show program version. |

---

## File Storage Locations

- **Configuration:** `~/.config/ghf-toolkit/config.json` (Windows: `%APPDATA%/ghf-toolkit/config.json`)
- **Follow State History:** `~/.config/ghf-toolkit/follow_state.json` (or `.follow_state.json` in current directory)
- **Whitelist:** `~/.config/ghf-toolkit/whitelist.json` (or `.whitelist.json` in current directory)
