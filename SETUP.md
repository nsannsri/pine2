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

## Full Installation Guide (Fresh Server)

### Step 1: Install Python

1. Download Python 3.11 from https://www.python.org/downloads/windows/
2. Run the installer — **check "Add Python to PATH"**
3. Verify installation:
```powershell
python --version
```

### Step 2: Install Python Packages

```powershell
pip install flask MetaTrader5
```

Verify:
```powershell
pip list | findstr -i "flask\|MetaTrader"
```

### Step 3: Copy webhook.py to Server

- Place `webhook.py` at `C:\trading\webhook.py`
- Create the folder if needed:
```powershell
mkdir C:\trading
```

### Step 4: Install nginx

```powershell
Invoke-WebRequest -Uri "http://nginx.org/download/nginx-1.26.2.zip" -OutFile "C:\nginx.zip"
Expand-Archive -Path "C:\nginx.zip" -DestinationPath "C:\"
Rename-Item "C:\nginx-1.26.2" "C:\nginx"
```

Create SSL folder:
```powershell
mkdir C:\nginx\ssl
```

### Step 5: Configure nginx

Replace contents of `C:\nginx\conf\nginx.conf` with:

```nginx
events {}

http {
    server {
        listen 80;
        server_name webhook.safeguardi.com;

        location /.well-known/acme-challenge/ {
            root C:/nginx/html;
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    server {
        listen 443 ssl;
        server_name webhook.safeguardi.com;

        ssl_certificate      C:/nginx/ssl/webhook.safeguardi.com-chain.pem;
        ssl_certificate_key  C:/nginx/ssl/webhook.safeguardi.com-key.pem;

        location / {
            proxy_pass http://127.0.0.1:5000;
        }
    }
}
```

> Replace `webhook.safeguardi.com` with your domain if different.

### Step 6: Install win-acme (Let's Encrypt SSL)

```powershell
Invoke-WebRequest -Uri "https://github.com/win-acme/win-acme/releases/download/v2.2.9.1701/win-acme.v2.2.9.1701.x64.trimmed.zip" -OutFile "C:\wacs.zip"
Expand-Archive -Path "C:\wacs.zip" -DestinationPath "C:\wacs"
```

### Step 7: Get SSL Certificate

> Before running, make sure:
> - DNS A record for your domain points to your server IP (Cloudflare gray cloud / DNS only)
> - Port 80 is open to `0.0.0.0/0` in AWS Security Group temporarily
> - nginx is NOT running (stop it if running)
> - Windows Firewall allows port 80:
> ```powershell
> netsh advfirewall firewall add rule name="HTTP-80" dir=in action=allow protocol=TCP localport=80
> ```

Run win-acme:
```powershell
cd C:\wacs
.\wacs.exe
```

Follow these steps in the menu:
1. Press `N` — Create certificate (default settings)
2. Press `2` — Manual input
3. Enter your domain: `webhook.safeguardi.com`
4. Press `3` — No additional installation steps
5. Press `y` — Accept terms
6. Enter your email for notifications

After certificate is issued, export PEM files for nginx:
```powershell
.\wacs.exe --source manual --host webhook.safeguardi.com --store pemfiles --pemfilespath C:\nginx\ssl
```

Verify files exist:
```powershell
dir C:\nginx\ssl
```

You should see:
- `webhook.safeguardi.com-chain.pem`
- `webhook.safeguardi.com-key.pem`
- `webhook.safeguardi.com-crt.pem`
- `webhook.safeguardi.com-chain-only.pem`

> After getting the cert, close port 80 in AWS Security Group (remove the 0.0.0.0/0 rule).

### Step 8: Configure Windows Firewall for HTTPS

```powershell
netsh advfirewall firewall add rule name="HTTPS-443" dir=in action=allow protocol=TCP localport=443
```

### Step 9: Install NSSM (for nginx auto-start)

