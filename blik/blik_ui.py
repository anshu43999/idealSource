from __future__ import annotations

import argparse
import io
import json
import os
import re
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
SCRIPT_PATH = ROOT / "blik_qr_extract.py"
UI_PATH = ROOT / "blik_ui.html"
PROXY_SEED_PATH = ROOT / "proxy_seeds.txt"
LEGACY_PROXY_PATHS = (
    ROOT / "checkout.json",
    ROOT / "provider.json",
)
TOKEN_PATH = ROOT / "token.txt"
MAX_LOG_LINES = 3000


def make_qr_svg(text: str) -> bytes:
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except Exception as exc:
        raise RuntimeError("缺少 qrcode 依赖，请先执行: python3 -m pip install qrcode") from exc

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=2,
        box_size=8,
    )
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue()


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
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


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


def resolve_proxy_file(value: str, label: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise ValueError(f"{label}不存在")
    return str(path.resolve())


def prepare_persistent_files(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    seed_text = clean_text(payload, "proxy_seeds", "", 400_000)
    seed_count = count_proxy_text(seed_text)
    if seed_count:
        write_text_atomic(PROXY_SEED_PATH, seed_text.rstrip() + "\n")
    else:
        seed_count = count_proxy_lines(str(PROXY_SEED_PATH))
        if not seed_count:
            raise ValueError("请填写代理 Seed 池")
    token_text = clean_text(payload, "token", "", 30000)
    if token_text:
        TOKEN_PATH.write_text(token_text.rstrip() + "\n", encoding="utf-8")

    task_payload = dict(payload)
    task_payload["proxy_seed_file"] = str(PROXY_SEED_PATH)
    for name in ("checkout_proxies", "provider_proxies", "checkout_file", "provider_file"):
        task_payload.pop(name, None)
    return task_payload, seed_count


def build_environment(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
    batch_size = as_int(payload, "batch_size", 5, 1, 100)
    max_batches = as_int(payload, "max_batches", 5, 1, 100)
    poll_timeout = as_int(payload, "poll_timeout", 45, 5, 300)
    max_attempts = max_batches

    proxy_seed_file = resolve_proxy_file(
        clean_text(payload, "proxy_seed_file", str(PROXY_SEED_PATH)),
        "代理 Seed 文件",
    )
    blik_code = clean_text(payload, "blik_code", "", 6)
    if not blik_code:
        raise ValueError("请填写 BLIK Code 后再启动任务")
    if not re.fullmatch(r"\d{6}", blik_code):
        raise ValueError("BLIK Code 必须是6位数字")

    promo_mode = clean_text(payload, "promo_mode", "campaign", 20).lower()
    if promo_mode not in {"coupon", "campaign", "query", "trial", "free_trial", "code", "off"}:
        raise ValueError("优惠模式不正确")
    proxy_default_scheme = clean_text(payload, "proxy_default_scheme", "socks5h", 20).lower()
    if proxy_default_scheme not in {"http", "socks5h"}:
        raise ValueError("代理默认协议不正确")

    pre_proxy = clean_text(payload, "pre_proxy", "", 500)
    env = os.environ.copy()
    for name in (
        "IDEAL_CHECKOUT_PROXY_FILE",
        "IDEAL_PROVIDER_PROXY_FILE",
        "PP_CHECKOUT_PROXY_FILE",
        "PP_PROVIDER_PROXY_FILE",
    ):
        env.pop(name, None)
    env.update(
        {
            "IDEAL_PAYMENT_METHOD": "blik",
            "IDEAL_BLIK_CODE": blik_code,
            "IDEAL_PROXY_SEED_FILE": proxy_seed_file,
            "IDEAL_FLOW_MODE": "single",
            "IDEAL_CHECKOUT_RETRY_MAX": str(batch_size),
            "IDEAL_PROVIDER_RETRY_MAX": "1",
            "IDEAL_PROVIDER_PER_CHECKOUT": "1",
            "IDEAL_WORKERS": "1",
            "IDEAL_WORKERS_MAX": "1",
            "IDEAL_MAX_RETRY": str(max_attempts),
            "IDEAL_MAX_APPROVE_BLOCKED": str(max_attempts),
            "IDEAL_PROXY_DEFAULT_SCHEME": proxy_default_scheme,
            "IDEAL_POLL_TIMEOUT": str(poll_timeout),
            "IDEAL_PROXY_PRECHECK": "0",
            "IDEAL_PROXY_GEO_CHECK": "0",
            "IDEAL_PROXY_GEO_USE_PRE_PROXY": "0",
            "IDEAL_PROXY_TARGET_CHECK": "1",
            "IDEAL_PROXY_TARGET_PRECHECK": "0",
            "IDEAL_PROXY_TARGET_USE_PRE_PROXY": "1",
            "IDEAL_PROXY_FAIL_COOLDOWN": "180",
            "IDEAL_PROXY_REMOVE_AFTER_FAILS": "3",
            "IDEAL_ZERO_CACHE": "1",
            "IDEAL_APPROVE_RETRY_MAX": "10",
            "IDEAL_APPROVE_PARALLEL": "1",
            "IDEAL_APPROVE_STICKY": "1",
            "IDEAL_CONFIRM_INLINE_PM": "0",
            "IDEAL_UPDATE_TAX_REGION": "0",
            "IDEAL_CHECKOUT_SNAPSHOT": "0",
            "IDEAL_APPROVE_WARMUP": "1",
            "IDEAL_SAVED_PAYMENT_VALUE": "never",
            "IDEAL_BROWSER_LOCALE": "pl-PL",
            "IDEAL_ELEMENTS_LOCALE": "pl-PL",
            "IDEAL_BROWSER_TIMEZONE": "Europe/Warsaw",
            "IDEAL_PROXY_REMOVE_FAILED": "1" if as_bool(payload, "remove_failed", True) else "0",
            "IDEAL_REQUIRE_ZERO": "1",
            "IDEAL_CHECKOUT_COUNTRY": "PL",
            "IDEAL_CHECKOUT_PROXY_COUNTRY": "PL",
            "IDEAL_PROVIDER_PROXY_COUNTRIES": "PL",
            "IDEAL_BANK": "",
            "IDEAL_PRE_PROXY": pre_proxy,
            "IDEAL_USE_LOCAL_PROXY_ONLY": "0",
            "PP_PROMO_MODE": promo_mode,
            "PP_PROMO_ID": clean_text(payload, "promo_id", "plus-1-month-free", 200),
        }
    )

    token = clean_text(payload, "token", "", 30000)
    session_token = clean_text(payload, "session_token", "", 30000)
    if token:
        env["PP_TOKEN"] = token
    else:
        if not (ROOT / "token.txt").is_file():
            raise ValueError("未填写Token，且项目中不存在 token.txt")
        env.pop("PP_TOKEN", None)
        env.pop("IDEAL_TOKEN", None)
    if session_token:
        env["PP_SESSION_TOKEN"] = session_token
    else:
        env.pop("PP_SESSION_TOKEN", None)

    public_config = {
        "batch_size": batch_size,
        "max_batches": max_batches,
        "max_attempts": max_attempts,
        "payment_method": "blik",
        "payment_flow": "PL/PL/PL",
        "poll_timeout": poll_timeout,
        "proxy_seed_file": proxy_seed_file,
        "checkout_country": "PL",
        "provider_countries": "PL",
        "proxy_default_scheme": proxy_default_scheme,
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
        self.proxy_texts: dict[str, str] = {
            "seed": read_proxy_text(str(PROXY_SEED_PATH)),
        }
        self.proxy_text_versions: dict[str, int] = {
            "seed": 1 if self.proxy_texts["seed"] else 0,
        }
        self.token_text = read_local_text(TOKEN_PATH)
        self.token_text_version = 1 if self.token_text else 0
        self.last_config: dict[str, Any] = {
            "payment_method": "blik",
            "payment_flow": "PL/PL/PL",
            "proxy_seed_file": str(PROXY_SEED_PATH),
        }

    def _append_locked(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        self.logs.append({"id": self.next_log_id, "text": line})
        self.next_log_id += 1
        if "最终扫码/授权 URL:" in line or "支付页 URL:" in line:
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
        text = read_proxy_text(path_value)
        if text != self.proxy_texts.get("seed", ""):
            self.proxy_texts["seed"] = text
            self.proxy_text_versions["seed"] = self.proxy_text_versions.get("seed", 0) + 1
        self.last_config["seed_count"] = count_proxy_text(text)

    def _sync_token_text_locked(self) -> None:
        text = read_local_text(TOKEN_PATH)
        if text != self.token_text:
            self.token_text = text
            self.token_text_version += 1

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("脚本正在运行")
            blik_code = clean_text(payload, "blik_code", "", 6)
            if not blik_code:
                raise ValueError("请填写 BLIK Code 后再启动任务")
            if not re.fullmatch(r"\d{6}", blik_code):
                raise ValueError("BLIK Code 必须是6位数字")
            task_payload, seed_count = prepare_persistent_files(payload)
            env, public_config = build_environment(task_payload)
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
            self.last_config = public_config
            self.proxy_texts = {
                "seed": clean_text(task_payload, "proxy_seeds", "", 400_000),
            }
            self.proxy_text_versions = {"seed": 1}
            self._sync_token_text_locked()
            self._append_locked(
                f"[UI] 启动 BLIK 任务: {public_config['payment_flow']}，"
                f"每轮Seed候选 {public_config['batch_size']}，总重试 {public_config['max_batches']}"
            )
            try:
                self.process = subprocess.Popen(
                    [sys.executable, "-u", str(SCRIPT_PATH)],
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
            self._append_locked(f"[UI] 进程已启动: pid={process.pid}")
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
            seed_count = (
                count_proxy_lines(proxy_seed_file)
                if Path(proxy_seed_file).is_file()
                else int(config.get("seed_count") or 0)
            )
            public_config = {
                key: value
                for key, value in config.items()
                if key not in {"proxy_seed_file", "pre_proxy"}
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
                "token_file": TOKEN_PATH.is_file(),
                "config": public_config,
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
                self._send_json({"error": "blik_ui.html 不存在"}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
        if parsed.path == "/api/qr":
            query = parse_qs(parsed.query)
            text = str((query.get("text") or [""])[0]).strip()
            if not text:
                self._send_json({"error": "QR 内容为空"}, HTTPStatus.BAD_REQUEST)
                return
            if len(text) > 3000:
                self._send_json({"error": "QR 内容过长"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                self._send_bytes(make_qr_svg(text), "image/svg+xml; charset=utf-8")
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
    parser = argparse.ArgumentParser(description="BLIK Runner 本地控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not SCRIPT_PATH.is_file() or not UI_PATH.is_file():
        print("缺少 blik_qr_extract.py 或 blik_ui.html", file=sys.stderr)
        return 1

    try:
        server = ThreadingHTTPServer((args.host, args.port), UIHandler)
    except OSError as exc:
        print(f"页面服务启动失败: {exc}，可使用 --port 更换端口", file=sys.stderr)
        return 1
    url = f"http://{args.host}:{args.port}"
    print(f"BLIK Runner UI: {url}")
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
