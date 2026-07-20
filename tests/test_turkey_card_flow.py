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
            text='{"checkout_session_id":"cs_test_card","checkout_url":"https://chatgpt.com/checkout/openai_llc/oaics_test_card"}',
            url=url,
            headers={},
            json=lambda: {
                "checkout_session_id": "cs_test_card",
                "publishable_key": "pk_test_card",
                "processor_entity": "openai_llc",
                "checkout_url": "https://chatgpt.com/checkout/openai_llc/oaics_test_card",
            },
        )


def checkout() -> dict[str, str]:
    return {
        "cs_id": "cs_test_card",
        "stripe_pk": "pk_test_card",
        "currency": "USD",
        "billing_country": "US",
        "processor_entity": "openai_llc",
        "checkout_page_id": "oaics_test_card",
        "checkout_page_processor": "openai_llc",
    }


def run_flow(monkeypatch, methods: list[str], events: list[str]) -> str:
    monkeypatch.setattr(
        card,
        "activate_turkey_checkout",
        lambda *args, **kwargs: events.append("activate"),
    )
    monkeypatch.setattr(
        card.flow,
        "build_chatgpt_session",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        card,
        "update_turkey_checkout_promotion",
        lambda *args, **kwargs: events.append("update"),
    )
    monkeypatch.setattr(
        card,
        "update_us_checkout_taxes",
        lambda *args, **kwargs: events.append("taxes"),
    )

    def fake_init(*args, **kwargs):
        if len([event for event in events if event == "init"]) == 0:
            events.append("init")
            due = 2000
        else:
            events.append("init")
            due = 0
        return {
            "payment_method_types": methods,
            "total_summary": {"due": due},
            "stripe_hosted_url": (
                "https://checkout.stripe.com/c/pay/cs_test_card?test=1"
            ),
        }

    monkeypatch.setattr(card.flow, "stripe_init", fake_init)
    monkeypatch.setattr(card.flow, "build_ctx", lambda payload, checkout: {})
    monkeypatch.setattr(card.flow, "new_session", lambda *args, **kwargs: SimpleNamespace(headers={}))
    monkeypatch.setattr(
        card.flow,
        "stripe_update_tax_region",
        lambda *args, **kwargs: events.append("tax_region") or True,
    )
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)
    result, qr_urls = card.run_manual_card_flow(
        "access-token",
        "session-token",
        "us-checkout-proxy",
        "tr-promotion-proxy",
        "us-provider-proxy",
        [],
        "device-id",
        checkout(),
        {},
    )
    assert qr_urls == []
    return result


def test_turkey_card_country_chain():
    assert card.flow.IDEAL_BOOTSTRAP_COUNTRY == "US"
    assert card.flow.IDEAL_PROMOTION_COUNTRY == "TR"
    assert card.flow.IDEAL_PROVIDER_COUNTRY == "US"
    assert card.flow.COUNTRY_CURRENCY["TR"] == "USD"
    assert card.flow.COUNTRY_CURRENCY["US"] == "USD"


def test_turkey_checkout_creates_with_campaign_promotion(monkeypatch):
    monkeypatch.setenv("PP_PROMO_MODE", "campaign")
    monkeypatch.setenv("IDEAL_DEFER_PROMO_TO_UPDATE", "0")
    chatgpt = FakeChatgptSession()

    created = card.flow.create_checkout(chatgpt, "US")

    assert created["billing_country"] == "US"
    assert created["currency"] == "USD"
    assert created["checkout_page_id"] == "oaics_test_card"
    assert created["checkout_page_processor"] == "openai_llc"
    assert card.flow.checkout_page_url(created) == "https://chatgpt.com/checkout/openai_llc/oaics_test_card"
    assert chatgpt.body["billing_details"] == {"country": "US", "currency": "USD"}
    assert chatgpt.body["promo_campaign"] == {
        "promo_campaign_id": "plus-1-month-free",
        "is_coupon_from_query_param": False,
    }
    assert "coupon" not in chatgpt.body


