# Payment Link Extractor

Supported methods include iDEAL, Turkey Card (`US checkout -> TR update -> TR manual card page`), PIX, Kakao Pay, BLIK, TWINT, and UPI.

本项目提供一个本地支付提链控制台，支持 iDEAL、Turkey Card、PIX、Kakao Pay、BLIK、TWINT 和 UPI。每种方式使用独立的代理池与 Token 文件；界面在切换方式时读取当前方式的已保存配置。

## 环境

- Python 3.10 或更高版本
- `pip install -r requirements.txt`

`curl_cffi` 用于需要浏览器指纹或本机前置代理的场景，`qrcode` 用于 BLIK 独立页面的二维码生成。

## 配置

不要把代理、Token、抓包、日志或状态文件提交到仓库。根目录和各支付方式子目录中的以下文件均被 `.gitignore` 排除：

- `proxy_seeds.txt`
- `token.txt`
- `proxy_state.json`
- `removed_proxies.jsonl`
- `logs/` 与 `dumps/`

可选的 `STRIPE_PUBLISHABLE_KEY` 放在本机环境变量或 `.env` 中。发布包没有内置任何线上公钥、代理或 Token。

## 运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 ideal_ui.py --host 127.0.0.1 --port 8787
```

默认建议仅绑定本机地址。若通过反向代理公开页面，请先配置访问控制，因为页面可读取当前支付方式已保存的代理和 Token。

## 发布前检查

```bash
git status --ignored
git check-ignore proxy_seeds.txt token.txt logs/example.log
```

确认运行数据均被忽略后再推送仓库。

## License

MIT License，见 [LICENSE](LICENSE)。
