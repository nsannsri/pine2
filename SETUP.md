# Webhook Server Setup Documentation

## Overview
TradingView webhook receiver running on AWS Windows EC2, forwarding signals to MetaTrader 5.

**Server IP:** `35.159.46.170`
**Domain:** `webhook.safeguardi.com`
**Webhook URL:** `https://webhook.safeguardi.com/webhook`

---

## Architecture
```
TradingView → AWS Security Group (IP whitelist) → nginx (443/HTTPS) → Flask webhook.py (5000) → MT5
```

---

## TradingView Payload Format
```json
{
  "token": "xau-tv-9x2k7p",
  "symbol": "XAUUSD",
  "action": "buy",
  "lots": 1,
  "tp": 20
}
```

- `action`: `buy`, `sell`, or `close`
- `lots`: lot size (e.g. `1`, `0.1`)
- `tp`: take profit in price points (optional, e.g. `20` means 20 points from entry)

---

## Security

### AWS Security Group - Inbound Rules
| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 443  | TCP      | 52.89.214.238/32 | TradingView |
| 443  | TCP      | 34.212.75.30/32  | TradingView |
| 443  | TCP      | 54.218.53.128/32 | TradingView |
| 443  | TCP      | 52.32.178.7/32   | TradingView |
| 3389 | TCP      | Your IP | RDP access |

> TradingView official IP reference: https://www.tradingview.com/support/solutions/43000529348

### Secret Token
Token is hardcoded in `webhook.py`:
```python
SECRET_TOKEN = "xau-tv-9x2k7p"
```

---

## Server Components

### 1. Flask App (webhook.py)
- **Location:** `C:\trading\webhook.py`
- **Port:** `5000`
- **Auto-start:** Windows Task Scheduler (runs at Administrator logon)

### 2. nginx
- **Location:** `C:\nginx`
- **Config:** `C:\nginx\conf\nginx.conf`
- **Port:** `443` (HTTPS) → proxies to Flask on `5000`
- **Auto-start:** NSSM Windows Service

### 3. SSL Certificate
- **Provider:** Let's Encrypt (via win-acme)
- **Location:** `C:\nginx\ssl\`
  - `webhook.safeguardi.com-chain.pem` (certificate)
  - `webhook.safeguardi.com-key.pem` (private key)
- **Expiry:** Every 198 days
- **Auto-renewal:** win-acme scheduled task runs daily at 09:00

---

## File Locations on Server
| File | Path |
|------|------|
| webhook.py | `C:\trading\webhook.py` |
| nginx binary | `C:\nginx\nginx.exe` |
| nginx config | `C:\nginx\conf\nginx.conf` |
| SSL certs | `C:\nginx\ssl\` |
| win-acme | `C:\wacs\` |
| NSSM | `C:\nssm\nssm-2.24\win64\nssm.exe` |

---

## Managing Services

### nginx (NSSM service)
```powershell
# Start
C:\nssm\nssm-2.24\win64\nssm.exe start nginx

# Stop
C:\nssm\nssm-2.24\win64\nssm.exe stop nginx

# Restart
C:\nssm\nssm-2.24\win64\nssm.exe restart nginx

# Status
C:\nssm\nssm-2.24\win64\nssm.exe status nginx
```

### webhook.py (Task Scheduler)
```powershell
# Run manually
schtasks /run /tn "webhook"

# Stop
schtasks /end /tn "webhook"

# Delete task
schtasks /delete /tn "webhook" /f
```

---

## SSL Certificate Renewal
win-acme auto-renews the certificate. If manual renewal needed:

```powershell
cd C:\wacs
.\wacs.exe --renew --baseuri "https://acme-v02.api.letsencrypt.org/"
```

After renewal, restart nginx:
```powershell
C:\nssm\nssm-2.24\win64\nssm.exe restart nginx
```

---

## Windows Firewall Rules
```powershell
# Allow HTTPS
netsh advfirewall firewall add rule name="HTTPS-443" dir=in action=allow protocol=TCP localport=443

# Allow HTTP (only needed temporarily for Let's Encrypt renewal)
netsh advfirewall firewall add rule name="HTTP-80" dir=in action=allow protocol=TCP localport=80
```

---

## Testing
```powershell
# Sell order with TP
curl -X POST https://webhook.safeguardi.com/webhook `
  -H "Content-Type: application/json" `
  -d '{"token":"xau-tv-9x2k7p","symbol":"XAUUSD","action":"sell","lots":1,"tp":20}'

# Buy order with TP
curl -X POST https://webhook.safeguardi.com/webhook `
  -H "Content-Type: application/json" `
  -d '{"token":"xau-tv-9x2k7p","symbol":"XAUUSD","action":"buy","lots":1,"tp":20}'

# Close position
curl -X POST https://webhook.safeguardi.com/webhook `
  -H "Content-Type: application/json" `
  -d '{"token":"xau-tv-9x2k7p","symbol":"XAUUSD","action":"close"}'
```

---

## Troubleshooting

### nginx not starting
```powershell
# Check config syntax
C:\nginx\nginx.exe -t

# Check if port 443 is listening
netstat -an | findstr :443
```

### webhook returning 502
- Flask is not running — run `schtasks /run /tn "webhook"` or start manually
- Check MT5 is running and logged in

### webhook returning 500
- MT5 not connected — ensure MT5 is running in the user session
- Services can't access MT5 GUI — always run webhook.py via Task Scheduler (not as a service)

### SSL certificate issues
- Check cert expiry date in `C:\nginx\ssl\`
- Run manual renewal if needed (see above)
- Temporarily open port 80 in AWS Security Group for Let's Encrypt validation
