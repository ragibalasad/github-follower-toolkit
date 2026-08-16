#!/usr/bin/env python3
"""
GitHub Auto-Follow Script (Dual CLI / Clean Interactive TUI Mode)
-----------------------------------------------------------------
Efficiently fetches followers of a target GitHub user and follows them
only if they do not already follow the authenticated user and are not already followed.

Features:
- Full Alternate Screen Buffer (Zero scrollback pollution, true in-place TUI)
- Fastfetch/Neofetch TrueColor avatar banner & live stats
- Micro-badge developer watermark (crafted by @ragibalasad)
- Pre-fetched bulk caching for O(1) in-memory relationship checks
- Primary rate-limit monitoring & auto-sleep
- Secondary rate-limit (abuse detection) mitigation & exponential backoff
- Single in-place status line above interactive prompt
- Resumable state persistence (.follow_state.json)
"""

import argparse
import datetime
import io
import json
import os
import random
import re
import readline
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Terminal color codes for rich CLI presentation
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
    
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    # Backgrounds
    BG_DARK = "\033[48;2;18;20;24m"

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

def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences to compute visible string width."""
    return re.sub(r'\x1b\[[0-9;]*[mGKHJ]', '', text)

def get_developer_watermark_divider(width: int = 68) -> str:
    """Renders a subtle, clean micro-badge developer watermark divider."""
    tag = " [crafted by @ragibalasad] "
    tag_colored = f" {Colors.DIM}[crafted by {Colors.CYAN}@ragibalasad{Colors.RESET}{Colors.DIM}]{Colors.RESET} "
    visible_tag_len = len(tag)
    side_len = max(4, (width - visible_tag_len) // 2)
    left_bar = f"{Colors.GRAY}{'─' * side_len}{Colors.RESET}"
    right_bar = f"{Colors.GRAY}{'─' * (width - visible_tag_len - side_len)}{Colors.RESET}"
    return f"  {left_bar}{tag_colored}{right_bar}"


# ==============================================================================
# Fastfetch / Neofetch ASCII & TrueColor Avatar Renderer
# ==============================================================================

OCTOCAT_FALLBACK_ASCII = [
    f"{Colors.CYAN}       .---.          {Colors.RESET}",
    f"{Colors.CYAN}      /     \\         {Colors.RESET}",
    f"{Colors.CYAN}     (  {Colors.WHITE}o   o{Colors.CYAN}  )        {Colors.RESET}",
    f"{Colors.CYAN}     /  {Colors.MAGENTA}==={Colors.CYAN}  \\        {Colors.RESET}",
    f"{Colors.CYAN}    / /     \\ \\       {Colors.RESET}",
    f"{Colors.CYAN}   ( (       ) )      {Colors.RESET}",
    f"{Colors.CYAN}    \\ \\_   _/ /       {Colors.RESET}",
    f"{Colors.CYAN}     \\__)-(__/        {Colors.RESET}",
    f"{Colors.CYAN}      /| | |\\         {Colors.RESET}",
    f"{Colors.CYAN}     ( | | | )        {Colors.RESET}",
    f"{Colors.CYAN}      \\| | |/         {Colors.RESET}",
    f"{Colors.CYAN}       '---'          {Colors.RESET}",
]

def image_to_ansi_halfblocks(img_bytes: bytes, target_width: int = 24, target_height: int = 24) -> List[str]:
    """
    Converts raw image bytes into TrueColor ANSI half-block (▀) strings.
    Each character row renders 2 vertical pixels (width x height/2 lines).
    """
    if not HAS_PIL:
        return OCTOCAT_FALLBACK_ASCII

    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        lines: List[str] = []
        bg_r, bg_g, bg_b = 18, 20, 24

        for y in range(0, target_height, 2):
            line_parts: List[str] = []
            for x in range(target_width):
                r1, g1, b1, a1 = img.getpixel((x, y))
                if a1 < 255:
                    alpha = a1 / 255.0
                    r1 = int(r1 * alpha + bg_r * (1 - alpha))
                    g1 = int(g1 * alpha + bg_g * (1 - alpha))
                    b1 = int(b1 * alpha + bg_b * (1 - alpha))

                if y + 1 < target_height:
                    r2, g2, b2, a2 = img.getpixel((x, y + 1))
                    if a2 < 255:
                        alpha = a2 / 255.0
                        r2 = int(r2 * alpha + bg_r * (1 - alpha))
                        g2 = int(g2 * alpha + bg_g * (1 - alpha))
                        b2 = int(b2 * alpha + bg_b * (1 - alpha))
                else:
                    r2, g2, b2 = bg_r, bg_g, bg_b

                line_parts.append(f"\033[38;2;{r1};{g1};{b1}m\033[48;2;{r2};{g2};{b2}m▀\033[0m")
            
            lines.append("".join(line_parts))
        return lines

    except Exception:
        return OCTOCAT_FALLBACK_ASCII

def fetch_avatar_ansi(avatar_url: Optional[str], session: requests.Session) -> List[str]:
    """Fetch user avatar and convert to ANSI TrueColor blocks."""
    if not avatar_url or not HAS_PIL:
        return OCTOCAT_FALLBACK_ASCII

    try:
        resp = session.get(avatar_url, timeout=5)
        if resp.status_code == 200:
            return image_to_ansi_halfblocks(resp.content, target_width=24, target_height=24)
    except Exception:
        pass
    return OCTOCAT_FALLBACK_ASCII

def build_neofetch_lines(
    auth_user: Dict[str, Any],
    target_info: Optional[Dict[str, Any]],
    api_remaining: int,
    api_limit: int,
    max_follows: int,
    delay_min: float,
    delay_max: float,
    dry_run: bool,
    avatar_lines: List[str],
    live_stats: Optional[Dict[str, int]] = None
) -> List[str]:
    """Builds the combined side-by-side Neofetch banner lines."""
    username = auth_user.get("login", "unknown")
    name = auth_user.get("name") or username
    bio = (auth_user.get("bio") or "GitHub Developer").replace("\n", " ").strip()
    if len(bio) > 34:
        bio = bio[:31] + "..."

    followers = auth_user.get("followers", 0)
    following = auth_user.get("following", 0)
    public_repos = auth_user.get("public_repos", 0)
    created_year = auth_user.get("created_at", "2020")[:4]

    if target_info:
        target_user = target_info.get("login", "unknown")
        target_followers = target_info.get("followers", 0)
        target_str = f"{Colors.YELLOW}@{target_user}{Colors.RESET} {Colors.DIM}({target_followers:,} followers){Colors.RESET}"
    else:
        target_str = f"{Colors.RED}Not Set{Colors.RESET} {Colors.DIM}(use 'set target <user>'){Colors.RESET}"

    api_pct = int((api_remaining / max(1, api_limit)) * 100)
    if api_pct > 50:
        quota_color = Colors.GREEN
    elif api_pct > 20:
        quota_color = Colors.YELLOW
    else:
        quota_color = Colors.RED

    mode_badge = f"{Colors.MAGENTA}{Colors.BOLD}DRY-RUN (Simulated){Colors.RESET}" if dry_run else f"{Colors.GREEN}{Colors.BOLD}ACTIVE (Live Follow){Colors.RESET}"

    header_title = f"{Colors.CYAN}{Colors.BOLD}{username}@github{Colors.RESET}"
    header_div = f"{Colors.GRAY}{'─' * 38}{Colors.RESET}"

    info_lines = [
        header_title,
        header_div,
        f"{Colors.BOLD}{Colors.WHITE}User:{Colors.RESET}       {name} {Colors.DIM}(@{username}){Colors.RESET}",
        f"{Colors.BOLD}{Colors.WHITE}Bio:{Colors.RESET}        {Colors.DIM}{bio}{Colors.RESET}",
        f"{Colors.BOLD}{Colors.WHITE}Account:{Colors.RESET}    Joined {created_year} | {public_repos} Repos",
        f"{Colors.BOLD}{Colors.WHITE}Network:{Colors.RESET}    {Colors.GREEN}{followers}{Colors.RESET} Followers | {Colors.BLUE}{following}{Colors.RESET} Following",
        f"{Colors.BOLD}{Colors.WHITE}Target:{Colors.RESET}     {target_str}",
        f"{Colors.BOLD}{Colors.WHITE}API Quota:{Colors.RESET}  {quota_color}{api_remaining:,} / {api_limit:,} ({api_pct}%){Colors.RESET}",
        f"{Colors.BOLD}{Colors.WHITE}Safety:{Colors.RESET}     Pacing {delay_min:.1f}s–{delay_max:.1f}s | Limit: {max_follows}",
        f"{Colors.BOLD}{Colors.WHITE}Mode:{Colors.RESET}       {mode_badge}",
    ]

    if live_stats:
        followed = live_stats.get("followed_success", 0)
        examined = live_stats.get("total_examined", 0)
        info_lines.append(f"{Colors.BOLD}{Colors.WHITE}Progress:{Colors.RESET}   {Colors.GREEN}{followed}/{max_follows} Followed{Colors.RESET} | {examined} Examined")
    else:
        info_lines.append(f" \033[90m●\033[0m \033[91m●\033[0m \033[92m●\033[0m \033[93m●\033[0m \033[94m●\033[0m \033[95m●\033[0m \033[96m●\033[0m \033[97m●\033[0m")

    total_rows = max(len(avatar_lines), len(info_lines))
    avatar_width = max(len(strip_ansi(line)) for line in avatar_lines) if avatar_lines else 24

    output_lines = []
    for i in range(total_rows):
        left = avatar_lines[i] if i < len(avatar_lines) else " " * avatar_width
        left_pad = avatar_width - len(strip_ansi(left))
        left_str = left + (" " * max(0, left_pad))

        right = info_lines[i] if i < len(info_lines) else ""
        output_lines.append(f"  {left_str}   {right}")

    return output_lines

def render_neofetch_banner(
    auth_user: Dict[str, Any],
    target_info: Optional[Dict[str, Any]],
    api_remaining: int,
    api_limit: int,
    max_follows: int,
    delay_min: float,
    delay_max: float,
    dry_run: bool,
    avatar_lines: List[str],
    live_stats: Optional[Dict[str, int]] = None
) -> None:
    lines = build_neofetch_lines(
        auth_user=auth_user,
        target_info=target_info,
        api_remaining=api_remaining,
        api_limit=api_limit,
        max_follows=max_follows,
        delay_min=delay_min,
        delay_max=delay_max,
        dry_run=dry_run,
        avatar_lines=avatar_lines,
        live_stats=live_stats
    )
    print("\n" + "\n".join(lines) + "\n")
    print(get_developer_watermark_divider(68) + "\n")


# ==============================================================================
# State Manager
# ==============================================================================

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


# ==============================================================================
# GitHub API Client
# ==============================================================================

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
            "User-Agent": "github-fastfetch-auto-follow/1.0 (@ragibalasad)"
        })
        self.rate_limit_limit = 5000
        self.rate_limit_remaining = 5000
        self.rate_limit_reset = 0

    def set_token(self, token: str) -> None:
        self.token = token.strip()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

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

                    if "bad credentials" in message:
                        log_error("Invalid GitHub Token. Please check your GITHUB_TOKEN permissions.")
                        sys.exit(1)

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
        """Fetch details of the authenticated user to verify token & get profile stats."""
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
        """Fetch all followers for a user in bulk pages (100/page) into a lowercase set."""
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
        """Fetch all accounts the authenticated user currently follows into a lowercase set."""
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
        """Stream target user's followers page by page (100 per request) lazily."""
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
        """Follow a target user via PUT /user/following/{username}."""
        endpoint = f"/user/following/{username}"
        resp = self.request("PUT", endpoint)
        if resp.status_code in (204, 200):
            return True
        else:
            log_error(f"Failed to follow '{username}': HTTP {resp.status_code} - {resp.text}")
            return False


