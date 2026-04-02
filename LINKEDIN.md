# LinkedIn Post — TradingView to MT5 Secure Webhook Architecture

---

## Post Content

---

**Built a production-grade secure webhook system connecting TradingView alerts to MetaTrader 5 — here's how I architected it.**

As an algorithmic trader, I needed a reliable and secure way to execute trades on MetaTrader 5 automatically when TradingView signals fire. What started as a simple webhook quickly evolved into a multi-layered security architecture on AWS.

---

### The Challenge

The core problem: how do you expose a trading endpoint to the internet that executes real money orders — without getting hacked, flooded with fake signals, or having your server overloaded?

Key challenges I faced:

- **Open internet exposure** — The server had to accept incoming HTTP requests from TradingView
- **Fake signal attacks** — Anyone with a TradingView account can send webhook requests to any URL
- **DDoS risk** — A flood of fake requests could overload the trading server
- **Secret token exposure** — Tokens in URLs appear in server logs and browser history
- **SSL/HTTPS** — Plain HTTP meant tokens and payloads travelled unencrypted
- **Auto-recovery** — The system had to survive server reboots without manual intervention

---

### The Architecture

```
TradingView Alert
      │
      ▼
Cloudflare WAF
  ├── Layer 1: IP Whitelist (TradingView's 4 official IPs only)
  └── Layer 2: Secret key in URL query string (?key=***)
      │
      ▼
Cloudflare Proxy (HTTPS/443)
      │
      ▼
AWS EC2 Windows Server
  └── AWS Security Group (Cloudflare IP ranges only)
      │
      ▼
nginx reverse proxy (port 443 → 5000)
      │
      ▼
Flask Webhook App
  └── Layer 3: Secret token validation in JSON body
      │
      ▼
MetaTrader 5 API
  └── Execute Buy/Sell/Close with Take Profit
```

---

### Security Layers — Defence in Depth

**Layer 1 — IP Whitelisting at Cloudflare WAF**
TradingView publishes 4 official webhook server IPs. I whitelisted only these at Cloudflare WAF level — all other IPs are blocked before they even reach my infrastructure.

**Layer 2 — Secret Key in URL Query String**
Even if someone spoofs a TradingView IP, they still need the secret key in the URL (`?key=***`). This is validated at Cloudflare WAF level — invalid requests are dropped at the edge, never touching the origin server.

**Layer 3 — Secret Token in JSON Body**
A final token check inside the Flask app. Since Cloudflare cannot inspect JSON request bodies, this layer is handled at the application level — ensuring only authenticated TradingView alerts execute trades.

**Layer 4 — AWS Security Group**
The EC2 instance only accepts traffic from Cloudflare's official IP ranges. Direct access to the server IP is impossible — all traffic must pass through Cloudflare first.

**Layer 5 — Cloudflare Proxy**
The real server IP is hidden behind Cloudflare. Even a full DDoS attack hits Cloudflare's global network, not the trading server.

---

### Tech Stack

| Component | Technology |
|---|---|
| Cloud | AWS EC2 (Windows Server) |
| Reverse Proxy | nginx |
| SSL Certificate | Let's Encrypt (win-acme) |
| CDN / WAF | Cloudflare |
| Webhook App | Python / Flask |
| Trading API | MetaTrader 5 Python API |
| Service Management | NSSM + Windows Task Scheduler |
| Infrastructure | AWS Security Groups, Cloudflare WAF Custom Rules |

---

### Key Engineering Decisions

**Why Cloudflare proxy over direct exposure?**
Cloudflare hides the real server IP, provides DDoS protection, and allows WAF rules at the edge — all for free.

**Why Task Scheduler instead of Windows Service for Flask?**
MetaTrader 5 requires an interactive Windows desktop session to function. Windows Services run in Session 0 (no GUI), so Flask must run as a scheduled task at user logon to share the same session as MT5.

**Why Let's Encrypt over AWS Certificate Manager?**
ACM certificates cannot be installed directly on EC2 instances (only works with ALB/CloudFront). Let's Encrypt via win-acme is free, automatic, and installs directly on the server.

**Why URL query key AND JSON body token?**
Cloudflare WAF cannot inspect JSON request bodies — so the URL key handles edge-level filtering. The JSON token adds a final application-level check as defence in depth.

---

### What I Learned

- Deep dive into Cloudflare WAF custom rule expressions
- AWS Security Group design for multi-layered network access control
- Windows service limitations with GUI-dependent applications
- Let's Encrypt certificate management on Windows
- nginx reverse proxy configuration on Windows
- Defence-in-depth security architecture for financial APIs

---

### Results

- **Zero open ports** to the public internet except through Cloudflare
- **3 independent security layers** before any trade executes
- **Fully automated** — survives server reboots, auto-renews SSL
- **Production ready** — handling live XAUUSD trades via TradingView alerts

---

*Interested in algorithmic trading infrastructure or have questions about the architecture? Drop a comment below.*

**#AWS #CloudSecurity #AlgoTrading #Cloudflare #Python #MetaTrader5 #TradingView #DevOps #WebSecurity #FinTech**

---
