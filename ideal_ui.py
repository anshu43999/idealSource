from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
IDEAL_SCRIPT_PATH = ROOT / "ideal_qr_extract.py"
UI_PATH = ROOT / "ideal_ui.html"
PROXY_SEED_PATH = ROOT / "proxy_seeds.txt"
IDEAL_PRIMARY_PROXY_SEED_PATH = ROOT / "nl_proxy_seeds.txt"
IDEAL_PROMOTION_PROXY_SEED_PATH = ROOT / "vn_proxy_seeds.txt"
BLIK_DIR = ROOT / "blik"
BLIK_SCRIPT_PATH = BLIK_DIR / "blik_qr_extract.py"
BLIK_PROXY_SEED_PATH = BLIK_DIR / "proxy_seeds.txt"
BLIK_TOKEN_PATH = BLIK_DIR / "token.txt"
KAKAO_DIR = ROOT / "kakao"
KAKAO_SCRIPT_PATH = KAKAO_DIR / "kakao_extract.py"
KAKAO_PROXY_SEED_PATH = KAKAO_DIR / "proxy_seeds.txt"
KAKAO_KR_PROXY_SEED_PATH = KAKAO_DIR / "kr_proxy_seeds.txt"
KAKAO_VN_PROXY_SEED_PATH = KAKAO_DIR / "vn_proxy_seeds.txt"
KAKAO_TOKEN_PATH = KAKAO_DIR / "token.txt"
PIX_DIR = ROOT / "pix"
PIX_SCRIPT_PATH = PIX_DIR / "pix_extract.py"
PIX_PROXY_SEED_PATH = PIX_DIR / "proxy_seeds.txt"
PIX_PRIMARY_PROXY_SEED_PATH = PIX_DIR / "br_proxy_seeds.txt"
# Keep the existing bind-mounted filename as the second BR pool for compatibility.
PIX_PROMOTION_PROXY_SEED_PATH = PIX_DIR / "vn_proxy_seeds.txt"
PIX_TOKEN_PATH = PIX_DIR / "token.txt"
TWINT_DIR = ROOT / "twint"
TWINT_SCRIPT_PATH = TWINT_DIR / "twint_extract.py"
TWINT_PROXY_SEED_PATH = TWINT_DIR / "proxy_seeds.txt"
TWINT_PRIMARY_PROXY_SEED_PATH = TWINT_DIR / "ch_proxy_seeds.txt"
TWINT_PROMOTION_PROXY_SEED_PATH = TWINT_DIR / "vn_proxy_seeds.txt"
TWINT_TOKEN_PATH = TWINT_DIR / "token.txt"
UPI_DIR = ROOT / "upi"
UPI_SCRIPT_PATH = UPI_DIR / "upi_extract.py"
UPI_PROXY_SEED_PATH = UPI_DIR / "proxy_seeds.txt"
UPI_PRIMARY_PROXY_SEED_PATH = UPI_DIR / "in_proxy_seeds.txt"
UPI_PROMOTION_PROXY_SEED_PATH = UPI_DIR / "vn_proxy_seeds.txt"
UPI_TOKEN_PATH = UPI_DIR / "token.txt"
LEGACY_PROXY_PATHS = (
    ROOT / "checkout.json",
    ROOT / "promotion.json",
    ROOT / "provider.json",
)
TOKEN_PATH = ROOT / "token.txt"
MAX_LOG_LINES = 3000

PAYMENT_METHODS: dict[str, dict[str, Any]] = {
    "ideal": {
        "label": "iDEAL",
        "flow": "NL/VN/NL",
        "available": True,
        "script_path": IDEAL_SCRIPT_PATH,
        "result_marker": "iDEAL 最终扫码/授权 URL:",
    },
    "pix": {
        "label": "PIX",
        "flow": "BR/BR/BR",
        "available": True,
        "script_path": PIX_SCRIPT_PATH,
        "result_marker": "PIX 最终支付 URL:",
    },
    "kakao_pay": {
        "label": "Kakao Pay",
        "flow": "KR/VN/KR",
        "available": True,
        "script_path": KAKAO_SCRIPT_PATH,
        "result_marker": "Kakao/Nicepay 最终跳转 URL:",
    },
    "blik": {
        "label": "BLIK",
        "flow": "PL/PL/PL",
        "available": True,
        "script_path": BLIK_SCRIPT_PATH,
        "result_marker": "",
    },
    "twint": {
        "label": "TWINT",
        "flow": "CH/VN/CH",
        "available": True,
        "script_path": TWINT_SCRIPT_PATH,
        "result_marker": "TWINT 最终支付 URL:",
    },
    "upi": {
        "label": "UPI",
        "flow": "IN/VN/IN",
        "available": True,
        "script_path": UPI_SCRIPT_PATH,
        "result_marker": "UPI 最终支付 URL:",
    },
}

PAYMENT_CHAIN_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "ideal": ("NL", "VN", "NL"),
    "pix": ("BR", "BR", "BR"),
    "kakao_pay": ("KR", "VN", "KR"),
    "twint": ("CH", "VN", "CH"),
    "upi": ("IN", "VN", "IN"),
}
MANUAL_PROXY_METHODS = {"ideal", "pix", "kakao_pay", "twint", "upi"}
COUNTRY_CODE_RE = re.compile(r"[A-Z]{2}")


def public_payment_methods() -> list[dict[str, Any]]:
    return [
        {
            "id": method_id,
            "label": method["label"],
            "flow": method["flow"],
            "available": method["available"],
        }
        for method_id, method in PAYMENT_METHODS.items()
    ]


