"""
Unit tests for GitHub Follower Toolkit.
Tests API client requests, rate limit backoff, state persistence,
whitelist filtering, follow/unfollow workflows, and interactive shell commands.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests

from PIL import Image
from rich.console import Console

# Add src to sys.path for direct test execution
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ghf_toolkit import (
    AutoFollowRunner,
    GitHubAPIClient,
    InteractiveSession,
    StateManager,
    UnfollowRunner,
    WhitelistManager,
    build_dashboard_renderable,
    get_saved_token,
    image_to_ansi_halfblocks,
    render_neofetch_banner,
    resolve_token,
    save_token
)


class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.state_file = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)

    def test_record_and_load(self):
        mgr = StateManager(state_file=self.state_file)
        self.assertFalse(mgr.is_previously_followed("alice"))
        
        mgr.record_follow("alice")
        self.assertTrue(mgr.is_previously_followed("alice"))
        self.assertTrue(mgr.is_previously_followed("ALICE"))  # Case-insensitive comparison
        self.assertEqual(mgr.state["total_followed_all_time"], 1)

        # Reload state from disk
        mgr2 = StateManager(state_file=self.state_file)
        self.assertTrue(mgr2.is_previously_followed("alice"))
        self.assertEqual(mgr2.state["total_followed_all_time"], 1)

    def test_clear_state(self):
        mgr = StateManager(state_file=self.state_file)
        mgr.record_follow("bob")
        self.assertTrue(mgr.is_previously_followed("bob"))
        mgr.clear()
        self.assertFalse(mgr.is_previously_followed("bob"))
        self.assertEqual(mgr.state["total_followed_all_time"], 0)


class TestWhitelistManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.wl_file = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.wl_file):
            os.unlink(self.wl_file)

    def test_add_remove_and_load(self):
        wl = WhitelistManager(whitelist_file=self.wl_file)
        self.assertFalse(wl.is_whitelisted("torvalds"))

        added = wl.add("torvalds", "@octocat")
        self.assertEqual(len(added), 2)
        self.assertTrue(wl.is_whitelisted("torvalds"))
        self.assertTrue(wl.is_whitelisted("TORVALDS"))  # Case-insensitive comparison
        self.assertTrue(wl.is_whitelisted("@octocat"))  # Remove leading @ character
        self.assertTrue(wl.is_whitelisted("octocat"))

        # Reload whitelist from disk
        wl2 = WhitelistManager(whitelist_file=self.wl_file)
        self.assertTrue(wl2.is_whitelisted("torvalds"))
        self.assertEqual(wl2.list(), ["octocat", "torvalds"])

        # Remove user from whitelist
        removed = wl.remove("@torvalds")
        self.assertEqual(removed, ["torvalds"])
        self.assertFalse(wl.is_whitelisted("torvalds"))

    def test_clear_whitelist(self):
        wl = WhitelistManager(whitelist_file=self.wl_file)
        wl.add("user1", "user2")
        self.assertEqual(len(wl.list()), 2)
        wl.clear()
        self.assertEqual(len(wl.list()), 0)
        self.assertFalse(wl.is_whitelisted("user1"))


class TestAvatarAndBanner(unittest.TestCase):
    def test_image_to_ansi_halfblocks(self):
        img = Image.new("RGBA", (24, 24), color=(255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        lines = image_to_ansi_halfblocks(img_bytes, target_width=24, target_height=24)
        self.assertEqual(len(lines), 12)

    def test_render_neofetch_banner_follow_and_unfollow(self):
        auth_user = {
            "login": "testuser",
            "name": "Test Developer",
            "bio": "Building cool CLI tools",
            "followers": 100,
            "following": 50,
            "public_repos": 10,
            "created_at": "2021-05-01T00:00:00Z"
        }
        target_info = {
            "login": "targetdev",
            "followers": 500
        }
        dummy_avatar_lines = ["▀" * 24] * 12

        # Follow dashboard rendering
        renderable_follow = build_dashboard_renderable(
            auth_user=auth_user,
            target_info=target_info,
            api_remaining=4950,
            api_limit=5000,
            max_follows=50,
            delay_min=2.0,
            delay_max=4.0,
            dry_run=True,
            avatar_lines=dummy_avatar_lines,
            live_stats={"op_type": "follow", "followed_success": 5, "total_examined": 10},
            status_msg="Testing follow status"
        )
        self.assertIsNotNone(renderable_follow)

        # Unfollow dashboard rendering
        renderable_unfollow = build_dashboard_renderable(
            auth_user=auth_user,
            target_info=None,
            api_remaining=4950,
            api_limit=5000,
            max_follows=30,
            delay_min=2.0,
            delay_max=4.0,
            dry_run=False,
            avatar_lines=dummy_avatar_lines,
            live_stats={"op_type": "unfollow", "unfollowed_success": 8, "target_total": 30, "skipped_whitelisted": 2},
            status_msg="Testing unfollow status"
        )
        self.assertIsNotNone(renderable_unfollow)


class TestGitHubAPIClient(unittest.TestCase):
    def setUp(self):
        self.client = GitHubAPIClient(token="mock_token_123")

    @patch.object(requests.Session, "request")
    def test_rate_limit_header_updating(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4950",
            "X-RateLimit-Reset": "1700000000"
        }
        mock_resp.json.return_value = {"login": "myuser", "followers": 10, "following": 5}
        mock_request.return_value = mock_resp

        user = self.client.get_authenticated_user()
        self.assertEqual(user["login"], "myuser")
        self.assertEqual(self.client.rate_limit_remaining, 4950)
        self.assertEqual(self.client.rate_limit_limit, 5000)

    @patch("time.sleep")
    @patch.object(requests.Session, "request")
    def test_secondary_rate_limit_retry(self, mock_request, mock_sleep):
        rate_limit_resp = MagicMock()
        rate_limit_resp.status_code = 403
        rate_limit_resp.headers = {"Retry-After": "2"}
        rate_limit_resp.json.return_value = {"message": "You have exceeded a secondary rate limit."}

        success_resp = MagicMock()
        success_resp.status_code = 204
        success_resp.headers = {}

        mock_request.side_effect = [rate_limit_resp, success_resp]

        success = self.client.follow_user("target_user")
        self.assertTrue(success)
        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called()

    @patch.object(requests.Session, "request")
    def test_unfollow_user(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_resp.headers = {}
        mock_request.return_value = mock_resp

        success = self.client.unfollow_user("@bad_user")
        self.assertTrue(success)
        mock_request.assert_called_once_with(
            method="DELETE",
            url="https://api.github.com/user/following/bad_user",
            params=None,
            json=None,
            timeout=30
        )


class TestAutoFollowPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.state_file = self.tmp.name
        self.state_mgr = StateManager(state_file=self.state_file)
        self.client = GitHubAPIClient(token="mock_token")

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)

    @patch.object(GitHubAPIClient, "follow_user")
    @patch.object(GitHubAPIClient, "stream_target_followers")
    @patch.object(GitHubAPIClient, "fetch_all_following_set")
    @patch.object(GitHubAPIClient, "fetch_all_followers_set")
    @patch.object(GitHubAPIClient, "get_user_info")
    @patch.object(GitHubAPIClient, "get_authenticated_user")
    def test_filtering_and_following(
        self,
        mock_auth_user,
        mock_target_info,
        mock_fetch_followers,
        mock_fetch_following,
        mock_stream_target,
        mock_follow_user
    ):
        mock_auth_user.return_value = {
            "login": "myuser",
            "name": "Me",
            "bio": "Dev",
            "followers": 2,
            "following": 1,
            "public_repos": 5,
            "created_at": "2022-01-01T00:00:00Z",
            "avatar_url": None
        }
        mock_target_info.return_value = {"login": "influencer", "followers": 5}
        mock_fetch_followers.return_value = {"follower_one", "follower_two"}
        mock_fetch_following.return_value = {"already_followed_friend"}
        self.state_mgr.record_follow("past_followed_user")

        mock_stream_target.return_value = [
            {"login": "myuser"},
            {"login": "follower_one"},
            {"login": "already_followed_friend"},
            {"login": "past_followed_user"},
            {"login": "new_lead_1"},
            {"login": "new_lead_2"},
            {"login": "new_lead_3"}
        ]

        mock_follow_user.return_value = True

        runner = AutoFollowRunner(
            client=self.client,
            state_mgr=self.state_mgr,
            target_username="influencer",
            max_follows=2,
            delay_min=0.01,
            delay_max=0.02,
            dry_run=False,
            interactive=False
        )

        runner.run()

        self.assertEqual(runner.stats["followed_success"], 2)
        self.assertEqual(runner.stats["skipped_already_follows_me"], 1)
        self.assertEqual(runner.stats["skipped_already_following"], 1)
        self.assertEqual(runner.stats["skipped_state_history"], 1)

        self.assertEqual(mock_follow_user.call_count, 2)
        mock_follow_user.assert_any_call("new_lead_1")
        mock_follow_user.assert_any_call("new_lead_2")

        self.assertTrue(self.state_mgr.is_previously_followed("new_lead_1"))
        self.assertTrue(self.state_mgr.is_previously_followed("new_lead_2"))


class TestUnfollowPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        self.wl_file = self.tmp.name
        self.wl_mgr = WhitelistManager(whitelist_file=self.wl_file)
        self.client = GitHubAPIClient(token="mock_token")

    def tearDown(self):
        if os.path.exists(self.wl_file):
            os.unlink(self.wl_file)

    @patch.object(GitHubAPIClient, "unfollow_user")
    @patch.object(GitHubAPIClient, "fetch_all_followers_set")
    @patch.object(GitHubAPIClient, "fetch_all_following_set")
    @patch.object(GitHubAPIClient, "get_authenticated_user")
    def test_unfollow_non_followers_with_whitelist(
        self,
        mock_auth_user,
        mock_fetch_following,
        mock_fetch_followers,
        mock_unfollow_user
    ):
        mock_auth_user.return_value = {
            "login": "myuser",
            "name": "Me",
            "followers": 2,
            "following": 4,
            "avatar_url": None
        }
        # Test candidates: user_a (mutual), user_b (non-follower), user_vip (whitelisted non-follower), user_c (non-follower)
        mock_fetch_following.return_value = {"user_a", "user_b", "user_vip", "user_c"}
        mock_fetch_followers.return_value = {"user_a"}
        self.wl_mgr.add("user_vip")

        mock_unfollow_user.return_value = True

        runner = UnfollowRunner(
            client=self.client,
            whitelist_mgr=self.wl_mgr,
            mode="non-followers",
            limit=5,
            delay_min=0.01,
            delay_max=0.02,
            dry_run=False,
            interactive=False
        )

        status = runner.run()

        self.assertEqual(runner.stats["unfollowed_success"], 2)
        self.assertEqual(runner.stats["skipped_whitelisted"], 1)
        self.assertEqual(mock_unfollow_user.call_count, 2)
        mock_unfollow_user.assert_any_call("user_b")
        mock_unfollow_user.assert_any_call("user_c")

    @patch.object(GitHubAPIClient, "unfollow_user")
    @patch.object(GitHubAPIClient, "fetch_all_following_set")
    @patch.object(GitHubAPIClient, "get_authenticated_user")
    def test_unfollow_all_dry_run(
        self,
        mock_auth_user,
        mock_fetch_following,
        mock_unfollow_user
    ):
        mock_auth_user.return_value = {
            "login": "myuser",
            "name": "Me",
            "followers": 1,
            "following": 3,
            "avatar_url": None
        }
        mock_fetch_following.return_value = {"user_1", "user_2", "user_vip"}
        self.wl_mgr.add("user_vip")

        runner = UnfollowRunner(
            client=self.client,
            whitelist_mgr=self.wl_mgr,
            mode="all",
            limit=10,
            delay_min=0.01,
            delay_max=0.02,
            dry_run=True,
            interactive=False,
            force=True
        )

        runner.run()

        # In simulation mode, verify API mutation calls are omitted while statistics update
        self.assertEqual(runner.stats["unfollowed_success"], 2)
        self.assertEqual(runner.stats["skipped_whitelisted"], 1)
        self.assertEqual(mock_unfollow_user.call_count, 0)


class TestInteractiveSession(unittest.TestCase):
    @patch.object(GitHubAPIClient, "get_user_info")
    @patch.object(GitHubAPIClient, "get_authenticated_user")
    def test_interactive_commands_and_toolkit(self, mock_auth_user, mock_user_info):
        mock_auth_user.return_value = {
            "login": "myuser",
            "name": "Me",
            "followers": 10,
            "following": 5,
            "public_repos": 3,
            "created_at": "2022-01-01T00:00:00Z"
        }
        mock_user_info.return_value = {"login": "octocat", "followers": 1000}

        session = InteractiveSession(token="mock_token")
        session.initialize()

        # Test target command
        session.handle_command("set target octocat")
        self.assertEqual(session.target_username, "octocat")
        self.assertIsNotNone(session.target_info)

        # Test limit command
        session.handle_command("set limit 75")
        self.assertEqual(session.max_follows, 75)

        # Test delay command
        session.handle_command("set delay 3.0 6.0")
        self.assertEqual(session.delay_min, 3.0)
        self.assertEqual(session.delay_max, 6.0)

        # Test dry-run command
        session.handle_command("set dry-run on")
        self.assertTrue(session.dry_run)

        session.handle_command("set dry-run off")
        self.assertFalse(session.dry_run)

        # Test whitelist commands
        session.handle_command("wl add torvalds octocat")
        self.assertTrue(session.whitelist_mgr.is_whitelisted("torvalds"))
        self.assertTrue(session.whitelist_mgr.is_whitelisted("octocat"))

        session.handle_command("wl rm torvalds")
        self.assertFalse(session.whitelist_mgr.is_whitelisted("torvalds"))
        self.assertTrue(session.whitelist_mgr.is_whitelisted("octocat"))

        session.handle_command("wl clear")
        self.assertEqual(len(session.whitelist_mgr.list()), 0)

        # Test show and status commands
        session.handle_command("status")
        self.assertEqual(session.auth_user["login"], "myuser")

        # Test exit command
        cont = session.handle_command("exit")
        self.assertFalse(cont)


class TestTokenResolution(unittest.TestCase):
    def test_cli_token_priority(self):
        self.assertEqual(resolve_token(cli_token="ghp_direct_cli_token"), "ghp_direct_cli_token")

    def test_env_token_priority(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_env_token_123"}):
            self.assertEqual(resolve_token(), "ghp_env_token_123")


if __name__ == "__main__":
    unittest.main()
