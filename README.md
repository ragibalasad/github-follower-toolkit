# GitHub Auto-Follow Script (Fastfetch Style & Rate-Limit Safe)

An ultra-efficient, rate-limit aware CLI tool with a **Fastfetch/Neofetch style system banner** to extract followers of a target GitHub user and follow them one-by-one with your authenticated GitHub account — **strictly filtering out any users who already follow you or are already followed by you**.

---

## Visual Presentation (Fastfetch / Neofetch Terminal Banner)

Upon starting, the script displays your GitHub avatar in **24-bit TrueColor ANSI half-blocks (`▀`)** alongside your live GitHub account metrics:

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

## Key Features & Efficiency Architecture

1. **Fastfetch / Neofetch Profile Banner**:
   - Converts your GitHub avatar thumbnail into high-fidelity ANSI half-block pixels.
   - Renders live API quota, network counts, safety bounds, and terminal color swatches.
   - Built-in ANSI Octocat fallback if offline or avatar is unavailable.

2. **$O(1)$ In-Memory Relationship Filtering**:
   - Pre-fetches your followers and following lists in bulk (`per_page=100`) into in-memory hash sets.
   - Avoids burning 1 API request per candidate, reducing API calls by **>99%**.

3. **Primary Rate-Limit Protection (5,000 req/hr)**:
   - Tracks `X-RateLimit-Remaining` and `X-RateLimit-Reset` headers on every response.
   - Automatically pauses if remaining quota drops below safety thresholds.

4. **Secondary Abuse Rate-Limit Mitigation**:
   - Implements **randomized jitter delays** (default 2.0s - 4.0s) between follow calls.
   - Listens to HTTP `403` / `429` with `Retry-After` headers and applies exponential backoff.

5. **Resumable State Caching (`.follow_state.json`)**:
   - Tracks followed accounts so restarting or running multiple sessions won't re-follow previously processed users.

6. **Dry-Run Mode (`--dry-run`)**:
   - Preview candidate filtering and potential follows without executing real `PUT` follow requests.

---

## Quick Setup

### 1. Prerequisites
- Python 3.8+
- GitHub Personal Access Token (PAT)

### 2. Clone & Install Dependencies
```bash
git clone https://github.com/ragibalasad/follow-active-users
cd follow-active-users

# Create virtual environment (optional but recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Generate a GitHub Personal Access Token (PAT)
1. Go to [GitHub Settings -> Developer Settings -> Personal Access Tokens](https://github.com/settings/tokens).
2. Choose **Tokens (classic)**:
   - Name: `Auto Follow Script`
   - Scope: Check **`user:follow`** (Follow or unfollow other users).
3. Copy the generated token (`ghp_...`).

*(Alternatively, for **Fine-grained Personal Access Tokens**, grant **Read and Write** permissions to **Followers**).*

### 4. Configure Environment
Copy the `.env.example` template:
```bash
cp .env.example .env
```
Edit `.env` and insert your token:
```env
GITHUB_TOKEN=ghp_your_actual_token_here
```

---

## Usage Examples

### 1. Test in Dry-Run Mode (Simulation)
Preview candidate filtering and stats without sending follow requests:
```bash
python3 auto_follow.py --target octocat --dry-run
```

### 2. Run with Session Follow Limit
Follow up to 25 filtered candidates from user `octocat`:
```bash
python3 auto_follow.py --target octocat --max-follows 25
```

### 3. Customize Delay & Pacing
Adjust the random delay range (in seconds) between follows:
```bash
python3 auto_follow.py --target octocat --max-follows 50 --delay-min 3.0 --delay-max 6.0
```

### 4. Enable Verbose Debug Logging
See detailed reasons for each skipped user in real-time:
```bash
python3 auto_follow.py --target octocat --verbose
```

### 5. Clear Saved History State
Reset the local `.follow_state.json` cache:
```bash
python3 auto_follow.py --clear-state
```

---

## CLI Options Reference

| Option | Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| `--target` | `-t` | `None` / `.env` | Target GitHub username whose followers will be extracted. |
| `--token` | | `None` / `.env` | GitHub Personal Access Token. |
| `--max-follows` | `-m` | `50` | Maximum number of users to follow in this session. |
| `--delay-min` | | `2.0` | Minimum delay in seconds between follow requests. |
| `--delay-max` | | `4.0` | Maximum delay in seconds between follow requests. |
| `--dry-run` | | `False` | Simulates filtering and actions without sending `PUT` follow requests. |
| `--state-file` | | `.follow_state.json` | Path to the local JSON state cache. |
| `--clear-state`| | `False` | Clears existing state history before running. |
| `--verbose` | `-v` | `False` | Displays verbose logs for every skipped candidate. |

---

## Account Safety Guidelines

> [!IMPORTANT]
> - **Follow Volume**: We recommend keeping follows under 100-200 accounts per day. Rapid spikes in follows can trigger GitHub automated spam filters.
> - **Pacing**: Do not set `--delay-min` below 1.5s. Natural random jitter between 2.0s and 4.0s is safe and prevents secondary rate limits.
> - **Token Security**: Never commit your `.env` file or GitHub Personal Access Token to a public git repository. The `.gitignore` file is preconfigured to protect `.env`.

---

## Running Automated Tests
To run the built-in unit tests and verify the pipeline logic:
```bash
python3 -m unittest test_auto_follow.py
```
