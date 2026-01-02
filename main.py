import os
import time
import json
import random
import requests
from datetime import datetime, timedelta, timezone
from dateutil.parser import isoparse
from dotenv import load_dotenv

# =========================
# Environment / Config
# =========================

load_dotenv()

GITHUB_API = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN")

DAYS_ACTIVE = 7
MAX_FOLLOWS_PER_DAY = 25
UNFOLLOW_AFTER_DAYS = 3

STATE_FILE = "followed_users.json"
DRY_RUN = False

MIN_DELAY = 30
MAX_DELAY = 120

HEADERS = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json"}

# =========================
# Utilities
# =========================


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def github_get(endpoint, params=None):
    r = requests.get(f"{GITHUB_API}{endpoint}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def github_put(endpoint):
    r = requests.put(f"{GITHUB_API}{endpoint}", headers=HEADERS)
    if r.status_code not in (204, 304):
        r.raise_for_status()


def github_delete(endpoint):
    r = requests.delete(f"{GITHUB_API}{endpoint}", headers=HEADERS)
    if r.status_code not in (204, 304):
        r.raise_for_status()


def sleep_random():
    time.sleep(random.randint(MIN_DELAY, MAX_DELAY))


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# =========================
# Discovery & Filters
# =========================


def search_active_repositories():
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS_ACTIVE)).date()
    query = f"pushed:>={since}"

    data = github_get(
        "/search/repositories",
        params={"q": query, "sort": "updated", "order": "desc", "per_page": 50},
    )
    return data["items"]


def user_is_active(username):
    events = github_get(f"/users/{username}/events/public", params={"per_page": 10})

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_ACTIVE)

    for event in events:
        if isoparse(event["created_at"]) >= cutoff:
            return True
    return False


def user_has_following(username):
    user = github_get(f"/users/{username}")
    return user.get("following", 0) > 0


# =========================
# Followers / Unfollow Logic
# =========================


def get_my_followers():
    followers = set()
    page = 1

    while True:
        data = github_get("/user/followers", params={"per_page": 100, "page": page})
        if not data:
            break
        followers.update(u["login"] for u in data)
        page += 1

    return followers


def unfollow_stale_users(state):
    followers = get_my_followers()
    cutoff = datetime.now(timezone.utc) - timedelta(days=UNFOLLOW_AFTER_DAYS)

    print("Unfollowing stale users...")
    for username, meta in list(state.items()):
        followed_at = isoparse(meta["followed_at"])

        if followed_at < cutoff and username not in followers:
            if DRY_RUN:
                print(f"[DRY RUN] Would unfollow {username}")
            else:
                github_delete(f"/user/following/{username}")
                print(f"Unfollowed {username}")

            state.pop(username, None)
            save_state(state)
            sleep_random()
    print("Done.")


# =========================
# Follow Logic
# =========================


def follow_user(username, state):
    if DRY_RUN:
        print(f"[DRY RUN] Would follow {username}")
        return

    github_put(f"/user/following/{username}")
    state[username] = {"followed_at": now_utc()}
    save_state(state)

    print(f"Followed {username}")


# =========================
# Execution
# =========================


def main():
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set")

    state = load_state()
    follows_today = 0

    # Unfollow pass first
    unfollow_stale_users(state)

    repos = search_active_repositories()
    candidates = {repo["owner"]["login"] for repo in repos}

    for username in candidates:
        if follows_today >= MAX_FOLLOWS_PER_DAY:
            break

        if username in state:
            continue

        try:
            if not user_is_active(username):
                continue

            if not user_has_following(username):
                continue

            follow_user(username, state)
            follows_today += 1
            sleep_random()

        except requests.HTTPError as e:
            print(f"Error with {username}: {e}")
            break

    print(f"Completed. Followed {follows_today} users today.")


if __name__ == "__main__":
    main()