def test_checkout_page_url_prefers_oaics_over_cs_checkout_url():
    fields = card.flow.checkout_page_fields_from_payload(
        {
            "checkout_url": "https://chatgpt.com/checkout/openai_llc/cs_test_card",
            "id": "oaics_real_card",
        }
    )
    checkout_data = {
        "cs_id": "cs_test_card",
        "billing_country": "US",
        "processor_entity": "openai_ie",
        **fields,
    }

    assert card.flow.checkout_page_url(checkout_data) == "https://chatgpt.com/checkout/openai_llc/oaics_real_card"


def test_manual_card_flow_updates_then_initializes(monkeypatch):
    events: list[str] = []

    result = run_flow(monkeypatch, ["card", "link"], events)

    assert events == ["activate", "init", "update", "taxes", "init", "tax_region", "init"]
    assert result == "https://chatgpt.com/checkout/openai_llc/oaics_test_card"


def test_manual_card_flow_accepts_card_with_other_methods(monkeypatch):
    result = run_flow(monkeypatch, ["link", "card"], [])

    assert result == "https://chatgpt.com/checkout/openai_llc/oaics_test_card"


def test_manual_card_flow_rejects_missing_card(monkeypatch):
    with pytest.raises(RuntimeError, match="不支持 Card"):
        run_flow(monkeypatch, ["link"], [])


def test_manual_card_flow_falls_back_to_checkout_page(monkeypatch):
    monkeypatch.setattr(card.flow, "build_chatgpt_session", lambda *args: object())
    monkeypatch.setattr(card, "activate_turkey_checkout", lambda *args: None)
    monkeypatch.setattr(card, "update_turkey_checkout_promotion", lambda *args: None)
    monkeypatch.setattr(card, "update_us_checkout_taxes", lambda *args: None)
    monkeypatch.setattr(
        card.flow,
        "stripe_init",
        lambda *args: {
            "payment_method_types": ["card", "link"],
            "total_summary": {"due": 0},
        },
    )
    monkeypatch.setattr(card.flow, "build_ctx", lambda payload, checkout: {})
    monkeypatch.setattr(card.flow, "new_session", lambda *args, **kwargs: SimpleNamespace(headers={}))
    monkeypatch.setattr(card.flow, "stripe_update_tax_region", lambda *args: True)
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)

    result, _ = card.run_manual_card_flow(
        "access-token",
        "session-token",
        "tr-checkout-proxy",
        "tr-promotion-proxy",
        "us-provider-proxy",
        [],
        "device-id",
        checkout(),
        {},
    )

    assert result == "https://chatgpt.com/checkout/openai_llc/oaics_test_card"


def test_final_checkout_taxes_uses_us_billing():
    session = FakeChatgptSession()

    card.update_us_checkout_taxes(
        session,
        checkout(),
        card.us_billing_profile(),
    )

    assert session.body["billing_country"] == "US"
    assert session.body["currency"] == "USD"
    assert session.body["billing_address"]["country"] == "US"
    assert session.body["billing_address"]["state"] == "NY"


def test_turkey_checkout_update_uses_tr_billing_details(monkeypatch):
    monkeypatch.setenv("PP_PROMO_MODE", "campaign")
    session = FakeChatgptSession()

    card.update_turkey_checkout_promotion(session, checkout())

    assert session.body["checkout_session_id"] == "cs_test_card"
    assert session.body["billing_details"] == {
        "country": "TR",
        "currency": "USD",
    }
    assert "subscription_data" not in session.body
    assert session.body["promo_campaign"] == {
        "promo_campaign_id": "plus-1-month-free",
        "is_coupon_from_query_param": False,
    }


