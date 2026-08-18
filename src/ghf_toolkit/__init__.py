"""
GitHub Follower Toolkit
-----------------------
A rate-limit aware follow and unfollow automation suite with terminal dashboard.
"""

__version__ = "1.2.0"
APP_NAME = "FollowerToolkit"
APP_VERSION = f"v{__version__}"

from .client import GitHubAPIClient
from .storage import (
    ConfigManager,
    StateManager,
    WhitelistManager,
    get_config_dir,
    get_default_state_path,
    get_default_whitelist_path,
    get_saved_token,
    resolve_token,
    save_token,
)
from .runners import AutoFollowRunner, UnfollowRunner
from .ui import (
    InteractiveSession,
    build_dashboard_renderable,
    console,
    fetch_avatar_ansi,
    image_to_ansi_halfblocks,
    render_neofetch_banner,
    set_terminal_title,
)
from .cli import main

__all__ = [
    "__version__",
    "APP_NAME",
    "APP_VERSION",
    "GitHubAPIClient",
    "ConfigManager",
    "StateManager",
    "WhitelistManager",
    "get_config_dir",
    "get_default_state_path",
    "get_default_whitelist_path",
    "get_saved_token",
    "save_token",
    "resolve_token",
    "AutoFollowRunner",
    "UnfollowRunner",
    "InteractiveSession",
    "build_dashboard_renderable",
    "render_neofetch_banner",
    "image_to_ansi_halfblocks",
    "fetch_avatar_ansi",
    "set_terminal_title",
    "console",
    "main",
]
