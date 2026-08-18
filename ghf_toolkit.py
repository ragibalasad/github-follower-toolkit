#!/usr/bin/env python3
"""
GitHub Follower Toolkit (Fastfetch TUI powered by Rich)
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
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Set, Tuple

try:
    import requests
    from dotenv import load_dotenv
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markup import escape
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

__version__ = "1.0.2"
APP_NAME = "FollowerToolkit"
APP_VERSION = f"v{__version__}"


def enter_alternate_screen() -> None:
    """Clears the screen and positions cursor at top-left while preserving terminal scrollback."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def exit_alternate_screen() -> None:
    """Restores cursor visibility."""
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def render_frame_in_place(renderable: Any, show_cursor: bool = False) -> None:
    """
    Renders a Rich renderable directly to stdout in a single atomic write.
    Uses cursor-home (\033[H) + line-clear (\033[K) on every line to overwrite in place
    with ZERO blank-frame flicker, stuttering, residual ghost characters, or scrollback pollution.
    """
    buf = io.StringIO()
    render_console = Console(file=buf, force_terminal=True, color_system=console.color_system, highlight=False, width=console.width)
    render_console.print(renderable)
    raw_output = buf.getvalue()

    # Append \033[K (clear to end of line) to each line so shorter lines erase trailing characters from previous frames
    lines = raw_output.splitlines()
    cleared_output = "\n".join(line + "\033[K" for line in lines)

    cursor_code = "\033[?25h" if show_cursor else "\033[?25l"
    sys.stdout.write(f"\033[H{cursor_code}{cleared_output}\n\033[J")
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
        op_type = live_stats.get("op_type", "follow")
        if op_type == "unfollow":
            unfollowed = live_stats.get("unfollowed_success", 0)
            target_total = live_stats.get("target_total", max_follows) or 1
            whitelisted = live_stats.get("skipped_whitelisted", 0)
            pct = min(1.0, unfollowed / max(1, target_total))
            pct_int = int(pct * 100)

            progress_bar = ProgressBar(
                total=target_total,
                completed=unfollowed,
                width=20,
                style="bright_black",
                complete_style="bold magenta"
            )
            progress_grid = Table.grid(padding=(0, 1))
            progress_grid.add_column(justify="left")
            progress_grid.add_column(justify="left")
            progress_grid.add_column(justify="left")

            stats_text = Text(" ")
            stats_text.append(f"[{unfollowed}/{target_total}]", style="bold magenta")
            stats_text.append(f" ({pct_int}%)", style="bold cyan")
            stats_text.append(" | ", style="dim bright_black")
            stats_text.append(f"Protected (Whitelist): {whitelisted}", style="bold yellow")

            progress_grid.add_row(
                Text("  Progress: ", style="bold white"),
                progress_bar,
                stats_text
            )
            renderables.append(Text(""))
            renderables.append(progress_grid)
        else:
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
# Global Configuration & Credential Helpers
# ==============================================================================

def get_config_dir() -> Path:
    """Returns ~/.config/ghf-toolkit on Linux/macOS or %APPDATA%/ghf-toolkit on Windows."""
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = base / "ghf-toolkit"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_saved_token() -> str:
    """Reads saved GitHub token from global configuration if available."""
    cfg_file = get_config_dir() / "config.json"
    if cfg_file.exists() and cfg_file.stat().st_size > 0:
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("github_token", "")).strip()
        except Exception:
            pass
    return ""


def save_token(token: str) -> None:
    """Saves GitHub token to global configuration (~/.config/ghf-toolkit/config.json)."""
    try:
        cfg_dir = get_config_dir()
        cfg_file = cfg_dir / "config.json"
        data: Dict[str, Any] = {}
        if cfg_file.exists() and cfg_file.stat().st_size > 0:
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["github_token"] = token.strip()
        temp_file = cfg_dir / "config.json.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if hasattr(os, "chmod") and sys.platform != "win32":
            try:
                os.chmod(temp_file, 0o600)
            except Exception:
                pass
        temp_file.replace(cfg_file)
        if hasattr(os, "chmod") and sys.platform != "win32":
            try:
                os.chmod(cfg_file, 0o600)
            except Exception:
                pass
    except Exception as e:
        console.print(f"[yellow][WARN][/yellow] Could not save token to global config: {e}")


