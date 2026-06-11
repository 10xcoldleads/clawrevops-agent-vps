# ClawRevOps.ai Agent VPS

Turns a fresh VPS into an AI agent server.

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

## What it installs

1. A safe non-root user (with SSH keys copied from root)
2. Docker
3. Tailscale private networking
4. Claude Code or OpenAI Codex CLI (your choice)
5. Hermes Agent or OpenClaw (your choice)
6. Agent workspace at `~/clawrevops/`
7. Optional SSH hardening (root login + password auth disabled)

## Resume

Setup is resumable. If anything interrupts it:

```bash
sudo agent-setup
```

Completed steps are skipped automatically.

## Community

https://join.clawrevops.ai
