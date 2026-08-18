"""
GitHub API Client Module
------------------------
Handles authenticated HTTP communication with the GitHub REST API,
rate limit monitoring, pagination, and exponential retry backoff.
"""

import datetime
import random
import sys
import time
from typing import Any, Dict, Generator, Optional, Set
import requests


class GitHubAPIClient:
    """GitHub REST API client with rate limit tracking, pagination, and retry backoff."""

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
        """Pause execution until the reset time when the remaining API quota is 10 or fewer."""
        if self.rate_limit_remaining <= 10:
            now = int(time.time())
            sleep_duration = max(5, self.rate_limit_reset - now + 2)
            reset_dt = datetime.datetime.fromtimestamp(self.rate_limit_reset, tz=datetime.timezone.utc)
            print(f"[WARN] Primary rate limit nearly exhausted ({self.rate_limit_remaining} left).", file=sys.stderr)
            print(f"[WARN] Sleeping {sleep_duration}s until reset at {reset_dt.strftime('%H:%M:%S UTC')}...", file=sys.stderr)
            time.sleep(sleep_duration)

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> requests.Response:
        url = f"{self.BASE_URL}{endpoint}" if endpoint.startswith("/") else endpoint
        attempt = 0
        backoff = 3.0

        while attempt < max_retries:
            attempt += 1
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
                        resp_json = response.json() if callable(response.json) else {}
                    except Exception:
                        pass

                    raw_text = getattr(response, "text", "")
                    body_text = raw_text.lower() if isinstance(raw_text, str) else ""
                    msg_text = str(resp_json.get("message", "")).lower() if isinstance(resp_json, dict) else ""
                    full_msg = f"{body_text} {msg_text}"

                    has_retry_header = "Retry-After" in response.headers or "retry-after" in response.headers
                    is_secondary = "secondary rate limit" in full_msg or "abuse detection" in full_msg or "please wait" in full_msg or has_retry_header

                    if is_secondary or response.status_code == 429:
                        retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                        wait_sec = int(retry_after) if retry_after else (backoff + random.uniform(1.0, 3.0))
                        if self.verbose:
                            print(f"[WARN] Secondary rate limit triggered. Backing off for {wait_sec:.1f}s (Attempt {attempt}/{max_retries})...", file=sys.stderr)
                        time.sleep(wait_sec)
                        backoff *= 2.0
                        continue

                    if self.rate_limit_remaining == 0:
                        now = int(time.time())
                        sleep_duration = max(5, self.rate_limit_reset - now + 2)
                        if self.verbose:
                            print(f"[WARN] Primary rate limit hit 0. Sleeping {sleep_duration}s until reset...", file=sys.stderr)
                        time.sleep(sleep_duration)
                        continue

                return response

            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= max_retries:
                    raise exc
                wait_sec = backoff + random.uniform(1.0, 2.0)
                if self.verbose:
                    print(f"[WARN] Network glitch ({exc}). Retrying in {wait_sec:.1f}s (Attempt {attempt}/{max_retries})...", file=sys.stderr)
                time.sleep(wait_sec)
                backoff *= 2.0

        raise requests.RequestException(f"Failed {method} {url} after {max_retries} retries.")

    def get_authenticated_user(self) -> Dict[str, Any]:
        resp = self.request("GET", "/user")
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to authenticate with GitHub API (Status: {resp.status_code}, Msg: {resp.text})")
        return resp.json()

    def get_user_info(self, username: str) -> Optional[Dict[str, Any]]:
        clean_user = username.strip().lstrip("@")
        resp = self.request("GET", f"/users/{clean_user}")
        if resp.status_code == 200:
            return resp.json()
        return None

    def fetch_all_followers_set(self) -> Set[str]:
        followers: Set[str] = set()
        page = 1
        while True:
            resp = self.request("GET", "/user/followers", params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data or not isinstance(data, list):
                break
            for u in data:
                followers.add(u["login"].lower())
            if len(data) < 100:
                break
            page += 1
        return followers

    def fetch_all_following_set(self) -> Set[str]:
        following: Set[str] = set()
        page = 1
        while True:
            resp = self.request("GET", "/user/following", params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data or not isinstance(data, list):
                break
            for u in data:
                following.add(u["login"].lower())
            if len(data) < 100:
                break
            page += 1
        return following

    def stream_target_followers(self, target_username: str) -> Generator[Dict[str, Any], None, None]:
        clean_target = target_username.strip().lstrip("@")
        page = 1
        while True:
            resp = self.request("GET", f"/users/{clean_target}/followers", params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                break
            data = resp.json()
            if not data or not isinstance(data, list):
                break
            for follower in data:
                yield follower
            if len(data) < 100:
                break
            page += 1

    def follow_user(self, username: str) -> bool:
        clean_user = username.strip().lstrip("@")
        resp = self.request("PUT", f"/user/following/{clean_user}")
        return resp.status_code == 204

    def unfollow_user(self, username: str) -> bool:
        clean_user = username.strip().lstrip("@")
        resp = self.request("DELETE", f"/user/following/{clean_user}")
        return resp.status_code == 204
