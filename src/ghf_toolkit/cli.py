"""
Command Line Interface (CLI) Entry Point
----------------------------------------
Parses command line arguments, dispatches subcommands, and initiates
either the interactive REPL session or direct command execution.
"""

import argparse
import os
import sys
from dotenv import load_dotenv
from rich.table import Table

from . import __version__
from .client import GitHubAPIClient
from .runners import AutoFollowRunner, UnfollowRunner
from .storage import StateManager, WhitelistManager, resolve_token, save_token
from .ui import InteractiveSession, console


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
        default=None,
        help="Path to custom JSON state history file (default: ~/.config/ghf-toolkit/follow_state.json)."
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
    # Process subcommands before standard argument parsing
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

    # Standard follow and interactive mode
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

    # Direct CLI execution
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
