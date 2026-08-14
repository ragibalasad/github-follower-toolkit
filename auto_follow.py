#!/usr/bin/env python3
"""
GitHub Auto-Follow Script
-------------------------
Efficiently fetches followers of a target GitHub user and follows them
only if they do not already follow the authenticated user and are not already followed.

Built with optimal bulk pre-fetching ($O(1)$ set lookups), rate limit protection,
secondary abuse rate limit backoff, state caching, and configurable pacing.
"""

import argparse
import datetime
import json
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

# Terminal color codes for rich CLI presentation
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

def log_info(msg: str) -> None:
    print(f"{Colors.BLUE}[INFO]{Colors.RESET} {msg}")

def log_success(msg: str) -> None:
    print(f"{Colors.GREEN}[SUCCESS]{Colors.RESET} {msg}")

def log_warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")

def log_error(msg: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")

def log_dim(msg: str) -> None:
    print(f"{Colors.DIM}       {msg}{Colors.RESET}")


class StateManager:
    """Manages persistent state across runs to avoid redundant actions."""

    def __init__(self, state_file: str = ".follow_state.json") -> None:
        self.state_path = Path(state_file)
        self.state: Dict[str, Any] = {
            "followed_users": {},
            "history_skipped": {},
            "last_run": None,
            "total_followed_all_time": 0
        }
        self.load()

    def load(self) -> None:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state.update(data)
            except Exception as e:
                log_warn(f"Could not load state from {self.state_path}: {e}. Initializing fresh state.")

    def save(self) -> None:
        try:
            self.state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log_error(f"Failed to save state to {self.state_path}: {e}")

    def is_previously_followed(self, username: str) -> bool:
        return username.lower() in (u.lower() for u in self.state.get("followed_users", {}))

    def record_follow(self, username: str) -> None:
        self.state.setdefault("followed_users", {})[username] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.state["total_followed_all_time"] = len(self.state["followed_users"])
        self.save()

    def clear(self) -> None:
        self.state = {
            "followed_users": {},
            "history_skipped": {},
            "last_run": None,
            "total_followed_all_time": 0
        }
        self.save()


class GitHubAPIClient:
    """Robust GitHub API client with rate-limit monitoring, pagination, and backoff."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, verbose: bool = False) -> None:
        self.token = token.strip()
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-efficient-auto-follow-tool/1.0"
        })
        self.rate_limit_limit = 5000
        self.rate_limit_remaining = 5000
        self.rate_limit_reset = 0

    def _update_rate_limit(self, response: requests.Response) -> None:
        headers = response.headers
        if "X-RateLimit-Limit" in headers:
            self.rate_limit_limit = int(headers["X-RateLimit-Limit"])
        if "X-RateLimit-Remaining" in headers:
            self.rate_limit_remaining = int(headers["X-RateLimit-Remaining"])
        if "X-RateLimit-Reset" in headers:
            self.rate_limit_reset = int(headers["X-RateLimit-Reset"])

        if self.verbose:
            log_dim(f"Rate Limit: {self.rate_limit_remaining}/{self.rate_limit_limit}")

    def _handle_rate_limit_pause_if_needed(self) -> None:
        """Pause if primary rate limit quota is dangerously low."""
        if self.rate_limit_remaining <= 10:
            now = int(time.time())
            sleep_duration = max(5, self.rate_limit_reset - now + 2)
            reset_dt = datetime.datetime.fromtimestamp(self.rate_limit_reset, tz=datetime.timezone.utc)
            log_warn(f"Primary rate limit nearly exhausted ({self.rate_limit_remaining} left).")
            log_warn(f"Sleeping for {sleep_duration}s until reset at {reset_dt.strftime('%H:%M:%S UTC')}...")
            time.sleep(sleep_duration)

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> requests.Response:
        url = endpoint if endpoint.startswith("http") else f"{self.BASE_URL}{endpoint}"
        retries = 0
        backoff_delay = 10.0

        while retries < max_retries:
            self._handle_rate_limit_pause_if_needed()

            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    timeout=30
                )
                self._update_rate_limit(response)

                # Check for secondary rate limit (HTTP 403 or 429)
                if response.status_code in (403, 429):
                    resp_json = {}
                    try:
                        resp_json = response.json()
                    except Exception:
                        pass
                    
                    message = resp_json.get("message", "").lower()
                    is_secondary = "secondary rate limit" in message or "abuse detection" in message or "retry-after" in response.headers

                    if is_secondary or response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            wait_seconds = int(retry_after) + 2
                        else:
                            wait_seconds = backoff_delay + random.uniform(1.0, 5.0)
                            backoff_delay *= 2.0

                        log_warn(f"GitHub Secondary Rate Limit triggered. Waiting {wait_seconds:.1f}s before retry...")
                        time.sleep(wait_seconds)
                        retries += 1
                        continue

                    # If bad credentials or permission forbidden
                    if "bad credentials" in message:
                        log_error("Invalid GitHub Token. Please check your GITHUB_TOKEN permissions.")
                        sys.exit(1)

                # 5xx Server errors - retry with backoff
                if 500 <= response.status_code < 600:
                    log_warn(f"GitHub server error ({response.status_code}). Retrying in {backoff_delay:.1f}s...")
                    time.sleep(backoff_delay)
                    backoff_delay *= 1.5
                    retries += 1
                    continue

                return response

            except (requests.ConnectionError, requests.Timeout) as e:
                log_warn(f"Network error ({e}). Retrying in {backoff_delay:.1f}s...")
                time.sleep(backoff_delay)
                backoff_delay *= 1.5
                retries += 1

        log_error(f"Failed request to {url} after {max_retries} attempts.")
        raise RuntimeError(f"Failed to execute API request to {url}")

    def get_authenticated_user(self) -> Dict[str, Any]:
        """Fetch details of the authenticated user to verify token & get base stats."""
        resp = self.request("GET", "/user")
        if resp.status_code != 200:
            log_error(f"Failed to authenticate user: HTTP {resp.status_code} - {resp.text}")
            sys.exit(1)
        return resp.json()

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch public profile details for a given username."""
        resp = self.request("GET", f"/users/{username}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            log_error(f"Failed to fetch user '{username}': HTTP {resp.status_code}")
            return None
        return resp.json()

    def fetch_all_followers_set(self, username: Optional[str] = None) -> Set[str]:
        """
        Fetch all followers for a user using max page size (100) into a set of lowercase usernames.
        If username is None, fetches for authenticated user (`/user/followers`).
        """
        endpoint = "/user/followers" if username is None else f"/users/{username}/followers"
        followers: Set[str] = set()
        page = 1

        while True:
            resp = self.request("GET", endpoint, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                log_error(f"Failed fetching followers page {page}: HTTP {resp.status_code}")
                break

            items = resp.json()
            if not items:
                break

            for user in items:
                followers.add(user["login"].lower())

            if len(items) < 100:
                break

            page += 1

        return followers

    def fetch_all_following_set(self) -> Set[str]:
        """Fetch all accounts the authenticated user currently follows into a set."""
        following: Set[str] = set()
        page = 1

        while True:
            resp = self.request("GET", "/user/following", params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                log_error(f"Failed fetching following page {page}: HTTP {resp.status_code}")
                break

            items = resp.json()
            if not items:
                break

            for user in items:
                following.add(user["login"].lower())

            if len(items) < 100:
                break

            page += 1

        return following

    def stream_target_followers(self, username: str) -> Generator[Dict[str, Any], None, None]:
        """
        Stream target user's followers page by page (100 per request) lazily.
        """
        endpoint = f"/users/{username}/followers"
        page = 1

        while True:
            resp = self.request("GET", endpoint, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                log_error(f"Failed streaming followers for '{username}' (page {page}): HTTP {resp.status_code}")
                break

            items = resp.json()
            if not items:
                break

            for user in items:
                yield user

            if len(items) < 100:
                break

            page += 1

    def follow_user(self, username: str) -> bool:
        """
        Follow a target user via PUT /user/following/{username}.
        Returns True if followed successfully (HTTP 204), False otherwise.
        """
        endpoint = f"/user/following/{username}"
        resp = self.request("PUT", endpoint)
        if resp.status_code in (204, 200):
            return True
        else:
            log_error(f"Failed to follow '{username}': HTTP {resp.status_code} - {resp.text}")
            return False


class AutoFollowRunner:
    """Coordinates filtering, rate-limit pacing, and execution."""

    def __init__(
        self,
        client: GitHubAPIClient,
        state_mgr: StateManager,
        target_username: str,
        max_follows: int = 50,
        delay_min: float = 2.0,
        delay_max: float = 4.0,
        dry_run: bool = False
    ) -> None:
        self.client = client
        self.state_mgr = state_mgr
        self.target_username = target_username
        self.max_follows = max_follows
        self.delay_min = max(0.5, delay_min)
        self.delay_max = max(self.delay_min, delay_max)
        self.dry_run = dry_run
        self.interrupted = False

        # Register graceful exit handlers
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

        # Statistics
        self.stats = {
            "total_examined": 0,
            "skipped_already_follows_me": 0,
            "skipped_already_following": 0,
            "skipped_state_history": 0,
            "followed_success": 0,
            "followed_failed": 0
        }

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        print("\n")
        log_warn("Interrupt received! Safely finishing current task and saving state...")
        self.interrupted = True

    def run(self) -> None:
        print(f"\n{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}              GitHub Auto-Follow Pipeline (High-Efficiency)            {Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}\n")

        # 1. Authenticated User Check
        auth_user = self.client.get_authenticated_user()
        auth_login = auth_user["login"]
        log_info(f"Authenticated as: {Colors.BOLD}@{auth_login}{Colors.RESET} ({auth_user.get('name', 'N/A')})")
        log_dim(f"Followers: {auth_user.get('followers', 0)} | Following: {auth_user.get('following', 0)}")
        log_dim(f"API Rate Limit: {self.client.rate_limit_remaining}/{self.client.rate_limit_limit} remaining")

        # 2. Target User Validation
        target_info = self.client.get_user_info(self.target_username)
        if not target_info:
            log_error(f"Target user '@{self.target_username}' not found or inaccessible.")
            return

        target_login = target_info["login"]
        target_follower_count = target_info.get("followers", 0)
        log_info(f"Target User: {Colors.BOLD}@{target_login}{Colors.RESET} ({target_follower_count} followers)")

        if target_login.lower() == auth_login.lower():
            log_error("Target user cannot be yourself!")
            return

        if target_follower_count == 0:
            log_warn(f"Target user '@{target_login}' has 0 followers. Nothing to do.")
            return

        if self.dry_run:
            print(f"\n{Colors.MAGENTA}{Colors.BOLD}⚠️  DRY-RUN MODE ENABLED — No follow requests will be sent.{Colors.RESET}\n")

        # 3. Pre-fetch My Followers & Following in Bulk for O(1) in-memory checks
        print(f"\n{Colors.BOLD}── Step 1: Pre-fetching Relationship Cache ───────────────────────────{Colors.RESET}")
        log_info(f"Fetching your followers (who follow @{auth_login})...")
        my_followers = self.client.fetch_all_followers_set()
        log_success(f"Cached {len(my_followers)} follower(s) who follow you.")

        log_info(f"Fetching your following list (who you already follow)...")
        my_following = self.client.fetch_all_following_set()
        log_success(f"Cached {len(my_following)} account(s) you already follow.")

        # 4. Stream Target's Followers & Filter Candidates
        print(f"\n{Colors.BOLD}── Step 2: Processing Followers & Filtering Candidates ───────────────{Colors.RESET}")
        log_info(f"Starting pipeline (Session limit: {self.max_follows} follows, Delay: {self.delay_min:.1f}s-{self.delay_max:.1f}s)...")

        for candidate in self.client.stream_target_followers(target_login):
            if self.interrupted:
                break

            if self.stats["followed_success"] >= self.max_follows:
                log_info(f"Reached target follow limit of {self.max_follows} for this session.")
                break

            self.stats["total_examined"] += 1
            candidate_login = candidate["login"]
            candidate_lower = candidate_login.lower()

            # Rule 1: Do not follow yourself
            if candidate_lower == auth_login.lower():
                continue

            # Rule 2: Do not follow if they already follow you
            if candidate_lower in my_followers:
                self.stats["skipped_already_follows_me"] += 1
                if self.client.verbose:
                    log_dim(f"Skipped @{candidate_login} (Already follows you)")
                continue

            # Rule 3: Do not follow if you already follow them
            if candidate_lower in my_following:
                self.stats["skipped_already_following"] += 1
                if self.client.verbose:
                    log_dim(f"Skipped @{candidate_login} (You already follow them)")
                continue

            # Rule 4: Do not follow if previously followed in state history
            if self.state_mgr.is_previously_followed(candidate_login):
                self.stats["skipped_state_history"] += 1
                if self.client.verbose:
                    log_dim(f"Skipped @{candidate_login} (Recorded in local history)")
                continue

            # Candidate meets all criteria!
            current_num = self.stats["followed_success"] + 1
            user_url = candidate.get("html_url", f"https://github.com/{candidate_login}")

            if self.dry_run:
                log_info(f"{Colors.MAGENTA}[DRY RUN]{Colors.RESET} [{current_num}/{self.max_follows}] Would follow {Colors.BOLD}@{candidate_login}{Colors.RESET} ({user_url})")
                self.stats["followed_success"] += 1
                # Small simulation delay
                time.sleep(0.1)
                continue

            # Real follow execution
            log_info(f"[{current_num}/{self.max_follows}] Following {Colors.BOLD}@{candidate_login}{Colors.RESET} ({user_url})...")
            success = self.client.follow_user(candidate_login)

            if success:
                self.stats["followed_success"] += 1
                self.state_mgr.record_follow(candidate_login)
                my_following.add(candidate_lower)
                log_success(f"Successfully followed @{candidate_login}!")

                # Pacing delay with random jitter to prevent secondary rate limits
                if self.stats["followed_success"] < self.max_follows:
                    sleep_time = random.uniform(self.delay_min, self.delay_max)
                    log_dim(f"Pacing delay: sleeping {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
            else:
                self.stats["followed_failed"] += 1

        # 5. Print Execution Summary
        self._print_summary()

    def _print_summary(self) -> None:
        print(f"\n{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}                         Execution Summary                            {Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}")
        print(f" Total Candidates Examined:        {self.stats['total_examined']}")
        print(f" {Colors.GREEN}Successfully Followed:{Colors.RESET}           {self.stats['followed_success']}")
        if self.stats["followed_failed"] > 0:
            print(f" {Colors.RED}Failed Follows:{Colors.RESET}                  {self.stats['followed_failed']}")
        print(f" Skipped (Already follow you):     {self.stats['skipped_already_follows_me']}")
        print(f" Skipped (Already following):      {self.stats['skipped_already_following']}")
        print(f" Skipped (State history):          {self.stats['skipped_state_history']}")
        print(f" Total Followed (All-Time Record): {self.state_mgr.state.get('total_followed_all_time', 0)}")
        print(f" Remaining API Quota:              {self.client.rate_limit_remaining}/{self.client.rate_limit_limit}")
        print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}\n")


def parse_args() -> argparse.Namespace:
    # Load .env file if present
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="High-efficiency, rate-limit aware GitHub Auto-Follow Script."
    )
    parser.add_argument(
        "-t", "--target",
        type=str,
        default=os.getenv("GITHUB_TARGET_USER"),
        help="Target GitHub username whose followers will be extracted (or set GITHUB_TARGET_USER in .env)."
    )
    parser.add_argument(
        "--token",
        type=str,
        default=os.getenv("GITHUB_TOKEN"),
        help="GitHub Personal Access Token (or set GITHUB_TOKEN in .env)."
    )
    parser.add_argument(
        "-m", "--max-follows",
        type=int,
        default=int(os.getenv("MAX_FOLLOWS", "50")),
        help="Maximum number of users to follow in this session (default: 50)."
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=float(os.getenv("DELAY_MIN", "2.0")),
        help="Minimum delay in seconds between follow requests (default: 2.0)."
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=float(os.getenv("DELAY_MAX", "4.0")),
        help="Maximum delay in seconds between follow requests (default: 4.0)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without making actual follow mutations (simulates filtering and actions)."
    )
    parser.add_argument(
        "--state-file",
        type=str,
        default=".follow_state.json",
        help="Path to JSON state history file (default: .follow_state.json)."
    )
    parser.add_argument(
        "--clear-state",
        action="store_true",
        help="Clear the existing follow history state before running."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logs for skipped users and rate limits."
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.token:
        log_error("GitHub Token is required! Provide it via --token or GITHUB_TOKEN in .env")
        print(f"\n{Colors.BOLD}How to create a GitHub token:{Colors.RESET}")
        print(" 1. Go to https://github.com/settings/tokens")
        print(" 2. Generate a Classic Token with scope 'user:follow', or a Fine-grained token with Follow (Read & Write)")
        print(" 3. Put it in .env file as: GITHUB_TOKEN=ghp_xxx")
        print(" 4. Or pass it directly: python auto_follow.py --target <user> --token <token>\n")
        sys.exit(1)

    if not args.target:
        log_error("Target GitHub username is required! Provide it via --target or GITHUB_TARGET_USER in .env")
        parser = argparse.ArgumentParser()
        sys.exit(1)

    state_mgr = StateManager(state_file=args.state_file)
    if args.clear_state:
        log_warn("Clearing existing state history...")
        state_mgr.clear()
        log_success("State cleared.")

    client = GitHubAPIClient(token=args.token, verbose=args.verbose)
    runner = AutoFollowRunner(
        client=client,
        state_mgr=state_mgr,
        target_username=args.target,
        max_follows=args.max_follows,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        dry_run=args.dry_run
    )

    try:
        runner.run()
    finally:
        state_mgr.save()


if __name__ == "__main__":
    main()
