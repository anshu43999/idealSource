from pathlib import Path
import sys
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from turkey_card import card_extract as card


class FakeStripeSession:
    def __init__(self):
        self.body = {}

    def post(self, url, data=None, timeout=None):
        self.body = dict(data or {})
        return SimpleNamespace(
            status_code=200,
            text='{"id":"pm_card_test"}',
            url=url,
            headers={},
            json=lambda: {"id": "pm_card_test"},
        )


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


def test_turkey_card_country_chain():
    assert card.flow.IDEAL_BOOTSTRAP_COUNTRY == "TR"
    assert card.flow.IDEAL_PROMOTION_COUNTRY == "GB"
    assert card.flow.IDEAL_PROVIDER_COUNTRY == "TR"
    assert card.flow.COUNTRY_CURRENCY["TR"] == "TRY"


def test_card_number_validation():
    assert card.card_number_is_valid("4242 4242 4242 4242")
    assert not card.card_number_is_valid("4242 4242 4242 4241")


def test_turkey_checkout_defers_promotion(monkeypatch):
    monkeypatch.setenv("IDEAL_DEFER_PROMO_TO_UPDATE", "1")
    chatgpt = FakeChatgptSession()

    checkout = card.flow.create_checkout(chatgpt, "TR")

    assert checkout["billing_country"] == "TR"
    assert checkout["currency"] == "TRY"
    assert "promo_campaign" not in chatgpt.body
    assert "coupon" not in chatgpt.body


def test_card_pm_uses_card_fields(monkeypatch):
    monkeypatch.setenv("TURKEY_CARD_NUMBER", "4242424242424242")
    monkeypatch.setenv("TURKEY_CARD_EXP_MONTH", "12")
    monkeypatch.setenv("TURKEY_CARD_EXP_YEAR", "2030")
    monkeypatch.setenv("TURKEY_CARD_CVC", "123")
    monkeypatch.setattr(card.flow, "dump_http", lambda *args, **kwargs: None)
    stripe = FakeStripeSession()
    billing = {
        "name": "Emir Yilmaz",
        "email": "emir@example.com",
        "line1": "Bagdat Caddesi 120",
        "city": "Istanbul",
        "postal_code": "34728",
        "state": "Istanbul",
    }

    pm_id = card.stripe_create_card_pm(
        stripe,
        "cs_test_card",
        "pk_test_card",
        billing,
        {},
    )

    assert pm_id == "pm_card_test"
    assert stripe.body["type"] == "card"
    assert stripe.body["card[number]"] == "4242424242424242"
    assert stripe.body["billing_details[address][country]"] == "TR"


def test_card_terminal_success_detects_payment_intent():
    assert card.card_terminal_success(
        {"payment_intent": {"object": "payment_intent", "status": "succeeded"}}
    )