def resolve_payment_method(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    method_id = clean_text(payload, "payment_method", "ideal", 40).lower()
    method = PAYMENT_METHODS.get(method_id)
    if method is None:
        raise ValueError("支付方式不受支持")
    if not method["available"]:
        raise ValueError(f"{method['label']} 链路尚未接入，当前不可启动任务")
    script_path = method["script_path"]
    if not isinstance(script_path, Path) or not script_path.is_file():
        raise ValueError(f"{method['label']} 提炼脚本不存在，当前不可启动任务")
    return method_id, method


def storage_file_path(path: Path) -> Path:
    """Use a writable file inside accidental Docker directory bind mounts."""
    return path / path.name if path.is_dir() else path


def payment_storage_paths(payment_method: str) -> tuple[Path, Path]:
    if payment_method == "blik":
        paths = BLIK_PROXY_SEED_PATH, BLIK_TOKEN_PATH
    elif payment_method == "kakao_pay":
        paths = KAKAO_KR_PROXY_SEED_PATH, KAKAO_TOKEN_PATH
    elif payment_method == "pix":
        paths = PIX_PRIMARY_PROXY_SEED_PATH, PIX_TOKEN_PATH
    elif payment_method == "twint":
        paths = TWINT_PRIMARY_PROXY_SEED_PATH, TWINT_TOKEN_PATH
    elif payment_method == "upi":
        paths = UPI_PRIMARY_PROXY_SEED_PATH, UPI_TOKEN_PATH
    elif payment_method == "ideal":
        paths = IDEAL_PRIMARY_PROXY_SEED_PATH, TOKEN_PATH
    else:
        paths = PROXY_SEED_PATH, TOKEN_PATH
    return storage_file_path(paths[0]), storage_file_path(paths[1])


def manual_proxy_paths(payment_method: str) -> tuple[Path, Path] | None:
    if payment_method == "ideal":
        paths = IDEAL_PRIMARY_PROXY_SEED_PATH, IDEAL_PROMOTION_PROXY_SEED_PATH
    elif payment_method == "pix":
        paths = PIX_PRIMARY_PROXY_SEED_PATH, PIX_PROMOTION_PROXY_SEED_PATH
    elif payment_method == "kakao_pay":
        paths = KAKAO_KR_PROXY_SEED_PATH, KAKAO_VN_PROXY_SEED_PATH
    elif payment_method == "twint":
        paths = TWINT_PRIMARY_PROXY_SEED_PATH, TWINT_PROMOTION_PROXY_SEED_PATH
    elif payment_method == "upi":
        paths = UPI_PRIMARY_PROXY_SEED_PATH, UPI_PROMOTION_PROXY_SEED_PATH
    else:
        return None
    return storage_file_path(paths[0]), storage_file_path(paths[1])


def count_proxy_lines(path_value: str) -> int:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return 0
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for line in handle if line.strip())


def read_proxy_text(path_value: str) -> str:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def write_text_atomic(path: Path, text: str) -> None:
    # Docker bind mounts put the target on a different filesystem than
    # the temp file, so os.replace() fails with EXDEV/EBUSY.  Fall back
    # to copy+unlink when atomic rename is not possible.
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except OSError:
        shutil.copy2(temp_path, path)
        temp_path.unlink()


def migrate_legacy_proxy_seeds() -> Path | None:
    if PROXY_SEED_PATH.is_file():
        return None
    for legacy_path in LEGACY_PROXY_PATHS:
        text = read_proxy_text(str(legacy_path))
        if text:
            write_text_atomic(PROXY_SEED_PATH, text.rstrip() + "\n")
            return legacy_path
    return None


LEGACY_PROXY_SEED_SOURCE = migrate_legacy_proxy_seeds()


def read_local_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def count_proxy_text(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if line.strip())


