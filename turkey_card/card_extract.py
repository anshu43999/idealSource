"""Turkey Card flow: US checkout -> TR promotion -> US manual card page."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("IDEAL_BOOTSTRAP_COUNTRY", "US")
os.environ.setdefault("IDEAL_PROMOTION_COUNTRY", "TR")
os.environ.setdefault("IDEAL_PROVIDER_COUNTRY", "US")
os.environ.setdefault("IDEAL_CHECKOUT_COUNTRY", "US")
os.environ.setdefault("IDEAL_BILLING_COUNTRY", "US")
os.environ.setdefault("IDEAL_STRIPE_PAYMENT_METHOD", "card")
os.environ.setdefault("IDEAL_RESULT_LABEL", "Turkey Card 最终支付 URL")
os.environ["PP_PROMO_MODE"] = "campaign"
os.environ.setdefault("IDEAL_DEFER_PROMO_TO_UPDATE", "0")
os.environ.setdefault("IDEAL_SKIP_BOOTSTRAP_INIT", "1")
os.environ.setdefault("IDEAL_CHECKOUT_PROXY_FILE", str(SCRIPT_DIR / "us_proxy_seeds.txt"))
os.environ.setdefault("IDEAL_PROMOTION_PROXY_FILE", str(SCRIPT_DIR / "tr_proxy_seeds.txt"))
os.environ.setdefault("IDEAL_PROVIDER_PROXY_FILE", str(SCRIPT_DIR / "us_proxy_seeds.txt"))

import ideal_qr_extract as flow


US_BILLING = {
    "email": "redacted@example.invalid",
    "name": "John Smith",
    "country": "US",
    "line1": "350 Fifth Avenue",
    "line2": "",
    "city": "New York",
    "postal_code": "10118",
    "state": "NY",
}


def us_billing_profile() -> dict[str, str]:
    profile = dict(US_BILLING)
    env_map = {
        "email": "IDEAL_EMAIL",
        "name": "IDEAL_NAME",
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
    profile["country"] = "US"
    return profile


def update_turkey_checkout_promotion(
    chatgpt: Any,
    checkout: dict[str, str],
) -> None:
    mode = os.environ.get("PP_PROMO_MODE", "campaign").strip().lower() or "campaign"
    promo_id = os.environ.get("PP_PROMO_ID", "plus-1-month-free").strip() or "plus-1-month-free"
    body: dict[str, Any] = {
        "checkout_session_id": checkout["cs_id"],
        "processor_entity": flow.processor_entity_for_country(
            flow.IDEAL_BOOTSTRAP_COUNTRY,
            checkout.get("processor_entity") or "",
        ),
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
        "billing_details": {
            "country": "TR",
            "currency": flow.currency_for_country("TR"),
        },
    }
    if mode in {"campaign", "query", "coupon"}:
        body["promo_campaign"] = {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": mode == "query",
        }
    url = "https://chatgpt.com/backend-api/payments/checkout/update"
    resp = chatgpt.post(
        url,
        json=body,
        headers={
            "Referer": flow.checkout_page_url(checkout),
            "x-openai-target-path": "/backend-api/payments/checkout/update",
            "x-openai-target-route": "/backend-api/payments/checkout/update",
        },
        timeout=flow.CHATGPT_TIMEOUT,
    )
    flow.dump_http(
        resp,
        "turkey_checkout_update",
        body,
        "POST",
        url,
        force=resp.status_code >= 400,
    )
    if resp.status_code >= 400:
        if flow.is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        raise RuntimeError(f"TR checkout/update 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        payload = resp.json() or {}
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"TR checkout/update rejected: {str(payload)[:500]}")
    new_cs_id = ""
    if isinstance(payload, dict):
        for key in ("checkout_session_id", "session_id", "id"):
            value = str(payload.get(key) or "")
            if value.startswith("cs_"):
                new_cs_id = value
                break
        if not new_cs_id:
            value = str(flow.first_value_by_key(payload, "checkout_session_id") or "")
            if value.startswith("cs_"):
                new_cs_id = value
        raw_pk = (
            payload.get("stripe_publishable_key")
            or payload.get("publishable_key")
            or payload.get("publishableKey")
            or payload.get("stripePublishableKey")
            or payload.get("key")
            or ""
        )
        if raw_pk:
            checkout["stripe_pk"] = str(raw_pk)
        processor = payload.get("processor_entity") or payload.get("processorEntity")
        if processor:
            checkout["processor_entity"] = str(processor)
    if new_cs_id and new_cs_id != checkout["cs_id"]:
        flow.log(f"TR checkout/update 返回新 checkout_session_id，切换 session: {new_cs_id}")
        checkout["cs_id"] = new_cs_id
    checkout.update(flow.checkout_page_fields_from_payload(payload))
    keys = ",".join(sorted(payload.keys())[:12]) if isinstance(payload, dict) else type(payload).__name__
    flow.log(
        f"TR checkout/update 成功: billing=TR/{flow.currency_for_country('TR')}, "
        f"promo={promo_id if 'promo_campaign' in body else 'off'}, "
        f"new_cs={'yes' if new_cs_id else 'none'}, update_keys={keys or 'none'}"
    )


def update_us_checkout_taxes(
    chatgpt: Any,
    checkout: dict[str, str],
    billing: dict[str, str],
) -> None:
    url = "https://chatgpt.com/backend-api/payments/checkout/taxes"
    body = {
        "checkout_session_id": checkout["cs_id"],
        "checkout_email": billing["email"],
        "billing_country": "US",
        "billing_name": billing["name"],
        "currency": flow.currency_for_country("US"),
        "tax_id": None,
        "processor_entity": flow.processor_entity_for_country(
            flow.IDEAL_BOOTSTRAP_COUNTRY,
            checkout.get("processor_entity") or "",
        ),
        "billing_address": {
            "line1": billing["line1"],
            "city": billing["city"],
            "country": "US",
            "postal_code": billing["postal_code"],
        },
    }
    if billing.get("state"):
        body["billing_address"]["state"] = billing["state"]
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
        raise RuntimeError(f"US checkout/taxes 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    flow.log(f"US checkout/taxes 同步成功: currency={flow.currency_for_country('US')}")


def enforce_us_checkout_identity(checkout: dict[str, str]) -> None:
    processor = str(checkout.get("processor_entity") or "")
    page_processor = str(checkout.get("checkout_page_processor") or "")
    if processor and processor != "openai_llc":
        raise RuntimeError(f"US 最终 session processor 不一致: {processor}")
    if page_processor and page_processor != "openai_llc":
        raise RuntimeError(f"US 最终短链 processor 不一致: {page_processor}")
    checkout.update(
        {
            "billing_country": "US",
            "currency": "USD",
            "processor_entity": "openai_llc",
            "checkout_page_processor": "openai_llc",
        }
    )


def oaics_checkout_url(checkout: dict[str, str]) -> str:
    page_id = str(checkout.get("checkout_page_id") or "").strip()
    if not page_id.startswith("oaics_"):
        current_id = page_id or str(checkout.get("cs_id") or "unknown")
        raise RuntimeError(
            "checkout/update 未返回 oaics_ 短链标识，"
            f"当前仅有 {current_id}，已跳过该轮并重试"
        )
    processor = str(checkout.get("checkout_page_processor") or "openai_llc")
    if processor != "openai_llc":
        raise RuntimeError(f"oaics_ 短链 processor 不是 openai_llc: {processor}")
    return f"https://chatgpt.com/checkout/openai_llc/{page_id}"


def inspect_card_init(
    checkout: dict[str, str],
    init_payload: dict[str, Any],
    stage: str,
) -> tuple[dict[str, Any], int]:
    methods_value = flow.first_value_by_key(init_payload, "payment_method_types")
    methods = (
        [str(item).lower() for item in methods_value]
        if isinstance(methods_value, list)
        else []
    )
    flow.log(f"{stage} Stripe 可用支付方式: {methods}")
    if "card" not in methods:
        raise RuntimeError(
            f"{flow.IDEAL_UNAVAILABLE_ERROR}: payment_method_types={methods}"
        )

    total_summary = init_payload.get("total_summary")
    invoice = init_payload.get("invoice")
    if not (
        isinstance(total_summary, dict) and total_summary.get("due") is not None
    ) and not (
        isinstance(invoice, dict) and invoice.get("amount_due") is not None
    ):
        raise RuntimeError(f"{stage} Stripe Init 缺少可确认的应付金额字段")
    amount = flow.amount_from_payload(init_payload)
    currency_value = flow.first_value_by_key(init_payload, "currency")
    currency = str(currency_value or checkout.get("currency") or "").upper()
    if not currency:
        currency = "UNKNOWN"
    flow.log(f"{stage} Stripe Init 成功, 金额={currency} {amount / 100:.2f}")
    return flow.build_ctx(init_payload, checkout), amount


def activate_turkey_checkout(checkout: dict[str, str], checkout_proxy: str) -> None:
    session = flow.new_session(checkout_proxy)
    session.headers.update(
        {
            "User-Agent": flow.random_user_agent(),
            "Accept-Language": flow.payment_accept_language(),
        }
    )
    urls = [
        flow.checkout_page_url(checkout),
        f"https://pay.openai.com/c/pay/{checkout['cs_id']}",
        f"https://checkout.stripe.com/c/pay/{checkout['cs_id']}",
    ]
    for index, url in enumerate(urls, start=1):
        try:
            resp = session.get(url, timeout=flow.DEFAULT_TIMEOUT, allow_redirects=True)
            flow.dump_http(resp, f"turkey_activate_checkout_{index}", None, "GET", url, force=resp.status_code >= 400)
        except Exception as exc:
            flow.log(f"US checkout 页面激活异常: {exc}", "[WARN] ")
    flow.log("US checkout 页面已按 Kakao 链路预热")


def run_manual_card_flow(
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
    device_id: str,
    checkout: dict[str, str],
    billing: dict[str, str],
    stop_event: Any = None,
) -> tuple[str, list[str]]:
    """Create in US, apply the promotion through TR, then finalize in US."""
    del approve_pool, billing
    us_billing = us_billing_profile()
    stripe_pk = checkout.get("stripe_pk") or flow.DEFAULT_STRIPE_PK

    def manual_card_url(payload: dict[str, Any]) -> str:
        del payload
        enforce_us_checkout_identity(checkout)
        return oaics_checkout_url(checkout)

    if stop_event and stop_event.is_set():
        raise RuntimeError("任务已停止，跳过本轮")

    flow.log(f"US Bootstrap Stripe Init: proxy={flow.proxy_label(checkout_proxy)}")
    activate_turkey_checkout(checkout, checkout_proxy)
    bootstrap_payload = flow.stripe_init(checkout["cs_id"], stripe_pk, checkout_proxy)
    _bootstrap_ctx, bootstrap_amount = inspect_card_init(
        checkout, bootstrap_payload, "US Bootstrap"
    )
    if bootstrap_amount == 0:
        flow.log(
            "US Bootstrap 已经是 0 元；跳过会重算价格的 TR checkout/update，"
            "直接用 US Provider 刷新并输出手动 Card 页面"
        )
        us_payload = flow.stripe_init(checkout["cs_id"], stripe_pk, provider_proxy)
        _us_ctx, us_amount = inspect_card_init(checkout, us_payload, "US Provider 直接刷新")
        flow.record_checkout_zero_result(provider_proxy, "US", us_amount)
        if us_amount != 0:
            raise RuntimeError(f"US Provider 最终金额不是 0: {us_amount}")
        manual_url = manual_card_url(us_payload)
        flow.log(f"US Provider direct refresh Card URL: {manual_url[:180]}")
        return manual_url, []

    if stop_event and stop_event.is_set():
        raise RuntimeError("任务已停止，跳过本轮")

    flow.log(f"TR checkout/update: proxy={flow.proxy_label(promotion_proxy)}")
    try:
        promotion_chatgpt = flow.build_chatgpt_session(
            access_token, device_id, promotion_proxy, session_token
        )
        update_turkey_checkout_promotion(promotion_chatgpt, checkout)
    except Exception as exc:
        if flow.is_checkout_not_active_error(exc):
            raise
        raise RuntimeError(f"promotion 阶段失败: {exc}") from exc
    flow.record_proxy_result(
        "promotion", promotion_proxy, True, "promotion_update_success"
    )
    stripe_pk = checkout.get("stripe_pk") or flow.DEFAULT_STRIPE_PK

    if stop_event and stop_event.is_set():
        raise RuntimeError("任务已停止，跳过本轮")

    flow.log(
        "TR checkout/update 后回到 US checkout/taxes 与 Stripe Init 校验 Card/0 元: "
        f"proxy={flow.proxy_label(provider_proxy)}"
    )
    enforce_us_checkout_identity(checkout)
    final_chatgpt = flow.build_chatgpt_session(
        access_token, device_id, provider_proxy, session_token
    )
    update_us_checkout_taxes(final_chatgpt, checkout, us_billing)
    init_payload = flow.stripe_init(checkout["cs_id"], stripe_pk, provider_proxy)
    ctx, amount = inspect_card_init(checkout, init_payload, "US checkout/taxes 后")
    flow.log("提交 US Stripe tax_region 并重新刷新 Stripe Init...")
    stripe = flow.new_session(provider_proxy)
    stripe.headers.update(
        {
            "User-Agent": flow.random_user_agent(),
            "Accept-Language": flow.payment_accept_language(),
        }
    )
    if not flow.stripe_update_tax_region(stripe, checkout["cs_id"], stripe_pk, ctx, us_billing):
        raise RuntimeError("US Stripe tax_region 提交失败，无法确认 0 元状态")
    init_payload = flow.stripe_init(checkout["cs_id"], stripe_pk, provider_proxy)
    _ctx, amount = inspect_card_init(checkout, init_payload, "US tax_region 后")
    flow.record_checkout_zero_result(provider_proxy, "US", amount)
    if amount != 0:
        raise RuntimeError(
            f"0 元优惠未生效，当前金额小单位={amount}，已停止生成手动 Card 支付链接"
        )

    manual_url = manual_card_url(init_payload)
    flow.log(f"US Card 渠道可用且最终金额为 0，返回手动填写卡片页面: {manual_url[:180]}")
    return manual_url, []


def configure_flow() -> None:
    flow.SCRIPT_DIR = SCRIPT_DIR
    flow.LOG_DIR = SCRIPT_DIR / "logs"
    flow.DUMP_DIR = SCRIPT_DIR / "dumps"
    flow.LOG_DIR.mkdir(parents=True, exist_ok=True)
    flow.DUMP_DIR.mkdir(parents=True, exist_ok=True)
    flow._log_file = flow.LOG_DIR / f"turkey_card_{time.strftime('%Y%m%d-%H%M%S')}.log"
    # TR is only used to apply the promotion; the payable session is finalized in US.
    flow.COUNTRY_CURRENCY.update({"TR": "USD", "US": "USD"})
    flow.IDEAL_BOOTSTRAP_COUNTRY = "US"
    flow.IDEAL_PROMOTION_COUNTRY = "TR"
    flow.IDEAL_PROVIDER_COUNTRY = "US"
    flow.EXPECTED_PAYMENT_METHOD_TYPE = "card"
    flow.RESULT_LABEL = "Turkey Card 最终支付 URL"
    flow.IDEAL_UNAVAILABLE_ERROR = "当前账号支付方式不支持 Card"
    flow.run_provider_flow = run_manual_card_flow


configure_flow()


if __name__ == "__main__":
    raise SystemExit(flow.main())
