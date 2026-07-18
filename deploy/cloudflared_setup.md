# Cloudflare Tunnel setup (stable HTTPS, no open ports)

The tunnel runs on the VPS, dials **out** to Cloudflare, and exposes the bridge
(`localhost:8080`) at a stable HTTPS hostname. No inbound ports, no firewall
changes, works even behind NAT.

There are two flavours:

## A. Named tunnel — RECOMMENDED (stable URL, survives restarts)
Requires a free Cloudflare account **and a domain added to Cloudflare**
(any cheap domain works; nameservers pointed at Cloudflare).

```bash
# on the VPS
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

cloudflared tunnel login                      # opens a URL — authorise your domain
cloudflared tunnel create binance-tv-bridge   # note the Tunnel ID it prints
cloudflared tunnel route dns binance-tv-bridge bridge.YOURDOMAIN.com
```

Create `/etc/cloudflared/config.yml`:
```yaml
tunnel: <TUNNEL_ID>
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: bridge.YOURDOMAIN.com
    service: http://localhost:8080
  - service: http_status:404
```

Install as a service:
```bash
cloudflared service install
systemctl enable --now cloudflared
```

Your webhook URL: `https://bridge.YOURDOMAIN.com/webhook/<WEBHOOK_TOKEN>`

## B. Quick tunnel — no domain, but EPHEMERAL
```bash
cloudflared tunnel --url http://localhost:8080
```
Prints a random `https://<random>.trycloudflare.com` URL. Fine for a quick test,
**not** for production: the URL changes on every restart and Cloudflare may rate-
limit it, which would silently drop your trading webhooks.

## Note on the IP allowlist
Behind the tunnel, the bridge reads the real TradingView IP from the
`CF-Connecting-IP` header (already handled in code), so `IP_ALLOWLIST` keeps
working. For extra lockdown you can also put **Cloudflare Access** in front of
the hostname.
