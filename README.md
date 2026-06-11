# ClawRevOps.ai Agent VPS

Turns a fresh VPS into a secure AI agent server.

Coded by Ty Shane using OpenAI.

## Setup

**main (stable)**
```bash
wget -qO /tmp/clawrevops-install.sh https://raw.githubusercontent.com/10xcoldleads/clawrevops-agent-vps/main/install.sh && sudo bash /tmp/clawrevops-install.sh
```

**dev (latest)**
```bash
wget -qO /tmp/clawrevops-install.sh https://raw.githubusercontent.com/10xcoldleads/clawrevops-agent-vps/dev/install.sh && sudo bash /tmp/clawrevops-install.sh dev
```

Type `LETSGO` when prompted to begin.

## What it does

**Phase 1 — Security first (mandatory):**
1. System update + swap guard for small VPSes
2. Safe non-root agent user (SSH keys copied from root when present)
3. Your choices: coding assistant, agent platform, full/headless mode
4. Desktop + Remote Desktop (xrdp) — full mode
5. Tailscale private networking (required)
6. Tailscale lockdown — firewall blocks ALL public access; SSH/RDP
   work only over your private Tailscale network

**Phase 2 — Agent stack (only after lockdown):**
7. Docker
8. Claude Code and/or OpenAI Codex CLI (installed under the agent user)
9. Hermes Agent (Docker container or native) and/or OpenClaw
   (native with Homebrew, or Docker container)
10. Google Chrome — full mode
11. Agent workspace at `~/clawrevops/`
12. Verification + a final report with your exact connect commands
    (SSH + Remote Desktop via your Tailscale IP)
13. Optional SSH key hardening

Docker containers are named `clawrevops-hermes` / `clawrevops-openclaw`,
appear in your VPS panel's Docker Manager, auto-restart, and publish
ports bound to your Tailscale IP only (never public).

## Resume

Setup is resumable. If anything interrupts it:

```bash
sudo agent-setup
```

Completed steps are skipped automatically. The wizard self-updates
from this repo on every run.

## Requirements

- Fresh Ubuntu 22.04 / 24.04 or Debian 12 VPS
- Full mode (desktop + RDP): 4GB+ RAM recommended
- Headless mode: 2GB+ RAM
- A free Tailscale account (https://tailscale.com)

## Community

https://join.clawrevops.ai
