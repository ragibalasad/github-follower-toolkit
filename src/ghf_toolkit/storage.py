"""
Storage and Configuration Module
--------------------------------
Handles application state, whitelist data, configuration files, and token resolution.
"""

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
from dotenv import load_dotenv


def get_config_dir() -> Path:
    """Return the application configuration directory path based on the operating system."""
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = base / "ghf-toolkit"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_saved_token() -> str:
    """Read the saved GitHub token from the configuration file."""
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
    """Save the GitHub token to the configuration file with restricted permissions (0600 on POSIX)."""
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
        print(f"[WARN] Could not save token to global config: {e}", file=sys.stderr)


def resolve_token(cli_token: Optional[str] = None) -> str:
    """
    Resolve the GitHub token using the following priority order:
    1. Command-line argument (--token)
    2. Local .env file
    3. Environment variable (GITHUB_TOKEN)
    4. Configuration file (config.json)
    """
    if cli_token:
        return cli_token.strip()

    load_dotenv()
    env_token = os.getenv("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token

    return get_saved_token()


def get_default_state_path() -> Path:
    """Return the path to the follow state file. Migrates local file to global path if found."""
    global_path = get_config_dir() / "follow_state.json"
    local_path = Path(".follow_state.json")
    if not global_path.exists() and local_path.exists():
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(global_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    return global_path


def get_default_whitelist_path() -> Path:
    """Return the path to the whitelist file. Migrates local file to global path if found."""
    global_path = get_config_dir() / "whitelist.json"
    local_path = Path(".whitelist.json")
    if not global_path.exists() and local_path.exists():
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(global_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    return global_path


class ConfigManager:
    """Manage persistent configuration data in config.json."""

    def __init__(self, config_file: Optional[Union[str, Path]] = None) -> None:
        self.config_path = Path(config_file) if config_file else (get_config_dir() / "config.json")
        self.config: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if self.config_path.exists() and self.config_path.stat().st_size > 0:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            except Exception as e:
                print(f"[WARN] Could not load config from {self.config_path}: {e}", file=sys.stderr)

    def save(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.config_path.with_name(f"{self.config_path.name}.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)

            if hasattr(os, "chmod") and sys.platform != "win32":
                try:
                    os.chmod(temp_file, 0o600)
                except Exception:
                    pass

            temp_file.replace(self.config_path)

            if hasattr(os, "chmod") and sys.platform != "win32":
                try:
                    os.chmod(self.config_path, 0o600)
                except Exception:
                    pass
        except Exception as e:
            print(f"[ERROR] Failed to save config to {self.config_path}: {e}", file=sys.stderr)

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value
        self.save()

    def set_token(self, token: str) -> None:
        self.set("github_token", token.strip())

    def get_token(self) -> str:
        return str(self.config.get("github_token", "")).strip()


class StateManager:
    """Manage persistent follow history to prevent repeated follow requests."""

    def __init__(self, state_file: Optional[Union[str, Path]] = None) -> None:
        self.state_path = Path(state_file) if state_file else get_default_state_path()
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
                print(f"[WARN] Could not load state from {self.state_path}: {e}", file=sys.stderr)

    def save(self) -> None:
        try:
            self.state["last_run"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.state_path.with_name(f"{self.state_path.name}.tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            temp_file.replace(self.state_path)
        except Exception as e:
            print(f"[ERROR] Failed to save state to {self.state_path}: {e}", file=sys.stderr)

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


class WhitelistManager:
    """Manage the list of protected GitHub accounts stored in whitelist.json."""

    def __init__(self, whitelist_file: Optional[Union[str, Path]] = None) -> None:
        self.whitelist_path = Path(whitelist_file) if whitelist_file else get_default_whitelist_path()
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
                print(f"[WARN] Could not load whitelist from {self.whitelist_path}: {e}", file=sys.stderr)

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
            print(f"[ERROR] Failed to save whitelist to {self.whitelist_path}: {e}", file=sys.stderr)

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
