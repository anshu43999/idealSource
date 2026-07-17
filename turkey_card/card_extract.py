"""Turkey Card flow: TR checkout -> GB update -> TR Stripe/Card/approve."""

from __future__ import annotations

import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("IDEAL_BOOTSTRAP_COUNTRY", "TR")
os.environ.setdefault("IDEAL_PROMOTION_COUNTRY", "GB")
os.environ.setdefault("IDEAL_PROVIDER_COUNTRY", "TR")
os.environ.setdefault("IDEAL_CHECKOUT_COUNTRY", "TR")
os.environ.setdefault("IDEAL_BILLING_COUNTRY", "TR")
os.environ.setdefault("IDEAL_STRIPE_PAYMENT_METHOD", "card")
os.environ.setdefault("IDEAL_RESULT_LABEL", "Turkey Card 最终支付 URL")
os.environ.setdefault("IDEAL_DEFER_PROMO_TO_UPDATE", "1")
os.environ.setdefault("IDEAL_SKIP_BOOTSTRAP_INIT", "1")
os.environ.setdefault("IDEAL_CHECKOUT_PROXY_FILE", str(SCRIPT_DIR / "tr_proxy_seeds.txt"))
os.environ.setdefault("IDEAL_PROMOTION_PROXY_FILE", str(SCRIPT_DIR / "gb_proxy_seeds.txt"))
os.environ.setdefault("IDEAL_PROVIDER_PROXY_FILE", str(SCRIPT_DIR / "tr_proxy_seeds.txt"))

import ideal_qr_extract as flow


TR_NAMES = (
    ("Emir", "Yilmaz"),
    ("Kerem", "Demir"),
    ("Mert", "Kaya"),
    ("Selin", "Aydin"),
    ("Elif", "Arslan"),
)
TR_ADDRESSES = (
    ("Bagdat Caddesi 120", "Istanbul", "34728", "Istanbul"),
    ("Istiklal Caddesi 85", "Istanbul", "34430", "Istanbul"),
    ("Tunali Hilmi Caddesi 42", "Ankara", "06680", "Ankara"),
    ("Ataturk Caddesi 150", "Izmir", "35220", "Izmir"),
)


