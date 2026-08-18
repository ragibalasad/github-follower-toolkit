# GitHub Follower Toolkit (Fastfetch TUI & High-Efficiency)

An ultra-efficient, rate-limit aware automation toolkit with both One-Liner CLI and Interactive TUI REPL modes featuring a Fastfetch/Neofetch system banner that updates in real-time.

Manage your entire GitHub network lifecycle:
* **Auto-Follow:** Follow target accounts' followers in bulk — strictly filtering out users who already follow you or are already followed.
* **Unfollow (`ufollow`):** Prune non-followers (who don't follow back) or mass unfollow with safety confirmation.
* **Whitelist (`wl`):** Protect VIP accounts, friends, mentors, and organizations from ever being unfollowed.

---

## Visual Presentation (Fastfetch / Neofetch Terminal Banner)

Upon starting, the script displays your GitHub avatar in 24-bit TrueColor ANSI half-blocks (`▀`) alongside your live GitHub metrics:

```text
       ▄▄▄▄▄▄▄▄▄▄▄▄       ragibalasad@github
    ▄████████████████▄    ──────────────────────────────────────
   ████████████████████   User:       Ragib Al Asad (@ragibalasad)
  ████▀▀            ▀▀██  Bio:        Full-Stack Engineer & Builder
  ████  ███      ███  ██  Account:    Joined 2021 | 48 Repos
  ████  ▀▀▀      ▀▀▀  ██  Network:    142 Followers | 95 Following
  ████     ▄▄▄▄▄▄     ██  Target:     @octocat (4,820 followers)
   ████▄   ▀▀▀▀▀▀   ▄██   API Quota:  4,985 / 5,000 (99%)
    ▀████████████████▀    Safety:     Pacing 2.0s–4.0s | Limit: 50
       ▀▀▀▀▀▀▀▀▀▀▀▀       Mode:       ACTIVE (Live Follow)
                          
                           ● ● ● ● ● ● ● ●
```

---

## Installation & Quick Start

### Option A: Install Directly as a Global CLI (No Git Clone Required)
Install globally in an isolated environment using [pipx](https://pypa.github.io/pipx/) or [uv](https://github.com/astral-sh/uv):

```bash
# Using pipx (recommended)
pipx install git+https://github.com/ragibalasad/github-follower-toolkit.git

# Or run instantly without installing via uvx
uvx --from git+https://github.com/ragibalasad/github-follower-toolkit.git ghf-toolkit
```

Once installed, simply run anywhere in your terminal:
```bash
ghf-toolkit
```

---

### Option B: Local Repository Setup (For Developers)

```bash
git clone https://github.com/ragibalasad/github-follower-toolkit
cd github-follower-toolkit

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### GitHub Token Configuration
Set your token in `.env` or pass it via the CLI:
```bash
cp .env.example .env
# Edit .env and set:
# GITHUB_TOKEN=ghp_your_actual_token_here
```
> **Token Scope:** Classic PAT with `user:follow` scope, or Fine-Grained PAT with `Followers: Read & Write`.

---

## Two Execution Modes

### 1. Interactive REPL Mode (Recommended)
Simply launch `ghf-toolkit` (or `python3 ghf_toolkit.py`):
```bash
ghf-toolkit
```
You will enter the interactive shell with state-aware prompt badges:
```text
ghf-toolkit [live] ❯ set target octocat
[INFO] Looking up GitHub user '@octocat'...
[SUCCESS] Target set to @octocat (4,820 followers).

ghf-toolkit [live] ❯ run
```

#### Synopsis & Command Reference:

```text
SYNOPSIS:
  run [-v]
  ufollow [-n | -a] [-l <N>] [-d <min> [max]] [-s] [-f] [-v]
  ufollow <username> [-s]
  wl <add | rm> <username...>
  wl <list | clear>
  set <target | limit | delay | dry-run | token> <value>
  show | clear-state | help | exit
```

**Commands:**
| Command | Arguments | Description |
| :--- | :--- | :--- |
| `run` | `[-v]` | Execute auto-follow pipeline for target account (`-v`: verbose) |
| `ufollow` | `[-n \| -a] [-l <N>] [-s] [-f] [-v]` | Execute unfollow pipeline with rate-limit protection |
| `ufollow` | `<username> [-s]` | Unfollow a specific user |
| `wl add` | `<user1> [user2...]` | Add username(s) to protected whitelist |
| `wl rm` | `<user1> [user2...]` | Remove username(s) from protected whitelist |
| `wl list` | | List all protected accounts |
| `wl clear` | | Clear the entire whitelist |
| `set` | `<key> <value>` | Update session settings (`target`, `limit`, `delay`, `dry-run`, `token`) |
| `show` | | Refresh profile metrics, API quota, and dashboard |
| `clear-state` | | Purge local follow history cache (`.follow_state.json`) |
| `help` | | Display manual and synopsis |
| `exit` | | Terminate interactive session |

**`ufollow` Options:**
| Option | Description |
| :--- | :--- |
| `-n, --non-followers` | Target accounts that do not follow back (default) |
| `-a, --all` | Target all accounts currently followed |
| `-l, --limit <N>` | Maximum number of accounts to process in session |
| `-d, --delay <min> [max]` | Random jitter pacing delay in seconds (default: 2.0 4.0) |
| `-s, --dry-run` | Simulate execution without modifying following list |
| `-f, --force` | Bypass confirmation prompt on destructive actions (`-a`) |
| `-v, --verbose` | Stream detailed execution logs in real-time |

---

### 2. One-Liner CLI Mode
Execute commands directly from bash, scripts, or cron jobs:

```bash
# Follow Pipeline
# Live run
ghf-toolkit --target octocat --max-follows 50

# Dry-run simulation with custom delay & verbose logging
ghf-toolkit --target octocat --dry-run --delay-min 3.0 --delay-max 6.0 -v

# Unfollow Pipeline
# Unfollow up to 30 non-followers in dry-run mode
ghf-toolkit ufollow -n -l 30 --dry-run

# Live unfollow non-followers
ghf-toolkit ufollow -n -l 50

# Mass unfollow with confirmation skip
ghf-toolkit ufollow -a -f

# Whitelist
# Manage protected VIP whitelist
ghf-toolkit wl add torvalds octocat
ghf-toolkit wl list
```

---

## Efficiency & Anti-Abuse Architecture

1. **O(1) In-Memory Relationship Filtering**:
   - Pre-fetches your followers and following lists in bulk (`100/page`) into hash sets.
   - Drops total network calls by >99%.
2. **Primary Rate-Limit Monitoring (5,000 req/hr)**:
   - Tracks `X-RateLimit-Remaining` and auto-sleeps before exhaustion.
3. **Secondary Anti-Abuse Protection**:
   - Random jitter delays (default 2.0s–4.0s) between follow/unfollow mutations.
   - Listens to HTTP `403`/`429` with `Retry-After` headers and handles exponential backoff.
4. **Resumable State Caching & Whitelisting**:
   - `.follow_state.json`: Automatically records and avoids re-processing previously followed users.
   - `.whitelist.json`: Protects VIP accounts from accidental unfollows.

---

## Running Automated Tests
```bash
python3 -m unittest test_ghf_toolkit.py
```
