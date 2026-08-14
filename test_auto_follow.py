#!/usr/bin/env python3
"""
Unit tests for GitHub Auto-Follow Script.
Tests filtering logic, rate-limit headers handling, state management, retry backoff,
Fastfetch/Neofetch ANSI avatar banner rendering, and Interactive REPL session commands.
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import requests

from PIL import Image

from auto_follow import (
    AutoFollowRunner,
    GitHubAPIClient,
    InteractiveSession,
    StateManager,
    image_to_ansi_halfblocks,
    render_neofetch_banner,
    strip_ansi
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
        self.assertTrue(mgr.is_previously_followed("ALICE"))  # Case insensitive
        self.assertEqual(mgr.state["total_followed_all_time"], 1)

        # Reload from disk
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


class TestAvatarAndBanner(unittest.TestCase):
    def test_image_to_ansi_halfblocks(self):
        img = Image.new("RGBA", (24, 24), color=(255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        lines = image_to_ansi_halfblocks(img_bytes, target_width=24, target_height=24)
        self.assertEqual(len(lines), 12)
        for line in lines:
            stripped = strip_ansi(line)
            self.assertEqual(len(stripped), 24)

    def test_render_neofetch_banner(self):
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

        render_neofetch_banner(
            auth_user=auth_user,
            target_info=target_info,
            api_remaining=4950,
            api_limit=5000,
            max_follows=50,
            delay_min=2.0,
            delay_max=4.0,
            dry_run=True,
            avatar_lines=dummy_avatar_lines
        )


class TestInteractiveSession(unittest.TestCase):
    @patch.object(GitHubAPIClient, "get_user_info")
    @patch.object(GitHubAPIClient, "get_authenticated_user")
    def test_interactive_commands(self, mock_auth_user, mock_user_info):
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

        # Test set target
        session.handle_command("set target octocat")
        self.assertEqual(session.target_username, "octocat")
        self.assertIsNotNone(session.target_info)

        # Test set limit
        session.handle_command("set limit 75")
        self.assertEqual(session.max_follows, 75)

        # Test set delay
        session.handle_command("set delay 3.0 6.0")
        self.assertEqual(session.delay_min, 3.0)
        self.assertEqual(session.delay_max, 6.0)

        # Test set dry-run
        session.handle_command("set dry-run on")
        self.assertTrue(session.dry_run)

        session.handle_command("set dry-run off")
        self.assertFalse(session.dry_run)

        # Test exit
        cont = session.handle_command("exit")
        self.assertFalse(cont)


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


if __name__ == "__main__":
    unittest.main()
