#!/usr/bin/env python3
"""
ClawRevOps.ai Agent VPS Setup
Turns a fresh VPS into an AI agent server.

Coded by Ty Shane using OpenAI.
https://clawrevops.ai

Modeled on the ClawGlue installer framework (proven on Hostinger VPS):
  - Bash bootstrapper downloads and launches this wizard
  - JSON state file makes every step resumable / idempotent
  - All user-facing software installs under a non-root user via `su - user -c`
  - Destructive steps are gated behind typed confirmations
  - SSH hardening runs LAST, only after a verified second-terminal login
"""

import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

STATE_FILE = "/var/lib/clawrevops/setup_state.json"
LOG_FILE = "/var/log/clawrevops_setup.log"

# Official Hermes Agent installer (Nous Research docs:
# hermes-agent.nousresearch.com/docs/getting-started/installation).
# Includes Hermes' own headless Chromium (Playwright) for browser automation.
HERMES_INSTALL_CMD = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"

OPENCLAW_INSTALL_CMD = "curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard"
CLAUDE_CODE_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"
CODEX_NPM_PACKAGE = "@openai/codex"

COMMUNITY_URL = "https://join.clawrevops.ai"

SSH_HARDENING_DROPIN = "/etc/ssh/sshd_config.d/00-clawrevops-hardening.conf"


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


class AgentVPSSetup:
    def __init__(self):
        self.setup_log = []
        self.os_info = self._detect_os_info()

        # Restore persisted state so re-runs skip completed steps
        state = self._load_state()
        self.agent_username = state.get("agent_username")
        self.tailscale_ip = state.get("tailscale_ip")
        self.ssh_keys_copied = state.get("ssh_keys_copied", False)
        self.assistant_choice = state.get("assistant_choice")  # codex|claude|both|skip
        self.platform_choice = state.get("platform_choice")    # hermes|openclaw|both|skip
        self.server_mode = state.get("server_mode")            # full|headless
        self.hermes_deploy = state.get("hermes_deploy")        # docker|native
        self.openclaw_deploy = state.get("openclaw_deploy")    # docker|native

    # ── State management ──────────────────────────────────────────────────────

    def _load_state(self):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self, **kwargs):
        state = self._load_state()
        state.update(kwargs)
        Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    def _step_done(self, step):
        return self._load_state().get(step, False)

    # ── OS helpers ────────────────────────────────────────────────────────────

    def _detect_os_info(self):
        info = {}
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, _, v = line.partition("=")
                        info[k] = v.strip('"')
        except Exception:
            pass
        return info

    def get_os_codename(self):
        return self.os_info.get("VERSION_CODENAME", "noble")

    def find_service(self, *candidates):
        for name in candidates:
            result = subprocess.run(
                f"systemctl list-unit-files {name}.service 2>/dev/null | grep -q {name}",
                shell=True, capture_output=True
            )
            if result.returncode == 0:
                return name
        return candidates[0]

    def service_command(self, action, *candidates):
        service = self.find_service(*candidates)
        self.run_command(f"systemctl {action} {service}")

    # ── Logging / commands ────────────────────────────────────────────────────

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{level}] {message}"
        self.setup_log.append(entry)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(entry + "\n")
        except Exception:
            pass

        color = {
            "INFO": Colors.CYAN,
            "SUCCESS": Colors.GREEN,
            "WARNING": Colors.WARNING,
            "ERROR": Colors.FAIL,
        }.get(level, Colors.ENDC)
        symbol = {
            "INFO": "ℹ", "SUCCESS": "✓", "WARNING": "⚠", "ERROR": "✗",
        }.get(level, "•")
        print(f"{color}  {symbol}  {message}{Colors.ENDC}")

    def run_command(self, command, check=True, shell=True, capture_output=True):
        try:
            self.log(f"Executing: {command}")
            result = subprocess.run(
                command, shell=shell, check=check,
                capture_output=capture_output, text=True
            )
            return result
        except subprocess.CalledProcessError as e:
            self.log(f"Command failed: {command}", "ERROR")
            if capture_output and e.stderr:
                self.log(f"Error output: {e.stderr.strip()}", "ERROR")
            raise

    def run_as_user(self, command, check=True, capture_output=True):
        """Run a command as the agent user in a fresh login shell.
        IMPORTANT: Ubuntu's default .bashrc exits early for non-interactive
        shells, so PATH lines appended to it are NOT seen by `su - user -c`.
        We therefore export the user tool paths explicitly on every call."""
        if not self.agent_username:
            raise RuntimeError("Agent user not created yet")
        path_prefix = ('export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:'
                       '$HOME/.hermes/bin:/home/linuxbrew/.linuxbrew/bin:$PATH"; ')
        escaped = (path_prefix + command).replace("'", "'\"'\"'")
        return self.run_command(
            f"su - {self.agent_username} -c '{escaped}'",
            check=check, capture_output=capture_output
        )

    # ── Input ─────────────────────────────────────────────────────────────────

    def get_user_input(self, message, options, default_index=0):
        print(f"\n{Colors.CYAN}{message}{Colors.ENDC}")
        for i, option in enumerate(options):
            print(f"  {i+1}. {option}")

        while True:
            try:
                choice = input(
                    f"\nEnter choice (1-{len(options)}) [default: {default_index+1}]: "
                ).strip()
                if not choice:
                    return default_index
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(options):
                    return choice_idx
                print(f"{Colors.WARNING}Please enter a number between 1 and {len(options)}{Colors.ENDC}")
            except ValueError:
                print(f"{Colors.WARNING}Invalid input. Please enter a number.{Colors.ENDC}")
            except KeyboardInterrupt:
                print(f"\n{Colors.WARNING}Setup interrupted.{Colors.ENDC}")
                sys.exit(1)

    # ── Pre-flight ────────────────────────────────────────────────────────────

    def check_root(self):
        if os.geteuid() != 0:
            print(f"{Colors.FAIL}This script must be run as root (sudo).{Colors.ENDC}")
            sys.exit(1)

    def check_os(self):
        os_id = self.os_info.get("ID", "")
        version = self.os_info.get("VERSION_ID", "")
        pretty = self.os_info.get("PRETTY_NAME", "Unknown OS")
        supported = (
            (os_id == "ubuntu" and version in ("22.04", "24.04")) or
            (os_id == "debian" and version in ("12",))
        )
        if supported:
            self.log(f"Detected supported OS: {pretty}", "SUCCESS")
            return
        self.log(f"Detected: {pretty} — not officially tested", "WARNING")
        choice = self.get_user_input(
            "This installer is tested on Ubuntu 22.04 / 24.04 and Debian 12. Continue anyway?",
            ["Continue anyway", "Exit setup"],
            default_index=1
        )
        if choice == 1:
            sys.exit(0)

    def show_startup_message(self):
        print(f"""
{Colors.BLUE}{Colors.BOLD}========================================
        ClawRevOps.ai Agent VPS
========================================{Colors.ENDC}

{Colors.DIM}Coded by Ty Shane using OpenAI.{Colors.ENDC}

This setup turns a fresh VPS into an AI agent server.

It installs:
  1) A safe non-root user
  2) Desktop + Remote Desktop (RDP) — full mode
  3) Tailscale private networking
  4) MANDATORY Tailscale lockdown (SSH/RDP become private-only)
  5) Docker
  6) Claude Code or OpenAI Codex CLI
  7) Hermes Agent or OpenClaw (+ Homebrew, Chrome)
  8) Agent folders and workspace
  9) Optional SSH key hardening

Security is not optional here. Your agent server locks down to
Tailscale-only access BEFORE any agent software installs.
""")

    # ── Step: server mode + desktop / RDP (ported from ClawGlue) ─────────────

    def choose_server_mode(self):
        if self.server_mode:
            return
        print(f"\n{Colors.HEADER}=== SERVER MODE ==={Colors.ENDC}")

        # OpenClaw's Chrome extension and browser tooling want a real desktop.
        # Hermes ships its OWN headless Chromium and is driven from Telegram /
        # terminal — no desktop needed.
        needs_desktop = self.platform_choice in ("openclaw", "both")

        if needs_desktop:
            print(f"""{Colors.CYAN}
  You picked OpenClaw, which works best WITH a desktop: connect by
  Remote Desktop from your computer, use Chrome ON the server, and
  click through every tool login like a normal computer.

  {Colors.WARNING}Full mode wants 4GB+ RAM (8GB comfortable).{Colors.ENDC}{Colors.CYAN}
{Colors.ENDC}""")
            default = 0
        else:
            print(f"""{Colors.CYAN}
  You picked Hermes, which does NOT need a desktop — it installs
  its own built-in browser for automation, and you talk to it from
  Telegram or the terminal. Headless keeps your server light.

  (Full mode is still available if you want a visual desktop.)
{Colors.ENDC}""")
            default = 1

        choice = self.get_user_input(
            "Which server mode?",
            ["Full — desktop + Remote Desktop (RDP)"
             + (" - recommended for OpenClaw" if needs_desktop else ""),
             "Headless — SSH terminal only"
             + ("" if needs_desktop else " - recommended for Hermes")],
            default_index=default
        )
        self.server_mode = "full" if choice == 0 else "headless"
        self._save_state(server_mode=self.server_mode)

    def install_desktop(self):
        """Install XFCE + LightDM + xrdp (ported from ClawGlue)."""
        if self.server_mode != "full":
            return
        if self._step_done("desktop_setup"):
            self.log("Desktop setup already completed — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== DESKTOP ENVIRONMENT ==={Colors.ENDC}")

        result = self.run_command("dpkg -l xfce4-session 2>/dev/null | grep '^ii'", check=False)
        if result.returncode == 0:
            self.log("XFCE already installed", "SUCCESS")
            result = self.run_command("dpkg -l xrdp 2>/dev/null | grep '^ii'", check=False)
            if result.returncode != 0:
                self.log("Installing xrdp...")
                self.run_command("apt install -y xrdp", capture_output=False)
                self.service_command("enable", "xrdp")
        else:
            self.log("Installing XFCE + LightDM + xrdp (this takes a few minutes)...", "WARNING")
            self.run_command("DEBIAN_FRONTEND=noninteractive apt install -y "
                             "xfce4 xfce4-goodies lightdm xrdp", capture_output=False)
            Path("/etc/lightdm").mkdir(parents=True, exist_ok=True)
            with open("/etc/lightdm/lightdm.conf", "w") as f:
                f.write("[Seat:*]\nWaylandEnable=false\nuser-session=xfce\n")
            self.service_command("enable", "lightdm")
            self.service_command("enable", "xrdp")
            self.log("XFCE desktop environment installed", "SUCCESS")

        self._save_state(desktop_setup=True, desktop_type="xfce")

    def setup_user_session(self):
        """Give the agent user an XFCE session for RDP logins."""
        if self.server_mode != "full" or self._step_done("user_session_setup"):
            return
        xsession_path = f"/home/{self.agent_username}/.xsession"
        with open(xsession_path, "w") as f:
            f.write("#!/bin/bash\nexec xfce4-session\n")
        self.run_command(f"chown {self.agent_username}:{self.agent_username} {xsession_path}")
        self.run_command(f"chmod 755 {xsession_path}")
        self._save_state(user_session_setup=True)

    def configure_rdp_persistence(self):
        """xrdp session persistence + XFCE no-sleep/no-lock (ported from ClawGlue)."""
        if self.server_mode != "full":
            return
        if self._step_done("rdp_configured"):
            self.log("RDP persistence already configured — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== RDP SESSION PERSISTENCE ==={Colors.ENDC}")
        needs_xrdp_restart = False

        xrdp_ini_path = "/etc/xrdp/xrdp.ini"
        try:
            with open(xrdp_ini_path, "r") as f:
                xrdp_config = f.read()
            if "[Xorg]" in xrdp_config and "libxup.so" in xrdp_config:
                self.log("xrdp Xorg persistence module already configured", "SUCCESS")
            else:
                self.run_command(f"cp {xrdp_ini_path} {xrdp_ini_path}.backup")
                xorg_block = """
[Xorg]
name=Xorg
lib=libxup.so
username=ask
password=ask
ip=127.0.0.1
port=-1
code=20
"""
                with open(xrdp_ini_path, "a") as f:
                    f.write(xorg_block)
                needs_xrdp_restart = True
                self.log("xrdp Xorg persistence module added", "SUCCESS")
        except FileNotFoundError:
            self.log("xrdp.ini not found - xrdp may not have installed correctly", "ERROR")

        # XFCE: disable sleep, DPMS, and screen lock (idempotent system defaults)
        xfconf_dir = Path("/etc/xdg/xfce4/xfconf/xfce-perchannel-xml")
        xfconf_dir.mkdir(parents=True, exist_ok=True)
        power_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="inactivity-sleep-mode-on-ac" type="uint" value="0"/>
    <property name="blank-on-ac" type="int" value="0"/>
    <property name="dpms-on-ac-sleep" type="uint" value="0"/>
    <property name="dpms-on-ac-off" type="uint" value="0"/>
  </property>
