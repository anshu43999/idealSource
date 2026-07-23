from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_upi_module(name: str = "upi_extract_opll2_flow"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "upi" / "upi_extract.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeChatGPT:
    def __init__(self):
        self.posts = []

    def post(self, url, json, headers, timeout):
        self.posts.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse(
            {
                "checkout_session_id": "cs_live_test_upi",
                "stripe_publishable_key": "pk_live_test",
                "processor_entity": "openai_llc",
                "promo_campaign": {"promo_campaign_id": "plus-1-month-free"},
            }
        )


class FakeStripe:
    def __init__(self):
        self.headers = {}


def test_create_checkout_uses_initial_campaign_promo(monkeypatch):
    monkeypatch.delenv("PP_PROMO_MODE", raising=False)
    monkeypatch.setenv("PP_PROMO_ID", "plus-1-month-free")
    upi = load_upi_module("upi_extract_checkout_campaign")
    monkeypatch.setattr(upi, "dump_http", lambda *args, **kwargs: None)

    chatgpt = FakeChatGPT()
    checkout = upi.create_checkout(chatgpt, "IN")

    body = chatgpt.posts[0]["json"]
    assert checkout["billing_country"] == "IN"
    assert checkout["currency"] == "INR"
    assert checkout["promo_mode"] == "campaign"
    assert body["billing_details"] == {"country": "IN", "currency": "INR"}
    assert body["promo_campaign"] == {
        "promo_campaign_id": "plus-1-month-free",
        "is_coupon_from_query_param": False,
    }


def test_run_provider_flow_skips_checkout_update_promotion(monkeypatch):
    monkeypatch.setenv("UPI_REQUIRE_ZERO", "1")
    monkeypatch.setenv("UPI_UPDATE_TAX_REGION", "0")
    upi = load_upi_module("upi_extract_provider_opll2")

    stripe_init_proxies = []

    def fake_stripe_init(cs_id, stripe_pk, proxy):
        stripe_init_proxies.append(proxy)
        return {
            "stripe_hosted_url": "https://checkout.stripe.test/hosted",
            "payment_method_types": ["upi"],
        }

    def fail_if_checkout_update_is_called(*args, **kwargs):
        raise AssertionError("UPI opll2 flow must not call checkout/update promotion")

    monkeypatch.setattr(upi, "stripe_init", fake_stripe_init)
    monkeypatch.setattr(upi, "build_ctx", lambda payload, checkout: {"checkout_amount": "0"})
    monkeypatch.setattr(upi, "update_checkout_promotion", fail_if_checkout_update_is_called)
    monkeypatch.setattr(upi, "new_session", lambda proxy: FakeStripe())
    monkeypatch.setattr(upi, "random_user_agent", lambda: "pytest-agent")
    monkeypatch.setattr(upi, "payment_accept_language", lambda: "en-IN,en;q=0.9")
    monkeypatch.setattr(upi, "stripe_create_upi_pm", lambda *args, **kwargs: "pm_test_upi")
    monkeypatch.setattr(upi, "stripe_confirm_upi", lambda *args, **kwargs: {"submission_attempt": {"state": "ok"}})
    monkeypatch.setattr(
        upi,
        "resolve_confirm_payload_upi",
        lambda *args, **kwargs: ("https://hooks.stripe.test/upi", ["upi://pay"], ""),
    )

    redirect_url, qr_urls = upi.run_provider_flow(
        access_token="access",
        session_token="session",
        checkout_proxy="http://checkout.proxy:8080",
        promotion_proxy="http://promotion.proxy:8080",
        provider_proxy="http://provider.proxy:8080",
        approve_pool=["http://provider.proxy:8080"],
        device_id="device",
        checkout={
            "cs_id": "cs_live_test_upi",
            "stripe_pk": "pk_live_test",
            "currency": "INR",
            "billing_country": "IN",
            "processor_entity": "openai_llc",
        },
        billing={
            "email": "aisha@example.invalid",
            "name": "Aisha Sharma",
            "country": "IN",
            "line1": "24 Park Street",
            "city": "Kolkata",
            "postal_code": "700016",
            "state": "WB",
        },
    )

    assert redirect_url == "https://hooks.stripe.test/upi"
    assert qr_urls == ["upi://pay"]
    assert stripe_init_proxies == ["http://checkout.proxy:8080", "http://provider.proxy:8080"]
