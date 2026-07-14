from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pix import pix_extract as pix


class FakeStripeSession:
    def __init__(self):
        self.headers = {}
        self.last_body = {}

    def post(self, url, data=None, timeout=None):
        self.last_body = dict(data or {})
        return SimpleNamespace(
            status_code=200,
            text='{"id":"pm_test_pix"}',
            url=url,
            headers={},
            json=lambda: {"id": "pm_test_pix"},
        )


def test_pix_billing_profile_generates_valid_cpf(monkeypatch):
    monkeypatch.delenv("PIX_TAX_ID", raising=False)

    billing = pix.pix_billing_profile()

    assert pix.is_valid_cpf(billing["tax_id"])


def test_pix_payment_method_includes_cpf(monkeypatch):
    monkeypatch.delenv("PIX_TAX_ID", raising=False)
    billing = pix.pix_billing_profile()
    stripe = FakeStripeSession()

    pm_id = pix.stripe_create_pix_pm(stripe, "cs_test_pix", "pk_test_pix", billing, {})

    assert pm_id == "pm_test_pix"
    assert stripe.last_body["billing_details[tax_id]"] == billing["tax_id"]
    assert pix.is_valid_cpf(stripe.last_body["billing_details[tax_id]"])


def test_pix_update_runs_before_first_stripe_init(monkeypatch):
    events = []
    billing = pix.pix_billing_profile()
    stripe = FakeStripeSession()
    checkout = {
        "cs_id": "cs_test_pix_order",
        "stripe_pk": "pk_test_pix_order",
        "processor_entity": "openai_llc",
        "billing_country": "BR",
        "currency": "BRL",
    }

    monkeypatch.setenv("PIX_UPDATE_TAX_REGION", "1")
    for name in ("PIX_UPDATE_CUSTOMER_DATA", "PIX_CHECKOUT_SNAPSHOT", "PIX_CONFIRM_INLINE_PM"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setattr(pix, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(pix, "dump_http", lambda *args, **kwargs: None)
    monkeypatch.setattr(pix, "manual_proxy_mode_enabled", lambda: False)
    monkeypatch.setattr(pix, "proxy_for_country", lambda proxy, country: proxy)
    monkeypatch.setattr(pix, "proxy_label", lambda proxy: proxy)
    monkeypatch.setattr(pix, "build_chatgpt_session", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        pix,
        "update_checkout_promotion",
        lambda *args, **kwargs: events.append("update"),
    )
    monkeypatch.setattr(pix, "record_proxy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(pix, "record_checkout_zero_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pix,
        "update_pix_checkout_taxes",
        lambda *args, **kwargs: events.append("chatgpt-tax"),
    )
    monkeypatch.setattr(
        pix,
        "stripe_update_tax_region",
        lambda *args, **kwargs: events.append("stripe-tax") or True,
    )

    def fake_init(*args, **kwargs):
        events.append("init")
        return {
            "payment_method_types": ["card", "pix"],
            "total_summary": {"due": 0},
            "currency": "brl",
            "config_id": "config_test_pix",
            "init_checksum": "checksum_test_pix",
        }

    monkeypatch.setattr(pix, "stripe_init", fake_init)
    monkeypatch.setattr(pix, "new_session", lambda *args, **kwargs: stripe)
    monkeypatch.setattr(
        pix,
        "stripe_create_pix_pm",
        lambda stripe, cs_id, stripe_pk, received_billing, ctx: events.append("pm") or "pm_test_pix",
    )
    monkeypatch.setattr(
        pix,
        "stripe_confirm_pix",
        lambda *args, **kwargs: events.append("confirm") or {},
    )
    monkeypatch.setattr(pix, "log_payment_page_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pix,
        "resolve_confirm_payload_pix",
        lambda *args, **kwargs: ("https://payments.example/pix", [], ""),
    )
    monkeypatch.setattr(pix, "resolve_external_redirect", lambda stripe, url: url)

    redirect_url, qr_urls = pix.run_provider_flow(
        "access-token",
        "",
        "http://br-checkout-proxy",
        "http://vn-promotion-proxy",
        "http://br-provider-proxy",
        [],
        "device-test",
        checkout,
        billing,
    )

    assert redirect_url == "https://payments.example/pix"
    assert qr_urls == []
    assert events == ["update", "chatgpt-tax", "stripe-tax", "init", "pm", "confirm"]