</channel>"""
        screensaver_xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-screensaver" version="1.0">
  <property name="saver" type="empty">
    <property name="enabled" type="bool" value="false"/>
    <property name="lock-enabled" type="bool" value="false"/>
  </property>
</channel>"""
        (xfconf_dir / "xfce4-power-manager.xml").write_text(power_xml)
        (xfconf_dir / "xfce4-screensaver.xml").write_text(screensaver_xml)
        self.log("XFCE persistence configured: no sleep, no screen lock", "SUCCESS")

        self.service_command("enable", "xrdp")
        if needs_xrdp_restart:
            self.service_command("restart", "xrdp")
        self._save_state(rdp_configured=True)

    def install_chrome(self):
        """Install Google Chrome for in-desktop browser logins (ported from ClawGlue)."""
        if self.server_mode != "full":
            return
        if self._step_done("chrome_installed"):
            self.log("Chrome already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== GOOGLE CHROME ==={Colors.ENDC}")
        result = self.run_command("dpkg -l | grep google-chrome", check=False)
        if result.returncode == 0:
            self.log("Google Chrome is already installed", "SUCCESS")
            self._save_state(chrome_installed=True)
            return
        try:
            self.log("Downloading Chrome package...")
            self.run_command("wget -q -O /tmp/google-chrome-stable_current_amd64.deb "
                             "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb")
            self.run_command("apt install -y /tmp/google-chrome-stable_current_amd64.deb",
                             capture_output=False)
            self.run_command("rm -f /tmp/google-chrome-stable_current_amd64.deb")
        except subprocess.CalledProcessError:
            self.log("Fallback: Installing Chrome via repository...", "WARNING")
            self.run_command("wget -q -O /usr/share/keyrings/google-chrome.gpg "
                             "https://dl.google.com/linux/linux_signing_key.pub")
            self.run_command('echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg]'
                             ' http://dl.google.com/linux/chrome/deb/ stable main"'
                             ' > /etc/apt/sources.list.d/google-chrome.list')
            self.run_command("apt update")
            self.run_command("apt install -y google-chrome-stable", capture_output=False)
        result = self.run_command("google-chrome --version", check=False)
        if result.returncode == 0:
            self.log(f"Chrome installed: {result.stdout.strip()}", "SUCCESS")
            self._save_state(chrome_installed=True)
        else:
            self.log("Chrome install could not be verified — re-run setup to retry", "WARNING")

    # ── Step: system update ───────────────────────────────────────────────────

    def update_system(self):
        if self._step_done("system_updated"):
            self.log("System already updated — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== SYSTEM UPDATE ==={Colors.ENDC}")
        self.log("Updating package lists and upgrading system (this can take a few minutes)...")
        self.run_command("DEBIAN_FRONTEND=noninteractive apt-get update -qq")
        self.run_command("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq")
        self.run_command(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "curl wget git ca-certificates gnupg ufw"
        )
        self.log("System updated", "SUCCESS")
        self._save_state(system_updated=True)

    # ── Step: swap guard (small VPS protection) ───────────────────────────────

    def ensure_swap(self):
        if self._step_done("swap_checked"):
            return
        try:
            mem_kb = 0
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        mem_kb = int(line.split()[1])
                        break
            swap_active = self.run_command(
                "swapon --show --noheadings", check=False
            ).stdout.strip()

            if mem_kb < 2 * 1024 * 1024 and not swap_active:
                print(f"\n{Colors.HEADER}=== SWAP SETUP ==={Colors.ENDC}")
                self.log("Less than 2GB RAM detected with no swap — adding a 2GB swap file "
                         "so agent installs don't run out of memory")
                self.run_command("fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048")
                self.run_command("chmod 600 /swapfile")
                self.run_command("mkswap /swapfile")
                self.run_command("swapon /swapfile")
                with open("/etc/fstab") as f:
                    fstab = f.read()
                if "/swapfile" not in fstab:
                    with open("/etc/fstab", "a") as f:
                        f.write("/swapfile none swap sw 0 0\n")
                self.log("2GB swap file active", "SUCCESS")
        except Exception as e:
            self.log(f"Swap setup skipped: {e}", "WARNING")
        self._save_state(swap_checked=True)

    # ── Step: create the user's login (non-root) ──────────────────────────────

    def create_agent_user(self):
        if self._step_done("agent_user_created") and self.agent_username:
            self.log(f"Your login '{self.agent_username}' already created — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== CREATE YOUR LOGIN ==={Colors.ENDC}")
        print(f"""{Colors.CYAN}
  You must now create YOUR login for this server — a brand new
  username and password. This is what you will use to connect
  from your computer (SSH terminal and Remote Desktop), and it's
  where all your AI tools install.

  Never run your agents as root. Root stays for system
  administration only.

  Pick something personal and easy to remember, like your first
  name (lowercase). Example: ty
{Colors.ENDC}""")

        while True:
            username = input(f"{Colors.CYAN}Choose your new username: {Colors.ENDC}").strip()
            if not username:
                print(f"{Colors.WARNING}You must choose a username to continue.{Colors.ENDC}")
                continue

            if not re.match(r'^[a-z_][a-z0-9_-]*$', username):
                print(f"{Colors.WARNING}Username must start with a lowercase letter and may only "
                      f"contain lowercase letters, digits, underscores, and hyphens.{Colors.ENDC}")
                continue

            if username in ("root", "admin"):
                print(f"{Colors.WARNING}'{username}' is reserved — choose a personal username.{Colors.ENDC}")
                continue

            result = self.run_command(f"id {username}", check=False)
            if result.returncode == 0:
                print(f"{Colors.WARNING}'{username}' already exists on this server. "
                      f"You must create a NEW username — pick a different one.{Colors.ENDC}")
                continue
            break

        # Generate secure 16-char password (exclude ambiguous chars: 0, O, I, l, 1)
        safe_chars = (
            [c for c in string.ascii_uppercase if c not in 'OI'] +
            [c for c in string.ascii_lowercase if c not in 'l'] +
            [c for c in string.digits if c not in '01']
        )
        generated_password = ''.join(secrets.choice(safe_chars) for _ in range(16))

        def show_cred_box(uname, pwd, extra_line=None):
            box_width = max(44, len(uname) + 16, len(pwd) + 16)
            inner = box_width - 2
            sep = "═" * inner

            def bl(content):
                return f"║ {content:<{inner - 2}} ║"

            lines = [
                f"╔{sep}╗",
                bl("YOUR LOGIN — SAVE THIS"),
                bl(""),
                bl(f"  USERNAME: {uname}"),
                bl(f"  PASSWORD: {pwd}"),
                bl(""),
            ]
            if extra_line:
                lines.append(bl(extra_line))
            lines.append(f"╚{sep}╝")
            print(f"\n{Colors.GREEN}{Colors.BOLD}" + "\n".join(lines) + f"{Colors.ENDC}\n")

        show_cred_box(username, generated_password, "  Save this password before continuing!")

        pwd_choice = self.get_user_input(
            "Would you like to use this generated password or set your own?",
            ["Use generated password", "Set my own password"],
            default_index=0
        )

        password = generated_password
        if pwd_choice == 1:
            import getpass
            max_attempts = 3
            attempt = 0
            chosen = None
            while attempt < max_attempts:
                attempt += 1
                pwd1 = getpass.getpass(f"\n{Colors.CYAN}Enter your password: {Colors.ENDC}")
                if not pwd1:
                    print(f"{Colors.WARNING}Password cannot be empty.{Colors.ENDC}")
                    attempt -= 1
                    continue
                pwd2 = getpass.getpass(f"{Colors.CYAN}Confirm your password: {Colors.ENDC}")
                if pwd1 != pwd2:
                    remaining = max_attempts - attempt
                    if remaining > 0:
                        print(f"{Colors.WARNING}Passwords do not match. {remaining} attempt(s) remaining.{Colors.ENDC}")
                    continue
                chosen = pwd1
                break
            if chosen is None:
                print(f"{Colors.WARNING}Passwords did not match after {max_attempts} attempts — "
                      f"using the generated password instead.{Colors.ENDC}")
            else:
                password = chosen
                show_cred_box(username, password, "  Save this password before continuing!")

        self.run_command(f"useradd -m -s /bin/bash -G sudo {username}")

        cp_result = subprocess.run(
            ['chpasswd'], input=f"{username}:{password}",
            text=True, capture_output=True
        )
        if cp_result.returncode != 0:
            self.log(f"Failed to set password: {cp_result.stderr}", "ERROR")
            raise subprocess.CalledProcessError(cp_result.returncode, 'chpasswd')

        print(f"{Colors.WARNING}{Colors.BOLD}  Make sure you have saved your username and password!{Colors.ENDC}")
        input(f"{Colors.CYAN}  Press Enter once you have saved your credentials to continue...{Colors.ENDC}")
        print()

        self.agent_username = username
        self.log(f"Your login '{username}' is created (with sudo access)", "SUCCESS")
        self._save_state(agent_user_created=True, agent_username=username)

        self.copy_ssh_keys()

    def copy_ssh_keys(self):
        """Copy root's authorized_keys to the new user so SSH key login works.
        This MUST succeed before hardening is allowed (lockout prevention)."""
        if self._step_done("ssh_keys_copied"):
            self.ssh_keys_copied = True
            return

        print(f"\n{Colors.HEADER}=== SSH KEY SETUP ==={Colors.ENDC}")

        user = self.agent_username
        user_ssh_dir = Path(f"/home/{user}/.ssh")
        user_auth_keys = user_ssh_dir / "authorized_keys"
        root_auth_keys = Path("/root/.ssh/authorized_keys")

        # If the user already has keys (existing user path), we're done
        if user_auth_keys.exists() and user_auth_keys.stat().st_size > 0:
            self.log(f"'{user}' already has SSH keys in place", "SUCCESS")
            self.ssh_keys_copied = True
            self._save_state(ssh_keys_copied=True)
            return

        user_ssh_dir.mkdir(mode=0o700, exist_ok=True)

        if root_auth_keys.exists() and root_auth_keys.stat().st_size > 0:
            shutil.copy2(root_auth_keys, user_auth_keys)
            self.run_command(f"chown -R {user}:{user} {user_ssh_dir}")
            self.run_command(f"chmod 700 {user_ssh_dir}")
            self.run_command(f"chmod 600 {user_auth_keys}")
            self.log(f"Copied root's SSH keys to '{user}' — you can now SSH in as {user} "
                     f"with the same key you used for root", "SUCCESS")
            self.ssh_keys_copied = True
            self._save_state(ssh_keys_copied=True)
        else:
            self.run_command(f"chown -R {user}:{user} {user_ssh_dir}", check=False)
            print(f"""{Colors.WARNING}
  No SSH key found for root on this server. You probably log in
  with a password (common on Hostinger and similar providers).

  That's fine — the recommended Tailscale lockdown at the end of
  setup does NOT need a key. But if you want one anyway, you can
  paste your public key now.

  (On Windows, get it with:  type %USERPROFILE%\\.ssh\\id_ed25519.pub
   On Mac/Linux:             cat ~/.ssh/id_ed25519.pub
   No key yet? Run:          ssh-keygen -t ed25519 )
{Colors.ENDC}""")
            pasted = input(f"{Colors.CYAN}Paste your public key (starts with 'ssh-'), "
                           f"or press Enter to skip: {Colors.ENDC}").strip()
            if pasted.startswith(("ssh-", "ecdsa-")) and len(pasted.split()) >= 2:
                with open(user_auth_keys, "a") as f:
                    f.write(pasted + "\n")
                self.run_command(f"chown -R {user}:{user} {user_ssh_dir}")
                self.run_command(f"chmod 700 {user_ssh_dir}")
                self.run_command(f"chmod 600 {user_auth_keys}")
                self.log(f"Public key installed for '{user}'", "SUCCESS")
                self.ssh_keys_copied = True
                self._save_state(ssh_keys_copied=True)
            else:
                if pasted:
                    self.log("That didn't look like a public key — skipping. "
                             "You can add one later with ssh-copy-id and re-run setup.",
                             "WARNING")
                self.ssh_keys_copied = False
                self._save_state(ssh_keys_copied=False)

    # ── Step: Docker ──────────────────────────────────────────────────────────

    def install_docker(self):
        if self._step_done("docker_installed"):
            self.log("Docker already installed — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== DOCKER INSTALLATION ==={Colors.ENDC}")

        result = self.run_command("command -v docker", check=False)
        if result.returncode != 0:
            self.log("Installing Docker (official convenience script)...")
            self.run_command("curl -fsSL https://get.docker.com -o /tmp/get-docker.sh")
            self.run_command("sh /tmp/get-docker.sh", capture_output=False)
        else:
            self.log("Docker binary already present", "SUCCESS")

        self.service_command("enable --now", "docker")
        self.run_command(f"usermod -aG docker {self.agent_username}")
        self.log(f"Added '{self.agent_username}' to the docker group", "SUCCESS")
        print(f"{Colors.DIM}  Note: docker group membership grants root-equivalent access "
              f"on this machine. That's expected for an agent server you control.{Colors.ENDC}")

        # Verify in a fresh login shell so the new group membership applies
        self.log(f"Verifying Docker works for '{self.agent_username}'...")
        verify = self.run_as_user("docker run --rm hello-world", check=False)
        if verify.returncode == 0:
            self.log(f"Docker verified (hello-world ran as '{self.agent_username}')", "SUCCESS")
        else:
            self.log("Docker hello-world failed — check 'systemctl status docker' "
                     "and re-run setup", "WARNING")

        self._save_state(docker_installed=True)

    # ── Step: Tailscale ───────────────────────────────────────────────────────

    def install_tailscale(self):
        if self._step_done("tailscale_installed"):
            self.log("Tailscale already installed — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== TAILSCALE INSTALLATION ==={Colors.ENDC}")

        result = self.run_command("command -v tailscale", check=False)
        if result.returncode == 0:
            self.log("Tailscale binary already present — skipping install", "SUCCESS")
        else:
            self.log("Installing Tailscale (official install script)...")
            self.run_command("curl -fsSL https://tailscale.com/install.sh | sh", capture_output=False)

        self.service_command("enable --now", "tailscaled")
        self.log("Tailscale installed and tailscaled enabled", "SUCCESS")
        self._save_state(tailscale_installed=True)

    def show_connection_info(self, heading="YOUR PRIVATE CONNECTION INFO — SAVE THIS"):
        """Print the user's Tailscale SSH (and RDP) details. Shown after
        Tailscale connects, on phase-2 resume, and in the final report."""
        if not self.tailscale_ip:
            return
        user = self.agent_username or "YOUR_USERNAME"
        print(f"""
{Colors.GREEN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗
║   {heading:<59}║
╚══════════════════════════════════════════════════════════════╝{Colors.ENDC}

{Colors.BOLD}  Tailscale IP:{Colors.ENDC}  {Colors.GREEN}{Colors.BOLD}{self.tailscale_ip}{Colors.ENDC}

{Colors.BOLD}  SSH from YOUR computer{Colors.ENDC} (Terminal on Mac, PowerShell on Windows):

      {Colors.BOLD}ssh {user}@{self.tailscale_ip}{Colors.ENDC}

  Password: the one you saved when you created '{user}'.""")
        if self.server_mode == "full":
            print(f"""
{Colors.BOLD}  Remote Desktop from YOUR computer:{Colors.ENDC}
      Windows:  Win+R → {Colors.BOLD}mstsc{Colors.ENDC} → connect to {Colors.BOLD}{self.tailscale_ip}{Colors.ENDC}
      Mac:      'Windows App' (free, App Store) → add PC {Colors.BOLD}{self.tailscale_ip}{Colors.ENDC}
      Login:    {Colors.BOLD}{user}{Colors.ENDC} + your saved password""")
        print(f"""
{Colors.DIM}  Requires Tailscale running on your computer, signed in to the
  SAME account: https://tailscale.com/download{Colors.ENDC}
""")

    def configure_tailscale(self):
        # Already authenticated? (works even without state file)
        result = self.run_command("tailscale status", check=False)
        if result.returncode == 0:
            ip_result = self.run_command("tailscale ip -4", check=False)
            if ip_result.returncode == 0 and ip_result.stdout.strip():
                self.tailscale_ip = ip_result.stdout.strip().splitlines()[0]
                self.log(f"Tailscale already authenticated (IP: {self.tailscale_ip}) — skipping",
                         "SUCCESS")
                self._save_state(tailscale_configured=True, tailscale_ip=self.tailscale_ip)
                return True

        print(f"\n{Colors.HEADER}=== TAILSCALE CONFIGURATION ==={Colors.ENDC}")
        print(f"""
{Colors.BOLD}What is Tailscale?{Colors.ENDC}
{Colors.CYAN}  Tailscale is a private VPN that connects your devices securely
  over the internet. Once set up, you can reach this agent server
  from anywhere using its Tailscale IP — no open ports, no exposed
  firewall rules.{Colors.ENDC}

{Colors.BOLD}Before you continue:{Colors.ENDC}
{Colors.WARNING}  You need a free Tailscale account. If you don't have one yet,
  create one now at:

      https://tailscale.com

  Sign up is free and takes about 2 minutes.{Colors.ENDC}
""")

        proceed = self.get_user_input(
            "How would you like to authenticate Tailscale? (required)",
            ["Browser login (a link will appear — open it and approve)",
             "Auth key (paste a tskey-auth-... key from the admin console)",
             "I need to create an account first"],
            default_index=0
        )

        if proceed == 2:
            print(f"\n{Colors.CYAN}  Go to {Colors.BOLD}https://tailscale.com{Colors.ENDC}{Colors.CYAN} "
                  f"and create your free account.{Colors.ENDC}")
            input(f"{Colors.WARNING}  Press Enter once your account is ready...{Colors.ENDC}")
            proceed = self.get_user_input(
                "Ready to authenticate?",
                ["Browser login", "Auth key"],
                default_index=0
            )

        try:
            if proceed == 1:
                authkey = input(f"\n{Colors.CYAN}Paste your Tailscale auth key: {Colors.ENDC}").strip()
                self.run_command(f"tailscale up --auth-key={authkey}", capture_output=False)
            else:
                self.run_command("tailscale up", capture_output=False)
        except subprocess.CalledProcessError:
            self.log("Tailscale authentication may have failed", "WARNING")
            retry = self.get_user_input(
                "Tailscale authentication failed. What would you like to do?",
                ["Retry with reset", "Exit setup (re-run 'sudo agent-setup' to resume)"],
                default_index=0
            )
            if retry == 0:
                self.run_command("tailscale up --reset", capture_output=False)
            else:
                sys.exit(0)

        time.sleep(5)
        try:
            result = self.run_command("tailscale ip -4")
            self.tailscale_ip = result.stdout.strip().splitlines()[0]
            self.log(f"Tailscale IP assigned: {self.tailscale_ip}", "SUCCESS")
            self._save_state(tailscale_configured=True, tailscale_ip=self.tailscale_ip)
            self.show_connection_info()
            return True
        except Exception:
            self.log("Failed to get Tailscale IP", "ERROR")
            return False

    # ── Step: coding assistants ───────────────────────────────────────────────

    def choose_coding_assistant(self):
        if self.assistant_choice:
            return
        choice = self.get_user_input(
            "Which coding assistant would you like to install?",
            ["OpenAI Codex CLI - recommended",
             "Claude Code",
             "Both",
             "Skip"],
            default_index=0
        )
        self.assistant_choice = ["codex", "claude", "both", "skip"][choice]
        self._save_state(assistant_choice=self.assistant_choice)

    def _ensure_node_for_user(self):
        """Install Node.js (system, via NodeSource) and a per-user npm prefix
        so global npm installs land in the agent user's home, never in root."""
        if self._step_done("node_ready"):
            return
        result = self.run_command("command -v node", check=False)
        if result.returncode != 0:
            self.log("Installing Node.js 22 (NodeSource)...")
            self.run_command("curl -fsSL https://deb.nodesource.com/setup_22.x | bash -", capture_output=False)
            self.run_command("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs")
        # Per-user global prefix
        self.run_as_user("mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global")
        # Write PATH to BOTH .profile (login shells) and .bashrc (interactive).
        # .bashrc alone is not enough: Ubuntu's default .bashrc exits early
        # for non-interactive shells before reaching appended lines.
        path_line = 'export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"'
        for rc in (".profile", ".bashrc"):
            rc_path = Path(f"/home/{self.agent_username}/{rc}")
            content = rc_path.read_text() if rc_path.exists() else ""
            if path_line not in content:
                with open(rc_path, "a") as f:
                    f.write(f"\n# Added by ClawRevOps Agent VPS setup\n{path_line}\n")
                self.run_command(f"chown {self.agent_username}:{self.agent_username} {rc_path}")
        self._save_state(node_ready=True)

    def install_codex_cli(self):
        if self._step_done("codex_installed"):
            self.log("Codex CLI already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== OPENAI CODEX CLI ==={Colors.ENDC}")
        print(f"""{Colors.CYAN}
  Codex is excellent for coding workflows and can be used alongside
  your AI agent systems. Its login is separate from your agent
  provider configuration — you'll sign in after setup completes.
{Colors.ENDC}""")
        self._ensure_node_for_user()
        self.log(f"Installing Codex CLI under '{self.agent_username}'...")
        self.run_as_user(f"npm install -g {CODEX_NPM_PACKAGE}")
        verify = self.run_as_user("command -v codex", check=False)
        if verify.returncode == 0:
            self.log("Codex CLI installed", "SUCCESS")
            self._save_state(codex_installed=True)
        else:
            self.log("Codex CLI install could not be verified — re-run setup to retry", "WARNING")

    def install_claude_code(self):
        if self._step_done("claude_code_installed"):
            self.log("Claude Code already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== CLAUDE CODE ==={Colors.ENDC}")
        print(f"""{Colors.CYAN}
  Claude Code is an excellent coding assistant. It installs under
  your own user '{self.agent_username}' (never run it as root). Login is handled
  separately after setup completes.
{Colors.ENDC}""")
        self.log(f"Installing Claude Code under '{self.agent_username}'...")
        self.run_as_user(CLAUDE_CODE_INSTALL_CMD, capture_output=False)
        verify = self.run_as_user("command -v claude", check=False)
        if verify.returncode == 0:
            self.log("Claude Code installed", "SUCCESS")
            self._save_state(claude_code_installed=True)
        else:
            self.log("Claude Code install could not be verified — re-run setup to retry", "WARNING")

    def install_coding_assistants(self):
        self.choose_coding_assistant()
        if self.assistant_choice in ("codex", "both"):
            self.install_codex_cli()
        if self.assistant_choice in ("claude", "both"):
            self.install_claude_code()
        if self.assistant_choice == "skip":
            self.log("Coding assistant installation skipped", "WARNING")

    # ── Step: agent platforms ─────────────────────────────────────────────────

    def choose_agent_platform(self):
        if self.platform_choice:
            return
        choice = self.get_user_input(
            "Which agent platform would you like to install?",
            ["Hermes Agent",
             "OpenClaw",
             "Both",
             "Skip"],
            default_index=1
        )
        self.platform_choice = ["hermes", "openclaw", "both", "skip"][choice]
        self._save_state(platform_choice=self.platform_choice)

    def install_homebrew(self):
        """Pre-install Homebrew so OpenClaw skills install correctly during
        onboarding (ported from ClawGlue post_lockdown_setup)."""
        if self._step_done("homebrew_installed"):
            self.log("Homebrew already installed — skipping", "SUCCESS")
            return
        result = self.run_as_user("command -v brew", check=False)
        if result.returncode == 0:
            self.log("Homebrew already present", "SUCCESS")
            self._save_state(homebrew_installed=True)
            return
        print(f"\n{Colors.HEADER}=== HOMEBREW (for OpenClaw skills) ==={Colors.ENDC}")
        self.log("Installing build tools and Homebrew (this can take a few minutes)...")
        self.run_command(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "build-essential procps file git"
        )
        self.run_command(
            "curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh "
            "-o /tmp/brew_install.sh"
        )
        self.run_as_user("NONINTERACTIVE=1 bash /tmp/brew_install.sh", check=False,
                         capture_output=False)
        # Make brew available in the user's shells
        brew_line = 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"'
        for rc in (".profile", ".bashrc"):
            rc_path = Path(f"/home/{self.agent_username}/{rc}")
            content = rc_path.read_text() if rc_path.exists() else ""
            if brew_line not in content and Path("/home/linuxbrew/.linuxbrew/bin/brew").exists():
                with open(rc_path, "a") as f:
                    f.write(f"\n{brew_line}\n")
                self.run_command(f"chown {self.agent_username}:{self.agent_username} {rc_path}")
        verify = self.run_as_user("command -v brew", check=False)
        if verify.returncode == 0:
            self.log("Homebrew installed", "SUCCESS")
            self._save_state(homebrew_installed=True)
        else:
            self.log("Homebrew install could not be verified — OpenClaw onboarding "
                     "may still work; some skills might need brew later", "WARNING")

    def install_openclaw(self):
        if self._step_done("openclaw_installed"):
            self.log("OpenClaw already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== OPENCLAW ==={Colors.ENDC}")
        print(f"""{Colors.CYAN}
  OpenClaw is an agent orchestration platform. It pairs well with
  Docker and Tailscale. After installation, you'll run its
  onboarding ('openclaw onboard') to connect your provider.
{Colors.ENDC}""")
        self.install_homebrew()
        self.log(f"Installing OpenClaw under '{self.agent_username}' (onboarding deferred)...")
        self.run_as_user(OPENCLAW_INSTALL_CMD, capture_output=False)
        verify = self.run_as_user("command -v openclaw", check=False)
        if verify.returncode == 0:
            self.log("OpenClaw installed", "SUCCESS")
            self._save_state(openclaw_installed=True)
        else:
            self.log("OpenClaw install could not be verified — re-run setup to retry", "WARNING")

    def install_hermes(self):
        if self._step_done("hermes_installed"):
            self.log("Hermes Agent already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== HERMES AGENT ==={Colors.ENDC}")
        print(f"""{Colors.CYAN}
  Hermes is an open-source agent platform (Nous Research) with
  memory, skills, and workflows. After setup completes you'll run
  'hermes setup' to connect your provider and channels.
{Colors.ENDC}""")
        self.log(f"Installing Hermes Agent under '{self.agent_username}' (setup deferred)...")
        self.run_as_user(HERMES_INSTALL_CMD, capture_output=False)
        verify = self.run_as_user("command -v hermes", check=False)
        if verify.returncode == 0:
            self.log("Hermes Agent installed", "SUCCESS")
            self._save_state(hermes_installed=True)
        else:
            self.log("Hermes install could not be verified — re-run setup to retry", "WARNING")

    def choose_deploy_methods(self):
        """Per-platform: Docker container vs native install."""
        if self.platform_choice in ("hermes", "both") and not self.hermes_deploy:
            print(f"""{Colors.CYAN}
  Hermes can run as a Docker container (official image, shows up
  in your VPS Docker Manager, auto-restarts, one-click updates)
  or installed natively on the server.
{Colors.ENDC}""")
            choice = self.get_user_input(
                "How should Hermes run?",
                ["Docker container - recommended",
                 "Native install (directly on the server)"],
                default_index=0
            )
            self.hermes_deploy = "docker" if choice == 0 else "native"
            self._save_state(hermes_deploy=self.hermes_deploy)

        if self.platform_choice in ("openclaw", "both") and not self.openclaw_deploy:
            print(f"""{Colors.CYAN}
  OpenClaw can install natively (full skill support + Homebrew +
  desktop Chrome — the proven ClawGlue way) or as a Docker
  container (isolated, visible in your VPS Docker Manager, BUT the
  official OpenClaw image has no Homebrew, so brew-based skills
  won't be available inside the container).
{Colors.ENDC}""")
            choice = self.get_user_input(
                "How should OpenClaw run?",
                ["Native install - recommended (full skills, ClawGlue-proven)",
                 "Docker container (some skills unavailable)"],
                default_index=0
            )
            self.openclaw_deploy = "native" if choice == 0 else "docker"
            self._save_state(openclaw_deploy=self.openclaw_deploy)

    def _docker_bind_ip(self):
        """IP to bind published container ports to. CRITICAL: Docker's
        published ports bypass UFW (iptables DOCKER chain), so publishing
        on 0.0.0.0 would expose agent gateways to the public internet
        despite the lockdown. Binding to the Tailscale IP keeps them
        private-network-only."""
        return self.tailscale_ip or "127.0.0.1"

    def _write_compose(self, platform, content):
        """Write a docker-compose.yml under the agent workspace and chown it."""
        user = self.agent_username
        stack_dir = Path(f"/home/{user}/clawrevops/agents/{platform}")
        stack_dir.mkdir(parents=True, exist_ok=True)
        (stack_dir / "data").mkdir(exist_ok=True)
        (stack_dir / "docker-compose.yml").write_text(content)
        self.run_command(f"chown -R {user}:{user} /home/{user}/clawrevops")
        return stack_dir

    def install_hermes_docker(self):
        if self._step_done("hermes_installed"):
            self.log("Hermes already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== HERMES AGENT (Docker) ==={Colors.ENDC}")
        bind_ip = self._docker_bind_ip()
        compose = f"""# ClawRevOps — Hermes Agent (official Nous Research image)
# Manage:  docker logs -f clawrevops-hermes
# Update:  docker compose pull && docker compose up -d
services:
  hermes:
    image: nousresearch/hermes-agent:latest
    container_name: clawrevops-hermes
    restart: unless-stopped
    command: gateway run
    ports:
      # Bound to your Tailscale IP — NOT public. Docker bypasses UFW,
      # so binding to {bind_ip} is what keeps this private.
      - "{bind_ip}:8642:8642"
    volumes:
      - ./data:/opt/data
"""
        stack_dir = self._write_compose("hermes", compose)
        self.log("Pulling Hermes image and starting container (this can take a few minutes)...")
        self.run_as_user(f"cd {stack_dir} && docker compose pull && docker compose up -d",
                         capture_output=False)
        verify = self.run_command(
            "docker ps --filter name=clawrevops-hermes --filter status=running -q",
            check=False
        )
        if verify.returncode == 0 and verify.stdout.strip():
            self.log("Hermes container running (visible in your VPS Docker Manager)", "SUCCESS")
            self._save_state(hermes_installed=True)
        else:
            self.log("Hermes container did not start — check "
                     f"'docker logs clawrevops-hermes' and re-run setup", "WARNING")

    def install_openclaw_docker(self):
        if self._step_done("openclaw_installed"):
            self.log("OpenClaw already installed — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== OPENCLAW (Docker) ==={Colors.ENDC}")
        bind_ip = self._docker_bind_ip()
        compose = f"""# ClawRevOps — OpenClaw gateway (official pre-built image)
# Onboard: docker exec -it clawrevops-openclaw node dist/index.js onboard
# Manage:  docker logs -f clawrevops-openclaw
# Update:  docker compose pull && docker compose up -d
services:
  openclaw:
    image: ghcr.io/openclaw/openclaw:latest
    container_name: clawrevops-openclaw
    restart: unless-stopped
    ports:
      # Bound to your Tailscale IP — NOT public. Docker bypasses UFW,
      # so binding to {bind_ip} is what keeps this private.
      - "{bind_ip}:18789:18789"
    volumes:
      - ./data:/home/node/.openclaw
"""
        stack_dir = self._write_compose("openclaw", compose)
        self.log("Pulling OpenClaw image and starting container (this can take a few minutes)...")
        self.run_as_user(f"cd {stack_dir} && docker compose pull && docker compose up -d",
                         capture_output=False)
        verify = self.run_command(
            "docker ps --filter name=clawrevops-openclaw --filter status=running -q",
            check=False
        )
        if verify.returncode == 0 and verify.stdout.strip():
            self.log("OpenClaw container running (visible in your VPS Docker Manager)", "SUCCESS")
            self._save_state(openclaw_installed=True)
        else:
            self.log("OpenClaw container did not start — check "
                     f"'docker logs clawrevops-openclaw' and re-run setup", "WARNING")

    def install_agent_platforms(self):
        self.choose_agent_platform()
        self.choose_deploy_methods()
        if self.platform_choice in ("hermes", "both"):
            if self.hermes_deploy == "docker":
                self.install_hermes_docker()
            else:
                self.install_hermes()
        if self.platform_choice in ("openclaw", "both"):
            if self.openclaw_deploy == "docker":
                self.install_openclaw_docker()
            else:
                self.install_openclaw()
        if self.platform_choice == "skip":
            self.log("Agent platform installation skipped", "WARNING")

    # ── Step: workspace ───────────────────────────────────────────────────────

    def create_workspace(self):
        if self._step_done("workspace_created"):
            self.log("Workspace already created — skipping", "SUCCESS")
            return
        print(f"\n{Colors.HEADER}=== AGENT WORKSPACE ==={Colors.ENDC}")
        user = self.agent_username
        for folder in ("agents", "projects", "memory", "logs", "backups"):
            self.run_command(
                f"install -d -o {user} -g {user} /home/{user}/clawrevops/{folder}"
            )
        self.log(f"Workspace created at /home/{user}/clawrevops/", "SUCCESS")
        print(f"""{Colors.DIM}
  ~/clawrevops/
  ├── agents/      your agent configs and skills
  ├── projects/    active client and build work
  ├── memory/      persistent agent memory
  ├── logs/        run logs
  └── backups/     snapshots and exports
{Colors.ENDC}""")
        self._save_state(workspace_created=True)

    # ── Step: verification ────────────────────────────────────────────────────

    def verify_installation(self):
        print(f"\n{Colors.HEADER}=== VERIFYING INSTALLATION ==={Colors.ENDC}")
        checks = []

        def check(label, command, as_user=False):
            if as_user:
                result = self.run_as_user(command, check=False)
            else:
                result = self.run_command(command, check=False)
            ok = result.returncode == 0
            checks.append((label, ok))
            symbol = f"{Colors.GREEN}✓{Colors.ENDC}" if ok else f"{Colors.FAIL}✗{Colors.ENDC}"
            print(f"  {symbol}  {label}")
            return ok

        check(f"Your login '{self.agent_username}' exists", f"id {self.agent_username}")
        check("Your login has sudo", f"groups {self.agent_username} | grep -q sudo")
        if self.ssh_keys_copied:
            check("SSH key in place for your login",
                  f"test -s /home/{self.agent_username}/.ssh/authorized_keys")
        if self._step_done("docker_installed"):
            check("Docker service running", "systemctl is-active --quiet docker")
            check("Docker usable by your login", "docker ps", as_user=True)
        if self._step_done("tailscale_configured"):
            check("Tailscale connected", "tailscale ip -4")
        if self._step_done("codex_installed"):
            check("Codex CLI on your PATH",
                  "command -v codex || test -x ~/.npm-global/bin/codex", as_user=True)
        if self._step_done("claude_code_installed"):
            check("Claude Code on your PATH",
                  "command -v claude || test -x ~/.local/bin/claude", as_user=True)
        if self._step_done("hermes_installed"):
            if self.hermes_deploy == "docker":
                check("Hermes container running",
                      "docker ps --filter name=clawrevops-hermes --filter status=running -q | grep -q .")
            else:
                check("Hermes Agent on your PATH", "command -v hermes", as_user=True)
        if self._step_done("openclaw_installed"):
            if self.openclaw_deploy == "docker":
                check("OpenClaw container running",
                      "docker ps --filter name=clawrevops-openclaw --filter status=running -q | grep -q .")
            else:
                check("OpenClaw on your PATH", "command -v openclaw", as_user=True)
        if self._step_done("desktop_setup"):
            check("xrdp (Remote Desktop) service enabled",
                  "systemctl is-enabled --quiet xrdp")
        if self._step_done("chrome_installed"):
            check("Google Chrome installed", "command -v google-chrome")
        check("Workspace folders exist",
              f"test -d /home/{self.agent_username}/clawrevops/agents")

        failed = [label for label, ok in checks if not ok]
        if failed:
            self.log(f"{len(failed)} check(s) failed — re-run 'sudo agent-setup' to retry, "
                     f"or check {LOG_FILE}", "WARNING")
        else:
            self.log("All checks passed", "SUCCESS")
        return not failed

    # ── Step: final report (logins, onboarding, community) ───────────────────

    def create_final_report(self):
        user = self.agent_username
        ts_ip = self.tailscale_ip or "YOUR_TAILSCALE_IP"

        print(f"""
{Colors.BLUE}{Colors.BOLD}========================================
ClawRevOps.ai setup is complete.
Coded by Ty Shane using OpenAI.
========================================{Colors.ENDC}

{Colors.BOLD}Your server:{Colors.ENDC}
  Your username:  {user}
  Tailscale IP:   {ts_ip}
  Workspace:      /home/{user}/clawrevops/
""")
        self.show_connection_info(heading="CONNECT FROM YOUR COMPUTER — SAVE THIS")
        if self.server_mode == "full":
            print(f"""{Colors.DIM}  Inside the Remote Desktop you have Chrome — do every tool login
  there, point and click, no terminal tricks needed.{Colors.ENDC}
""")

        print(f"{Colors.BOLD}Then start your tools (each one walks you through its own login):{Colors.ENDC}\n")

        # Driven by CHOICES, not just verified flags — the user always gets
        # their launch commands even if a verification check was flaky.
        plain_lines = []
        if self.assistant_choice in ("codex", "both"):
            if self.server_mode == "full":
                print(f"""{Colors.CYAN}  Codex CLI:
    In your Remote Desktop session, open a terminal and run:
      codex
    First run opens login — finish it in Chrome right there.{Colors.ENDC}
""")
            else:
                print(f"""{Colors.CYAN}  Codex CLI:
      codex
    First run opens login. On a headless VPS the browser login needs
    port forwarding — connect from your computer with:
      ssh -L 1455:localhost:1455 {user}@{ts_ip}
    then run 'codex' and open the link in your local browser.
    (Alternative: sign in with an API key — see Codex docs){Colors.ENDC}
""")
            plain_lines.append(f"Codex:    run 'codex' as {user}")
        if self.assistant_choice in ("claude", "both"):
            print(f"""{Colors.CYAN}  Claude Code:
      claude
    First run prints a login link — open it in any browser.{Colors.ENDC}
""")
            plain_lines.append(f"Claude Code:  run 'claude' as {user}")
        if self.platform_choice in ("hermes", "both"):
            if self.hermes_deploy == "docker":
                print(f"""{Colors.CYAN}  Hermes Agent (Docker container 'clawrevops-hermes'):
      docker exec -it clawrevops-hermes hermes setup
    Walks you through provider, channels (Telegram etc.), and tools.
    Once a channel is connected, you DM your agent from your phone.
    Logs:    docker logs -f clawrevops-hermes
    Manage:  your VPS panel's Docker Manager, or
             cd ~/clawrevops/agents/hermes && docker compose ...{Colors.ENDC}
""")
                plain_lines.append("Hermes:   docker exec -it clawrevops-hermes hermes setup")
            else:
                print(f"""{Colors.CYAN}  Hermes Agent:
      hermes setup
    Walks you through provider, channels (Telegram etc.), and tools.
    Then start it with:  hermes
    Once a channel is connected, you DM your agent from your phone.
    (Hermes has its own built-in browser for web automation.){Colors.ENDC}
""")
                plain_lines.append(f"Hermes:   run 'hermes setup' then 'hermes' as {user}")
        if self.platform_choice in ("openclaw", "both"):
            if self.openclaw_deploy == "docker":
                print(f"""{Colors.CYAN}  OpenClaw (Docker container 'clawrevops-openclaw'):
      docker exec -it clawrevops-openclaw node dist/index.js onboard
    Connects your provider and preferences.
    Logs:    docker logs -f clawrevops-openclaw
    Manage:  your VPS panel's Docker Manager, or
             cd ~/clawrevops/agents/openclaw && docker compose ...{Colors.ENDC}
""")
                plain_lines.append("OpenClaw: docker exec -it clawrevops-openclaw node dist/index.js onboard")
            else:
                print(f"""{Colors.CYAN}  OpenClaw:
      openclaw onboard
    Connects your provider and preferences.{Colors.ENDC}
""")
                plain_lines.append(f"OpenClaw: run 'openclaw onboard' as {user}")

        print(f"""{Colors.BOLD}Have you joined the AI community where we teach setups like this?{Colors.ENDC}

    {Colors.GREEN}{Colors.BOLD}{COMMUNITY_URL}{Colors.ENDC}
""")

        # Save a plain-text copy in the agent user's home
        try:
            report_path = f"/home/{user}/clawrevops/SETUP_REPORT.txt"
            with open(report_path, "w") as f:
                f.write("ClawRevOps.ai Agent VPS — Setup Report\n")
                f.write("Coded by Ty Shane using OpenAI.\n\n")
                f.write(f"Your username: {user}\n")
                f.write(f"Tailscale IP: {ts_ip}\n")
                f.write(f"SSH:          ssh {user}@{ts_ip}\n")
                if self.server_mode == "full":
                    f.write(f"Remote Desktop: connect to {ts_ip} (Windows: mstsc, "
                            f"Mac: Windows App) as {user}\n")
                f.write(f"Workspace:    /home/{user}/clawrevops/\n\n")
                f.write("Launch commands:\n")
                for line in plain_lines:
                    f.write(f"  {line}\n")
                f.write(f"\nCommunity: {COMMUNITY_URL}\n")
                f.write(f"Setup log: {LOG_FILE}\n")
                f.write("Re-run setup any time: sudo agent-setup\n")
            self.run_command(f"chown {user}:{user} {report_path}")
            self.log(f"Report saved to {report_path}", "SUCCESS")
        except Exception:
            pass

    # ── Step: security (always LAST) ──────────────────────────────────────────

    def lockdown_server(self):
        """Tailscale-only lockdown — ported from the proven ClawGlue pattern.
        UFW denies all incoming, SSH is reachable ONLY over Tailscale.
        Password login stays enabled but becomes unreachable from the
        public internet, so no SSH key is required."""
        if self._step_done("server_locked_down"):
            self.log("Server already locked down — skipping", "SUCCESS")
            return True

        print(f"\n{Colors.HEADER}=== TAILSCALE LOCKDOWN ==={Colors.ENDC}")

        if not self.tailscale_ip:
            self.log("Tailscale is not configured — lockdown unavailable. "
                     "Re-run setup and complete Tailscale first.", "WARNING")
            return False

        user = self.agent_username
        print(f"""
{Colors.FAIL}{Colors.BOLD}WARNING: This will lock down the server!{Colors.ENDC}
{Colors.WARNING}After this step, you will only be able to connect via Tailscale.{Colors.ENDC}

{Colors.BOLD}Before you confirm:{Colors.ENDC}
{Colors.CYAN}  1. Install Tailscale on YOUR computer: https://tailscale.com/download
     Sign in with the SAME account you used to authorise this server.
  2. Open a NEW terminal on your computer (keep this one open!)
  3. Test:  ssh {user}@{self.tailscale_ip}
  4. Once logged in, run:  sudo whoami   (should print: root)
{Colors.ENDC}
{Colors.WARNING}  Do NOT continue until that test works.{Colors.ENDC}
{Colors.DIM}  Recovery if anything goes wrong: your provider's web console
  (e.g. Hostinger's Browser Terminal) always works.{Colors.ENDC}
""")

        while True:
            test_result = self.get_user_input(
                f"Can you SSH to {self.tailscale_ip} from a second terminal?",
                ["Yes, the Tailscale connection works",
                 "No, having issues"],
                default_index=0
            )
            if test_result == 0:
                break
            print(f"""{Colors.CYAN}
  Troubleshooting:
    • Is Tailscale running on your computer? (check the tray icon)
    • Same Tailscale account on both devices?
    • Try:  tailscale ping {self.tailscale_ip}   from your computer
    • On this server:  tailscale status
{Colors.ENDC}""")

        confirmation = input(f"\n{Colors.WARNING}Type 'LOCKDOWN' to confirm: {Colors.ENDC}")
        if confirmation != 'LOCKDOWN':
            self.log("Lockdown not confirmed. Lockdown is REQUIRED before agent "
                     "software installs — re-run 'sudo agent-setup' when ready.", "WARNING")
            return False

        self.log("Beginning server lockdown...")

        # Explicitly enable IPv6 filtering before resetting rules
        self.run_command("sed -i 's/^IPV6=no/IPV6=yes/' /etc/default/ufw", check=False)
        result = self.run_command("grep -c '^IPV6=' /etc/default/ufw", check=False)
        if result.stdout.strip() == "0":
            self.run_command("echo 'IPV6=yes' >> /etc/default/ufw")

        self.run_command("ufw --force reset")
        self.run_command("ufw default deny incoming")
        self.run_command("ufw default allow outgoing")
        self.run_command("ufw allow in on tailscale0")
        self.run_command("ufw allow out on tailscale0")

        # Allow SSH (and RDP in full mode) from Tailscale IPv4 CGNAT and IPv6 ranges
        lockdown_ports = ["22"] + (["3389"] if self.server_mode == "full" else [])
        for subnet in ("100.64.0.0/10", "fd7a:115c:a1e0::/48"):
            for port in lockdown_ports:
                self.run_command(f"ufw allow from {subnet} to any port {port}")
        self.run_command("ufw --force enable")

        # Bind sshd to the Tailscale IP only
        with open("/etc/ssh/sshd_config", "r") as f:
            sshd_config = f.read()
        if f"ListenAddress {self.tailscale_ip}" not in sshd_config:
            with open("/etc/ssh/sshd_config", "a") as f:
                f.write(f"\n# Tailscale only configuration\nListenAddress {self.tailscale_ip}\n")

        # Reboot safety: if sshd starts before tailscale0 has its IP, the
        # ListenAddress bind would fail and lock the user out after a reboot.
        # 1) allow binding to a not-yet-present address
        with open("/etc/sysctl.d/99-clawrevops-nonlocal-bind.conf", "w") as f:
            f.write("# ClawRevOps: let sshd bind the Tailscale IP before it exists at boot\n"
                    "net.ipv4.ip_nonlocal_bind=1\n"
                    "net.ipv6.ip_nonlocal_bind=1\n")
        self.run_command("sysctl --system", check=False)
        # 2) start ssh after tailscaled so the interface is usually up first
        dropin_dir = Path("/etc/systemd/system/ssh.service.d")
        dropin_dir.mkdir(parents=True, exist_ok=True)
        with open(dropin_dir / "10-clawrevops-after-tailscale.conf", "w") as f:
            f.write("[Unit]\nAfter=tailscaled.service network-online.target\n"
                    "Wants=tailscaled.service\n")
        self.run_command("systemctl daemon-reload", check=False)

        self.service_command("restart", "ssh", "sshd")

        self.log("Server lockdown completed — SSH is now Tailscale-only", "SUCCESS")
        self._save_state(server_locked_down=True)

        print(f"""{Colors.GREEN}{Colors.BOLD}
  Phase 1 complete! Your server is locked down.
{Colors.ENDC}{Colors.WARNING}  ⚠  Your connection may disconnect — this is normal.{Colors.ENDC}

{Colors.BOLD}  What to do next:{Colors.ENDC}
{Colors.WARNING}  • If you stay connected: run  sudo agent-setup  right here in this window.
  • If you get disconnected: reconnect with

        ssh {user}@{self.tailscale_ip}

    then run:  sudo agent-setup
    (this installs Docker, your coding assistants, and agent platforms){Colors.ENDC}
""")
        for i in range(10, 0, -1):
            print(f"{Colors.WARNING}  Continuing in {i}...{Colors.ENDC}")
            time.sleep(1)
        return True

    def offer_ssh_hardening(self):
        if self._step_done("ssh_hardened"):
            self.log("SSH already hardened — skipping", "SUCCESS")
            return

        print(f"\n{Colors.HEADER}=== OPTIONAL: SSH HARDENING ==={Colors.ENDC}")

        # Hard gate: never disable password auth if the agent user has no key
        user = self.agent_username
        auth_keys = Path(f"/home/{user}/.ssh/authorized_keys")
        if not (auth_keys.exists() and auth_keys.stat().st_size > 0):
            print(f"""{Colors.WARNING}
  SSH key hardening is LOCKED because '{user}' has no SSH key yet.
  Hardening disables password login — without a key you would be
  locked out of your own server.

  No key? No problem — the Tailscale lockdown option secures the
  server without one (recommended).

  To unlock key hardening: from YOUR computer run

      ssh-copy-id {user}@{self.tailscale_ip or 'YOUR_SERVER_IP'}

  Then re-run:  sudo agent-setup
{Colors.ENDC}""")
            return

        print(f"""{Colors.CYAN}
  Hardening will:
    • Disable root SSH login          (PermitRootLogin no)
    • Disable password authentication (PasswordAuthentication no)

  After this, you log in ONLY as '{user}' with your SSH key.
{Colors.ENDC}
{Colors.BOLD}Before you confirm, verify from a SECOND terminal on your computer:{Colors.ENDC}

    1. Open a NEW terminal window (keep this one open!)
    2. Run:  ssh {user}@{self.tailscale_ip or 'YOUR_SERVER_IP'}
    3. Once logged in, run:  sudo whoami
       (it should print: root)

{Colors.WARNING}  Do NOT continue until both commands work.{Colors.ENDC}
""")

        choice = self.get_user_input(
            "Did 'ssh' and 'sudo whoami' both succeed in a second terminal?",
            ["Yes — harden SSH now", "Not yet — skip hardening (you can re-run later)"],
            default_index=1
        )
        if choice == 1:
            self.log("SSH hardening skipped — re-run 'sudo agent-setup' any time", "WARNING")
            return

        confirmation = input(f"\n{Colors.WARNING}Type 'HARDEN' to confirm SSH hardening: {Colors.ENDC}")
        if confirmation != 'HARDEN':
            self.log("SSH hardening cancelled", "WARNING")
            return

        # Write a drop-in with a LOW number so it wins over cloud-init drop-ins
        # (e.g. 50-cloud-init.conf often sets PasswordAuthentication yes).
        # For these keywords, sshd uses the FIRST value it reads.
        dropin = (
            "# ClawRevOps Agent VPS hardening\n"
            "# First-match wins: this file sorts before cloud-init drop-ins.\n"
            "PermitRootLogin no\n"
            "PasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\n"
        )
        Path(SSH_HARDENING_DROPIN).parent.mkdir(parents=True, exist_ok=True)
        with open(SSH_HARDENING_DROPIN, "w") as f:
            f.write(dropin)

        # Ensure the main config actually includes the drop-in directory
        with open("/etc/ssh/sshd_config") as f:
            main_cfg = f.read()
        if "sshd_config.d" not in main_cfg:
            with open("/etc/ssh/sshd_config", "w") as f:
                f.write("Include /etc/ssh/sshd_config.d/*.conf\n" + main_cfg)

        # Validate before restarting — never restart sshd with a broken config
        result = self.run_command("sshd -t", check=False)
        if result.returncode != 0:
            os.remove(SSH_HARDENING_DROPIN)
            self.log("sshd config validation failed — hardening rolled back, "
                     "SSH unchanged. Check the log and re-run.", "ERROR")
            return

        self.service_command("restart", "ssh", "sshd")
        self.log("SSH hardened: root login and password auth disabled", "SUCCESS")
        self._save_state(ssh_hardened=True)

        print(f"""{Colors.GREEN}{Colors.BOLD}
  Done. From now on, connect with:

      ssh {user}@{self.tailscale_ip or 'YOUR_SERVER_IP'}
{Colors.ENDC}""")

    # ── Orchestration ─────────────────────────────────────────────────────────

    def run_setup(self):
        try:
            self.show_startup_message()

            # ── Phase 2: already locked down → install the agent stack ────────
            if self._step_done("server_locked_down"):
                print(f"{Colors.GREEN}Phase 1 (security) already complete — "
                      f"continuing with agent stack installation.{Colors.ENDC}")
                self.show_connection_info()
                self.check_root()
                self.install_docker()
                self.install_coding_assistants()
                self.install_agent_platforms()
                self.install_chrome()
                self.create_workspace()
                self.verify_installation()
                self.create_final_report()
                self.offer_ssh_hardening()
                print(f"\n{Colors.GREEN}{Colors.BOLD}  Setup finished. "
                      f"Re-run any time with: sudo agent-setup{Colors.ENDC}\n")
                return

            # ── Phase 1: system, user, Tailscale, MANDATORY lockdown ─────────
            response = self.get_user_input(
                "Ready to begin Agent VPS setup?",
                ["Start setup", "Exit"],
                default_index=0
            )
            if response == 1:
                self.log("Setup cancelled by user", "WARNING")
                sys.exit(0)

            self.check_root()
            self.check_os()
            self.update_system()
            self.ensure_swap()
            self.choose_coding_assistant()
            self.choose_agent_platform()
            self.choose_server_mode()
            self.install_desktop()
            self.create_agent_user()
            self.setup_user_session()
            self.configure_rdp_persistence()
            self.install_tailscale()

            if not self.configure_tailscale():
                print(f"{Colors.FAIL}Tailscale is REQUIRED for this setup — it is how "
                      f"you securely reach your agent server.{Colors.ENDC}")
                print(f"{Colors.WARNING}Re-run 'sudo agent-setup' to try again. "
                      f"Progress is saved.{Colors.ENDC}")
                sys.exit(1)

            if not self.lockdown_server():
                print(f"{Colors.FAIL}Setup paused: lockdown is REQUIRED before agent "
                      f"software installs.{Colors.ENDC}")
                print(f"{Colors.WARNING}Re-run 'sudo agent-setup' when ready. "
                      f"Progress is saved.{Colors.ENDC}")
                sys.exit(1)

            # Lockdown succeeded. If the SSH connection survived, continue
            # straight into phase 2 in this same session.
            print(f"\n{Colors.GREEN}Connection survived lockdown — continuing with "
                  f"agent stack installation...{Colors.ENDC}")
            self.install_docker()
            self.install_coding_assistants()
            self.install_agent_platforms()
            self.install_chrome()
            self.create_workspace()
            self.verify_installation()
            self.create_final_report()
            self.offer_ssh_hardening()

            print(f"\n{Colors.GREEN}{Colors.BOLD}  Setup finished. "
                  f"Re-run any time with: sudo agent-setup{Colors.ENDC}\n")

        except KeyboardInterrupt:
            print(f"\n{Colors.WARNING}Setup interrupted — progress is saved. "
                  f"Re-run 'sudo agent-setup' to resume.{Colors.ENDC}")
            sys.exit(1)
        except Exception as e:
            self.log(f"Setup failed: {str(e)}", "ERROR")
            print(f"{Colors.WARNING}Progress is saved — re-run 'sudo agent-setup' to resume. "
                  f"Full log: {LOG_FILE}{Colors.ENDC}")
            sys.exit(1)


def main():
    setup = AgentVPSSetup()
    setup.run_setup()


if __name__ == "__main__":
    main()