```powershell
Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile "C:\nssm.zip"
Expand-Archive -Path "C:\nssm.zip" -DestinationPath "C:\nssm"
```

Register nginx as a Windows service:
```powershell
C:\nssm\nssm-2.24\win64\nssm.exe install nginx C:\nginx\nginx.exe
C:\nssm\nssm-2.24\win64\nssm.exe start nginx
```

Verify:
```powershell
C:\nssm\nssm-2.24\win64\nssm.exe status nginx
# Should show: SERVICE_RUNNING
```

### Step 10: Setup webhook.py Auto-start (Task Scheduler)

> We use Task Scheduler (not a service) because MT5 requires an interactive user session.

```powershell
$action = New-ScheduledTaskAction -Execute "C:\Program Files\Python311\python.exe" -Argument "C:\trading\webhook.py"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
Register-ScheduledTask -TaskName "webhook" -Action $action -Trigger $trigger -RunLevel Highest -Force
```

Test it runs:
```powershell
schtasks /run /tn "webhook"
```

---

## Security Architecture

```
TradingView → Cloudflare WAF (allow only TradingView IPs) → Cloudflare Proxy → AWS Security Group (allow only Cloudflare IPs) → nginx (443) → Flask (5000) → MT5
```

---

## Cloudflare Setup

**Dashboard:** https://dash.cloudflare.com/bc285e21dde1921ae41e3e8877632d22/safeguardi.com/security/security-rules

**DNS Record:**
| Name | Type | Value | Proxy |
|------|------|-------|-------|
| webhook | A | 35.159.46.170 | Orange cloud (Proxied) |

### Cloudflare WAF Rules

**Rule 1 — Allow TradingView (order: 1)**
- Name: `Allow TradingView`
- Condition: `IP Source Address is in` → TradingView IPs below
- Action: **Skip** → All remaining custom rules

**Rule 2 — Block everyone else (order: 2)**
- Name: `Block all others`
- Condition: `IP Source Address is not in` → TradingView IPs below
- Action: **Block**

**TradingView IPs (official):**
```
52.89.214.238
34.212.75.30
54.218.53.128
52.32.178.7
```
> Reference: https://www.tradingview.com/support/solutions/43000529348

---

## AWS Security Group - Inbound Rules

**Security Group ID:** `sg-0cb697626eb8c84e0`

| Port | Protocol | Source | Description |
|------|----------|--------|-------------|
| 443  | TCP | 173.245.48.0/20 | Cloudflare |
| 443  | TCP | 103.21.244.0/22 | Cloudflare |
| 443  | TCP | 103.22.200.0/22 | Cloudflare |
| 443  | TCP | 103.31.4.0/22   | Cloudflare |
| 443  | TCP | 141.101.64.0/18 | Cloudflare |
| 443  | TCP | 108.162.192.0/18 | Cloudflare |
| 443  | TCP | 190.93.240.0/20 | Cloudflare |
| 443  | TCP | 188.114.96.0/20 | Cloudflare |
| 443  | TCP | 197.234.240.0/22 | Cloudflare |
| 443  | TCP | 198.41.128.0/17 | Cloudflare |
| 443  | TCP | 162.158.0.0/15  | Cloudflare |
| 443  | TCP | 104.16.0.0/13   | Cloudflare |
| 443  | TCP | 104.24.0.0/14   | Cloudflare |
| 443  | TCP | 172.64.0.0/13   | Cloudflare |
| 443  | TCP | 131.0.72.0/22   | Cloudflare |
| 3389 | TCP | Your IP | RDP access |

> Cloudflare official IP reference: https://www.cloudflare.com/ips-v4

### Get Latest Cloudflare IPs
Always fetch the latest IPs from Cloudflare before updating Security Group rules.

**Via browser:**
```
https://www.cloudflare.com/ips-v4
```

**Via API:**
```bash
curl --request GET \
  --url https://api.cloudflare.com/client/v4/ips \
  --header 'Content-Type: application/json'
```

