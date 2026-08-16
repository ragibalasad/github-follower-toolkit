#!/usr/bin/env python3
"""
GitHub Auto-Follow Script (Fastfetch TUI powered by Rich)
---------------------------------------------------------
Efficiently fetches followers of a target GitHub user and follows them
only if they do not already follow the authenticated user and are not already followed.

Built using the industry-standard 'Rich' library for flawless terminal rendering,
true in-place live dashboard updates, and zero scrollback pollution.
"""

import argparse
import datetime
import io
import json
import os
import random
import readline
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

try:
    import requests
    from dotenv import load_dotenv
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import ProgressBar
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError as e:
    missing_pkg = getattr(e, "name", str(e))
    print(f"\n\033[91m[ERROR]\033[0m Missing required Python package: '{missing_pkg}'.")
    print(f"\033[1mPlease install required packages by running:\033[0m")
    print(f"    pip install -r requirements.txt\n")
    sys.exit(1)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Global Rich console instance
console = Console(highlight=False)

APP_NAME = "ActiveFollow"
APP_VERSION = "v1.0"


def enter_alternate_screen() -> None:
    """Switches terminal to Alternate Screen Buffer with zero scrollback history."""
    sys.stdout.write("\033[?1049h\033[H\033[2J\033[3J")
    sys.stdout.flush()


def exit_alternate_screen() -> None:
    """Restores primary terminal buffer and unhides cursor."""
    sys.stdout.write("\033[?1049l\033[?25h")
    sys.stdout.flush()


def purge_screen_and_home() -> None:
    """Moves cursor to row 1 col 1, clears entire visible viewport, and purges scrollback buffer."""
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()


# ==============================================================================
# Fastfetch / Neofetch ASCII & TrueColor Avatar Renderer
# ==============================================================================

OCTOCAT_FALLBACK_ASCII = [
    "\033[96m       .---.          \033[0m",
    "\033[96m      /     \\         \033[0m",
    "\033[96m     (  \033[97mo   o\033[96m  )        \033[0m",
    "\033[96m     /  \033[95m===\033[96m  \\        \033[0m",
    "\033[96m    / /     \\ \\       \033[0m",
    "\033[96m   ( (       ) )      \033[0m",
    "\033[96m    \\ \\_   _/ /       \033[0m",
    "\033[96m     \\__)-(__/        \033[0m",
    "\033[96m      /| | |\\         \033[0m",
    "\033[96m     ( | | | )        \033[0m",
    "\033[96m      \\| | |/         \033[0m",
    "\033[96m       '---'          \033[0m",
]

def image_to_ansi_halfblocks(img_bytes: bytes, target_width: int = 24, target_height: int = 24) -> List[str]:
    """Converts raw image bytes into TrueColor ANSI half-block (▀) strings."""
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


