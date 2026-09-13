#!/usr/bin/env bash
set -euo pipefail
# Install the proxy's CA so HTTPS interception works (waits for the proxy to
# have generated it on first start).
for _ in $(seq 1 30); do
  if [ -f /chassis/ca/mitmproxy-ca-cert.pem ]; then
    cp /chassis/ca/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
    update-ca-certificates >/dev/null 2>&1 || true
    break
  fi
  sleep 1
done
exec su -s /bin/bash agent -c "$*"
