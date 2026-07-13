#!/bin/bash
set -e

# Docker creates empty directories for bind-mounted file paths when the
# host file doesn't exist yet.  Replace any such directories with real
# (empty) files so the application can write to them.
for f in \
    /app/nl_proxy_seeds.txt /app/vn_proxy_seeds.txt \
    /app/pix/br_proxy_seeds.txt /app/pix/vn_proxy_seeds.txt \
    /app/twint/ch_proxy_seeds.txt /app/twint/vn_proxy_seeds.txt \
    /app/upi/in_proxy_seeds.txt /app/upi/vn_proxy_seeds.txt \
    /app/kakao/kr_proxy_seeds.txt /app/kakao/vn_proxy_seeds.txt /app/kakao/token.txt \
    /app/upi/proxy_seeds.txt /app/upi/token.txt \
    /app/blik/token.txt /app/pix/token.txt /app/twint/token.txt; do
    if [ -d "$f" ]; then
        rm -rf "$f"
    fi
    if [ ! -f "$f" ]; then
        touch "$f"
    fi
done

exec python ideal_ui.py --host 0.0.0.0 --port 8060 --no-open