# ==============================================================================
# Runner Pipeline
# ==============================================================================

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
        dry_run: bool = False,
        interactive: bool = False,
        verbose: bool = False,
        auth_user_cache: Optional[Dict[str, Any]] = None,
        avatar_lines_cache: Optional[List[str]] = None
    ) -> None:
        self.client = client
        self.state_mgr = state_mgr
        self.target_username = target_username
        self.max_follows = max_follows
        self.delay_min = max(0.5, delay_min)
        self.delay_max = max(self.delay_min, delay_max)
        self.dry_run = dry_run
        self.interactive = interactive
        self.verbose = verbose
        self.interrupted = False
        self.auth_user_cache = auth_user_cache
        self.avatar_lines_cache = avatar_lines_cache

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
        if not self.interactive:
            print("\n")
        self.interrupted = True

    def _update_ui(self, auth_user: Dict[str, Any], target_info: Dict[str, Any], avatar_lines: List[str], status_text: str) -> None:
        """In-place redraw for interactive mode: homes cursor in alternate screen buffer with zero scrollback pollution."""
        if not self.interactive or self.verbose:
            return

        # Move cursor to top-left (1,1) of the alternate screen buffer without adding to scrollback
        sys.stdout.write("\033[H")
        
        banner_lines = build_neofetch_lines(
            auth_user=auth_user,
            target_info=target_info,
            api_remaining=self.client.rate_limit_remaining,
            api_limit=self.client.rate_limit_limit,
            max_follows=self.max_follows,
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            dry_run=self.dry_run,
            avatar_lines=avatar_lines,
            live_stats=self.stats
        )
        print("\n" + "\n".join(banner_lines) + "\n\033[K")
        print(get_developer_watermark_divider(68) + "\n\033[K")

        pct = min(1.0, self.stats["followed_success"] / max(1, self.max_follows))
        bar_len = 20
        filled = int(bar_len * pct)
        bar = f"{Colors.GREEN}{'█' * filled}{Colors.GRAY}{'░' * (bar_len - filled)}{Colors.RESET}"
        
        print(f"  {Colors.BOLD}Progress:{Colors.RESET} [{bar}] {self.stats['followed_success']}/{self.max_follows} ({int(pct * 100)}%) | {Colors.DIM}Examined: {self.stats['total_examined']} | Skipped: {self.stats['skipped_already_follows_me'] + self.stats['skipped_already_following'] + self.stats['skipped_state_history']}{Colors.RESET}\033[K")
        print(f"  {Colors.BOLD}[Status]{Colors.RESET} {status_text}\033[K")
        # Clear any remaining lines below
        sys.stdout.write("\033[J")
        sys.stdout.flush()

    def run(self) -> str:
        """Runs the pipeline. Returns the final status message for the interactive prompt."""
        old_sigint = signal.getsignal(signal.SIGINT)
        old_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

        final_status = ""

        try:
            auth_user = self.auth_user_cache or self.client.get_authenticated_user()
            auth_login = auth_user["login"]

            target_info = self.client.get_user_info(self.target_username)
            if not target_info:
                err_msg = f"{Colors.RED}Target user '@{self.target_username}' not found or inaccessible.{Colors.RESET}"
                log_error(f"Target user '@{self.target_username}' not found or inaccessible.")
                return err_msg

            target_login = target_info["login"]
            target_follower_count = target_info.get("followers", 0)

            if target_login.lower() == auth_login.lower():
                err_msg = f"{Colors.RED}Target user cannot be yourself!{Colors.RESET}"
                log_error("Target user cannot be yourself!")
                return err_msg

            if target_follower_count == 0:
                warn_msg = f"{Colors.YELLOW}Target user '@{target_login}' has 0 followers.{Colors.RESET}"
                log_warn(f"Target user '@{target_login}' has 0 followers. Nothing to do.")
                return warn_msg

            avatar_lines = self.avatar_lines_cache or fetch_avatar_ansi(auth_user.get("avatar_url"), self.client.session)

            if not self.interactive or self.verbose:
                render_neofetch_banner(
                    auth_user=auth_user,
                    target_info=target_info,
                    api_remaining=self.client.rate_limit_remaining,
                    api_limit=self.client.rate_limit_limit,
                    max_follows=self.max_follows,
                    delay_min=self.delay_min,
                    delay_max=self.delay_max,
                    dry_run=self.dry_run,
                    avatar_lines=avatar_lines
                )
            else:
                self._update_ui(auth_user, target_info, avatar_lines, f"{Colors.CYAN}Fetching relationship cache...{Colors.RESET}")

            # Pre-fetch cache
            if not self.interactive or self.verbose:
                print(f"{Colors.BOLD}── Step 1: Pre-fetching Relationship Cache ───────────────────────────{Colors.RESET}")
                log_info(f"Fetching your followers (who follow @{auth_login})...")
            
            my_followers = self.client.fetch_all_followers_set()
            
            if not self.interactive or self.verbose:
                log_success(f"Cached {len(my_followers)} follower(s) who follow you.")
                log_info(f"Fetching your following list (who you already follow)...")
            
            my_following = self.client.fetch_all_following_set()
            
            if not self.interactive or self.verbose:
                log_success(f"Cached {len(my_following)} account(s) you already follow.")
                print(f"\n{Colors.BOLD}── Step 2: Processing Followers & Filtering Candidates ───────────────{Colors.RESET}")
                log_info(f"Starting pipeline (Session limit: {self.max_follows} follows, Delay: {self.delay_min:.1f}s-{self.delay_max:.1f}s)...")

            # Stream target's followers
            for candidate in self.client.stream_target_followers(target_login):
                if self.interrupted:
                    final_status = f"{Colors.YELLOW}Execution stopped by user (Ctrl+C). Followed {self.stats['followed_success']} users.{Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, final_status)
                    break

                if self.stats["followed_success"] >= self.max_follows:
                    final_status = f"{Colors.GREEN}Completed session! Followed {self.stats['followed_success']} users from @{target_login}.{Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, final_status)
                    if not self.interactive or self.verbose:
                        log_info(f"Reached target follow limit of {self.max_follows} for this session.")
                    break

                self.stats["total_examined"] += 1
                candidate_login = candidate["login"]
                candidate_lower = candidate_login.lower()

                if candidate_lower == auth_login.lower():
                    continue

                if candidate_lower in my_followers:
                    self.stats["skipped_already_follows_me"] += 1
                    status = f"{Colors.DIM}Skipped @{candidate_login} (Already follows you){Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, status)
                    if self.verbose:
                        log_dim(f"Skipped @{candidate_login} (Already follows you)")
                    continue

                if candidate_lower in my_following:
                    self.stats["skipped_already_following"] += 1
                    status = f"{Colors.DIM}Skipped @{candidate_login} (You already follow them){Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, status)
                    if self.verbose:
                        log_dim(f"Skipped @{candidate_login} (You already follow them)")
                    continue

                if self.state_mgr.is_previously_followed(candidate_login):
                    self.stats["skipped_state_history"] += 1
                    status = f"{Colors.DIM}Skipped @{candidate_login} (In local history cache){Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, status)
                    if self.verbose:
                        log_dim(f"Skipped @{candidate_login} (Recorded in local history)")
                    continue

                current_num = self.stats["followed_success"] + 1
                user_url = candidate.get("html_url", f"https://github.com/{candidate_login}")

                if self.dry_run:
                    self.stats["followed_success"] += 1
                    status = f"{Colors.MAGENTA}[DRY RUN]{Colors.RESET} [{current_num}/{self.max_follows}] Would follow {Colors.BOLD}@{candidate_login}{Colors.RESET}"
                    self._update_ui(auth_user, target_info, avatar_lines, status)
                    if not self.interactive or self.verbose:
                        log_info(f"{Colors.MAGENTA}[DRY RUN]{Colors.RESET} [{current_num}/{self.max_follows}] Would follow {Colors.BOLD}@{candidate_login}{Colors.RESET} ({user_url})")
                    time.sleep(0.08)
                    continue

                status_start = f"[{current_num}/{self.max_follows}] Following {Colors.BOLD}@{candidate_login}{Colors.RESET}..."
                self._update_ui(auth_user, target_info, avatar_lines, status_start)
                if not self.interactive or self.verbose:
                    log_info(f"[{current_num}/{self.max_follows}] Following {Colors.BOLD}@{candidate_login}{Colors.RESET} ({user_url})...")
                
                success = self.client.follow_user(candidate_login)

                if success:
                    self.stats["followed_success"] += 1
                    self.state_mgr.record_follow(candidate_login)
                    my_following.add(candidate_lower)
                    
                    sleep_time = random.uniform(self.delay_min, self.delay_max)
                    status_done = f"{Colors.GREEN}[SUCCESS]{Colors.RESET} Followed {Colors.BOLD}@{candidate_login}{Colors.RESET}! (Sleeping {sleep_time:.2f}s pacing)"
                    self._update_ui(auth_user, target_info, avatar_lines, status_done)
                    
                    if not self.interactive or self.verbose:
                        log_success(f"Successfully followed @{candidate_login}!")
                        log_dim(f"Pacing delay: sleeping {sleep_time:.2f}s...")

                    if self.stats["followed_success"] < self.max_follows:
                        end_sleep = time.time() + sleep_time
                        while time.time() < end_sleep:
                            if self.interrupted:
                                break
                            time.sleep(0.1)
                else:
                    self.stats["followed_failed"] += 1
                    status_err = f"{Colors.RED}[FAILED]{Colors.RESET} Could not follow @{candidate_login}"
                    self._update_ui(auth_user, target_info, avatar_lines, status_err)

            if not final_status:
                final_status = f"{Colors.GREEN}Finished. Followed {self.stats['followed_success']} candidates from @{target_login}.{Colors.RESET}"

            if not self.interactive or self.verbose:
                self._print_summary()

            return final_status

        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)
            self.state_mgr.save()

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
        print(f"{Colors.BOLD}{Colors.CYAN}══════════════════════════════════════════════════════════════════════{Colors.RESET}")
        print(get_developer_watermark_divider(68) + "\n")