def as_int(payload: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def as_bool(payload: dict[str, Any], name: str, default: bool) -> bool:
    value = payload.get(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clean_text(payload: dict[str, Any], name: str, default: str = "", limit: int = 500) -> str:
    value = str(payload.get(name, default) or "").strip()
    if len(value) > limit:
        raise ValueError(f"{name} 内容过长")
    return value


def clean_country_code(payload: dict[str, Any], name: str, default: str) -> str:
    value = clean_text(payload, name, default, 2).upper()
    if not COUNTRY_CODE_RE.fullmatch(value):
        raise ValueError(f"{name} 必须是两位国家代码")
    return value


def clean_country_codes(payload: dict[str, Any], name: str, default: str) -> str:
    value = clean_text(payload, name, default, 80)
    countries = [part.strip().upper() for part in value.split(",")]
    if not countries or any(not COUNTRY_CODE_RE.fullmatch(country) for country in countries):
        raise ValueError(f"{name} 必须是以逗号分隔的两位国家代码")
    return ",".join(countries)


def resolve_proxy_file(value: str, label: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise ValueError(f"{label}不存在")
    return str(path.resolve())


def prepare_persistent_files(payload: dict[str, Any], payment_method: str) -> tuple[dict[str, Any], int]:
    if payment_method in MANUAL_PROXY_METHODS:
        primary_path, promotion_path = manual_proxy_paths(payment_method) or (KAKAO_KR_PROXY_SEED_PATH, KAKAO_VN_PROXY_SEED_PATH)
        shared_proxy_path = primary_path == promotion_path
        method_label = str(PAYMENT_METHODS.get(payment_method, {}).get("label") or payment_method)
        chain = PAYMENT_CHAIN_DEFAULTS.get(payment_method, ("", "", ""))
        kr_text = clean_text(
            payload,
            "manual_primary_proxies",
            clean_text(payload, "kakao_kr_proxies", "", 400_000),
            400_000,
        )
        vn_text = clean_text(
            payload,
            "manual_promotion_proxies",
            clean_text(payload, "kakao_vn_proxies", "", 400_000),
            400_000,
        )
        kr_count = count_proxy_text(kr_text)
        vn_count = count_proxy_text(vn_text)
        if shared_proxy_path and not kr_count and vn_count:
            kr_text = vn_text
            kr_count = vn_count
            vn_text = ""
            vn_count = 0
        if kr_count:
            write_text_atomic(primary_path, kr_text.rstrip() + "\n")
        else:
            kr_count = count_proxy_lines(str(primary_path))
            if not kr_count:
                raise ValueError(f"请填写 {method_label} {chain[0]} 主链路代理")
        if shared_proxy_path:
            vn_count = 0
        elif vn_count:
            write_text_atomic(promotion_path, vn_text.rstrip() + "\n")
        else:
            vn_count = count_proxy_lines(str(promotion_path))
            if not vn_count:
                stage = "优惠/税务" if payment_method == "pix" else "checkout/update"
                raise ValueError(f"请填写 {method_label} {chain[1]} {stage}代理")

        token_text = clean_text(payload, "token", "", 30000)
        if token_text:
            write_text_atomic(payment_storage_paths(payment_method)[1], token_text.rstrip() + "\n")

        task_payload = dict(payload)
        task_payload["proxy_seed_file"] = str(primary_path)
        task_payload["manual_checkout_proxy_file"] = str(primary_path)
        task_payload["manual_provider_proxy_file"] = str(primary_path)
        task_payload["manual_promotion_proxy_file"] = str(promotion_path)
        task_payload["kakao_checkout_proxy_file"] = str(primary_path)
        task_payload["kakao_provider_proxy_file"] = str(primary_path)
        task_payload["kakao_promotion_proxy_file"] = str(promotion_path)
        task_payload["token_file"] = str(payment_storage_paths(payment_method)[1])
        for name in (
            "checkout_proxies",
            "promotion_proxies",
            "provider_proxies",
            "checkout_file",
            "promotion_file",
            "provider_file",
        ):
            task_payload.pop(name, None)
        return task_payload, kr_count + vn_count

    seed_text = clean_text(payload, "proxy_seeds", "", 400_000)
    seed_count = count_proxy_text(seed_text)
    proxy_seed_path, token_path = payment_storage_paths(payment_method)
    if seed_count:
        write_text_atomic(proxy_seed_path, seed_text.rstrip() + "\n")
    else:
        seed_count = count_proxy_lines(str(proxy_seed_path))
        if not seed_count:
            raise ValueError("请填写代理 Seed 池")
    token_text = clean_text(payload, "token", "", 30000)
    if token_text:
        write_text_atomic(token_path, token_text.rstrip() + "\n")

    task_payload = dict(payload)
    task_payload["proxy_seed_file"] = str(proxy_seed_path)
    task_payload["token_file"] = str(token_path)
    for name in (
        "checkout_proxies",
        "promotion_proxies",
        "provider_proxies",
        "checkout_file",
        "promotion_file",
        "provider_file",
    ):
        task_payload.pop(name, None)
    return task_payload, seed_count


def build_environment(
    payload: dict[str, Any], payment_method: str, method: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any]]:
    batch_size = as_int(payload, "batch_size", 5, 1, 100)
    max_batches = as_int(payload, "max_batches", 5, 1, 100)
    provider_retry = 1
    poll_timeout = as_int(payload, "poll_timeout", 45, 5, 300)
    max_attempts = max_batches

    default_proxy_seed_path, token_path = payment_storage_paths(payment_method)
    proxy_seed_file = resolve_proxy_file(
        clean_text(payload, "proxy_seed_file", str(default_proxy_seed_path)),
        "代理 Seed 文件",
    )
    manual_checkout_proxy_file = ""
    manual_promotion_proxy_file = ""
    manual_provider_proxy_file = ""
    default_chain = PAYMENT_CHAIN_DEFAULTS.get(payment_method)
    if default_chain:
        if payment_method == "pix":
            bootstrap_country = "BR"
            promotion_country = "BR"
            provider_country = "BR"
        else:
            bootstrap_country = clean_country_code(payload, "bootstrap_country", default_chain[0])
            promotion_country = clean_country_code(payload, "promotion_country", default_chain[1])
            provider_country = clean_country_code(payload, "provider_country", default_chain[2])
        checkout_country = bootstrap_country
        payment_method_country = provider_country
    elif payment_method == "blik":
        bootstrap_country = "PL"
        promotion_country = "PL"
        provider_country = "PL"
        checkout_country = "PL"
        payment_method_country = "PL"
    else:
        bootstrap_country = "NL"
        promotion_country = "VN"
        provider_country = "NL"
        checkout_country = "NL"
        payment_method_country = "NL"
    blik_code = clean_text(payload, "blik_code", "", 6)
    if payment_method == "blik":
        if not blik_code:
            raise ValueError("请填写 BLIK Code 后再启动任务")
        if not blik_code.isdigit() or len(blik_code) != 6:
            raise ValueError("BLIK Code 必须是6位数字")

    promo_mode = clean_text(payload, "promo_mode", "campaign", 20).lower()
    if promo_mode not in {"coupon", "campaign", "query", "trial", "free_trial", "code", "off"}:
        raise ValueError("优惠模式不正确")
    if payment_method == "kakao_pay" and promo_mode not in {"campaign", "off"}:
        raise ValueError("Kakao 当前仅支持 campaign 或 off 优惠模式")
    if payment_method == "kakao_pay":
        if bootstrap_country != provider_country:
            raise ValueError("Kakao 手动代理模式要求第一段和第三段国家一致")
        primary_path, promotion_path = manual_proxy_paths(payment_method) or (KAKAO_KR_PROXY_SEED_PATH, KAKAO_VN_PROXY_SEED_PATH)
        manual_checkout_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_checkout_proxy_file", clean_text(payload, "kakao_checkout_proxy_file", str(primary_path))),
            "Kakao KR 代理文件",
        )
        manual_provider_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_provider_proxy_file", clean_text(payload, "kakao_provider_proxy_file", manual_checkout_proxy_file)),
            "Kakao Provider 代理文件",
        )
        manual_promotion_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_promotion_proxy_file", clean_text(payload, "kakao_promotion_proxy_file", str(promotion_path))),
            "Kakao VN 代理文件",
        )
    if payment_method in MANUAL_PROXY_METHODS and not manual_checkout_proxy_file:
        primary_path, promotion_path = manual_proxy_paths(payment_method) or (KAKAO_KR_PROXY_SEED_PATH, KAKAO_VN_PROXY_SEED_PATH)
        manual_checkout_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_checkout_proxy_file", clean_text(payload, "kakao_checkout_proxy_file", str(primary_path))),
            f"{method['label']} {bootstrap_country} proxy file",
        )
        manual_provider_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_provider_proxy_file", clean_text(payload, "kakao_provider_proxy_file", manual_checkout_proxy_file)),
            f"{method['label']} provider proxy file",
        )
        manual_promotion_proxy_file = resolve_proxy_file(
            clean_text(payload, "manual_promotion_proxy_file", clean_text(payload, "kakao_promotion_proxy_file", str(promotion_path))),
            f"{method['label']} {promotion_country} proxy file",
        )

    promo_id = clean_text(payload, "promo_id", "plus-1-month-free", 200)
    proxy_default_scheme = clean_text(payload, "proxy_default_scheme", "http", 20).lower()
    if proxy_default_scheme not in {"http", "socks5h"}:
        raise ValueError("代理默认协议不正确")

    env = os.environ.copy()
    for name in (
        "IDEAL_CHECKOUT_PROXY_FILE",
        "IDEAL_PROMOTION_PROXY_FILE",
        "IDEAL_PROVIDER_PROXY_FILE",
        "PP_CHECKOUT_PROXY_FILE",
        "PP_PROMOTION_PROXY_FILE",
        "PP_PROVIDER_PROXY_FILE",
        "KAKAO_TOKEN",
        "KAKAO_PROXY_SEED_FILE",
        "KAKAO_CHECKOUT_PROXY_FILE",
        "KAKAO_PROMOTION_PROXY_FILE",
        "KAKAO_PROVIDER_PROXY_FILE",
        "KAKAO_PROXY_DEFAULT_SCHEME",
        "KAKAO_SEEDS_PER_ROUND",
        "KAKAO_MAX_RETRY",
        "KAKAO_POLL_TIMEOUT",
        "KAKAO_PROMO_MODE",
        "KAKAO_PROMO_ID",
        "KAKAO_PROXY_REMOVE_FAILED",
        "KAKAO_PROXY_FAIL_COOLDOWN",
        "KAKAO_PROXY_REMOVE_AFTER_FAILS",
        "PIX_TOKEN",
        "PIX_PROXY_SEED_FILE",
        "PIX_CHECKOUT_PROXY_FILE",
        "PIX_PROMOTION_PROXY_FILE",
        "PIX_PROVIDER_PROXY_FILE",
        "PIX_FLOW_MODE",
        "PIX_CHECKOUT_RETRY_MAX",
        "PIX_PROVIDER_RETRY_MAX",
        "PIX_PROVIDER_PER_CHECKOUT",
        "PIX_WORKERS",
        "PIX_WORKERS_MAX",
        "PIX_MAX_RETRY",
        "PIX_MAX_APPROVE_BLOCKED",
        "PIX_PROXY_DEFAULT_SCHEME",
        "PIX_POLL_TIMEOUT",
        "PIX_PROXY_FAIL_COOLDOWN",
        "PIX_PROXY_REMOVE_AFTER_FAILS",
        "PIX_ZERO_CACHE",
        "PIX_ZERO_CACHE_SCHEDULING",
        "PIX_APPROVE_RETRY_MAX",
        "PIX_APPROVE_PARALLEL",
        "PIX_APPROVE_STICKY",
        "PIX_CONFIRM_INLINE_PM",
        "PIX_UPDATE_TAX_REGION",
        "PIX_PROCESSOR_ENTITY",
        "PIX_CHECKOUT_SNAPSHOT",
        "PIX_APPROVE_WARMUP",
        "PIX_SAVED_PAYMENT_VALUE",
        "PIX_BROWSER_LOCALE",
        "PIX_ELEMENTS_LOCALE",
        "PIX_BROWSER_TIMEZONE",
        "PIX_REQUIRE_ZERO",
        "PIX_BOOTSTRAP_COUNTRY",
        "PIX_PROMOTION_COUNTRY",
        "PIX_PROVIDER_COUNTRY",
        "PIX_CHECKOUT_COUNTRY",
        "PIX_BILLING_COUNTRY",
        "PIX_CHECKOUT_PROXY_COUNTRY",
        "PIX_PROVIDER_PROXY_COUNTRIES",
        "PIX_PRE_PROXY",
        "TWINT_TOKEN",
        "TWINT_PROXY_SEED_FILE",
        "TWINT_CHECKOUT_PROXY_FILE",
        "TWINT_PROMOTION_PROXY_FILE",
        "TWINT_PROVIDER_PROXY_FILE",
        "TWINT_FLOW_MODE",
        "TWINT_CHECKOUT_RETRY_MAX",
        "TWINT_PROVIDER_RETRY_MAX",
        "TWINT_PROVIDER_PER_CHECKOUT",
        "TWINT_WORKERS",
        "TWINT_WORKERS_MAX",
        "TWINT_MAX_RETRY",
        "TWINT_MAX_APPROVE_BLOCKED",
        "TWINT_PROXY_DEFAULT_SCHEME",
        "TWINT_POLL_TIMEOUT",
        "TWINT_PROXY_FAIL_COOLDOWN",
        "TWINT_PROXY_REMOVE_AFTER_FAILS",
        "TWINT_ZERO_CACHE",
        "TWINT_ZERO_CACHE_SCHEDULING",
        "TWINT_APPROVE_RETRY_MAX",
        "TWINT_APPROVE_PARALLEL",
        "TWINT_APPROVE_STICKY",
        "TWINT_CONFIRM_INLINE_PM",
        "TWINT_UPDATE_TAX_REGION",
        "TWINT_CHECKOUT_SNAPSHOT",
        "TWINT_APPROVE_WARMUP",
        "TWINT_SAVED_PAYMENT_VALUE",
        "TWINT_BROWSER_LOCALE",
        "TWINT_ELEMENTS_LOCALE",
        "TWINT_BROWSER_TIMEZONE",
        "TWINT_REQUIRE_ZERO",
        "TWINT_BOOTSTRAP_COUNTRY",
        "TWINT_PROMOTION_COUNTRY",
        "TWINT_PROVIDER_COUNTRY",
        "TWINT_CHECKOUT_COUNTRY",
        "TWINT_BILLING_COUNTRY",
        "TWINT_CHECKOUT_PROXY_COUNTRY",
        "TWINT_PROVIDER_PROXY_COUNTRIES",
        "TWINT_PRE_PROXY",
        "TWINT_PROXY_REMOVE_FAILED",
        "UPI_TOKEN",
        "UPI_PROXY_SEED_FILE",
        "UPI_CHECKOUT_PROXY_FILE",
        "UPI_PROMOTION_PROXY_FILE",
        "UPI_PROVIDER_PROXY_FILE",
        "UPI_FLOW_MODE",
        "UPI_CHECKOUT_RETRY_MAX",
        "UPI_PROVIDER_RETRY_MAX",
        "UPI_PROVIDER_PER_CHECKOUT",
        "UPI_WORKERS",
        "UPI_WORKERS_MAX",
        "UPI_MAX_RETRY",
        "UPI_MAX_APPROVE_BLOCKED",
        "UPI_PROXY_DEFAULT_SCHEME",
        "UPI_POLL_TIMEOUT",
        "UPI_PROXY_FAIL_COOLDOWN",
        "UPI_PROXY_REMOVE_AFTER_FAILS",
        "UPI_ZERO_CACHE",
        "UPI_ZERO_CACHE_SCHEDULING",
        "UPI_APPROVE_RETRY_MAX",
        "UPI_APPROVE_PARALLEL",
        "UPI_APPROVE_STICKY",
        "UPI_CONFIRM_INLINE_PM",
        "UPI_UPDATE_TAX_REGION",
        "UPI_CHECKOUT_SNAPSHOT",
        "UPI_APPROVE_WARMUP",
        "UPI_SAVED_PAYMENT_VALUE",
        "UPI_BROWSER_LOCALE",
        "UPI_ELEMENTS_LOCALE",
        "UPI_BROWSER_TIMEZONE",
        "UPI_REQUIRE_ZERO",
        "UPI_BOOTSTRAP_COUNTRY",
        "UPI_PROMOTION_COUNTRY",
        "UPI_PROVIDER_COUNTRY",
        "UPI_CHECKOUT_COUNTRY",
        "UPI_BILLING_COUNTRY",
        "UPI_CHECKOUT_PROXY_COUNTRY",
        "UPI_PROVIDER_PROXY_COUNTRIES",
        "UPI_PRE_PROXY",
        "UPI_PROXY_REMOVE_FAILED",
        "KAKAO_BOOTSTRAP_COUNTRY",
        "KAKAO_PROMOTION_COUNTRY",
        "KAKAO_PROVIDER_COUNTRY",
        "IDEAL_BOOTSTRAP_COUNTRY",
        "IDEAL_PROMOTION_COUNTRY",
        "IDEAL_PROVIDER_COUNTRY",
    ):
        env.pop(name, None)
    env.update(
        {
            "IDEAL_PAYMENT_METHOD": payment_method,
            "IDEAL_PROXY_SEED_FILE": proxy_seed_file,
            "IDEAL_FLOW_MODE": "single",
            "IDEAL_CHECKOUT_RETRY_MAX": str(batch_size),
            "IDEAL_PROVIDER_RETRY_MAX": str(provider_retry),
            "IDEAL_PROVIDER_PER_CHECKOUT": str(provider_retry),
            "IDEAL_WORKERS": "1",
            "IDEAL_WORKERS_MAX": "1",
            "IDEAL_MAX_RETRY": str(max_attempts),
            "IDEAL_MAX_APPROVE_BLOCKED": str(max_attempts),
            "IDEAL_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
            "IDEAL_POLL_TIMEOUT": str(poll_timeout),
            "IDEAL_PROXY_FAIL_COOLDOWN": "180",
            "IDEAL_PROXY_REMOVE_AFTER_FAILS": "3",
            "IDEAL_ZERO_CACHE": "1",
            "IDEAL_ZERO_CACHE_SCHEDULING": "0",
            "IDEAL_APPROVE_RETRY_MAX": "10",
            "IDEAL_APPROVE_PARALLEL": "1",
            "IDEAL_APPROVE_STICKY": "1",
            "IDEAL_CONFIRM_INLINE_PM": "0",
            "IDEAL_UPDATE_TAX_REGION": "0" if payment_method == "blik" else "1",
            "IDEAL_CHECKOUT_SNAPSHOT": "0",
            "IDEAL_APPROVE_WARMUP": "1",
            "IDEAL_SAVED_PAYMENT_VALUE": "never",
            "IDEAL_BROWSER_LOCALE": (
                "pl-PL" if payment_method == "blik" else "ko-KR" if payment_method == "kakao_pay" else "nl-NL"
            ),
            "IDEAL_ELEMENTS_LOCALE": (
                "pl-PL" if payment_method == "blik" else "ko" if payment_method == "kakao_pay" else "nl"
            ),
            "IDEAL_BROWSER_TIMEZONE": (
                "Europe/Warsaw"
                if payment_method == "blik"
                else "Asia/Seoul"
                if payment_method == "kakao_pay"
                else "Europe/Amsterdam"
            ),
            "IDEAL_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
            "IDEAL_REQUIRE_ZERO": "1",
            "IDEAL_BOOTSTRAP_COUNTRY": bootstrap_country,
            "IDEAL_PROMOTION_COUNTRY": promotion_country,
            "IDEAL_PROVIDER_COUNTRY": provider_country,
            "IDEAL_CHECKOUT_COUNTRY": checkout_country,
            "IDEAL_BILLING_COUNTRY": payment_method_country,
            "IDEAL_CHECKOUT_PROXY_COUNTRY": checkout_country,
            "IDEAL_PROVIDER_PROXY_COUNTRIES": payment_method_country,
            "IDEAL_BANK": "" if payment_method in {"blik", "kakao_pay", "pix", "twint", "upi"} else clean_text(payload, "bank", "", 40),
            "IDEAL_PRE_PROXY": clean_text(payload, "pre_proxy", "", 500),
            "PP_PROMO_MODE": promo_mode,
            "PP_PROMO_ID": promo_id,
        }
    )
    if payment_method == "blik":
        env["IDEAL_BLIK_CODE"] = blik_code
        env.update(
            {
                "IDEAL_PROXY_PRECHECK": "0",
                "IDEAL_PROXY_GEO_CHECK": "0",
                "IDEAL_PROXY_GEO_USE_PRE_PROXY": "0",
                "IDEAL_PROXY_TARGET_CHECK": "1",
                "IDEAL_PROXY_TARGET_PRECHECK": "0",
                "IDEAL_PROXY_TARGET_USE_PRE_PROXY": "1",
            }
        )
    elif payment_method == "ideal":
        env.update(
            {
                "IDEAL_CHECKOUT_PROXY_FILE": manual_checkout_proxy_file,
                "IDEAL_PROMOTION_PROXY_FILE": manual_promotion_proxy_file,
                "IDEAL_PROVIDER_PROXY_FILE": manual_provider_proxy_file,
            }
        )
        env.pop("IDEAL_BLIK_CODE", None)
    elif payment_method == "kakao_pay":
        env.update(
            {
                "KAKAO_PROXY_SEED_FILE": proxy_seed_file,
                "KAKAO_CHECKOUT_PROXY_FILE": manual_checkout_proxy_file,
                "KAKAO_PROMOTION_PROXY_FILE": manual_promotion_proxy_file,
                "KAKAO_PROVIDER_PROXY_FILE": manual_provider_proxy_file,
                "KAKAO_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
                "KAKAO_SEEDS_PER_ROUND": str(batch_size),
                "KAKAO_MAX_RETRY": str(max_batches),
                "KAKAO_POLL_TIMEOUT": str(poll_timeout),
                "KAKAO_PROMO_MODE": promo_mode,
                "KAKAO_PROMO_ID": promo_id,
                "KAKAO_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
                "KAKAO_PROXY_FAIL_COOLDOWN": "180",
                "KAKAO_PROXY_REMOVE_AFTER_FAILS": "3",
                "KAKAO_BOOTSTRAP_COUNTRY": bootstrap_country,
                "KAKAO_PROMOTION_COUNTRY": promotion_country,
                "KAKAO_PROVIDER_COUNTRY": provider_country,
                "KAKAO_PRE_PROXY": clean_text(payload, "pre_proxy", "", 500),
            }
        )
        env.pop("IDEAL_BLIK_CODE", None)
    elif payment_method == "pix":
        env.update(
            {
                "PIX_PROXY_SEED_FILE": proxy_seed_file,
                "PIX_CHECKOUT_PROXY_FILE": manual_checkout_proxy_file,
                "PIX_PROMOTION_PROXY_FILE": manual_promotion_proxy_file,
                "PIX_PROVIDER_PROXY_FILE": manual_provider_proxy_file,
                "PIX_FLOW_MODE": "single",
                "PIX_CHECKOUT_RETRY_MAX": str(batch_size),
                "PIX_PROVIDER_RETRY_MAX": str(provider_retry),
                "PIX_PROVIDER_PER_CHECKOUT": str(provider_retry),
                "PIX_WORKERS": "1",
                "PIX_WORKERS_MAX": "1",
                "PIX_MAX_RETRY": str(max_attempts),
                "PIX_MAX_APPROVE_BLOCKED": str(max_attempts),
                "PIX_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
                "PIX_POLL_TIMEOUT": str(poll_timeout),
                "PIX_PROXY_FAIL_COOLDOWN": "180",
                "PIX_PROXY_REMOVE_AFTER_FAILS": "3",
                "PIX_ZERO_CACHE": "1",
                "PIX_ZERO_CACHE_SCHEDULING": "0",
                "PIX_APPROVE_RETRY_MAX": "10",
                "PIX_APPROVE_PARALLEL": "1",
                "PIX_APPROVE_STICKY": "1",
                "PIX_CONFIRM_INLINE_PM": "0",
                "PIX_UPDATE_TAX_REGION": "1",
                "PIX_PROCESSOR_ENTITY": "openai_llc",
                "PIX_CHECKOUT_SNAPSHOT": "0",
                "PIX_APPROVE_WARMUP": "1",
                "PIX_SAVED_PAYMENT_VALUE": "never",
                "PIX_BROWSER_LOCALE": "pt-BR",
                "PIX_ELEMENTS_LOCALE": "pt-BR",
                "PIX_BROWSER_TIMEZONE": "America/Sao_Paulo",
                "PIX_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
                "PIX_REQUIRE_ZERO": "1",
                "PIX_BOOTSTRAP_COUNTRY": bootstrap_country,
                "PIX_PROMOTION_COUNTRY": promotion_country,
                "PIX_PROVIDER_COUNTRY": provider_country,
                "PIX_CHECKOUT_COUNTRY": bootstrap_country,
                "PIX_BILLING_COUNTRY": provider_country,
                "PIX_CHECKOUT_PROXY_COUNTRY": bootstrap_country,
                "PIX_PROVIDER_PROXY_COUNTRIES": provider_country,
                "PIX_PRE_PROXY": clean_text(payload, "pre_proxy", "", 500),
            }
        )
        env.pop("IDEAL_BLIK_CODE", None)
    elif payment_method == "twint":
        env.update(
            {
                "TWINT_PROXY_SEED_FILE": proxy_seed_file,
                "TWINT_CHECKOUT_PROXY_FILE": manual_checkout_proxy_file,
                "TWINT_PROMOTION_PROXY_FILE": manual_promotion_proxy_file,
                "TWINT_PROVIDER_PROXY_FILE": manual_provider_proxy_file,
                "TWINT_FLOW_MODE": "single",
                "TWINT_CHECKOUT_RETRY_MAX": str(batch_size),
                "TWINT_PROVIDER_RETRY_MAX": str(provider_retry),
                "TWINT_PROVIDER_PER_CHECKOUT": str(provider_retry),
                "TWINT_WORKERS": "1",
                "TWINT_WORKERS_MAX": "1",
                "TWINT_MAX_RETRY": str(max_attempts),
                "TWINT_MAX_APPROVE_BLOCKED": str(max_attempts),
                "TWINT_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
                "TWINT_POLL_TIMEOUT": str(poll_timeout),
                "TWINT_PROXY_FAIL_COOLDOWN": "180",
                "TWINT_PROXY_REMOVE_AFTER_FAILS": "3",
                "TWINT_ZERO_CACHE": "1",
                "TWINT_ZERO_CACHE_SCHEDULING": "0",
                "TWINT_APPROVE_RETRY_MAX": "10",
                "TWINT_APPROVE_PARALLEL": "1",
                "TWINT_APPROVE_STICKY": "1",
                "TWINT_CONFIRM_INLINE_PM": "0",
                "TWINT_UPDATE_TAX_REGION": "1",
                "TWINT_CHECKOUT_SNAPSHOT": "0",
                "TWINT_APPROVE_WARMUP": "1",
                "TWINT_SAVED_PAYMENT_VALUE": "never",
                "TWINT_BROWSER_LOCALE": "de-CH",
                "TWINT_ELEMENTS_LOCALE": "de",
                "TWINT_BROWSER_TIMEZONE": "Europe/Zurich",
                "TWINT_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
                "TWINT_REQUIRE_ZERO": "1",
                "TWINT_BOOTSTRAP_COUNTRY": bootstrap_country,
                "TWINT_PROMOTION_COUNTRY": promotion_country,
                "TWINT_PROVIDER_COUNTRY": provider_country,
                "TWINT_CHECKOUT_COUNTRY": bootstrap_country,
                "TWINT_BILLING_COUNTRY": provider_country,
                "TWINT_CHECKOUT_PROXY_COUNTRY": bootstrap_country,
                "TWINT_PROVIDER_PROXY_COUNTRIES": provider_country,
                "TWINT_PRE_PROXY": clean_text(payload, "pre_proxy", "", 500),
            }
        )
        env.pop("IDEAL_BLIK_CODE", None)
    elif payment_method == "upi":
        env.update(
            {
                "UPI_PROXY_SEED_FILE": proxy_seed_file,
                "UPI_CHECKOUT_PROXY_FILE": manual_checkout_proxy_file,
                "UPI_PROMOTION_PROXY_FILE": manual_promotion_proxy_file,
                "UPI_PROVIDER_PROXY_FILE": manual_provider_proxy_file,
                "UPI_FLOW_MODE": "single",
                "UPI_CHECKOUT_RETRY_MAX": str(batch_size),
                "UPI_PROVIDER_RETRY_MAX": str(provider_retry),
                "UPI_PROVIDER_PER_CHECKOUT": str(provider_retry),
                "UPI_WORKERS": "1",
                "UPI_WORKERS_MAX": "1",
                "UPI_MAX_RETRY": str(max_attempts),
                "UPI_MAX_APPROVE_BLOCKED": str(max_attempts),
                "UPI_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
                "UPI_POLL_TIMEOUT": str(poll_timeout),
                "UPI_PROXY_FAIL_COOLDOWN": "180",
                "UPI_PROXY_REMOVE_AFTER_FAILS": "3",
                "UPI_ZERO_CACHE": "1",
                "UPI_ZERO_CACHE_SCHEDULING": "0",
                "UPI_APPROVE_RETRY_MAX": "10",
                "UPI_APPROVE_PARALLEL": "1",
                "UPI_APPROVE_STICKY": "1",
                "UPI_CONFIRM_INLINE_PM": "0",
                "UPI_UPDATE_TAX_REGION": "1",
                "UPI_CHECKOUT_SNAPSHOT": "0",
                "UPI_APPROVE_WARMUP": "1",
                "UPI_SAVED_PAYMENT_VALUE": "never",
                "UPI_BROWSER_LOCALE": "en-IN",
                "UPI_ELEMENTS_LOCALE": "en",
                "UPI_BROWSER_TIMEZONE": "Asia/Kolkata",
                "UPI_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
                "UPI_REQUIRE_ZERO": "1",
                "UPI_BOOTSTRAP_COUNTRY": bootstrap_country,
                "UPI_PROMOTION_COUNTRY": promotion_country,
                "UPI_PROVIDER_COUNTRY": provider_country,
                "UPI_CHECKOUT_COUNTRY": bootstrap_country,
                "UPI_BILLING_COUNTRY": provider_country,
                "UPI_CHECKOUT_PROXY_COUNTRY": bootstrap_country,
                "UPI_PROVIDER_PROXY_COUNTRIES": provider_country,
                "UPI_PRE_PROXY": clean_text(payload, "pre_proxy", "", 500),
            }
        )
        env.pop("IDEAL_BLIK_CODE", None)
    else:
        env.pop("IDEAL_BLIK_CODE", None)

    token = clean_text(payload, "token", "", 30000)
    session_token = clean_text(payload, "session_token", "", 30000)
    resolved_token = token or read_local_text(token_path)
    if not resolved_token:
        raise ValueError("未填写Token，且当前支付方式不存在可用的 token.txt")
    env["PP_TOKEN"] = resolved_token
    if payment_method == "kakao_pay":
        env["KAKAO_TOKEN"] = resolved_token
    if payment_method == "pix":
        env["PIX_TOKEN"] = resolved_token
    if payment_method == "twint":
        env["TWINT_TOKEN"] = resolved_token
    if payment_method == "upi":
        env["UPI_TOKEN"] = resolved_token
    if session_token:
        env["PP_SESSION_TOKEN"] = session_token
    else:
        env.pop("PP_SESSION_TOKEN", None)

    public_config = {
        "payment_method": payment_method,
        "payment_label": method["label"],
        "payment_flow": (
            f"{bootstrap_country}/{promotion_country}/{provider_country}"
            if default_chain
            else method["flow"]
        ),
        "batch_size": batch_size,
        "max_batches": max_batches,
        "max_attempts": max_attempts,
        "provider_retry": provider_retry,
        "poll_timeout": poll_timeout,
        "proxy_seed_file": proxy_seed_file,
        "token_file": str(token_path),
        "checkout_country": checkout_country,
        "promotion_country": promotion_country,
        "payment_method_country": payment_method_country,
        "proxy_default_scheme": proxy_default_scheme,
        "bank": env["IDEAL_BANK"],
        "promo_mode": promo_mode,
        "promo_id": env["PP_PROMO_ID"],
        "remove_failed": env["IDEAL_PROXY_REMOVE_FAILED"] == "1",
        "pre_proxy": env["IDEAL_PRE_PROXY"],
        "token_source": "页面输入" if token else "token.txt",
    }
    return env, public_config


