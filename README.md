# GitHub Auto-Follow Script (Fastfetch TUI & High-Efficiency)

An ultra-efficient, rate-limit aware tool with **both One-Liner CLI and Interactive TUI REPL modes** featuring a **Fastfetch/Neofetch system banner** that updates in real-time. Follows target followers with your authenticated GitHub account — **strictly filtering out any users who already follow you or are already followed by you**.

---

## Visual Presentation (Fastfetch / Neofetch Terminal Banner)

Upon starting, the script displays your GitHub avatar in **24-bit TrueColor ANSI half-blocks (`▀`)** alongside your live GitHub metrics:

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

## Two Execution Modes

### 1. 🚀 Interactive REPL Mode (Recommended)
Simply run the script with no arguments:
```bash
python3 auto_follow.py
```
You will enter the interactive shell where you can configure options dynamically:
```text
github-follow ❯ set target octocat
[INFO] Looking up GitHub user '@octocat'...
[SUCCESS] Target set to @octocat (4,820 followers).

github-follow ❯ set limit 30
[SUCCESS] Session limit set to 30 follows.

github-follow ❯ set dry-run on
[SUCCESS] Dry-run mode: ENABLED (Simulation).

github-follow ❯ run
```

#### Interactive Real-Time Dashboard:
During `run`, the Fastfetch interface stays fixed on screen and updates stats **in real-time** with an **in-place single debug line and live progress bar**:
```text
  Live Progress: [████████████░░░░░░░░░░░░] 15/30 (50%)
  Examined: 42 | Skipped (Follows you): 8 | Skipped (Following): 12 | History: 7
  Status: [SUCCESS] Followed @devuser! (Pacing: sleeping 2.8s)
```

To see full streaming logs instead of single-line status, run with `-v`:
```text
github-follow ❯ run -v
```

#### Interactive Commands:
| Command | Example | Description |
| :--- | :--- | :--- |
| `set target <user>` | `set target torvalds` | Set the target user whose followers to extract |
| `set limit <num>` | `set limit 50` | Set maximum follows for this session |
| `set delay <min> <max>` | `set delay 2.5 5.0` | Set pacing delay range in seconds |
| `set dry-run <on\|off>` | `set dry-run on` | Toggle dry-run simulation mode |
| `set token <token>` | `set token ghp_xxx` | Switch or set GitHub PAT token |
| `show` / `status` | `show` | Refresh & redraw current profile and settings |
| `clear-state` | `clear-state` | Clear the local `.follow_state.json` history |
| `run` | `run` | Execute with real-time in-place dashboard |
| `run -v` | `run -v` | Execute with full streaming verbose output |
| `help` / `?` | `help` | Show command reference |
| `exit` / `quit` | `exit` | Exit the interactive session |

---

### 2. ⚡ One-Liner CLI Mode
You can still execute everything in a single shell command without entering interactive mode:

```bash
# Live run
python3 auto_follow.py --target octocat --max-follows 50

# Dry-run simulation
python3 auto_follow.py --target octocat --dry-run

# Custom delay & verbose logging
python3 auto_follow.py --target octocat --delay-min 3.0 --delay-max 6.0 -v
```

---

## Quick Setup

### 1. Prerequisites
- Python 3.8+
- GitHub Personal Access Token (PAT)

### 2. Clone & Install Dependencies
```bash
git clone https://github.com/ragibalasad/follow-active-users
cd follow-active-users

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set GitHub Personal Access Token
Create a classic PAT with `user:follow` scope (or fine-grained token with `Followers: Read & Write`):
```bash
cp .env.example .env
# Edit .env and set:
# GITHUB_TOKEN=ghp_your_actual_token_here
```

---

## Efficiency & Anti-Abuse Architecture

1. **$O(1)$ In-Memory Relationship Filtering**:
   - Pre-fetches your followers and following lists in bulk (`100/page`) into hash sets.
   - Drops total network calls by **>99%**.
2. **Primary Rate-Limit Monitoring (5,000 req/hr)**:
   - Tracks `X-RateLimit-Remaining` and auto-sleeps before exhaustion.
3. **Secondary Anti-Abuse Protection**:
   - Random jitter delays (default 2.0s–4.0s) between follow mutations (`PUT /user/following/:username`).
   - Listens to HTTP `403`/`429` with `Retry-After` headers and handles exponential backoff.
4. **Resumable State Caching (`.follow_state.json`)**:
   - Automatically saves and restores follow history so repeated runs never re-process old targets.

---

## Running Automated Tests
```bash
python3 -m unittest test_auto_follow.py
```