def test_turkey_checkout_update_switches_returned_session(monkeypatch):
    monkeypatch.setenv("PP_PROMO_MODE", "campaign")

    class UpdateSession(FakeChatgptSession):
        def post(self, url, json=None, headers=None, timeout=None):
            self.body = dict(json or {})
            return SimpleNamespace(
                status_code=200,
                text='{"checkout_session_id":"cs_test_tr_zero"}',
                url=url,
                headers={},
                json=lambda: {
                    "checkout_session_id": "cs_test_tr_zero",
                    "publishable_key": "pk_test_tr",
                    "processor_entity": "openai_llc",
                    "checkout_url": "https://chatgpt.com/checkout/openai_llc/oaics_test_tr_zero",
                },
            )

    session = UpdateSession()
    checkout_data = checkout()

    card.update_turkey_checkout_promotion(session, checkout_data)

    assert checkout_data["cs_id"] == "cs_test_tr_zero"
    assert checkout_data["stripe_pk"] == "pk_test_tr"
    assert checkout_data["processor_entity"] == "openai_llc"
    assert checkout_data["checkout_page_id"] == "oaics_test_tr_zero"
    assert card.flow.checkout_page_url(checkout_data) == "https://chatgpt.com/checkout/openai_llc/oaics_test_tr_zero"


def test_manual_card_flow_preserves_existing_zero_without_update(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(card, "activate_turkey_checkout", lambda *args: events.append("activate"))
    monkeypatch.setattr(card.flow, "build_chatgpt_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(card, "update_turkey_checkout_promotion", lambda *args: events.append("update"))
    monkeypatch.setattr(card, "update_us_checkout_taxes", lambda *args: events.append("taxes"))
    monkeypatch.setattr(card.flow, "build_ctx", lambda payload, checkout: {})
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)

    def fake_init(*args, **kwargs):
        events.append("init")
        return {
            "payment_method_types": ["card", "link"],
            "total_summary": {"due": 0},
            "stripe_hosted_url": (
                "https://checkout.stripe.com/c/pay/cs_test_card?test=1"
            ),
        }

    monkeypatch.setattr(card.flow, "stripe_init", fake_init)

    result, qr_urls = card.run_manual_card_flow(
        "access-token",
        "session-token",
        "us-checkout-proxy",
        "tr-promotion-proxy",
        "us-provider-proxy",
        [],
        "device-id",
        checkout(),
        {},
    )

    assert qr_urls == []
    assert result == "https://chatgpt.com/checkout/openai_llc/oaics_test_card"
    assert events == ["activate", "init", "init"]


def test_manual_card_flow_rejects_nonzero_us_direct_refresh(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(card, "activate_turkey_checkout", lambda *args: events.append("activate"))
    monkeypatch.setattr(card.flow, "build_chatgpt_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(card, "update_turkey_checkout_promotion", lambda *args: events.append("update"))
    monkeypatch.setattr(card, "update_us_checkout_taxes", lambda *args: events.append("taxes"))
    monkeypatch.setattr(card.flow, "build_ctx", lambda payload, checkout: {})
    monkeypatch.setattr(card.flow, "record_proxy_result", lambda *args: None)
    monkeypatch.setattr(card.flow, "record_checkout_zero_result", lambda *args: None)

    def fake_init(*args, **kwargs):
        events.append("init")
        if len([event for event in events if event == "init"]) == 1:
            due = 0
            hosted_url = "https://checkout.stripe.com/c/pay/cs_bootstrap?test=1"
        else:
            due = 2000
            hosted_url = "https://checkout.stripe.com/c/pay/cs_direct_refresh?test=1"
        return {
            "payment_method_types": ["card", "link"],
            "total_summary": {"due": due},
            "stripe_hosted_url": hosted_url,
        }

    monkeypatch.setattr(card.flow, "stripe_init", fake_init)

    with pytest.raises(RuntimeError, match="US Provider 最终金额不是 0"):
        card.run_manual_card_flow(
            "access-token",
            "session-token",
            "us-checkout-proxy",
            "tr-promotion-proxy",
            "us-provider-proxy",
            [],
            "device-id",
            checkout(),
            {},
        )

    assert events == ["activate", "init", "init"]
