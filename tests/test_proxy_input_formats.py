from pathlib import Path
import importlib.util
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROXY_INPUTS = [
    "proxy.example:3010:user:pass",
    "proxy.example:3010@user:pass",
    "user:pass:proxy.example:3010",
    "user:pass@proxy.example:3010",
]
EXPECTED_HTTP = "http://user:pass@proxy.example:3010"
EXPECTED_SOCKS5H = "socks5h://user:pass@proxy.example:3010"


def load_module(relative_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_business_flows_accept_supported_proxy_formats(monkeypatch):
    modules = [
        ("ideal_qr_extract.py", "ideal_qr_extract", "IDEAL_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
        ("pix/pix_extract.py", "pix_extract", "PIX_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
        ("twint/twint_extract.py", "twint_extract", "TWINT_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
        ("upi/upi_extract.py", "upi_extract", "UPI_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
        ("blik/blik_qr_extract.py", "blik_qr_extract", "IDEAL_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
        ("kakao/kakao_extract.py", "kakao_extract", "KAKAO_PROXY_DEFAULT_SCHEME", EXPECTED_HTTP),
    ]
    for relative_path, module_name, env_name, expected in modules:
        monkeypatch.setenv(env_name, "http")
        module = load_module(relative_path, module_name)
        assert [module.normalize_proxy_url(value) for value in PROXY_INPUTS] == [expected] * 4


def test_pre_proxy_formats_use_socks5h_default(monkeypatch):
    monkeypatch.delenv("IDEAL_PROXY_DEFAULT_SCHEME", raising=False)
    module = load_module("ideal_qr_extract.py", "ideal_qr_extract_pre_proxy")

    assert [module.normalize_pre_proxy_url(value) for value in PROXY_INPUTS] == [EXPECTED_SOCKS5H] * 4


def test_turkey_card_reuses_main_proxy_parser(monkeypatch):
    monkeypatch.setenv("IDEAL_PROXY_DEFAULT_SCHEME", "http")
    from turkey_card import card_extract as card

    assert [card.flow.normalize_proxy_url(value) for value in PROXY_INPUTS] == [EXPECTED_HTTP] * 4