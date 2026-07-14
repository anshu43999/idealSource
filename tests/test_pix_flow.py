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


class FakeChatgptSession:
    def __init__(self):
        self.last_body = {}

    def post(self, url, json=None, headers=None, timeout=None):
        self.last_body = dict(json or {})
        return SimpleNamespace(
            status_code=200,
            text='{"checkout_session_id":"cs_test_checkout"}',
            url=url,
            headers={},
            json=lambda: {
                "checkout_session_id": "cs_test_checkout",
                "publishable_key": "pk_test_pix",
                "checkout_url": (
                    "https://chatgpt.com/checkout/openai_llc/cs_test_checkout"
                ),
            },
        )


class FlakyPollStripeSession:
    def __init__(self):
        self.calls = 0
        self.timeouts = []

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        if self.calls == 1:
            raise RuntimeError("Connection reset by peer")
        return SimpleNamespace(
            status_code=200,
            text='{"redirect":"https://payments.example/pix"}',
            url=url,
            headers={},
            json=lambda: {"redirect": "https://payments.example/pix"},
        )


def test_pix_checkout_defers_promotion_to_update(monkeypatch):
    monkeypatch.setenv("PP_PROMO_MODE", "campaign")
    monkeypatch.setenv("PP_PROMO_ID", "plus-1-month-free")
    chatgpt = FakeChatgptSession()

    checkout = pix.create_checkout(chatgpt, "BR")

    assert checkout["processor_entity"] == "openai_llc"
    assert "promo_campaign" not in chatgpt.last_body
    assert "coupon" not in chatgpt.last_body
    assert "promotion_code" not in chatgpt.last_body
    assert "subscription_data" not in chatgpt.last_body

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


def test_pix_seven_step_order_initializes_before_promotion(monkeypatch):
    events = []
    chatgpt = FakeChatgptSession()
    update_sessions = []
    update_countries = []
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
        lambda session, checkout, country: (
            update_sessions.append(session),
            update_countries.append(country),
            events.append("update"),
        ),
    )
    monkeypatch.setattr(pix, "record_proxy_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(pix, "record_checkout_zero_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pix,
        "update_pix_checkout_taxes",
        lambda session, *args, **kwargs: (
            update_sessions.append(session),
            events.append("chatgpt-tax"),
        ),
    )
    monkeypatch.setattr(
        pix,
        "stripe_update_tax_region",
        lambda *args, **kwargs: events.append("stripe-tax") or True,
    )

    def fake_init(*args, **kwargs):
        events.append("init")
        due = 2000 if events.count("init") == 1 else 0
        return {
            "mode": "subscription",
            "payment_method_types": ["card", "pix"],
            "automatic_payment_methods": True,
            "total_summary": {"due": due},
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
        "http://br-promotion-proxy",
        "http://br-provider-proxy",
        [],
        "device-test",
        checkout,
        billing,
        chatgpt_session=chatgpt,
    )

    assert redirect_url == "https://payments.example/pix"
    assert qr_urls == []
    assert events == ["init", "update", "chatgpt-tax", "stripe-tax", "init", "pm", "confirm"]
    assert update_sessions == [chatgpt, chatgpt]
    assert update_countries == ["BR"]


def test_pix_br_processor_entity_defaults_to_openai_llc(monkeypatch):
    monkeypatch.delenv("PIX_PROCESSOR_ENTITY", raising=False)

    assert pix.processor_entity_for_country("BR") == "openai_llc"


def test_pix_country_chain_uses_two_br_pools(monkeypatch):
    monkeypatch.delenv("PIX_PROMOTION_PROXY_FILE", raising=False)

    assert pix.PIX_BOOTSTRAP_COUNTRY == "BR"
    assert pix.PIX_PROMOTION_COUNTRIES == ["BR"]
    assert pix.PIX_PROVIDER_COUNTRY == "BR"
    assert pix.promotion_proxy_file().name == "vn_proxy_seeds.txt"


def test_pix_manual_chain_uses_independent_br_proxies(monkeypatch):
    monkeypatch.setattr(pix, "log", lambda *args, **kwargs: None)
    checkout_proxy = "http://main-br-proxy"
    promotion_proxy = "http://promotion-br-proxy"

    assert pix.pix_manual_proxy_chain(checkout_proxy, promotion_proxy) == (
        checkout_proxy,
        promotion_proxy,
        checkout_proxy,
    )


def test_pix_manual_chain_rejects_explicit_vn_proxy(monkeypatch):
    monkeypatch.setattr(pix, "proxy_label", lambda proxy: proxy)

    try:
        pix.pix_manual_proxy_chain(
            "http://user:password-BR-session@example.com:1000",
            "http://user:password-VN-session@example.com:1000",
        )
    except RuntimeError as exc:
        assert "必须是 BR" in str(exc)
    else:
        raise AssertionError("explicit VN proxy must be rejected")


def test_pix_poll_retries_transient_network_error(monkeypatch):
    stripe = FlakyPollStripeSession()
    monkeypatch.setenv("PIX_POLL_TIMEOUT", "5")
    monkeypatch.setenv("PIX_POLL_REQUEST_TIMEOUT", "2")
    monkeypatch.setattr(pix.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pix, "dump_http", lambda *args, **kwargs: None)
    monkeypatch.setattr(pix, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pix,
        "extract_redirect_url",
        lambda payload: str(payload.get("redirect") or ""),
    )

    redirect_url, qr_urls = pix.poll_payment_page(
        stripe,
        {"cs_id": "cs_test_poll"},
        "pk_test_pix",
        {},
        current_pm_id="pm_test_pix",
    )

    assert redirect_url == "https://payments.example/pix"
    assert qr_urls == []
    assert stripe.calls == 2
    assert stripe.timeouts == [2, 2]