def normalize_card_number(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def card_number_is_valid(value: str) -> bool:
    number = normalize_card_number(value)
    if not 12 <= len(number) <= 19:
        return False
    total = 0
    parity = len(number) % 2
    for index, char in enumerate(number):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def card_details() -> dict[str, str]:
    number = normalize_card_number(os.environ.get("TURKEY_CARD_NUMBER", ""))
    month = os.environ.get("TURKEY_CARD_EXP_MONTH", "").strip()
    year = os.environ.get("TURKEY_CARD_EXP_YEAR", "").strip()
    cvc = re.sub(r"\D+", "", os.environ.get("TURKEY_CARD_CVC", ""))
    if not card_number_is_valid(number):
        raise RuntimeError("Turkey Card 卡号无效")
    if not month.isdigit() or not 1 <= int(month) <= 12:
        raise RuntimeError("Turkey Card 有效期月份无效")
    if not year.isdigit() or len(year) not in {2, 4}:
        raise RuntimeError("Turkey Card 有效期年份无效")
    if len(cvc) not in {3, 4}:
        raise RuntimeError("Turkey Card CVC 无效")
    return {
        "number": number,
        "exp_month": str(int(month)),
        "exp_year": year,
        "cvc": cvc,
    }


def turkey_billing_profile() -> dict[str, str]:
    first_name, last_name = random.choice(TR_NAMES)
    line1, city, postal_code, state = random.choice(TR_ADDRESSES)
    profile = {
        "email": flow.build_email(first_name, last_name),
        "name": f"{first_name} {last_name}",
        "country": "TR",
        "line1": line1,
        "line2": "",
        "city": city,
        "postal_code": postal_code,
        "state": state,
    }
    env_map = {
        "email": "IDEAL_EMAIL",
        "name": "TURKEY_CARD_HOLDER",
        "line1": "IDEAL_LINE1",
        "line2": "IDEAL_LINE2",
        "city": "IDEAL_CITY",
        "postal_code": "IDEAL_POSTAL_CODE",
        "state": "IDEAL_STATE",
    }
    for key, env_name in env_map.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            profile[key] = value
    return profile


def update_turkey_checkout_taxes(
    chatgpt: Any,
    checkout: dict[str, str],
    billing: dict[str, str],
) -> None:
    url = "https://chatgpt.com/backend-api/payments/checkout/taxes"
    body = {
        "checkout_session_id": checkout["cs_id"],
        "checkout_email": billing["email"],
        "billing_country": "TR",
        "billing_name": billing["name"],
        "currency": "TRY",
        "tax_id": None,
        "processor_entity": flow.processor_entity_for_country(
            "TR", checkout.get("processor_entity") or ""
        ),
        "billing_address": {
            "line1": billing["line1"],
            "city": billing["city"],
            "country": "TR",
            "postal_code": billing["postal_code"],
            "state": billing.get("state", ""),
        },
    }
    resp = chatgpt.post(
        url,
        json=body,
        headers={
            "Referer": flow.checkout_page_url(checkout),
            "x-openai-target-path": "/backend-api/payments/checkout/taxes",
            "x-openai-target-route": "/backend-api/payments/checkout/taxes",
        },
        timeout=flow.CHATGPT_TIMEOUT,
    )
    flow.dump_http(resp, "turkey_checkout_taxes", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        if flow.is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        raise RuntimeError(f"Turkey checkout/taxes 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    flow.log("TR checkout/taxes 同步成功")


def stripe_create_card_pm(
    stripe: Any,
    cs_id: str,
    stripe_pk: str,
    billing: dict[str, str],
    ctx: dict[str, Any],
) -> str:
    card = card_details()
    body = {
        "type": "card",
        "card[number]": card["number"],
        "card[exp_month]": card["exp_month"],
        "card[exp_year]": card["exp_year"],
        "card[cvc]": card["cvc"],
        "billing_details[name]": billing["name"],
        "billing_details[email]": billing["email"],
        "billing_details[address][country]": "TR",
        "billing_details[address][line1]": billing["line1"],
        "billing_details[address][city]": billing["city"],
        "billing_details[address][postal_code]": billing["postal_code"],
        "billing_details[address][state]": billing.get("state", ""),
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "key": stripe_pk,
    }
    url = "https://api.stripe.com/v1/payment_methods"
    resp = stripe.post(url, data=body, timeout=flow.DEFAULT_TIMEOUT)
    safe_body = {
        "type": "card",
        "last4": card["number"][-4:],
        "billing_country": "TR",
        "checkout_session_id": cs_id,
    }
    flow.dump_http(resp, "turkey_card_pm", safe_body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        raise RuntimeError(f"创建 Turkey Card PM 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    pm_id = str((resp.json() or {}).get("id") or "")
    if not pm_id.startswith("pm_"):
        raise RuntimeError(f"创建 Turkey Card PM 响应异常: {resp.text[:300]}")
    return pm_id


def stripe_confirm_card(
    stripe: Any,
    cs_id: str,
    pm_id: str,
    stripe_pk: str,
    init_payload: dict[str, Any],
    ctx: dict[str, Any],
    checkout: dict[str, str],
    stripe_hosted_url: str,
    billing: dict[str, str],
) -> dict[str, Any]:
    runtime_version = str(ctx.get("runtime_version") or flow.DEFAULT_STRIPE_RUNTIME_VERSION)
    body = {
        "eid": "NA",
        "expected_amount": os.environ.get("PP_EXPECTED_AMOUNT", "").strip()
        or str(ctx.get("checkout_amount") or flow.amount_from_payload(init_payload)),
        "expected_payment_method_type": "card",
        "payment_method": pm_id,
        "return_url": flow.stripe_confirm_return_url(cs_id, checkout, stripe_hosted_url),
        "_stripe_version": str(ctx.get("stripe_version") or flow.STRIPE_VERSION_FULL),
        "guid": str(ctx.get("guid") or flow.stripe_browser_id()),
        "muid": str(ctx.get("muid") or flow.stripe_browser_id()),
        "sid": str(ctx.get("sid") or flow.stripe_browser_id()),
        "key": stripe_pk,
        "version": runtime_version,
        "init_checksum": str(init_payload.get("init_checksum") or ctx.get("init_checksum") or ""),
        "client_attribution_metadata[client_session_id]": str(
            ctx.get("client_session_id") or ctx["stripe_js_id"]
        ),
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "client_attribution_metadata[checkout_config_id]": ctx.get("config_id") or "",
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[elements_session_id]": ctx["elements_session_id"],
        "client_attribution_metadata[elements_session_config_id]": ctx["elements_session_config_id"],
        "consent[terms_of_service]": "accepted",
        "link_brand": "link",
    }
    body.update(flow.stripe_elements_session_params(ctx))
    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}/confirm"
    resp = stripe.post(url, data=body, timeout=flow.DEFAULT_TIMEOUT)
    flow.dump_http(resp, "turkey_card_confirm", body, "POST", url, force=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"Turkey Card confirm 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json() or {}


def card_terminal_success(payload: Any) -> bool:
    if isinstance(payload, dict):
        object_type = str(payload.get("object") or "").lower()
        status = str(payload.get("status") or "").lower()
        payment_status = str(payload.get("payment_status") or "").lower()
        state = str(payload.get("state") or "").lower()
        if object_type in {"payment_intent", "setup_intent"} and status in {
            "succeeded",
            "requires_capture",
        }:
            return True
        if object_type == "checkout.session" and (
            status == "complete" or payment_status in {"paid", "no_payment_required"}
        ):
            return True
        if state in {"approved", "complete", "completed", "succeeded"}:
            return True
        return any(card_terminal_success(value) for value in payload.values())
    if isinstance(payload, list):
        return any(card_terminal_success(value) for value in payload)
    return False


def poll_card_result(
    stripe: Any,
    checkout: dict[str, str],
    stripe_pk: str,
    ctx: dict[str, Any],
    pm_id: str,
) -> str:
    deadline = time.time() + flow.env_int("IDEAL_POLL_TIMEOUT", 45)
    params = {
        **flow.stripe_elements_session_params(ctx),
        "key": stripe_pk,
        "_stripe_version": str(ctx.get("stripe_version") or flow.STRIPE_VERSION_FULL),
    }
    url = f"https://api.stripe.com/v1/payment_pages/{checkout['cs_id']}"
    last_error = "waiting"
    while time.time() < deadline:
        try:
            resp = stripe.get(url, params=params, timeout=min(flow.DEFAULT_TIMEOUT, 10))
        except Exception as exc:
            last_error = f"network: {type(exc).__name__}: {str(exc)[:160]}"
            time.sleep(1)
            continue
        if resp.status_code >= 400:
            if flow.is_checkout_not_active_error(resp.text):
                raise RuntimeError("checkout_not_active_session")
            last_error = f"HTTP {resp.status_code}"
            time.sleep(1)
            continue
        payload = resp.json() or {}
        flow.raise_if_setup_intent_blocked(payload, "Turkey Card poll", current_pm_id=pm_id)
        redirect_url = flow.extract_redirect_url(payload)
        if not redirect_url:
            redirect_url = flow.stripe_payload_intent_redirect_url(
                stripe, payload, stripe_pk, current_pm_id=pm_id
            )
        if redirect_url:
            return redirect_url
        if card_terminal_success(payload):
            return flow.checkout_page_url(checkout)
        submission = flow.find_submission_attempt(payload)
        if submission.get("state") == "failed":
            raise RuntimeError(f"Turkey Card submission failed: {submission}")
        last_error = str(submission or "waiting")
        time.sleep(1)
    raise RuntimeError(f"Turkey Card result timeout: {last_error}")


def resolve_confirm_payload_card(
    stripe: Any,
    confirm_payload: dict[str, Any],
    checkout: dict[str, str],
    stripe_pk: str,
    ctx: dict[str, Any],
    pm_id: str,
    access_token: str,
    device_id: str,
    session_token: str,
    checkout_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
) -> tuple[str, list[str], str]:
    flow.raise_if_setup_intent_blocked(confirm_payload, "Turkey Card confirm", current_pm_id=pm_id)
    redirect_url = flow.extract_redirect_url(confirm_payload)
    if not redirect_url:
        redirect_url = flow.stripe_payload_intent_redirect_url(
            stripe, confirm_payload, stripe_pk, current_pm_id=pm_id
        )
    submission = flow.find_submission_attempt(confirm_payload)
    approve_proxy = ""
    if not redirect_url and submission.get("state") == "requires_approval":
        approve_proxies = flow.approve_proxy_candidates(
            checkout_proxy, provider_proxy, approve_pool
        )
        approve_proxy = flow.approve_with_retry(
            access_token,
            device_id,
            checkout,
            approve_proxies,
            session_token,
            "provider",
        )
        redirect_url = poll_card_result(stripe, checkout, stripe_pk, ctx, pm_id)
    elif not redirect_url:
        redirect_url = (
            flow.checkout_page_url(checkout)
            if card_terminal_success(confirm_payload)
            else poll_card_result(stripe, checkout, stripe_pk, ctx, pm_id)
        )
    return redirect_url, [], approve_proxy


def configure_flow() -> None:
    flow.SCRIPT_DIR = SCRIPT_DIR
    flow.LOG_DIR = SCRIPT_DIR / "logs"
    flow.DUMP_DIR = SCRIPT_DIR / "dumps"
    flow.LOG_DIR.mkdir(parents=True, exist_ok=True)
    flow.DUMP_DIR.mkdir(parents=True, exist_ok=True)
    flow._log_file = flow.LOG_DIR / f"turkey_card_{time.strftime('%Y%m%d-%H%M%S')}.log"
    flow.COUNTRY_CURRENCY.update({"TR": "TRY", "GB": "GBP"})
    flow.IDEAL_BOOTSTRAP_COUNTRY = "TR"
    flow.IDEAL_PROMOTION_COUNTRY = "GB"
    flow.IDEAL_PROVIDER_COUNTRY = "TR"
    flow.EXPECTED_PAYMENT_METHOD_TYPE = "card"
    flow.RESULT_LABEL = "Turkey Card 最终支付 URL"
    flow.IDEAL_UNAVAILABLE_ERROR = "当前账号支付方式不支持 Card"
    flow.ideal_billing_profile = turkey_billing_profile
    flow.update_ideal_checkout_taxes = update_turkey_checkout_taxes
    flow.stripe_create_ideal_pm = stripe_create_card_pm
    flow.stripe_confirm_ideal = stripe_confirm_card
    flow.resolve_confirm_payload_ideal = resolve_confirm_payload_card


configure_flow()


if __name__ == "__main__":
    raise SystemExit(flow.main())