### Add Cloudflare IPs via AWS CLI
```powershell
aws ec2 authorize-security-group-ingress --group-id sg-0cb697626eb8c84e0 --ip-permissions file://C:\cf_rules.json
```
> `cf_rules.json` is stored in this repo at `cf_rules.json`

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
C:\nssm\nssm-2.24\win64\nssm.exe start nginx
C:\nssm\nssm-2.24\win64\nssm.exe stop nginx
C:\nssm\nssm-2.24\win64\nssm.exe restart nginx
C:\nssm\nssm-2.24\win64\nssm.exe status nginx
```

### webhook.py (Task Scheduler)
```powershell
schtasks /run /tn "webhook"      # Start
schtasks /end /tn "webhook"      # Stop
schtasks /delete /tn "webhook" /f  # Delete task
```

---

## SSL Certificate Renewal

**Certificate validity:** 90 days
**Current cert issued:** 2026/04/02
**Renewal due:** 2026/05/27 (set a calendar reminder for 2026/05/20)

> Port 80 must be open temporarily during renewal for Let's Encrypt domain validation.
> Auto-renewal via scheduled task is disabled — renewal is done manually.

### Manual Renewal Steps

**Step 1: Open port 80 in AWS Security Group**
- Go to AWS Console → EC2 → Security Groups
- Add inbound rule: HTTP (port 80) → `0.0.0.0/0`

**Step 2: Run renewal**
```powershell
cd C:\wacs
.\wacs.exe --renew --baseuri "https://acme-v02.api.letsencrypt.org/"
```

**Step 3: Re-export PEM files**
```powershell
.\wacs.exe --source manual --host webhook.safeguardi.com --store pemfiles --pemfilespath C:\nginx\ssl
```

**Step 4: Restart nginx**
```powershell
C:\nssm\nssm-2.24\win64\nssm.exe restart nginx
```

**Step 5: Close port 80 in AWS Security Group**
- Go to AWS Console → EC2 → Security Groups
- Remove the HTTP (port 80) `0.0.0.0/0` inbound rule

**Step 6: Verify HTTPS still works**
```powershell
curl -I https://webhook.safeguardi.com
```
Should return `HTTP/1.1 404 NOT FOUND` with `Server: nginx`

---

## Testing

```powershell
# Sell order with TP
curl -X POST https://webhook.safeguardi.com/webhook -H "Content-Type: application/json" -d '{\"token\":\"xau-tv-9x2k7p\",\"symbol\":\"XAUUSD\",\"action\":\"sell\",\"lots\":1,\"tp\":20}'

# Buy order with TP
curl -X POST https://webhook.safeguardi.com/webhook -H "Content-Type: application/json" -d '{\"token\":\"xau-tv-9x2k7p\",\"symbol\":\"XAUUSD\",\"action\":\"buy\",\"lots\":1,\"tp\":20}'

# Close position
curl -X POST https://webhook.safeguardi.com/webhook -H "Content-Type: application/json" -d '{\"token\":\"xau-tv-9x2k7p\",\"symbol\":\"XAUUSD\",\"action\":\"close\"}'
```

---

## Troubleshooting

### nginx not starting
```powershell
C:\nginx\nginx.exe -t          # Check config syntax
netstat -an | findstr :443     # Check if port 443 is listening
```

### webhook returning 502
- Flask is not running — run `schtasks /run /tn "webhook"` or start manually
- Check MT5 is running and logged in

### webhook returning 500
- MT5 not connected — ensure MT5 is running in the user session
- Always run webhook.py via Task Scheduler (not as a Windows service) — MT5 needs interactive session

### SSL certificate issues
- Check cert files exist in `C:\nginx\ssl\`
- Run manual renewal (see above)
- Temporarily open port 80 in AWS Security Group for Let's Encrypt validation

### Let's Encrypt rate limit error
- Too many failed attempts on same domain — use a different subdomain and try again after 1 hour