# ==============================================================================
# Interactive REPL Environment
# ==============================================================================

class InteractiveSession:
    """Manages the interactive command-line environment using the Alternate Screen Buffer."""

    def __init__(self, token: str, default_target: Optional[str] = None) -> None:
        self.token = token
        self.client = GitHubAPIClient(token=self.token)
        self.state_mgr = StateManager()

        self.target_username = default_target
        self.target_info: Optional[Dict[str, Any]] = None
        self.max_follows = 50
        self.delay_min = 2.0
        self.delay_max = 4.0
        self.dry_run = False
        self.state_file = ".follow_state.json"

        # Cached profile & avatar
        self.auth_user: Optional[Dict[str, Any]] = None
        self.avatar_lines: Optional[List[str]] = None
        self.current_status: str = f"{Colors.DIM}Ready. Type {Colors.GREEN}'help'{Colors.RESET}{Colors.DIM} for commands or {Colors.GREEN}'run'{Colors.RESET}{Colors.DIM} to start.{Colors.RESET}"

    def initialize(self) -> None:
        """Initial check of user and target."""
        try:
            self.auth_user = self.client.get_authenticated_user()
            self.avatar_lines = fetch_avatar_ansi(self.auth_user.get("avatar_url"), self.client.session)
            if self.target_username:
                self.target_info = self.client.get_user_info(self.target_username)
        except Exception as e:
            self.current_status = f"{Colors.RED}Initialization error: {e}{Colors.RESET}"

    def redraw_screen(self, status_msg: Optional[str] = None) -> None:
        """Homes cursor in the Alternate Screen Buffer and renders the full dashboard with zero scrollback."""
        if status_msg is not None:
            self.current_status = status_msg

        if not self.auth_user:
            return

        # Move cursor to top-left (1,1) without pushing content into scrollback history
        sys.stdout.write("\033[H")
        banner_lines = build_neofetch_lines(
            auth_user=self.auth_user,
            target_info=self.target_info,
            api_remaining=self.client.rate_limit_remaining,
            api_limit=self.client.rate_limit_limit,
            max_follows=self.max_follows,
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            dry_run=self.dry_run,
            avatar_lines=self.avatar_lines or OCTOCAT_FALLBACK_ASCII
        )
        print("\n" + "\n".join(banner_lines) + "\n\033[K")
        print(get_developer_watermark_divider(68) + "\n\033[K")
        print(f"  {Colors.BOLD}[Status]{Colors.RESET} {self.current_status}\033[K")
        # Clear everything below the status line to ensure old text does not linger
        sys.stdout.write("\033[J")
        sys.stdout.flush()

    def print_help_screen(self) -> None:
        self.redraw_screen()
        print(f"\n  {Colors.BOLD}{Colors.CYAN}Interactive Commands Reference:{Colors.RESET}\033[K")
        print(f"    {Colors.GREEN}set target <username>{Colors.RESET}     Set the target user whose followers to extract\033[K")
        print(f"    {Colors.GREEN}set limit <num>{Colors.RESET}           Set max follows for session (e.g., set limit 30)\033[K")
        print(f"    {Colors.GREEN}set delay <min> <max>{Colors.RESET}     Set pacing delay range in seconds (e.g., set delay 2.5 5.0)\033[K")
        print(f"    {Colors.GREEN}set dry-run <on|off>{Colors.RESET}      Toggle dry-run simulation mode\033[K")
        print(f"    {Colors.GREEN}set token <token>{Colors.RESET}         Update GitHub PAT token\033[K")
        print(f"    {Colors.GREEN}show{Colors.RESET} / {Colors.GREEN}status{Colors.RESET}              Refresh and redraw profile & settings\033[K")
        print(f"    {Colors.GREEN}clear-state{Colors.RESET}               Reset local followed history cache (.follow_state.json)\033[K")
        print(f"    {Colors.GREEN}run{Colors.RESET}                       Start execution with live in-place single-line status\033[K")
        print(f"    {Colors.GREEN}run -v{Colors.RESET} / {Colors.GREEN}run --verbose{Colors.RESET}    Start execution with scrolling verbose logs\033[K")
        print(f"    {Colors.GREEN}exit{Colors.RESET} / {Colors.GREEN}quit{Colors.RESET}               Exit the program\033[K\n")
        sys.stdout.flush()

    def handle_command(self, cmd_line: str) -> bool:
        """Processes an interactive command and updates status in place. Returns False to exit."""
        parts = cmd_line.strip().split()
        if not parts:
            self.redraw_screen()
            return True

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("exit", "quit", "q"):
            return False

        elif cmd in ("help", "?"):
            self.print_help_screen()
            return True

        elif cmd in ("show", "status", "info"):
            self.redraw_screen(f"{Colors.GREEN}Profile & configuration refreshed.{Colors.RESET}")

        elif cmd == "clear-state":
            self.state_mgr.clear()
            self.redraw_screen(f"{Colors.GREEN}Local history cache cleared successfully.{Colors.RESET}")

        elif cmd == "set":
            if not args:
                self.redraw_screen(f"{Colors.YELLOW}Usage: set <target|limit|delay|dry-run|token> <value>{Colors.RESET}")
                return True

            sub = args[0].lower()
            val_args = args[1:]

            if sub in ("target", "user", "-t"):
                if not val_args:
                    self.redraw_screen(f"{Colors.YELLOW}Usage: set target <username>{Colors.RESET}")
                else:
                    new_target = val_args[0].lstrip("@")
                    info = self.client.get_user_info(new_target)
                    if info:
                        self.target_username = new_target
                        self.target_info = info
                        self.redraw_screen(f"{Colors.GREEN}Target set to @{new_target} ({info.get('followers', 0):,} followers).{Colors.RESET}")
                    else:
                        self.redraw_screen(f"{Colors.RED}User '@{new_target}' not found on GitHub.{Colors.RESET}")

            elif sub in ("limit", "max", "max-follows", "-m"):
                if not val_args:
                    self.redraw_screen(f"{Colors.YELLOW}Usage: set limit <number>{Colors.RESET}")
                else:
                    try:
                        self.max_follows = max(1, int(val_args[0]))
                        self.redraw_screen(f"{Colors.GREEN}Session follow limit set to {self.max_follows}.{Colors.RESET}")
                    except ValueError:
                        self.redraw_screen(f"{Colors.RED}Invalid number for follow limit.{Colors.RESET}")

            elif sub in ("delay", "pacing"):
                if len(val_args) == 1:
                    try:
                        d = float(val_args[0])
                        self.delay_min = d
                        self.delay_max = d + 2.0
                        self.redraw_screen(f"{Colors.GREEN}Pacing delay set to {self.delay_min:.1f}s–{self.delay_max:.1f}s.{Colors.RESET}")
                    except ValueError:
                        self.redraw_screen(f"{Colors.RED}Invalid delay value.{Colors.RESET}")
                elif len(val_args) >= 2:
                    try:
                        self.delay_min = float(val_args[0])
                        self.delay_max = float(val_args[1])
                        self.redraw_screen(f"{Colors.GREEN}Pacing delay set to {self.delay_min:.1f}s–{self.delay_max:.1f}s.{Colors.RESET}")
                    except ValueError:
                        self.redraw_screen(f"{Colors.RED}Invalid delay values.{Colors.RESET}")

            elif sub in ("dry-run", "dryrun"):
                if not val_args:
                    self.dry_run = not self.dry_run
                else:
                    self.dry_run = val_args[0].lower() in ("true", "1", "yes", "on", "enable")
                state_str = f"{Colors.MAGENTA}ENABLED (Simulation){Colors.RESET}" if self.dry_run else f"{Colors.GREEN}DISABLED (Live){Colors.RESET}"
                self.redraw_screen(f"{Colors.GREEN}Dry-run mode {state_str}.{Colors.RESET}")

            elif sub == "token":
                if not val_args:
                    self.redraw_screen(f"{Colors.YELLOW}Usage: set token <ghp_token>{Colors.RESET}")
                else:
                    self.token = val_args[0]
                    self.client.set_token(self.token)
                    self.initialize()
                    self.redraw_screen(f"{Colors.GREEN}GitHub Token updated and profile reloaded.{Colors.RESET}")

            else:
                self.redraw_screen(f"{Colors.YELLOW}Unknown setting '{sub}'. Type 'help' for options.{Colors.RESET}")

        elif cmd == "run":
            if not self.target_username:
                self.redraw_screen(f"{Colors.RED}No target user set! Use 'set target <username>' first.{Colors.RESET}")
                return True

            verbose = ("-v" in args or "--verbose" in args)
            runner = AutoFollowRunner(
                client=self.client,
                state_mgr=self.state_mgr,
                target_username=self.target_username,
                max_follows=self.max_follows,
                delay_min=self.delay_min,
                delay_max=self.delay_max,
                dry_run=self.dry_run,
                interactive=True,
                verbose=verbose,
                auth_user_cache=self.auth_user,
                avatar_lines_cache=self.avatar_lines
            )
            result_status = runner.run()
            if not verbose:
                self.redraw_screen(result_status)

        else:
            self.redraw_screen(f"{Colors.YELLOW}Unknown command '{cmd}'. Type 'help' for list of commands.{Colors.RESET}")

        return True

    def start_repl(self) -> None:
        """Starts the interactive prompt loop in the Alternate Screen Buffer."""
        try:
            # Enter Alternate Screen Buffer & clear alternate viewport
            sys.stdout.write("\033[?1049h\033[H\033[2J")
            sys.stdout.flush()

            self.initialize()
            self.redraw_screen()

            while True:
                try:
                    prompt_str = f"  {Colors.BOLD}{Colors.CYAN}github-follow{Colors.RESET} {Colors.GRAY}❯{Colors.RESET} "
                    cmd_line = input(prompt_str)
                    should_continue = self.handle_command(cmd_line)
                    if not should_continue:
                        break
                except (KeyboardInterrupt, EOFError):
                    break
        finally:
            # Cleanly exit Alternate Screen Buffer and restore original user terminal buffer
            sys.stdout.write("\033[?1049l\033[?25h")
            sys.stdout.flush()
            print(f"\n  {Colors.CYAN}Exited GitHub Follow TUI. Goodbye! 👋{Colors.RESET}\n")


