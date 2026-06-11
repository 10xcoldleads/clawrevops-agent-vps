#!/bin/bash
# ClawRevOps.ai Agent VPS — Setup Installer
# Turns a fresh VPS into an AI agent server.
# https://clawrevops.ai

set -e

# Branch is passed as $1 (e.g. "dev"). Defaults to "main".
BRANCH="${1:-main}"
if [[ "$BRANCH" != "main" && "$BRANCH" != "dev" ]]; then
    BRANCH="main"
fi

# IMPORTANT: update this to the real repo before publishing
REPO="10xcoldleads/clawrevops-agent-vps"
REPO_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

# ── Colors ────────────────────────────────────────────────────────────────────
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
WHITE=$'\033[1;37m'

# ── Helpers ───────────────────────────────────────────────────────────────────
print_banner() {
    clear
    echo
    echo -e "${BLUE}${BOLD}  ╔══════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BLUE}${BOLD}  ║                                                              ║${RESET}"
    echo -e "${BLUE}${BOLD}  ║      🦞  ClawRevOps.ai Agent VPS                              ║${RESET}"
    echo -e "${BLUE}${BOLD}  ║      AI Agent Server Setup                                   ║${RESET}"
    echo -e "${BLUE}${BOLD}  ║                                       By: Ty Shane          ║${RESET}"
    echo -e "${BLUE}${BOLD}  ║      https://clawrevops.ai                                   ║${RESET}"
    echo -e "${BLUE}${BOLD}  ║                                                              ║${RESET}"
    echo -e "${BLUE}${BOLD}  ╚══════════════════════════════════════════════════════════════╝${RESET}"
    echo
    echo -e "  ${DIM}Coded by Ty Shane using OpenAI.${RESET}"
    echo
    echo -e "  ${WHITE}This setup turns a fresh VPS into an AI agent server.${RESET}"
    echo
    echo -e "  ${WHITE}It can install:${RESET}"
    echo -e "  ${CYAN}  1)${RESET} A safe non-root user"
    echo -e "  ${CYAN}  2)${RESET} Docker"
    echo -e "  ${CYAN}  3)${RESET} Tailscale private networking"
    echo -e "  ${CYAN}  4)${RESET} Claude Code or OpenAI Codex CLI"
    echo -e "  ${CYAN}  5)${RESET} Hermes Agent or OpenClaw"
    echo -e "  ${CYAN}  6)${RESET} Agent folders and workspace"
    echo -e "  ${CYAN}  7)${RESET} Optional SSH hardening"
    echo
    echo -e "  ${YELLOW}${BOLD}  ⚠  WARNING${RESET}"
    echo -e "  ${YELLOW}  This script modifies users, system packages and (optionally) SSH${RESET}"
    echo -e "  ${YELLOW}  configuration. Incorrect use on a system you depend on could lock${RESET}"
    echo -e "  ${YELLOW}  you out. By continuing, you accept all responsibility for any${RESET}"
    echo -e "  ${YELLOW}  data loss or damages. Run this on a fresh VPS only.${RESET}"
    echo
}

print_divider() {
    echo -e "${DIM}  ──────────────────────────────────────────────────────────────${RESET}"
}

print_step() {
    printf "  ${CYAN}${BOLD}[%s/%s]${RESET}  %s" "$1" "$2" "$3"
}

print_ok()    { echo -e "  ${GREEN}${BOLD}✓ Done${RESET}"; }
print_info()  { echo -e "  ${WHITE}ℹ${RESET}  $1"; }
print_error() { echo -e "  ${RED}${BOLD}✗ Error:${RESET} $1"; }

# ── Checks ────────────────────────────────────────────────────────────────────
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root."
        echo
        echo -e "  Please run:  ${YELLOW}sudo bash install.sh${RESET}"
        echo
        exit 1
    fi
}

check_ubuntu() {
    if ! command -v apt &> /dev/null; then
        print_error "This installer only supports Ubuntu/Debian systems."
        exit 1
    fi
}

