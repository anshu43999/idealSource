from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from turkey_card import card_extract as card


class FakeChatgptSession:
    def __init__(self):
        self.body = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.body = dict(json or {})
        return SimpleNamespace(
            status_code=200,
            text='{"checkout_session_id":"cs_test_card"}',
            url=url,
            headers={},
            json=lambda: {
                "checkout_session_id": "cs_test_card",
                "publishable_key": "pk_test_card",
                "processor_entity": "openai_ie",
            },
        )


def checkout() -> dict[str, str]:
    return {
        "cs_id": "cs_test_card",
        "stripe_pk": "pk_test_card",
        "currency": "TRY",
        "billing_country": "TR",
        "processor_entity": "openai_ie",
    }


def run_flow(monkeypatch, methods: list[str], events: list[str]) -> str:
    monkeypatch.setattr(
        card.flow,
        "build_chatgpt_session",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        card.flow,
        "update_checkout_promotion",
        lambda *args, **kwargs: events.append("update"),
    )

    def fake_init(*args, **kwargs):
        events.append("init")
        return {
            "payment_method_types": methods,
            "total_summary": {"due": 0},
            "stripe_hosted_url": (
                "https://checkout.stripe.com/c/pay/cs_test_card?test=1"
            ),
        }

    monkeypatch.setattr(card.flow, "stripe_init", fake_init)
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)
    result, qr_urls = card.run_manual_card_flow(
        "access-token",
        "session-token",
        "tr-checkout-proxy",
        "gb-promotion-proxy",
        "tr-provider-proxy",
        [],
        "device-id",
        checkout(),
        {},
    )
    assert qr_urls == []
    return result


def test_turkey_card_country_chain():
    assert card.flow.IDEAL_BOOTSTRAP_COUNTRY == "TR"
    assert card.flow.IDEAL_PROMOTION_COUNTRY == "GB"
    assert card.flow.IDEAL_PROVIDER_COUNTRY == "TR"
    assert card.flow.COUNTRY_CURRENCY["TR"] == "TRY"


def test_turkey_checkout_defers_promotion(monkeypatch):
    monkeypatch.setenv("IDEAL_DEFER_PROMO_TO_UPDATE", "1")
    chatgpt = FakeChatgptSession()

    created = card.flow.create_checkout(chatgpt, "TR")

    assert created["billing_country"] == "TR"
    assert created["currency"] == "TRY"
    assert "promo_campaign" not in chatgpt.body
    assert "coupon" not in chatgpt.body


def test_manual_card_flow_updates_then_initializes(monkeypatch):
    events: list[str] = []

    result = run_flow(monkeypatch, ["card", "link"], events)

    assert events == ["update", "init"]
    assert result == "https://pay.openai.com/c/pay/cs_test_card?test=1"


def test_manual_card_flow_accepts_card_with_other_methods(monkeypatch):
    result = run_flow(monkeypatch, ["link", "card"], [])

    assert result.startswith("https://pay.openai.com/")


def test_manual_card_flow_rejects_missing_card(monkeypatch):
    with pytest.raises(RuntimeError, match="不支持 Card"):
        run_flow(monkeypatch, ["link"], [])


def test_manual_card_flow_falls_back_to_checkout_page(monkeypatch):
    monkeypatch.setattr(card.flow, "build_chatgpt_session", lambda *args: object())
    monkeypatch.setattr(card.flow, "update_checkout_promotion", lambda *args: None)
    monkeypatch.setattr(
        card.flow,
        "stripe_init",
        lambda *args: {
            "payment_method_types": ["card", "link"],
            "total_summary": {"due": 0},
        },
    )
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)

    result, _ = card.run_manual_card_flow(
        "access-token",
        "session-token",
        "tr-checkout-proxy",
        "gb-promotion-proxy",
        "tr-provider-proxy",
        [],
        "device-id",
        checkout(),
        {},
    )

    assert result == card.flow.checkout_page_url(checkout())
