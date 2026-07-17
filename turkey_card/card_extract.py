"""Turkey Card flow: TR checkout -> GB update -> TR manual card page."""

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
    """Apply the GB promotion, validate Card in TR, and return the hosted form."""
    del approve_pool, billing
    if stop_event and stop_event.is_set():
        raise RuntimeError("任务已停止，跳过本轮")

    flow.log(f"GB checkout/update: proxy={flow.proxy_label(promotion_proxy)}")
    try:
        chatgpt = flow.build_chatgpt_session(
            access_token, device_id, promotion_proxy, session_token
        )
        flow.update_checkout_promotion(chatgpt, checkout)
    except Exception as exc:
        if flow.is_checkout_not_active_error(exc):
            raise
        raise RuntimeError(f"promotion 阶段失败: {exc}") from exc
    flow.record_proxy_result(
        "promotion", promotion_proxy, True, "promotion_update_success"
    )

    if stop_event and stop_event.is_set():
        raise RuntimeError("任务已停止，跳过本轮")

    flow.log(
        "GB checkout/update 后通过 TR Stripe Init 校验 Card: "
        f"proxy={flow.proxy_label(provider_proxy)}"
    )
    stripe_pk = checkout.get("stripe_pk") or flow.DEFAULT_STRIPE_PK
    init_payload = flow.stripe_init(checkout["cs_id"], stripe_pk, provider_proxy)
    methods_value = flow.first_value_by_key(init_payload, "payment_method_types")
    methods = (
        [str(item).lower() for item in methods_value]
        if isinstance(methods_value, list)
        else []
    )
    flow.log(f"Stripe 可用支付方式: {methods}")
    if "card" not in methods:
        raise RuntimeError(
            f"{flow.IDEAL_UNAVAILABLE_ERROR}: payment_method_types={methods}"
        )

    amount = flow.amount_from_payload(init_payload)
    flow.log(f"TR Stripe Init 成功, 金额={checkout['currency']} {amount / 100:.2f}")
    flow.record_checkout_zero_result(checkout_proxy, "TR", amount)
    if amount != 0:
        raise RuntimeError(
            f"0 元优惠未生效，当前金额小单位={amount}，已停止生成手动 Card 支付链接"
        )

    hosted_url = str(init_payload.get("stripe_hosted_url") or "")
    manual_url = flow.to_openai_pay_url(hosted_url) or flow.checkout_page_url(checkout)
    flow.log(f"Card 渠道可用，返回手动填写卡片页面: {manual_url[:180]}")
    return manual_url, []


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
    flow.run_provider_flow = run_manual_card_flow


configure_flow()


if __name__ == "__main__":
    raise SystemExit(flow.main())
