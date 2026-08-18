"""
Workflow Runners Module
-----------------------
Coordinates the discovery, filtering, rate limit pacing,
and execution pipelines for follow and unfollow operations.
"""

import random
import signal
import time
from typing import Any, Dict, List, Optional
from rich.table import Table

from .client import GitHubAPIClient
from .storage import StateManager, WhitelistManager
from .ui import (
    build_dashboard_renderable,
    console,
    fetch_avatar_ansi,
    get_developer_watermark_divider,
    render_frame_in_place,
    render_neofetch_banner,
)


class AutoFollowRunner:
    """Execute the auto-follow workflow with candidate filtering and pacing delays."""

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

        # Operation counters
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
        """Run the follow process. Return the final status message."""
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

            # In interactive mode, update the live dashboard in place
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

            # Pre-fetch followers and following lists into memory for rapid lookups
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

            # Stream target followers and apply filter rules
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


class UnfollowRunner:
    """Execute the unfollow workflow with whitelist filtering and pacing delays."""

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
        """Run the unfollow process. Return the final status message."""
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

            # Collect unfollow candidates based on the selected mode
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

            # Remove whitelisted users from candidate list
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

            # Request confirmation before unfollowing all accounts
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