class ScriptRunner:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_LINES)
        self.next_log_id = 1
        self.run_id = 0
        self.started_at = 0.0
        self.finished_at = 0.0
        self.exit_code: int | None = None
        self.result_url = ""
        self.result_at = 0.0
        self.awaiting_result_url = False
        self.result_marker = ""
        self.proxy_texts: dict[str, str] = {
            "seed": read_proxy_text(str(PROXY_SEED_PATH)),
        }
        self.proxy_text_versions: dict[str, int] = {
            "seed": 1 if self.proxy_texts["seed"] else 0,
        }
        _, initial_token_path = payment_storage_paths("ideal")
        self.token_text = read_local_text(initial_token_path)
        self.token_text_version = 1 if self.token_text else 0
        self.last_config: dict[str, Any] = {
            "payment_method": "ideal",
            "payment_label": PAYMENT_METHODS["ideal"]["label"],
            "payment_flow": PAYMENT_METHODS["ideal"]["flow"],
            "proxy_seed_file": str(PROXY_SEED_PATH),
            "token_file": str(initial_token_path),
        }

    def _append_locked(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        self.logs.append({"id": self.next_log_id, "text": line})
        self.next_log_id += 1
        if self.result_marker and self.result_marker in line:
            self.awaiting_result_url = True
        elif self.awaiting_result_url and line.startswith(("http://", "https://")):
            self.result_url = line
            self.result_at = time.time()
            self.awaiting_result_url = False

    def append(self, line: str) -> None:
        with self.lock:
            self._append_locked(line)

    def _sync_proxy_texts_locked(self) -> None:
        path_value = str(self.last_config.get("proxy_seed_file") or PROXY_SEED_PATH)
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            return
        seed_text = read_proxy_text(path_value)
        if seed_text != self.proxy_texts.get("seed", ""):
            self.proxy_texts["seed"] = seed_text
            self.proxy_text_versions["seed"] = self.proxy_text_versions.get("seed", 0) + 1
        self.last_config["seed_count"] = count_proxy_text(seed_text)

    def _sync_token_text_locked(self) -> None:
        token_file = Path(str(self.last_config.get("token_file") or TOKEN_PATH)).expanduser()
        text = read_local_text(token_file)
        if text != self.token_text:
            self.token_text = text
            self.token_text_version += 1

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("脚本正在运行")
            payment_method, method = resolve_payment_method(payload)
            task_payload, seed_count = prepare_persistent_files(payload, payment_method)
            env, public_config = build_environment(task_payload, payment_method, method)
            public_config["seed_count"] = seed_count
            self.logs.clear()
            self.next_log_id = 1
            self.run_id += 1
            self.started_at = time.time()
            self.finished_at = 0.0
            self.exit_code = None
            self.result_url = ""
            self.result_at = 0.0
            self.awaiting_result_url = False
            self.result_marker = str(method["result_marker"])
            self.last_config = public_config
            self.proxy_texts = {
                "seed": clean_text(task_payload, "proxy_seeds", "", 400_000),
            }
            self.proxy_text_versions = {"seed": 1}
            self._sync_token_text_locked()
            if payment_method == "kakao_pay":
                self._append_locked(
                    f"[UI] 启动 {public_config['payment_label']} 任务: {public_config['payment_flow']}，"
                    f"每轮 Seed 尝试数 {public_config['batch_size']}，"
                    f"重试轮数 {public_config['max_batches']}，"
                    f"最多完整链路 {public_config['batch_size'] * public_config['max_batches']}"
                )
            else:
                self._append_locked(
                    f"[UI] 启动 {public_config['payment_label']} 任务: {public_config['payment_flow']}，"
                    f"每轮Seed候选 {public_config['batch_size']}，"
                    f"总重试 {public_config['max_batches']}"
                )
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", str(method["script_path"])],
                    cwd=str(ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except Exception as exc:
                self.finished_at = time.time()
                self.exit_code = -1
                self._append_locked(f"[UI] 启动失败: {exc}")
                raise RuntimeError(f"启动失败: {exc}") from exc
            process = self.process
            self._append_locked("[UI] 进程已启动")
            threading.Thread(target=self._read_output, args=(process,), daemon=True).start()
            return self.status(0)

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self.append(line)
        code = process.wait()
        with self.lock:
            if self.process is process:
                self.finished_at = time.time()
                self.exit_code = code
                self._sync_proxy_texts_locked()
                self._sync_token_text_locked()
                self.last_config["seed_count"] = count_proxy_lines(
                    str(self.last_config.get("proxy_seed_file") or PROXY_SEED_PATH)
                )
            self._append_locked(f"[UI] 进程已结束: exit={code}")

    def stop(self) -> bool:
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                return False
            self._append_locked("[UI] 正在停止任务...")
            process.terminate()
            threading.Thread(target=self._force_stop, args=(process,), daemon=True).start()
            return True

    def _force_stop(self, process: subprocess.Popen[str]) -> None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def status(self, since: int) -> dict[str, Any]:
        with self.lock:
            process = self.process
            running = process is not None and process.poll() is None
            self._sync_proxy_texts_locked()
            self._sync_token_text_locked()
            entries = list(self.logs)
            reset_logs = bool(entries and since and since < entries[0]["id"] - 1)
            if since and not reset_logs:
                entries = [entry for entry in entries if entry["id"] > since]
            config = dict(self.last_config)
            proxy_seed_file = str(config.get("proxy_seed_file") or PROXY_SEED_PATH)
            token_file = Path(str(config.get("token_file") or TOKEN_PATH)).expanduser()
            seed_count = (
                count_proxy_lines(proxy_seed_file)
                if Path(proxy_seed_file).is_file()
                else int(config.get("seed_count") or 0)
            )
            payment_storage: dict[str, dict[str, Any]] = {}
            for method_id in ("ideal", "pix", "blik", "kakao_pay", "twint", "upi"):
                method_proxy_file, method_token_file = payment_storage_paths(method_id)
                proxy_count = count_proxy_lines(str(method_proxy_file))
                manual_paths = manual_proxy_paths(method_id)
                if manual_paths:
                    primary_path, promotion_path = manual_paths
                    proxy_count = count_proxy_lines(str(primary_path))
                    if promotion_path != primary_path:
                        proxy_count += count_proxy_lines(str(promotion_path))
                payment_storage[method_id] = {
                    "proxy_count": proxy_count,
                    "token_file": method_token_file.is_file(),
                }
            public_config = {
                key: value
                for key, value in config.items()
                if key not in {"proxy_seed_file", "token_file", "pre_proxy"}
            }
            elapsed_until = time.time() if running or not self.finished_at else self.finished_at
            return {
                "running": running,
                "pid": process.pid if running and process is not None else None,
                "exit_code": None if running else self.exit_code,
                "run_id": self.run_id,
                "started_at": self.started_at,
                "elapsed": int(elapsed_until - self.started_at) if self.started_at else 0,
                "result_url": self.result_url,
                "result_at": self.result_at,
                "logs": entries,
                "reset_logs": reset_logs,
                "proxy_counts": {
                    "seed": seed_count,
                },
                "token_file": token_file.is_file(),
                "config": public_config,
                "payment_methods": public_payment_methods(),
                "payment_storage": payment_storage,
            }


RUNNER = ScriptRunner()


class UIHandler(BaseHTTPRequestHandler):
    server_version = "IdealRunnerUI/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度错误") from exc
        if length <= 0 or length > 1_000_000:
            raise ValueError("请求内容为空或过大")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求JSON格式错误") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求必须是JSON对象")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            if not UI_PATH.is_file():
                self._send_json({"error": "ideal_ui.html 不存在"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_bytes(UI_PATH.read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/status":
            query = parse_qs(parsed.query)
            try:
                since = max(0, int((query.get("since") or ["0"])[0]))
            except ValueError:
                since = 0
            self._send_json(RUNNER.status(since))
            return
        if parsed.path == "/api/storage":
            method_id = str((parse_qs(parsed.query).get("payment_method") or [""])[0]).strip().lower()
            if method_id not in PAYMENT_METHODS:
                self._send_json({"error": "支付方式不受支持"}, HTTPStatus.BAD_REQUEST)
                return
            proxy_seed_path, token_path = payment_storage_paths(method_id)
            payload = {
                "payment_method": method_id,
                "proxy_seeds": read_local_text(proxy_seed_path),
                "token": read_local_text(token_path),
            }
            manual_paths = manual_proxy_paths(method_id)
            if manual_paths:
                primary_path, promotion_path = manual_paths
                payload["manual_primary_proxies"] = read_local_text(primary_path)
                payload["manual_promotion_proxies"] = read_local_text(promotion_path)
            if method_id == "kakao_pay":
                payload["kakao_kr_proxies"] = read_local_text(KAKAO_KR_PROXY_SEED_PATH)
                payload["kakao_vn_proxies"] = read_local_text(KAKAO_VN_PROXY_SEED_PATH)
            self._send_json(payload)
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/start":
                self._send_json(RUNNER.start(self._read_json()))
                return
            if parsed.path == "/api/stop":
                self._send_json({"stopped": RUNNER.stop()})
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, RuntimeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": f"服务器错误: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> int:
    parser = argparse.ArgumentParser(description="支付提链控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not IDEAL_SCRIPT_PATH.is_file() or not UI_PATH.is_file():
        print("缺少 ideal_qr_extract.py 或 ideal_ui.html", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), UIHandler)
    except OSError as exc:
        print(f"页面服务启动失败: {exc}，可使用 --port 更换端口", file=sys.stderr)
        return 1
    url = f"http://{args.host}:{args.port}"
    print(f"支付提链控制台: {url}")
    print("按 Ctrl+C 关闭页面服务")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    def shutdown(_signum: int, _frame: Any) -> None:
        RUNNER.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