def get_developer_watermark_divider(width: int = 68) -> Text:
    """Renders a subtle, clean micro-badge developer watermark divider with exact fixed width."""
    tag_text = " [crafted by @ragibalasad] "
    side_len = max(4, (width - len(tag_text)) // 2)
    right_len = max(4, width - len(tag_text) - side_len)

    t = Text("  ")
    t.append("─" * side_len, style="dim bright_black")
    t.append(" [crafted by ", style="dim")
    t.append("@ragibalasad", style="bold cyan")
    t.append("] ", style="dim")
    t.append("─" * right_len, style="dim bright_black")
    return t


def build_dashboard_renderable(
    auth_user: Dict[str, Any],
    target_info: Optional[Dict[str, Any]],
    api_remaining: int,
    api_limit: int,
    max_follows: int,
    delay_min: float,
    delay_max: float,
    dry_run: bool,
    avatar_lines: List[str],
    live_stats: Optional[Dict[str, int]] = None,
    status_msg: Optional[str] = None
) -> Group:
    """Builds a rich renderable Group containing Fastfetch card, watermark, and status."""
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
        target_str = f"[bold yellow]@{target_user}[/bold yellow] [dim]({target_followers:,} followers)[/dim]"
    else:
        target_str = "[bold red]Not Set[/bold red] [dim](use 'set target <user>')[/dim]"

    api_pct = int((api_remaining / max(1, api_limit)) * 100)
    quota_color = "green" if api_pct > 50 else ("yellow" if api_pct > 20 else "red")
    mode_badge = "[bold magenta]DRY-RUN (Simulated)[/bold magenta]" if dry_run else "[bold green]ACTIVE (Live Follow)[/bold green]"

    # Right side stats table
    info_table = Table.grid(padding=(0, 2))
    info_table.add_column(justify="left", style="bold white", width=12, no_wrap=True)
    info_table.add_column(justify="left")

    info_table.add_row("User:", f"{name} [dim](@{username})[/dim]")
    info_table.add_row("Bio:", f"[dim]{bio}[/dim]")
    info_table.add_row("Account:", f"Joined {created_year} | {public_repos} Repos")
    info_table.add_row("Network:", f"[bold green]{followers}[/bold green] Followers | [bold blue]{following}[/bold blue] Following")
    info_table.add_row("Target:", target_str)
    info_table.add_row("API Quota:", f"[{quota_color}]{api_remaining:,} / {api_limit:,} ({api_pct}%)[/{quota_color}]")
    info_table.add_row("Safety:", f"Pacing {delay_min:.1f}s–{delay_max:.1f}s | Limit: {max_follows}")
    info_table.add_row("Mode:", mode_badge)

    info_table.add_row("", " \033[90m●\033[0m \033[91m●\033[0m \033[92m●\033[0m \033[93m●\033[0m \033[94m●\033[0m \033[95m●\033[0m \033[96m●\033[0m \033[97m●\033[0m")

    # Combine Title, Divider, and Key-Value Table cleanly
    right_column = Group(
        Text.from_markup(f"[bold cyan]{APP_NAME} {APP_VERSION}[/bold cyan]"),
        Text("─" * 36, style="dim bright_black"),
        info_table
    )

    # Avatar on left, info on right (with 2-space left margin matching layout)
    padded_avatar_lines = ["  " + line for line in avatar_lines]
    avatar_text = Text.from_ansi("\n".join(padded_avatar_lines))
    card_table = Table.grid(padding=(0, 3))
    card_table.add_column(justify="left")
    card_table.add_column(justify="left")
    card_table.add_row(avatar_text, right_column)

    renderables: List[Any] = [
        Text(""),
        card_table,
        Text(""),
        get_developer_watermark_divider(68),
    ]

    # Live progress bar if stats provided
    if live_stats:
        followed = live_stats.get("followed_success", 0)
        examined = live_stats.get("total_examined", 0)
        skipped_follows = live_stats.get("skipped_already_follows_me", 0)
        skipped_following = live_stats.get("skipped_already_following", 0)
        skipped_history = live_stats.get("skipped_state_history", 0)
        skipped_total = skipped_follows + skipped_following + skipped_history
        pct = min(1.0, followed / max(1, max_follows))
        pct_int = int(pct * 100)

        progress_bar = ProgressBar(
            total=max_follows,
            completed=followed,
            width=20,
            style="bright_black",
            complete_style="bold green"
        )
        
        progress_grid = Table.grid(padding=(0, 1))
        progress_grid.add_column(justify="left")
        progress_grid.add_column(justify="left")
        progress_grid.add_column(justify="left")
        
        stats_text = Text(" ")
        stats_text.append(f"[{followed}/{max_follows}]", style="bold green")
        stats_text.append(f" ({pct_int}%)", style="bold cyan")
        stats_text.append(" | ", style="dim bright_black")
        stats_text.append(f"Examined: {examined}", style="dim")
        stats_text.append(" | ", style="dim bright_black")
        stats_text.append(f"Skipped: {skipped_total}", style="dim yellow")
        
        progress_grid.add_row(
            Text("  Progress: ", style="bold white"),
            progress_bar,
            stats_text
        )
        renderables.append(Text(""))
        renderables.append(progress_grid)

    # Status message line
    if status_msg:
        renderables.append(Text(""))
        status_line = Text("  [Status] ", style="bold")
        try:
            status_line.append_text(Text.from_markup(status_msg))
        except Exception:
            status_line.append(status_msg)
        renderables.append(status_line)

    return Group(*renderables)


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
    live_stats: Optional[Dict[str, int]] = None,
    status_msg: Optional[str] = None
) -> None:
    dashboard = build_dashboard_renderable(
        auth_user=auth_user,
        target_info=target_info,
        api_remaining=api_remaining,
        api_limit=api_limit,
        max_follows=max_follows,
        delay_min=delay_min,
        delay_max=delay_max,
        dry_run=dry_run,
        avatar_lines=avatar_lines,
        live_stats=live_stats,
        status_msg=status_msg
    )
    console.print(dashboard)


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
                console.print(f"[yellow][WARN][/yellow] Could not load state from {self.state_path}: {e}.")

    def save(self) -> None:
        try:
            self.state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            console.print(f"[red][ERROR][/red] Failed to save state to {self.state_path}: {e}")

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

    def _handle_rate_limit_pause_if_needed(self) -> None:
        """Pause if primary rate limit quota is dangerously low."""
        if self.rate_limit_remaining <= 10:
            now = int(time.time())
            sleep_duration = max(5, self.rate_limit_reset - now + 2)
            reset_dt = datetime.datetime.fromtimestamp(self.rate_limit_reset, tz=datetime.timezone.utc)
            console.print(f"[yellow][WARN][/yellow] Primary rate limit nearly exhausted ({self.rate_limit_remaining} left).")
            console.print(f"[yellow][WARN][/yellow] Sleeping {sleep_duration}s until reset at {reset_dt.strftime('%H:%M:%S UTC')}...")
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

                        console.print(f"[yellow][WARN][/yellow] GitHub Secondary Rate Limit triggered. Waiting {wait_seconds:.1f}s...")
                        time.sleep(wait_seconds)
                        retries += 1
                        continue

                    if "bad credentials" in message:
                        console.print("[red][ERROR][/red] Invalid GitHub Token. Please check your GITHUB_TOKEN.")
                        sys.exit(1)

                if 500 <= response.status_code < 600:
                    console.print(f"[yellow][WARN][/yellow] GitHub server error ({response.status_code}). Retrying in {backoff_delay:.1f}s...")
                    time.sleep(backoff_delay)
                    backoff_delay *= 1.5
                    retries += 1
                    continue

                return response

            except (requests.ConnectionError, requests.Timeout) as e:
                console.print(f"[yellow][WARN][/yellow] Network error ({e}). Retrying in {backoff_delay:.1f}s...")
                time.sleep(backoff_delay)
                backoff_delay *= 1.5
                retries += 1

        console.print(f"[red][ERROR][/red] Failed request to {url} after {max_retries} attempts.")
        raise RuntimeError(f"Failed to execute API request to {url}")

    def get_authenticated_user(self) -> Dict[str, Any]:
        resp = self.request("GET", "/user")
        if resp.status_code != 200:
            console.print(f"[red][ERROR][/red] Failed to authenticate: HTTP {resp.status_code} - {resp.text}")
            sys.exit(1)
        return resp.json()

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        resp = self.request("GET", f"/users/{username}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            console.print(f"[red][ERROR][/red] Failed to fetch user '{username}': HTTP {resp.status_code}")
            return None
        return resp.json()

    def fetch_all_followers_set(self, username: Optional[str] = None) -> Set[str]:
        endpoint = "/user/followers" if username is None else f"/users/{username}/followers"
        followers: Set[str] = set()
        page = 1

        while True:
            resp = self.request("GET", endpoint, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                console.print(f"[red][ERROR][/red] Failed fetching followers page {page}: HTTP {resp.status_code}")
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
        following: Set[str] = set()
        page = 1

        while True:
            resp = self.request("GET", "/user/following", params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                console.print(f"[red][ERROR][/red] Failed fetching following page {page}: HTTP {resp.status_code}")
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
        endpoint = f"/users/{username}/followers"
        page = 1

        while True:
            resp = self.request("GET", endpoint, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                console.print(f"[red][ERROR][/red] Failed streaming followers for '{username}' (page {page}): HTTP {resp.status_code}")
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
        endpoint = f"/user/following/{username}"
        resp = self.request("PUT", endpoint)
        if resp.status_code in (204, 200):
            return True
        else:
            console.print(f"[red][ERROR][/red] Failed to follow '{username}': HTTP {resp.status_code} - {resp.text}")
            return False


# ==============================================================================
# Runner Pipeline
# ==============================================================================

class AutoFollowRunner:
    """Coordinates filtering, rate-limit pacing, and execution with Rich Live TUI."""

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
        self.interrupted = True

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
                err_msg = f"[red]Target user '@{self.target_username}' not found or inaccessible.[/red]"
                console.print(err_msg)
                return err_msg

            target_login = target_info["login"]
            target_follower_count = target_info.get("followers", 0)

            if target_login.lower() == auth_login.lower():
                err_msg = "[red]Target user cannot be yourself![/red]"
                console.print(err_msg)
                return err_msg

            if target_follower_count == 0:
                warn_msg = f"[yellow]Target user '@{target_login}' has 0 followers.[/yellow]"
                console.print(warn_msg)
                return warn_msg

            avatar_lines = self.avatar_lines_cache or fetch_avatar_ansi(auth_user.get("avatar_url"), self.client.session)

            # In interactive non-verbose mode, use in-place screen purge and repaint
            use_live = (self.interactive and not self.verbose)

            def update_live_ui(status_msg: str) -> None:
                if use_live:
                    purge_screen_and_home()
                    dashboard = build_dashboard_renderable(
                        auth_user=auth_user,
                        target_info=target_info,
                        api_remaining=self.client.rate_limit_remaining,
                        api_limit=self.client.rate_limit_limit,
                        max_follows=self.max_follows,
                        delay_min=self.delay_min,
                        delay_max=self.delay_max,
                        dry_run=self.dry_run,
                        avatar_lines=avatar_lines,
                        live_stats=self.stats,
                        status_msg=status_msg
                    )
                    console.print(dashboard)
                    sys.stdout.flush()

            if use_live:
                update_live_ui("[cyan]Fetching relationship cache...[/cyan]")
            elif not self.interactive or self.verbose:
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

            # 3. Pre-fetch My Followers & Following in Bulk for O(1) in-memory checks
            if not use_live:
                console.print(f"[bold]── Step 1: Pre-fetching Relationship Cache ───────────────────────────[/bold]")
                console.print(f"[blue][INFO][/blue] Fetching your followers (who follow @{auth_login})...")
            
            my_followers = self.client.fetch_all_followers_set()
            
            if not use_live:
                console.print(f"[green][SUCCESS][/green] Cached {len(my_followers)} follower(s) who follow you.")
                console.print(f"[blue][INFO][/blue] Fetching your following list (who you already follow)...")
            
            my_following = self.client.fetch_all_following_set()
            
            if not use_live:
                console.print(f"[green][SUCCESS][/green] Cached {len(my_following)} account(s) you already follow.")
                console.print(f"\n[bold]── Step 2: Processing Followers & Filtering Candidates ───────────────[/bold]")
                console.print(f"[blue][INFO][/blue] Starting pipeline (Session limit: {self.max_follows} follows, Delay: {self.delay_min:.1f}s-{self.delay_max:.1f}s)...")

            # 4. Stream Target's Followers & Filter Candidates
            for candidate in self.client.stream_target_followers(target_login):
                if self.interrupted:
                    final_status = f"[yellow]Execution stopped by user (Ctrl+C). Followed {self.stats['followed_success']} users.[/yellow]"
                    update_live_ui(final_status)
                    break

                if self.stats["followed_success"] >= self.max_follows:
                    final_status = f"[green]Completed session! Followed {self.stats['followed_success']} users from @{target_login}.[/green]"
                    update_live_ui(final_status)
                    if not self.interactive or self.verbose:
                        console.print(f"[blue][INFO][/blue] Reached target follow limit of {self.max_follows} for this session.")
                    break

                self.stats["total_examined"] += 1
                candidate_login = candidate["login"]
                candidate_lower = candidate_login.lower()

                if candidate_lower == auth_login.lower():
                    continue

                if candidate_lower in my_followers:
                    self.stats["skipped_already_follows_me"] += 1
                    status = f"[dim]Skipped @{candidate_login} (Already follows you)[/dim]"
                    if use_live:
                        update_live_ui(status)
                    elif self.verbose:
                        console.print(f"       [dim]Skipped @{candidate_login} (Already follows you)[/dim]")
                    continue

                if candidate_lower in my_following:
                    self.stats["skipped_already_following"] += 1
                    status = f"[dim]Skipped @{candidate_login} (You already follow them)[/dim]"
                    if use_live:
                        update_live_ui(status)
                    elif self.verbose:
                        console.print(f"       [dim]Skipped @{candidate_login} (You already follow them)[/dim]")
                    continue

                if self.state_mgr.is_previously_followed(candidate_login):
                    self.stats["skipped_state_history"] += 1
                    status = f"[dim]Skipped @{candidate_login} (In local history cache)[/dim]"
                    if use_live:
                        update_live_ui(status)
                    elif self.verbose:
                        console.print(f"       [dim]Skipped @{candidate_login} (Recorded in local history)[/dim]")
                    continue

                current_num = self.stats["followed_success"] + 1
                user_url = candidate.get("html_url", f"https://github.com/{candidate_login}")

                if self.dry_run:
                    self.stats["followed_success"] += 1
                    status = f"[magenta][DRY RUN][/magenta] [{current_num}/{self.max_follows}] Would follow [bold]@{candidate_login}[/bold]"
                    if use_live:
                        update_live_ui(status)
                    elif not self.interactive or self.verbose:
                        console.print(f"[magenta][DRY RUN][/magenta] [{current_num}/{self.max_follows}] Would follow [bold]@{candidate_login}[/bold] ({user_url})")
                    time.sleep(0.08)
                    continue

                status_start = f"[{current_num}/{self.max_follows}] Following [bold]@{candidate_login}[/bold]..."
                if use_live:
                    update_live_ui(status_start)
                elif not self.interactive or self.verbose:
                    console.print(f"[blue][INFO][/blue] [{current_num}/{self.max_follows}] Following [bold]@{candidate_login}[/bold] ({user_url})...")
                
                success = self.client.follow_user(candidate_login)

                if success:
                    self.stats["followed_success"] += 1
                    self.state_mgr.record_follow(candidate_login)
                    my_following.add(candidate_lower)
                    
                    sleep_time = random.uniform(self.delay_min, self.delay_max)
                    status_done = f"[green][SUCCESS][/green] Followed [bold]@{candidate_login}[/bold]! (Sleeping {sleep_time:.2f}s pacing)"
                    if use_live:
                        update_live_ui(status_done)
                    elif not self.interactive or self.verbose:
                        console.print(f"[green][SUCCESS][/green] Successfully followed @{candidate_login}!")
                        console.print(f"       [dim]Pacing delay: sleeping {sleep_time:.2f}s...[/dim]")

                    if self.stats["followed_success"] < self.max_follows:
                        end_sleep = time.time() + sleep_time
                        while time.time() < end_sleep:
                            if self.interrupted:
                                break
                            time.sleep(0.1)
                else:
                    self.stats["followed_failed"] += 1
                    status_err = f"[red][FAILED][/red] Could not follow @{candidate_login}"
                    if use_live:
                        update_live_ui(status_err)

            if not final_status:
                final_status = f"[green]Finished. Followed {self.stats['followed_success']} candidates from @{target_login}.[/green]"

            if not self.interactive or self.verbose:
                self._print_summary()

            return final_status

        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)
            self.state_mgr.save()

    def _print_summary(self) -> None:
        table = Table(title="Execution Summary", border_style="cyan", show_header=False)
        table.add_column("Metric", style="bold white")
        table.add_column("Value", style="cyan")

        table.add_row("Total Candidates Examined", str(self.stats["total_examined"]))
        table.add_row("Successfully Followed", f"[green]{self.stats['followed_success']}[/green]")
        if self.stats["followed_failed"] > 0:
            table.add_row("Failed Follows", f"[red]{self.stats['followed_failed']}[/red]")
        table.add_row("Skipped (Already follow you)", str(self.stats["skipped_already_follows_me"]))
        table.add_row("Skipped (Already following)", str(self.stats["skipped_already_following"]))
        table.add_row("Skipped (State history)", str(self.stats["skipped_state_history"]))
        table.add_row("Total Followed (All-Time Record)", str(self.state_mgr.state.get("total_followed_all_time", 0)))
        table.add_row("Remaining API Quota", f"{self.client.rate_limit_remaining}/{self.client.rate_limit_limit}")

        console.print(table)
        console.print(get_developer_watermark_divider(68))
        console.print("")


# ==============================================================================
# Interactive REPL Environment
# ==============================================================================

class InteractiveSession:
    """Manages the interactive command-line environment using Rich."""

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
        self.current_status: str = "[dim]Ready. Type [green]'help'[/green] for commands or [green]'run'[/green] to start.[/dim]"

    def initialize(self) -> None:
        """Initial check of user and target."""
        try:
            self.auth_user = self.client.get_authenticated_user()
            self.avatar_lines = fetch_avatar_ansi(self.auth_user.get("avatar_url"), self.client.session)
            if self.target_username:
                self.target_info = self.client.get_user_info(self.target_username)
        except Exception as e:
            self.current_status = f"[red]Initialization error: {e}[/red]"

    def redraw_screen(self, status_msg: Optional[str] = None) -> None:
        """Purges screen and renders the full Rich dashboard at (1,1)."""
        if status_msg is not None:
            self.current_status = status_msg

        if not self.auth_user:
            return

        purge_screen_and_home()
        dashboard = build_dashboard_renderable(
            auth_user=self.auth_user,
            target_info=self.target_info,
            api_remaining=self.client.rate_limit_remaining,
            api_limit=self.client.rate_limit_limit,
            max_follows=self.max_follows,
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            dry_run=self.dry_run,
            avatar_lines=self.avatar_lines or OCTOCAT_FALLBACK_ASCII,
            status_msg=self.current_status
        )
        console.print(dashboard)
        sys.stdout.flush()

    def print_help_screen(self) -> None:
        self.redraw_screen()
        help_table = Table(title="Interactive Commands Reference", border_style="cyan")
        help_table.add_column("Command", style="bold green", no_wrap=True)
        help_table.add_column("Example / Description", style="white")

        help_table.add_row("set target <username>", "Set target user whose followers to extract (e.g. set target torvalds)")
        help_table.add_row("set limit <number>", "Set max follows for session (e.g. set limit 30)")
        help_table.add_row("set delay <min> <max>", "Set pacing delay range in seconds (e.g. set delay 2.5 5.0)")
        help_table.add_row("set dry-run <on|off>", "Toggle simulation dry-run mode")
        help_table.add_row("set token <ghp_token>", "Update GitHub PAT token")
        help_table.add_row("show / status", "Refresh and redraw profile & settings")
        help_table.add_row("clear-state", "Reset local followed history cache (.follow_state.json)")
        help_table.add_row("run", "Start execution with live in-place single-line status")
        help_table.add_row("run -v / run --verbose", "Start execution with scrolling verbose logs")
        help_table.add_row("exit / quit", "Exit the program")

        console.print(help_table)

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
            self.redraw_screen("[green]Profile & configuration refreshed.[/green]")

        elif cmd == "clear-state":
            self.state_mgr.clear()
            self.redraw_screen("[green]Local history cache cleared successfully.[/green]")

        elif cmd == "set":
            if not args:
                self.redraw_screen("[yellow]Usage: set <target|limit|delay|dry-run|token> <value>[/yellow]")
                return True

            sub = args[0].lower()
            val_args = args[1:]

            if sub in ("target", "user", "-t"):
                if not val_args:
                    self.redraw_screen("[yellow]Usage: set target <username>[/yellow]")
                else:
                    new_target = val_args[0].lstrip("@")
                    info = self.client.get_user_info(new_target)
                    if info:
                        self.target_username = new_target
                        self.target_info = info
                        self.redraw_screen(f"[green]Target set to @{new_target} ({info.get('followers', 0):,} followers).[/green]")
                    else:
                        self.redraw_screen(f"[red]User '@{new_target}' not found on GitHub.[/red]")

            elif sub in ("limit", "max", "max-follows", "-m"):
                if not val_args:
                    self.redraw_screen("[yellow]Usage: set limit <number>[/yellow]")
                else:
                    try:
                        self.max_follows = max(1, int(val_args[0]))
                        self.redraw_screen(f"[green]Session follow limit set to {self.max_follows}.[/green]")
                    except ValueError:
                        self.redraw_screen("[red]Invalid number for follow limit.[/red]")

            elif sub in ("delay", "pacing"):
                if len(val_args) == 1:
                    try:
                        d = float(val_args[0])
                        self.delay_min = d
                        self.delay_max = d + 2.0
                        self.redraw_screen(f"[green]Pacing delay set to {self.delay_min:.1f}s–{self.delay_max:.1f}s.[/green]")
                    except ValueError:
                        self.redraw_screen("[red]Invalid delay value.[/red]")
                elif len(val_args) >= 2:
                    try:
                        self.delay_min = float(val_args[0])
                        self.delay_max = float(val_args[1])
                        self.redraw_screen(f"[green]Pacing delay set to {self.delay_min:.1f}s–{self.delay_max:.1f}s.[/green]")
                    except ValueError:
                        self.redraw_screen("[red]Invalid delay values.[/red]")

            elif sub in ("dry-run", "dryrun"):
                if not val_args:
                    self.dry_run = not self.dry_run
                else:
                    self.dry_run = val_args[0].lower() in ("true", "1", "yes", "on", "enable")
                state_str = "[bold magenta]ENABLED (Simulation)[/bold magenta]" if self.dry_run else "[bold green]DISABLED (Live)[/bold green]"
                self.redraw_screen(f"[green]Dry-run mode {state_str}.[/green]")

            elif sub == "token":
                if not val_args:
                    self.redraw_screen("[yellow]Usage: set token <ghp_token>[/yellow]")
                else:
                    self.token = val_args[0]
                    self.client.set_token(self.token)
                    self.initialize()
                    self.redraw_screen("[green]GitHub Token updated and profile reloaded.[/green]")

            else:
                self.redraw_screen(f"[yellow]Unknown setting '{sub}'. Type 'help' for options.[/yellow]")

        elif cmd == "run":
            if not self.target_username:
                self.redraw_screen("[red]No target user set! Use 'set target <username>' first.[/red]")
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
            self.redraw_screen(f"[yellow]Unknown command '{cmd}'. Type 'help' for list of commands.[/yellow]")

        return True

    def start_repl(self) -> None:
        """Starts the interactive prompt loop in the Alternate Screen Buffer."""
        try:
            enter_alternate_screen()
            self.initialize()
            self.redraw_screen()

            while True:
                try:
                    prompt_str = f"  \033[1;96mgithub-follow\033[0m \033[90m❯\033[0m "
                    cmd_line = input(prompt_str)
                    should_continue = self.handle_command(cmd_line)
                    if not should_continue:
                        break
                except (KeyboardInterrupt, EOFError):
                    break
        finally:
            exit_alternate_screen()
            console.print("\n  [cyan]Exited GitHub Follow TUI. Goodbye! 👋[/cyan]\n")


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
            console.print("\n[bold yellow]GitHub Personal Access Token not found in .env![/bold yellow]")
            try:
                token = input("Enter GITHUB_TOKEN: ").strip()
            except (KeyboardInterrupt, EOFError):
                sys.exit(0)
        
        if not token:
            console.print("[red][ERROR][/red] GitHub Token is required! Provide it via --token or GITHUB_TOKEN in .env")
            console.print("\n[bold]How to create a GitHub token:[/bold]")
            console.print(" 1. Go to https://github.com/settings/tokens")
            console.print(" 2. Generate a Classic Token with scope 'user:follow', or a Fine-grained token with Follow (Read & Write)")
            console.print(" 3. Put it in .env file as: GITHUB_TOKEN=ghp_xxx\n")
            sys.exit(1)

    if is_interactive:
        session = InteractiveSession(token=token, default_target=args.target)
        if args.clear_state:
            session.state_mgr.clear()
        session.start_repl()
        return

    # Non-interactive / One-line CLI Mode
    if not args.target:
        console.print("[red][ERROR][/red] Target GitHub username is required in CLI mode! Use --target <username> or run interactively.")
        sys.exit(1)

    state_mgr = StateManager(state_file=args.state_file)
    if args.clear_state:
        console.print("[yellow][WARN][/yellow] Clearing existing state history...")
        state_mgr.clear()
        console.print("[green][SUCCESS][/green] State cleared.")

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