def resolve_token(cli_token: Optional[str] = None) -> str:
    """
    Resolves GITHUB_TOKEN following priority:
    1. CLI Argument (--token)
    2. Local .env file (via python-dotenv)
    3. System Environment Variable (GITHUB_TOKEN)
    4. Global config (~/.config/ghf-toolkit/config.json)
    """
    if cli_token:
        return cli_token.strip()

    load_dotenv()
    env_token = os.getenv("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token

    return get_saved_token()


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
        if self.state_path.exists() and self.state_path.stat().st_size > 0:
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.state.update(data)
            except Exception as e:
                console.print(f"[yellow][WARN][/yellow] Could not load state from {self.state_path}: {e}.")

    def save(self) -> None:
        try:
            self.state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.state_path.with_name(f"{self.state_path.name}.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            temp_file.replace(self.state_path)
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
# Whitelist Manager
# ==============================================================================

class WhitelistManager:
    """Manages protected GitHub users stored in .whitelist.json."""

    def __init__(self, whitelist_file: str = ".whitelist.json") -> None:
        self.whitelist_path = Path(whitelist_file)
        self.whitelist: Set[str] = set()
        self.load()

    def load(self) -> None:
        if self.whitelist_path.exists() and self.whitelist_path.stat().st_size > 0:
            try:
                with open(self.whitelist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.whitelist = {u.strip().lstrip("@").lower() for u in data if isinstance(u, str) and u.strip()}
                    elif isinstance(data, dict) and "whitelist" in data:
                        self.whitelist = {u.strip().lstrip("@").lower() for u in data["whitelist"] if isinstance(u, str) and u.strip()}
            except Exception as e:
                console.print(f"[yellow][WARN][/yellow] Could not load whitelist from {self.whitelist_path}: {e}.")

    def save(self) -> None:
        try:
            self.whitelist_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.whitelist_path.with_name(f"{self.whitelist_path.name}.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump({
                    "whitelist": sorted(list(self.whitelist)),
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }, f, indent=2)
            temp_file.replace(self.whitelist_path)
        except Exception as e:
            console.print(f"[red][ERROR][/red] Failed to save whitelist to {self.whitelist_path}: {e}")

    def is_whitelisted(self, username: str) -> bool:
        return username.strip().lstrip("@").lower() in self.whitelist

    def add(self, *usernames: str) -> List[str]:
        added: List[str] = []
        for u in usernames:
            cleaned = u.strip().lstrip("@").lower()
            if cleaned and cleaned not in self.whitelist:
                self.whitelist.add(cleaned)
                added.append(cleaned)
        if added:
            self.save()
        return added

    def remove(self, *usernames: str) -> List[str]:
        removed: List[str] = []
        for u in usernames:
            cleaned = u.strip().lstrip("@").lower()
            if cleaned in self.whitelist:
                self.whitelist.remove(cleaned)
                removed.append(cleaned)
        if removed:
            self.save()
        return removed

    def list(self) -> List[str]:
        return sorted(list(self.whitelist))

    def clear(self) -> None:
        self.whitelist.clear()
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
        safe_user = urllib.parse.quote(username.strip().lstrip("@"))
        resp = self.request("GET", f"/users/{safe_user}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            console.print(f"[red][ERROR][/red] Failed to fetch user '{username}': HTTP {resp.status_code}")
            return None
        return resp.json()

    def fetch_all_followers_set(self, username: Optional[str] = None) -> Set[str]:
        if username is None:
            endpoint = "/user/followers"
        else:
            safe_user = urllib.parse.quote(username.strip().lstrip("@"))
            endpoint = f"/users/{safe_user}/followers"
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
        safe_user = urllib.parse.quote(username.strip().lstrip("@"))
        endpoint = f"/users/{safe_user}/followers"
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
        safe_user = urllib.parse.quote(username.strip().lstrip("@"))
        endpoint = f"/user/following/{safe_user}"
        resp = self.request("PUT", endpoint)
        if resp.status_code in (204, 200):
            return True
        else:
            console.print(f"[red][ERROR][/red] Failed to follow '{username}': HTTP {resp.status_code} - {resp.text}")
            return False

    def unfollow_user(self, username: str) -> bool:
        safe_user = urllib.parse.quote(username.strip().lstrip("@"))
        endpoint = f"/user/following/{safe_user}"
        resp = self.request("DELETE", endpoint)
        if resp.status_code in (204, 200):
            return True
        else:
            console.print(f"[red][ERROR][/red] Failed to unfollow '{username}': HTTP {resp.status_code} - {resp.text}")
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
                    render_frame_in_place(dashboard, show_cursor=False)

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
# Unfollow Pipeline Runner
# ==============================================================================

class UnfollowRunner:
    """Coordinates discovering candidates, whitelist filtering, rate-limit pacing, and execution for unfollow."""

    def __init__(
        self,
        client: GitHubAPIClient,
        whitelist_mgr: WhitelistManager,
        mode: str = "non-followers",  # "non-followers", "all", "user"
        target_user: Optional[str] = None,
        limit: Optional[int] = None,
        delay_min: float = 2.0,
        delay_max: float = 4.0,
        dry_run: bool = False,
        interactive: bool = False,
        verbose: bool = False,
        force: bool = False,
        auth_user_cache: Optional[Dict[str, Any]] = None,
        avatar_lines_cache: Optional[List[str]] = None
    ) -> None:
        self.client = client
        self.whitelist_mgr = whitelist_mgr
        self.mode = mode
        self.target_user = target_user
        self.limit = limit
        self.delay_min = max(0.5, delay_min)
        self.delay_max = max(self.delay_min, delay_max)
        self.dry_run = dry_run
        self.interactive = interactive
        self.verbose = verbose
        self.force = force
        self.interrupted = False
        self.auth_user_cache = auth_user_cache
        self.avatar_lines_cache = avatar_lines_cache

        self.stats = {
            "op_type": "unfollow",
            "total_candidates": 0,
            "unfollowed_success": 0,
            "unfollowed_failed": 0,
            "skipped_whitelisted": 0,
            "target_total": 0
        }

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def run(self) -> str:
        """Runs the unfollow pipeline. Returns status message."""
        old_sigint = signal.getsignal(signal.SIGINT)
        old_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)

        final_status = ""

        try:
            auth_user = self.auth_user_cache or self.client.get_authenticated_user()
            auth_login = auth_user["login"]
            avatar_lines = self.avatar_lines_cache or fetch_avatar_ansi(auth_user.get("avatar_url"), self.client.session)

            use_live = (self.interactive and not self.verbose)

            def update_live_ui(status_msg: str) -> None:
                if use_live:
                    dashboard = build_dashboard_renderable(
                        auth_user=auth_user,
                        target_info={"login": f"ufollow ({self.mode})", "followers": self.stats["target_total"]},
                        api_remaining=self.client.rate_limit_remaining,
                        api_limit=self.client.rate_limit_limit,
                        max_follows=self.stats["target_total"] or 1,
                        delay_min=self.delay_min,
                        delay_max=self.delay_max,
                        dry_run=self.dry_run,
                        avatar_lines=avatar_lines,
                        live_stats=self.stats,
                        status_msg=status_msg
                    )
                    render_frame_in_place(dashboard, show_cursor=False)

            if use_live:
                update_live_ui("[cyan]Fetching relationship & whitelist cache...[/cyan]")
            elif not self.interactive or self.verbose:
                render_neofetch_banner(
                    auth_user=auth_user,
                    target_info={"login": f"ufollow ({self.mode})", "followers": 0},
                    api_remaining=self.client.rate_limit_remaining,
                    api_limit=self.client.rate_limit_limit,
                    max_follows=self.limit or 0,
                    delay_min=self.delay_min,
                    delay_max=self.delay_max,
                    dry_run=self.dry_run,
                    avatar_lines=avatar_lines
                )
                console.print(f"[bold]── Step 1: Pre-fetching Relationship & Whitelist Cache ───────────────[/bold]")

            # 1. Collect candidates
            candidates: List[str] = []

            if self.mode == "user" and self.target_user:
                cand = self.target_user.lstrip("@").lower()
                candidates = [cand]
            else:
                if not use_live:
                    console.print(f"[blue][INFO][/blue] Fetching accounts you follow (@{auth_login})...")
                following_set = self.client.fetch_all_following_set()
                if not use_live:
                    console.print(f"[green][SUCCESS][/green] Cached {len(following_set)} account(s) you follow.")

                if self.mode == "non-followers":
                    if not use_live:
                        console.print(f"[blue][INFO][/blue] Fetching accounts that follow you (@{auth_login})...")
                    followers_set = self.client.fetch_all_followers_set()
                    if not use_live:
                        console.print(f"[green][SUCCESS][/green] Cached {len(followers_set)} follower(s).")
                    candidates = [u for u in following_set if u not in followers_set]
                elif self.mode == "all":
                    candidates = list(following_set)

            # Filter candidates against Whitelist
            filtered_candidates: List[str] = []
            for u in candidates:
                if self.whitelist_mgr.is_whitelisted(u):
                    self.stats["skipped_whitelisted"] += 1
                else:
                    filtered_candidates.append(u)

            total_found = len(filtered_candidates)
            self.stats["total_candidates"] = total_found
            max_to_process = min(total_found, self.limit) if self.limit else total_found
            self.stats["target_total"] = max_to_process

            if total_found == 0:
                msg = f"[green]No candidates to unfollow in '{self.mode}' mode (Whitelisted protected: {self.stats['skipped_whitelisted']}).[/green]"
                if use_live:
                    update_live_ui(msg)
                else:
                    console.print(msg)
                return msg

            # Safety Confirmation for 'all' mode
            if self.mode == "all" and not self.force and not self.dry_run:
                if not self.interactive:
                    console.print(f"[bold red][WARNING][/bold red] This will unfollow ALL {max_to_process} accounts!")
                    try:
                        confirm = input("Are you sure you want to proceed? [y/N]: ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        confirm = "no"
                    if confirm not in ("y", "yes"):
                        return "[yellow]Unfollow cancelled by user.[/yellow]"

            if not use_live:
                console.print(f"\n[bold]── Step 2: Unfollowing Candidates (Target: {max_to_process}, Delay: {self.delay_min:.1f}s-{self.delay_max:.1f}s) ───────[/bold]")

            candidates_to_unfollow = filtered_candidates[:max_to_process]

            for idx, username in enumerate(candidates_to_unfollow, 1):
                if self.interrupted:
                    final_status = f"[yellow]Stopped by user (Ctrl+C). Unfollowed {self.stats['unfollowed_success']} accounts.[/yellow]"
                    update_live_ui(final_status)
                    break

                step_info = f"[{idx}/{max_to_process}] Unfollowing @{username}..."
                if self.verbose or not self.interactive:
                    console.print(f"[blue][INFO][/blue] {step_info}")

                if self.dry_run:
                    self.stats["unfollowed_success"] += 1
                    status_text = f"[bold magenta][DRY-RUN][/bold magenta] Simulated unfollow @{username}."
                    if use_live:
                        update_live_ui(status_text)
                    elif self.verbose:
                        console.print(f"[magenta][DRY-RUN][/magenta] Would unfollow @{username}.")
                    continue

                success = self.client.unfollow_user(username)
                if success:
                    self.stats["unfollowed_success"] += 1
                    status_text = f"[green][SUCCESS][/green] Unfollowed @{username}."
                    if not use_live and self.verbose:
                        console.print(f"[green][SUCCESS][/green] Unfollowed @{username}!")
                else:
                    self.stats["unfollowed_failed"] += 1
                    status_text = f"[red][ERROR][/red] Failed to unfollow @{username}."

                if idx < max_to_process:
                    sleep_sec = random.uniform(self.delay_min, self.delay_max)
                    pacing_msg = f"{status_text} (Pacing: sleeping {sleep_sec:.2f}s)"
                    update_live_ui(pacing_msg)
                    if not use_live and self.verbose:
                        console.print(f"       Pacing delay: sleeping {sleep_sec:.2f}s...")
                    time.sleep(sleep_sec)
                else:
                    update_live_ui(status_text)

            if not self.interrupted:
                mode_label = "Simulated" if self.dry_run else "Successfully"
                final_status = f"[green]{mode_label} unfollowed {self.stats['unfollowed_success']} user(s). Whitelisted protected: {self.stats['skipped_whitelisted']}.[/green]"
                update_live_ui(final_status)

            if not self.interactive or self.verbose:
                self._print_summary(total_found)

            return final_status

        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

    def _print_summary(self, total_found: int) -> None:
        table = Table(title="Unfollow Execution Summary", border_style="cyan", show_header=False)
        table.add_column("Metric", style="bold white")
        table.add_column("Value", style="cyan")

        table.add_row("Total Target Candidates", str(total_found))
        table.add_row("Successfully Unfollowed", f"[green]{self.stats['unfollowed_success']}[/green]")
        if self.stats["unfollowed_failed"] > 0:
            table.add_row("Failed Unfollows", f"[red]{self.stats['unfollowed_failed']}[/red]")
        table.add_row("Protected by Whitelist", f"[yellow]{self.stats['skipped_whitelisted']}[/yellow]")
        table.add_row("Remaining API Quota", f"{self.client.rate_limit_remaining}/{self.client.rate_limit_limit}")

        console.print("")
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
        self.whitelist_mgr = WhitelistManager()

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
        """Renders the full Rich dashboard at (1,1) without flicker."""
        if status_msg is not None:
            self.current_status = status_msg

        if not self.auth_user:
            return

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
        render_frame_in_place(dashboard, show_cursor=True)

    def print_help_screen(self) -> None:
        try:
            with console.pager(styles=True):
                console.print("\n[bold cyan]GitHub Follower Toolkit - Command Reference Manual[/bold cyan]\n")
                console.print("[bold cyan]SYNOPSIS[/bold cyan]")
                console.print("  [bold white]run[/bold white] [-v]")
                syn_uf = escape("[-n | -a] [-l <N>] [-d <min> [max]] [-s] [-f] [-v]")
                console.print(f"  [bold white]ufollow[/bold white] [bold green]{syn_uf}[/bold green]")
                console.print(f"  [bold white]ufollow[/bold white] [bold green]<username>[/bold green] [bold green]{escape('[-s]')}[/bold green]")
                console.print("  [bold white]wl[/bold white] [bold green]<add | rm>[/bold green] <username...>")
                console.print("  [bold white]wl[/bold white] [bold green]<list | clear>[/bold green]")
                console.print("  [bold white]set[/bold white] [bold green]<target | limit | delay | dry-run | token>[/bold green] <value>")
                console.print("  [bold white]show[/bold white] | [bold white]clear-state[/bold white] | [bold white]help[/bold white] | [bold white]exit[/bold white]\n")

                console.print("[bold cyan]COMMANDS[/bold cyan]")
                commands = [
                    ("run", "Execute auto-follow pipeline for current target user"),
                    ("ufollow, uf, rm", "Execute unfollow pipeline with rate-limit protection"),
                    ("wl, whitelist", "Manage persistent protected VIP whitelist (.whitelist.json)"),
                    ("set", "Modify runtime session configuration parameters"),
                    ("show, status", "Refresh profile specs, API quota, and system dashboard"),
                    ("clear-state", "Purge local follow history cache (.follow_state.json)"),
                    ("help, ?", "Display this command reference manual"),
                    ("exit, quit, q", "Terminate interactive toolkit session"),
                ]
                for cmd_name, desc in commands:
                    pad = " " * max(2, 22 - len(cmd_name))
                    console.print(f"  [bold green]{cmd_name}[/bold green]{pad}{desc}")
                console.print("")

                console.print("[bold cyan]UFOLLOW OPTIONS[/bold cyan]")
                uf_options = [
                    ("-n, --non-followers", "Target accounts that do not follow back (default)"),
                    ("-a, --all", "Target all accounts currently followed"),
                    ("-l, --limit <N>", "Maximum number of accounts to process in session"),
                    ("-d, --delay <min> [max]", "Random jitter pacing delay in seconds (default: 2.0 4.0)"),
                    ("-s, --dry-run", "Simulate execution without modifying following list"),
                    ("-f, --force", "Bypass confirmation prompt on destructive actions (-a)"),
                    ("-v, --verbose", "Stream detailed execution logs in real-time"),
                ]
                for opt_name, desc in uf_options:
                    pad = " " * max(2, 26 - len(opt_name))
                    console.print(f"  [bold yellow]{escape(opt_name)}[/bold yellow]{pad}{desc}")
                console.print("")

                console.print("[bold cyan]CONFIGURATION KEYS (set)[/bold cyan]")
                set_keys = [
                    ("target <username>", "Target GitHub account to harvest followers from"),
                    ("limit <N>", "Default session follow/unfollow limit"),
                    ("delay <min> [max]", "Default pacing delay range in seconds"),
                    ("dry-run <on|off>", "Toggle global simulation mode"),
                    ("token <ghp_token>", "Switch active GitHub Personal Access Token"),
                ]
                for key_name, desc in set_keys:
                    pad = " " * max(2, 22 - len(key_name))
                    console.print(f"  [bold green]{escape(key_name)}[/bold green]{pad}{desc}")
                console.print("")

                console.print("[bold cyan]EXAMPLES[/bold cyan]")
                console.print("  [dim]#[/dim] Follow target's followers:")
                console.print("    [bold white]set target torvalds[/bold white]")
                console.print("    [bold white]run[/bold white]\n")
                console.print("  [dim]#[/dim] Unfollow up to 30 non-followers:")
                console.print("    [bold white]ufollow -n -l 30[/bold white]\n")
                console.print("  [dim]#[/dim] Add accounts to protected whitelist:")
                console.print("    [bold white]wl add torvalds octocat[/bold white]\n")
                console.print("  [dim]#[/dim] Mass unfollow all followed accounts:")
                console.print("    [bold white]ufollow -a -f[/bold white]\n")
        finally:
            self.redraw_screen()

    def _display_whitelist_table(self) -> None:
        self.redraw_screen()
        wl_users = self.whitelist_mgr.list()
        if not wl_users:
            console.print("\n  [yellow]Protected whitelist is currently empty.[/yellow]\n")
            return

        table = Table(title=f"Protected Whitelist Accounts ({len(wl_users)})", border_style="cyan", show_header=True)
        table.add_column("#", style="dim", width=4, justify="right")
        table.add_column("Username", style="bold white")
        table.add_column("Status", style="bold yellow")
        table.add_column("GitHub Profile", style="dim blue")

        for idx, username in enumerate(wl_users, 1):
            table.add_row(
                str(idx),
                f"@{username}",
                "Protected (VIP)",
                f"https://github.com/{username}"
            )

        console.print("")
        console.print(table)
        console.print("")

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
            self.redraw_screen("[cyan]Fetching latest live stats and API quota from GitHub...[/cyan]")
            try:
                self.auth_user = self.client.get_authenticated_user()
                if self.target_username:
                    self.target_info = self.client.get_user_info(self.target_username)
                now_str = datetime.datetime.now().strftime("%H:%M:%S")
                self.redraw_screen(f"[green]Stats and API quota synced from GitHub ({now_str}).[/green]")
            except Exception as e:
                self.redraw_screen(f"[red]Failed to refresh stats from GitHub: {e}[/red]")

        elif cmd == "clear-state":
            self.state_mgr.clear()
            self.redraw_screen("[green]Local history cache cleared successfully.[/green]")

        elif cmd in ("wl", "whitelist", "keep", "pin", "vip"):
            if not args:
                self._display_whitelist_table()
                return True

            sub = args[0].lower()
            val_args = args[1:]

            if sub in ("add", "-a", "+"):
                if not val_args:
                    self.redraw_screen("[yellow]Usage: wl add <username1> [username2...][/yellow]")
                else:
                    added = self.whitelist_mgr.add(*val_args)
                    if added:
                        users_str = ", ".join(f"@{u}" for u in added)
                        self.redraw_screen(f"[green]Added to whitelist: {users_str}[/green]")
                    else:
                        self.redraw_screen("[yellow]User(s) already in whitelist.[/yellow]")

            elif sub in ("rm", "del", "delete", "remove", "-d", "-"):
                if not val_args:
                    self.redraw_screen("[yellow]Usage: wl rm <username1> [username2...][/yellow]")
                else:
                    removed = self.whitelist_mgr.remove(*val_args)
                    if removed:
                        users_str = ", ".join(f"@{u}" for u in removed)
                        self.redraw_screen(f"[green]Removed from whitelist: {users_str}[/green]")
                    else:
                        self.redraw_screen("[yellow]User(s) not found in whitelist.[/yellow]")

            elif sub in ("ls", "list", "show"):
                self._display_whitelist_table()

            elif sub == "clear":
                self.whitelist_mgr.clear()
                self.redraw_screen("[green]Whitelist cleared successfully.[/green]")

            else:
                self.redraw_screen(f"[yellow]Unknown whitelist subcommand '{sub}'. Usage: wl <add|rm|list|clear>[/yellow]")

        elif cmd in ("ufollow", "uf", "rm", "unfollow"):
            # Parse flags for ufollow
            mode = "non-followers"
            target_user = None
            limit = self.max_follows
            dry_run = self.dry_run
            force = False
            verbose = False
            delay_min = self.delay_min
            delay_max = self.delay_max

            i = 0
            while i < len(args):
                arg = args[i]
                if arg in ("-n", "--non-followers", "--non-mutuals"):
                    mode = "non-followers"
                elif arg in ("-a", "--all"):
                    mode = "all"
                elif arg in ("-f", "--force", "-y", "--yes"):
                    force = True
                elif arg in ("-s", "--dry-run", "--simulate"):
                    dry_run = True
                elif arg in ("--live",):
                    dry_run = False
                elif arg in ("-v", "--verbose"):
                    verbose = True
                elif arg in ("-l", "--limit", "-m", "--max"):
                    if i + 1 < len(args):
                        try:
                            limit = int(args[i + 1])
                            i += 1
                        except ValueError:
                            pass
                elif arg in ("-d", "--delay"):
                    if i + 2 < len(args) and not args[i + 1].startswith("-") and not args[i + 2].startswith("-"):
                        try:
                            delay_min = float(args[i + 1])
                            delay_max = float(args[i + 2])
                            i += 2
                        except ValueError:
                            pass
                    elif i + 1 < len(args) and not args[i + 1].startswith("-"):
                        try:
                            delay_min = float(args[i + 1])
                            delay_max = delay_min + 2.0
                            i += 1
                        except ValueError:
                            pass
                elif arg.startswith("@") or (not arg.startswith("-") and not target_user and mode != "all"):
                    target_user = arg.lstrip("@")
                    mode = "user"
                i += 1

            if mode == "all" and not force and not dry_run:
                self.redraw_screen()
                console.print("\n  [bold red][WARNING][/bold red] This will unfollow ALL accounts you follow (excluding whitelist)!")
                try:
                    ans = input("  Are you sure you want to proceed? [y/N]: ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    ans = "no"
                if ans not in ("y", "yes"):
                    self.redraw_screen("[yellow]Unfollow all cancelled by user.[/yellow]")
                    return True

            runner = UnfollowRunner(
                client=self.client,
                whitelist_mgr=self.whitelist_mgr,
                mode=mode,
                target_user=target_user,
                limit=limit,
                delay_min=delay_min,
                delay_max=delay_max,
                dry_run=dry_run,
                interactive=True,
                verbose=verbose,
                force=force,
                auth_user_cache=self.auth_user,
                avatar_lines_cache=self.avatar_lines
            )
            result_status = runner.run()
            if not verbose:
                self.redraw_screen(result_status)

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
                        self.redraw_screen(f"[green]Session limit set to {self.max_follows}.[/green]")
                    except ValueError:
                        self.redraw_screen("[red]Invalid number for limit.[/red]")

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
                    save_token(self.token)
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
                    mode_badge = "\001\033[1;35m\002[dry-run]\001\033[0m\002" if self.dry_run else "\001\033[1;32m\002[live]\033[0m\002"
                    prompt_str = f"  \001\033[1;96m\002ghf-toolkit\001\033[0m\002 {mode_badge} \001\033[90m\002❯\001\033[0m\002 "
                    cmd_line = input(prompt_str)
                    should_continue = self.handle_command(cmd_line)
                    if not should_continue:
                        break
                except (KeyboardInterrupt, EOFError):
                    break
        finally:
            exit_alternate_screen()
            console.print("\n  [cyan]Exited Follower Toolkit TUI. Goodbye![/cyan]\n")


# ==============================================================================
# CLI Argument Parser & Entry Point
# ==============================================================================

def parse_args() -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="High-efficiency, rate-limit aware GitHub Auto-Follow Script."
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {__version__}"
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
        default=None,
        help="GitHub Personal Access Token (or set GITHUB_TOKEN in env/config/.env)."
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
    # Check for CLI subcommands first
    if len(sys.argv) > 1:
        subcmd = sys.argv[1].lower()

        if subcmd in ("wl", "whitelist", "keep", "pin", "vip"):
            wl_mgr = WhitelistManager()
            wl_args = sys.argv[2:]
            if not wl_args or wl_args[0] in ("ls", "list", "show"):
                users = wl_mgr.list()
                if not users:
                    console.print("[yellow]Protected whitelist is currently empty.[/yellow]")
                else:
                    table = Table(title=f"Protected Whitelist Accounts ({len(users)})", border_style="cyan")
                    table.add_column("#", justify="right", style="dim", width=4)
                    table.add_column("Username", style="bold white")
                    table.add_column("Role", style="bold yellow")
                    for i, u in enumerate(users, 1):
                        table.add_row(str(i), f"@{u}", "Protected (VIP)")
                    console.print(table)
                return

            action = wl_args[0].lower()
            targets = wl_args[1:]
            if action in ("add", "-a", "+"):
                if not targets:
                    console.print("[yellow]Usage: ghf-toolkit wl add <user1> [user2...][/yellow]")
                else:
                    added = wl_mgr.add(*targets)
                    console.print(f"[green]Added {len(added)} user(s) to whitelist: {', '.join('@' + u for u in added)}[/green]")
            elif action in ("rm", "del", "delete", "remove", "-d", "-"):
                if not targets:
                    console.print("[yellow]Usage: ghf-toolkit wl rm <user1> [user2...][/yellow]")
                else:
                    removed = wl_mgr.remove(*targets)
                    console.print(f"[green]Removed {len(removed)} user(s) from whitelist: {', '.join('@' + u for u in removed)}[/green]")
            elif action == "clear":
                wl_mgr.clear()
                console.print("[green]Whitelist cleared.[/green]")
            else:
                console.print(f"[yellow]Unknown wl action '{action}'. Usage: wl <add|rm|ls|clear>[/yellow]")
            return


        elif subcmd in ("ufollow", "uf", "rm", "unfollow"):
            token = resolve_token()
            if not token:
                console.print("[red][ERROR][/red] GITHUB_TOKEN required for 'ufollow' command.")
                sys.exit(1)
            uf_parser = argparse.ArgumentParser(prog="ghf-toolkit ufollow", description="Unfollow non-followers or all accounts with safety whitelists.")
            uf_parser.add_argument("-n", "--non-followers", action="store_true", default=True, help="Unfollow non-followers (default)")
            uf_parser.add_argument("-a", "--all", action="store_true", help="Unfollow ALL accounts you follow")
            uf_parser.add_argument("user", nargs="?", default=None, help="Specific user to unfollow")
            uf_parser.add_argument("-l", "--limit", type=int, default=None, help="Max accounts to unfollow")
            uf_parser.add_argument("-d", "--delay-min", type=float, default=2.0, help="Min pacing delay (default: 2.0s)")
            uf_parser.add_argument("--delay-max", type=float, default=4.0, help="Max pacing delay (default: 4.0s)")
            uf_parser.add_argument("-s", "--dry-run", action="store_true", help="Simulate without making mutations")
            uf_parser.add_argument("-f", "--force", "-y", "--yes", action="store_true", help="Skip confirmation prompt")
            uf_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose streaming logs")

            uf_args = uf_parser.parse_args(sys.argv[2:])
            mode = "all" if uf_args.all else ("user" if uf_args.user else "non-followers")

            client = GitHubAPIClient(token=token, verbose=uf_args.verbose)
            wl_mgr = WhitelistManager()
            runner = UnfollowRunner(
                client=client,
                whitelist_mgr=wl_mgr,
                mode=mode,
                target_user=uf_args.user,
                limit=uf_args.limit,
                delay_min=uf_args.delay_min,
                delay_max=uf_args.delay_max,
                dry_run=uf_args.dry_run,
                interactive=False,
                verbose=uf_args.verbose,
                force=uf_args.force
            )
            runner.run()
            return

    # Standard / Interactive / Follow CLI Mode
    args = parse_args()
    is_interactive = args.interactive or (len(sys.argv) == 1 and not args.target)

    token = resolve_token(args.token)
    if not token:
        if is_interactive:
            console.print("\n[bold yellow]GitHub Personal Access Token not found in .env![/bold yellow]")
            try:
                token = input("Enter GITHUB_TOKEN: ").strip()
            except (KeyboardInterrupt, EOFError):
                sys.exit(0)
            if token:
                save_token(token)
                console.print("[green][SUCCESS][/green] Saved token to ~/.config/ghf-toolkit/config.json for future runs.\n")

        if not token:
            console.print("[red][ERROR][/red] GitHub Token is required! Provide it via --token, GITHUB_TOKEN, or ~/.config/ghf-toolkit/config.json")
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
