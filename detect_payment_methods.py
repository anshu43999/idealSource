#!/usr/bin/env python3
"""按账单国家检测 0 元 ChatGPT Plus checkout 暴露的 Stripe 支付方式。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path

import ideal_qr_extract as core


COUNTRY_CURRENCY = {
    "AF": "AFN",
    "AL": "ALL",
    "DZ": "DZD",
    "AD": "EUR",
    "AO": "AOA",
    "AG": "XCD",
    "AR": "ARS",
    "AM": "AMD",
    "AT": "EUR",
    "AU": "AUD",
    "AZ": "AZN",
    "BS": "BSD",
    "BH": "BHD",
    "BD": "BDT",
    "BB": "BBD",
    "BY": "BYN",
    "BE": "EUR",
    "BZ": "BZD",
    "BJ": "XOF",
    "BT": "BTN",
    "BO": "BOB",
    "BA": "BAM",
    "BW": "BWP",
    "BG": "EUR",
    "BR": "BRL",
    "BN": "BND",
    "BF": "XOF",
    "BI": "BIF",
    "CV": "CVE",
    "KH": "KHR",
    "CM": "XAF",
    "CA": "CAD",
    "CF": "XAF",
    "TD": "XAF",
    "CH": "CHF",
    "CL": "CLP",
    "CO": "COP",
    "KM": "KMF",
    "CG": "XAF",
    "CD": "CDF",
    "CR": "CRC",
    "CI": "XOF",
    "CY": "EUR",
    "CZ": "CZK",
    "DE": "EUR",
    "DK": "DKK",
    "CU": "CUP",
    "DJ": "DJF",
    "DM": "XCD",
    "DO": "DOP",
    "EC": "USD",
    "EG": "EGP",
    "SV": "USD",
    "GQ": "XAF",
    "ER": "ERN",
    "EE": "EUR",
    "SZ": "SZL",
    "ET": "ETB",
    "FJ": "FJD",
    "ES": "EUR",
    "FI": "EUR",
    "FR": "EUR",
    "GA": "XAF",
    "GM": "GMD",
    "GE": "GEL",
    "GB": "GBP",
    "GH": "GHS",
    "GR": "EUR",
    "GD": "XCD",
    "GT": "GTQ",
    "GN": "GNF",
    "GW": "XOF",
    "GY": "GYD",
    "HT": "HTG",
    "HN": "HNL",
    "HR": "EUR",
    "HU": "HUF",
    "IS": "ISK",
    "ID": "IDR",
    "IE": "EUR",
    "IN": "INR",
    "IR": "IRR",
    "IQ": "IQD",
    "IL": "ILS",
    "IT": "EUR",
    "JM": "JMD",
    "JO": "JOD",
    "KZ": "KZT",
    "KE": "KES",
    "KI": "AUD",
    "KP": "KPW",
    "KR": "KRW",
    "KW": "KWD",
    "KG": "KGS",
    "LA": "LAK",
    "LT": "EUR",
    "LU": "EUR",
    "LV": "EUR",
    "LB": "LBP",
    "LS": "LSL",
    "LR": "LRD",
    "LY": "LYD",
    "LI": "CHF",
    "MG": "MGA",
    "MW": "MWK",
    "MT": "EUR",
    "MV": "MVR",
    "ML": "XOF",
    "MH": "USD",
    "MR": "MRU",
    "MU": "MUR",
    "MX": "MXN",
    "MY": "MYR",
    "FM": "USD",
    "MD": "MDL",
    "MC": "EUR",
    "MN": "MNT",
    "ME": "EUR",
    "MA": "MAD",
    "MZ": "MZN",
    "MM": "MMK",
    "NA": "NAD",
    "NR": "AUD",
    "NP": "NPR",
    "NL": "EUR",
    "NI": "NIO",
    "NE": "XOF",
    "NG": "NGN",
    "MK": "MKD",
    "NO": "NOK",
    "NZ": "NZD",
    "OM": "OMR",
    "PK": "PKR",
    "PW": "USD",
    "PA": "PAB",
    "PG": "PGK",
    "PY": "PYG",
    "PE": "PEN",
    "PH": "PHP",
    "PL": "PLN",
    "PT": "EUR",
    "QA": "QAR",
    "RO": "RON",
    "RU": "RUB",
    "RW": "RWF",
    "KN": "XCD",
    "LC": "XCD",
    "VC": "XCD",
    "WS": "WST",
    "SM": "EUR",
    "ST": "STN",
    "SA": "SAR",
    "SN": "XOF",
    "RS": "RSD",
    "SC": "SCR",
    "SL": "SLE",
    "SE": "SEK",
    "SG": "SGD",
    "SI": "EUR",
    "SK": "EUR",
    "SB": "SBD",
    "SO": "SOS",
    "ZA": "ZAR",
    "SS": "SSP",
    "LK": "LKR",
    "SD": "SDG",
    "SR": "SRD",
    "SY": "SYP",
    "TJ": "TJS",
    "TZ": "TZS",
    "TH": "THB",
    "TL": "USD",
    "TG": "XOF",
    "TO": "TOP",
    "TT": "TTD",
    "TN": "TND",
    "TR": "TRY",
    "TM": "TMT",
    "TV": "AUD",
    "UG": "UGX",
    "UA": "UAH",
    "AE": "AED",
    "UY": "UYU",
    "UZ": "UZS",
    "VU": "VUV",
    "VA": "EUR",
    "VE": "VES",
    "VN": "VND",
    "YE": "YER",
    "ZM": "ZMW",
    "ZW": "ZWG",
    "PS": "ILS",
}

SUPPORTED_CURRENCIES = {
    "AED", "AUD", "BRL", "CAD", "CHF", "CLP", "COP", "CZK", "DKK", "EGP",
    "EUR", "GBP", "HUF", "IDR", "ILS", "INR", "JPY", "KRW", "KZT", "MXN",
    "MYR", "NGN", "NOK", "NZD", "PEN", "PHP", "PKR", "PLN", "QAR", "RON",
    "SAR", "SEK", "SGD", "THB", "TWD", "TZS", "USD", "VND", "ZAR",
}

IGNORED_METHODS = {"card", "paypal", "link"}


def supported_currency(currency: str) -> str:
    return currency if currency in SUPPORTED_CURRENCIES else "USD"


def parse_target(value: str) -> tuple[str, str]:
    country, separator, currency = value.strip().upper().partition(":")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError(f"国家代码格式错误: {value!r}，应为 PL 或 PL:PLN")
    if separator:
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"币种格式错误: {value!r}，应为 PL:PLN")
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"checkout 不支持币种 {currency}")
        return country, currency
    if country not in COUNTRY_CURRENCY:
        raise ValueError(f"未内置 {country} 的币种，请使用 {country}:币种，例如 ZA:ZAR")
    return country, supported_currency(COUNTRY_CURRENCY[country])


def first_proxy(path: Path) -> str:
    if not path.exists():
        raise RuntimeError("代理文件不存在")
    for line in path.read_text(encoding="utf-8").splitlines():
        proxy = core.normalize_proxy_url(line)
        if proxy:
            return proxy
    raise RuntimeError("代理文件为空")


def resolve_proxy(explicit: str, file_name: str) -> str:
    if explicit.strip():
        return core.normalize_proxy_url(explicit)
    return first_proxy(Path(file_name))


def detect_one(
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    provider_proxy: str,
    country: str,
    currency: str,
) -> tuple[int, list[str]]:
    # 原脚本会把未知国家回退到 NL；仅在当前进程补入目标国家，不修改原文件。
    core.COUNTRY_CURRENCY[country] = currency
    chatgpt = core.build_chatgpt_session(
        access_token,
        str(uuid.uuid4()),
        checkout_proxy,
        session_token,
    )
    checkout = core.create_checkout(chatgpt, country)
    payload = core.stripe_init(
        checkout["cs_id"],
        checkout.get("stripe_pk") or core.DEFAULT_STRIPE_PK,
        provider_proxy,
    )
    amount = core.amount_from_payload(payload)
    if amount != 0:
        raise RuntimeError(f"0 元检测失败: amount={amount}")
    raw_methods = core.first_value_by_key(payload, "payment_method_types")
    if not isinstance(raw_methods, list):
        raise RuntimeError(f"Stripe init 未返回 payment_method_types: {raw_methods!r}")
    methods = list(dict.fromkeys(str(item).lower() for item in raw_methods))
    return amount, [method for method in methods if method not in IGNORED_METHODS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创建 0 元 checkout 并读取 Stripe payment_method_types，不发起支付。",
    )
    parser.add_argument(
        "countries",
        nargs="*",
        metavar="COUNTRY[:CURRENCY]",
        help="不传时检测除 CN、US、JP 外的 192 个国家；也可指定 PL 或 ZA:ZAR",
    )
    parser.add_argument("--proxy", default="", help="同一个代理用于 checkout 和 Stripe init")
    parser.add_argument(
        "--output",
        default=str(core.SCRIPT_DIR / "payment_methods_results.json"),
        help="结果文件，默认 payment_methods_results.json",
    )
    parser.add_argument("--checkout-proxy", default="", help="checkout 阶段代理")
    parser.add_argument("--provider-proxy", default="", help="Stripe init 阶段代理")
    parser.add_argument(
        "--checkout-proxy-file",
        default=str(core.SCRIPT_DIR / "checkout.json"),
        help="checkout 代理文件，默认 checkout.json",
    )
    parser.add_argument(
        "--provider-proxy-file",
        default=str(core.SCRIPT_DIR / "provider.json"),
        help="Stripe 代理文件，默认 provider.json",
    )
    parser.add_argument("--promo-mode", default="campaign", help="优惠模式，默认 campaign")
    parser.add_argument("--promo-id", default="plus-1-month-free", help="优惠ID，默认 plus-1-month-free")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        targets = (
            [parse_target(value) for value in args.countries]
            if args.countries
            else [
                (country, supported_currency(currency))
                for country, currency in COUNTRY_CURRENCY.items()
            ]
        )
        checkout_proxy = resolve_proxy(args.checkout_proxy or args.proxy, args.checkout_proxy_file)
        provider_proxy = resolve_proxy(args.provider_proxy or args.proxy, args.provider_proxy_file)
        access_token, session_token = core.load_token()
        if not access_token:
            raise RuntimeError("access_token 为空")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误: {core.redact_log_text(str(exc))}", file=sys.stderr)
        return 2

    os.environ["PP_PROMO_MODE"] = args.promo_mode
    os.environ["PP_PROMO_ID"] = args.promo_id
    output_path = Path(args.output)
    results: list[dict[str, object]] = []
    print("\n检测结果")
    failed = False
    for country, currency in targets:
        try:
            amount, methods = detect_one(
                access_token,
                session_token,
                checkout_proxy,
                provider_proxy,
                country,
                currency,
            )
            print(f"{country} / {currency}: amount={amount}, {', '.join(methods) or '(空)'}")
            results.append({"country": country, "currency": currency, "amount": amount, "methods": methods})
        except Exception as exc:
            failed = True
            error_text = core.redact_log_text(str(exc))
            print(f"{country} / {currency}: 检测失败 - {error_text}")
            results.append({"country": country, "currency": currency, "error": error_text})
        output_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(f"结果文件: {output_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