# ── Steps ─────────────────────────────────────────────────────────────────────
install_python() {
    print_step 2 4 "Installing Python and dependencies...    "
    if ! command -v python3 &> /dev/null; then
        apt-get update -qq
        apt-get install -y -qq python3 > /dev/null 2>&1
    fi
    if ! command -v curl &> /dev/null; then
        apt-get install -y -qq curl > /dev/null 2>&1
    fi
    print_ok
}

install_scripts() {
    print_step 3 4 "Installing setup scripts...              "

    # Prefer local copy (repo checkout), fall back to GitHub download
    if [[ -f "ubuntu/clawrevops_setup.py" ]]; then
        cp "ubuntu/clawrevops_setup.py" /usr/local/bin/
    elif [[ -f "clawrevops_setup.py" ]]; then
        cp "clawrevops_setup.py" /usr/local/bin/
    else
        curl -fsSL "$REPO_BASE/ubuntu/clawrevops_setup.py" -o /usr/local/bin/clawrevops_setup.py
    fi
    chmod +x /usr/local/bin/clawrevops_setup.py
    print_ok
}

create_shortcuts() {
    print_step 4 4 "Creating shortcuts...                    "

    cat > /usr/local/bin/agent-setup << EOF
#!/bin/bash
REPO_BASE="${REPO_BASE}"
curl -fsSL "\$REPO_BASE/ubuntu/clawrevops_setup.py?\$(date +%s)" -o /usr/local/bin/clawrevops_setup.py \
    && chmod +x /usr/local/bin/clawrevops_setup.py \
    || echo "  Warning: could not fetch latest script, running cached version"
python3 /usr/local/bin/clawrevops_setup.py "\$@"
EOF

    chmod +x /usr/local/bin/agent-setup
    print_ok
}

show_complete() {
    echo
    print_divider
    echo
    echo -e "  ${GREEN}${BOLD}  Bootstrap complete!${RESET}"
    echo
    echo -e "  ${BOLD}What happens next:${RESET}"
    echo
    echo -e "  ${CYAN}  1.${RESET}  The setup wizard will guide you step by step"
    echo -e "  ${CYAN}  2.${RESET}  You will create a safe non-root user for your agents"
    echo -e "  ${CYAN}  3.${RESET}  You will pick your coding assistant and agent platform"
    echo -e "  ${CYAN}  4.${RESET}  You will be asked to authenticate Tailscale"
    echo -e "       ${DIM}(a link will appear — open it in your browser)${RESET}"
    echo -e "  ${CYAN}  5.${RESET}  SSH hardening is offered last, only after verification"
    echo
    echo -e "  ${DIM}Re-run or resume any time with:  ${YELLOW}sudo agent-setup${RESET}"
    echo
    print_divider
    echo
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    print_banner

    check_root
    check_ubuntu

    # If stdin is not a terminal (e.g. curl | bash), reconnect to the
    # controlling terminal so interactive read prompts work.
    if [[ ! -t 0 ]]; then
        exec < /dev/tty 2>/dev/null || true
    fi

    print_divider
    echo
    echo -e "  Type ${YELLOW}${BOLD}INSTALL${RESET} to accept and continue, or anything else to cancel."
    echo
    read -rp "  > " confirm
    echo
    if [[ "$confirm" != "INSTALL" ]]; then
        echo -e "  ${YELLOW}Cancelled.${RESET} No changes were made to your server."
        echo
        exit 0
    fi

    echo -e "  ${BOLD}Preparing your server...${RESET}"
    echo
    print_step 1 4 "Checking system...                       "
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        print_ok
        print_info "Detected: ${PRETTY_NAME}"
    else
        print_ok
    fi

    install_python
    install_scripts
    create_shortcuts
    show_complete

    echo -e "  ${GREEN}${BOLD}Starting setup wizard...${RESET}"
    echo
    exec python3 /usr/local/bin/clawrevops_setup.py
}

main