# ==============================================================================
# CLI Argument Parser & Entry Point
# ==============================================================================

def parse_args() -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="High-efficiency, rate-limit aware GitHub Auto-Follow Script."
    )
    parser.add_argument(
        "-t", "--target",
        type=str,
        default=os.getenv("GITHUB_TARGET_USER"),
        help="Target GitHub username whose followers will be extracted."
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
        "-i", "--interactive",
        action="store_true",
        help="Start in interactive command REPL mode."
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

    is_interactive = args.interactive or (len(sys.argv) == 1 and not args.target)

    token = args.token
    if not token:
        if is_interactive:
            print(f"\n{Colors.BOLD}{Colors.YELLOW}GitHub Personal Access Token not found in .env!{Colors.RESET}")
            try:
                token = input("Enter GITHUB_TOKEN: ").strip()
            except (KeyboardInterrupt, EOFError):
                sys.exit(0)
        
        if not token:
            log_error("GitHub Token is required! Provide it via --token or GITHUB_TOKEN in .env")
            print(f"\n{Colors.BOLD}How to create a GitHub token:{Colors.RESET}")
            print(" 1. Go to https://github.com/settings/tokens")
            print(" 2. Generate a Classic Token with scope 'user:follow', or a Fine-grained token with Follow (Read & Write)")
            print(" 3. Put it in .env file as: GITHUB_TOKEN=ghp_xxx\n")
            sys.exit(1)

    if is_interactive:
        session = InteractiveSession(token=token, default_target=args.target)
        if args.clear_state:
            session.state_mgr.clear()
        session.start_repl()
        return

    # Non-interactive / One-line CLI Mode
    if not args.target:
        log_error("Target GitHub username is required in CLI mode! Use --target <username> or run interactively.")
        sys.exit(1)

    state_mgr = StateManager(state_file=args.state_file)
    if args.clear_state:
        log_warn("Clearing existing state history...")
        state_mgr.clear()
        log_success("State cleared.")

    client = GitHubAPIClient(token=token, verbose=args.verbose)
    runner = AutoFollowRunner(
        client=client,
        state_mgr=state_mgr,
        target_username=args.target,
        max_follows=args.max_follows,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        dry_run=args.dry_run,
        interactive=False,
        verbose=args.verbose
    )

    try:
        runner.run()
    finally:
        state_mgr.save()


if __name__ == "__main__":
    main()
