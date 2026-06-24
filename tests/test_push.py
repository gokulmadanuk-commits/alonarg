"""Tests for the Web Push module (keys + subscriptions + send; pywebpush mocked)."""
from __future__ import annotations

import types

import pytest

from alonarg import push


@pytest.fixture()
def push_dir(tmp_path, monkeypatch):
    from alonarg import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_keys_generate_and_persist(push_dir):
    appkey1, priv1 = push.get_or_create_keys()
    assert appkey1 and "=" not in appkey1  # base64url, unpadded
    assert push_dir.joinpath("vapid_private.pem").exists()
    appkey2, priv2 = push.get_or_create_keys()
    assert appkey1 == appkey2 and priv1 == priv2  # stable across calls


def test_subscriptions_add_list_remove(push_dir):
    assert push.list_subscriptions() == []
    push.add_subscription({"endpoint": "https://x/1", "keys": {}})
    push.add_subscription({"endpoint": "https://x/2", "keys": {}})
    push.add_subscription({"endpoint": "https://x/1", "keys": {"a": "b"}})  # dedupe by endpoint
    assert len(push.list_subscriptions()) == 2
    push.add_subscription({"no": "endpoint"})  # ignored
    assert len(push.list_subscriptions()) == 2
    push.remove_subscription("https://x/1")
    assert [s["endpoint"] for s in push.list_subscriptions()] == ["https://x/2"]


def test_send_to_all_no_subs(push_dir):
    assert push.send_to_all("t", "b") == {"sent": 0, "removed": 0}


def test_send_to_all_sends_and_prunes(push_dir, monkeypatch):
    import pywebpush

    push.add_subscription({"endpoint": "https://good/1", "keys": {}})
    push.add_subscription({"endpoint": "https://gone/2", "keys": {}})

    def fake_webpush(subscription_info=None, data=None, vapid_private_key=None, vapid_claims=None, ttl=None):
        if "gone" in subscription_info["endpoint"]:
            raise pywebpush.WebPushException("gone", response=types.SimpleNamespace(status_code=410))
        return True

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    assert push.send_to_all("Title", "Body", "/") == {"sent": 1, "removed": 1}
    assert [s["endpoint"] for s in push.list_subscriptions()] == ["https://good/1"]
