"""
Terminal User Interface (TUI) & Graphics Module
------------------------------------------------
Handles terminal screen buffers, ANSI TrueColor avatar conversion,
Rich dashboard layout cards, and the interactive command shell.
"""

import datetime
import io
import readline
import sys
import time
from typing import Any, Dict, List, Optional
import requests
from rich.console import Console, Group
from rich.markup import escape
from rich.progress import ProgressBar
from rich.table import Table
from rich.text import Text

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from . import __version__
from .client import GitHubAPIClient
from .storage import StateManager, WhitelistManager, save_token

# Rich console instance
console = Console(highlight=False)

APP_NAME = "FollowerToolkit"
APP_VERSION = f"v{__version__}"


def set_terminal_title(title: str) -> None:
    """Set the terminal window and tab title using ANSI OSC 0 escape codes."""
    if sys.stdout.isatty():
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()


def enter_alternate_screen() -> None:
    """Clear the terminal screen and reset the cursor position."""
    set_terminal_title("ghf-toolkit")
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def exit_alternate_screen() -> None:
    """Restore the terminal cursor visibility and clear window title."""
    set_terminal_title("")
    sys.stdout.write("\033[?25h")
    sys.stdout.flush()


def render_frame_in_place(renderable: Any, show_cursor: bool = False) -> None:
    """
    Write a rendered frame to standard output in place.
    Uses ANSI escape codes to redraw lines without scrolling or screen flicker.
    """
    buf = io.StringIO()
    render_console = Console(file=buf, force_terminal=True, color_system=console.color_system, highlight=False, width=console.width)
    render_console.print(renderable)
    raw_output = buf.getvalue()

    # Append ANSI clear-to-end-of-line escape code to each line to erase remaining characters
    lines = raw_output.splitlines()
    cleared_output = "\n".join(line + "\033[K" for line in lines)

    cursor_code = "\033[?25h" if show_cursor else "\033[?25l"
    sys.stdout.write(f"\033[H{cursor_code}{cleared_output}\n\033[J")
    sys.stdout.flush()


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
    """Convert image bytes into 24-bit TrueColor ANSI half-block (▀) text lines."""
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
    """Download the user avatar image and convert it to ANSI text lines."""
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
    """Render a divider line with developer information."""
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
    """Create a layout group containing the profile card, divider, progress bar, and status."""
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

    # Profile and quota table
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

    # Combine title, divider, and metrics table
    right_column = Group(
        Text.from_markup(f"[bold cyan]{APP_NAME} {APP_VERSION}[/bold cyan]"),
        Text("─" * 36, style="dim bright_black"),
        info_table
    )

    # Place avatar on left and account metrics on right
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

    # Render progress bar when statistics are available
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

    # Render current status message
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


class InteractiveSession:
    """Manage the interactive command-line shell and command loop."""

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

        # Cached user profile and avatar lines
        self.auth_user: Optional[Dict[str, Any]] = None
        self.avatar_lines: Optional[List[str]] = None
        self.current_status: str = "[dim]Ready. Type [green]'help'[/green] for commands or [green]'run'[/green] to start.[/dim]"

    def initialize(self) -> None:
        """Fetch initial user account and target user details."""
        try:
            self.auth_user = self.client.get_authenticated_user()
            self.avatar_lines = fetch_avatar_ansi(self.auth_user.get("avatar_url"), self.client.session)
            if self.target_username:
                self.target_info = self.client.get_user_info(self.target_username)
        except Exception as e:
            self.current_status = f"[red]Initialization error: {e}[/red]"

    def redraw_screen(self, status_msg: Optional[str] = None) -> None:
        """Render the full dashboard at the top of the terminal screen."""
        if status_msg is not None:
            self.current_status = status_msg

        if self.auth_user:
            auth_login = self.auth_user.get("login", "unknown")
            if self.target_username:
                set_terminal_title(f"ghf-toolkit — @{auth_login} (Target: @{self.target_username})")
            else:
                set_terminal_title(f"ghf-toolkit — @{auth_login}")

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
        """Process a user command from the interactive shell. Return False to exit."""
        parts = cmd_line.strip().split()
        if not parts:
            self.redraw_screen()
            return True

        cmd = parts[0].lower()
        args = parts[1:]

        # Lazy import to avoid circular dependencies
        from .runners import AutoFollowRunner, UnfollowRunner

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
            # Parse arguments for the ufollow command
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
        """Start the interactive command loop in the terminal alternate screen buffer."""
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
