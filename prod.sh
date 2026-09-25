#!/bin/bash
set -e

# =============================================================================
# Kerf production script (same server as crm_3k, reached by IP, no domain)
# Runs commands on the production server via SSH
# Usage: ./prod.sh [--host <ip>] <command> [command2] ...
# =============================================================================

SERVER_USER="${SERVER_USER:-ubuntu}"
SERVER_HOST="${SERVER_HOST:-3.136.238.189}"      # crm_3k server (t4g.small, arm64)
PROJECT_DIR="${PROJECT_DIR:-/home/ubuntu/kerf}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
GITHUB_REPO="${GITHUB_REPO:-juancresc/mcp-svg}"
HTTP_PORT=8765
MCP_PORT=8766

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            SERVER_HOST="$2"
            shift 2
            ;;
        --user)
            SERVER_USER="$2"
            shift 2
            ;;
        *)
            break
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_help() {
    echo "Kerf Production Script"
    echo ""
    echo "Usage: ./prod.sh [--host <ip>] [--user <user>] <command> [command2] ..."
    echo ""
    echo "Options:"
    echo "  --host <ip>   Override server host (default: ${SERVER_HOST})"
    echo "  --user <user> Override server user (default: ${SERVER_USER})"
    echo ""
    echo "Commands (setup: init-ssh → init-dir → init-docker → init-env → deploy):"
    echo "  init-ssh      Setup server SSH key for GitHub access (deploy key)"
    echo "  init-dir      Clone the repo into ${PROJECT_DIR}"
    echo "  init-docker   Install Docker on the server (skipped if present)"
    echo "  init-env      Create .env on the server (public URL + random token), if missing"
    echo "  commit        Git add, commit 'auto', push to main"
    echo "  deploy        Git pull, build and restart (on server)"
    echo "  pull          Git pull and restart (no build: web/ changes only)"
    echo "  restart       Restart the container (no build)"
    echo "  stop          Stop the container"
    echo "  test          Run the pytest suite inside the prod image"
    echo "  backup        Download data/ from the server as a tar.gz"
    echo "  push-data     Upload local data/ to the server (overwrites, asks first)"
    echo "  pull-data     Download the server's data/ into local data/ (overwrites, asks first)"
    echo "  logs          Tail production logs"
    echo "  status        Show container status and check the editor responds"
    echo "  url           Print the editor URL (with token) and the MCP config"
    echo "  push-env <f>  Copy a local env file to the server as .env"
    echo "  ssh           SSH into the server"
    echo ""
    echo "Examples:"
    echo "  ./prod.sh commit deploy"
    echo "  ./prod.sh backup"
    echo "  ./prod.sh url"
}

run_remote() {
    echo -e "${YELLOW}→ ${SERVER_USER}@${SERVER_HOST}${NC}"
    ssh ${SERVER_USER}@${SERVER_HOST} "cd ${PROJECT_DIR} && export DC='docker compose --env-file .env -f docker-compose-prod.yml' && $1"
}

confirm() {
    read -r -p "$1 [y/N] " answer
    [[ "$answer" =~ ^[yY]$ ]] || { echo "Aborted"; exit 1; }
}

# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

cmd_init_ssh() {
    # Derive project name from repo (e.g. juancresc/mcp-svg -> mcp-svg)
    local PROJECT_NAME="${GITHUB_REPO##*/}"
    echo "Initializing SSH key for ${PROJECT_NAME}..."

    ssh ${SERVER_USER}@${SERVER_HOST} "
        set -e
        PROJECT_NAME='${PROJECT_NAME}'
        SSH_KEY_PATH=\"\$HOME/.ssh/id_\${PROJECT_NAME}\"

        mkdir -p \"\$HOME/.ssh\"
        chmod 700 \"\$HOME/.ssh\"

        # Generate per-project key if it doesn't exist
        if [ ! -f \"\$SSH_KEY_PATH\" ]; then
            echo \"Generating SSH key: \$SSH_KEY_PATH\"
            ssh-keygen -t ed25519 -f \"\$SSH_KEY_PATH\" -N '' -C \"\${PROJECT_NAME}-deploy\"
        else
            echo \"SSH key already exists: \$SSH_KEY_PATH\"
        fi

        # Add SSH config alias for this project
        SSH_ALIAS=\"github-\${PROJECT_NAME}\"
        if ! grep -q \"Host \${SSH_ALIAS}\" \"\$HOME/.ssh/config\" 2>/dev/null; then
            echo \"\" >> \"\$HOME/.ssh/config\"
            echo \"Host \${SSH_ALIAS}\" >> \"\$HOME/.ssh/config\"
            echo \"    HostName github.com\" >> \"\$HOME/.ssh/config\"
            echo \"    User git\" >> \"\$HOME/.ssh/config\"
            echo \"    IdentityFile \${SSH_KEY_PATH}\" >> \"\$HOME/.ssh/config\"
            echo \"    IdentitiesOnly yes\" >> \"\$HOME/.ssh/config\"
            chmod 600 \"\$HOME/.ssh/config\"
            echo \"Added SSH config alias '\${SSH_ALIAS}'\"
        else
            echo \"SSH config alias '\${SSH_ALIAS}' already exists\"
        fi

        # Add GitHub to known_hosts if missing
        if ! ssh-keygen -F github.com > /dev/null 2>&1; then
            ssh-keyscan -t ed25519 github.com >> \"\$HOME/.ssh/known_hosts\" 2>/dev/null
        fi

        echo ''
        echo '=========================================='
        echo 'PUBLIC KEY (add to GitHub deploy keys):'
        echo '=========================================='
        cat \"\${SSH_KEY_PATH}.pub\"
        echo '=========================================='
        echo ''
        echo 'Add this key at:'
        echo 'https://github.com/${GITHUB_REPO}/settings/keys'
        echo ''
    "
}

cmd_init_dir() {
    local PROJECT_NAME="${GITHUB_REPO##*/}"
    local SSH_ALIAS="github-${PROJECT_NAME}"
    echo "Creating project directory and cloning repo via ${SSH_ALIAS}..."

    ssh ${SERVER_USER}@${SERVER_HOST} "
        set -e

        if [ -d '${PROJECT_DIR}' ]; then
            echo 'Directory ${PROJECT_DIR} already exists'
            cd '${PROJECT_DIR}'
            git remote -v
            exit 0
        fi

        echo 'Cloning repository...'
        git clone git@${SSH_ALIAS}:${GITHUB_REPO}.git '${PROJECT_DIR}'
        mkdir -p '${PROJECT_DIR}/data'

        echo ''
        echo 'Repository cloned to ${PROJECT_DIR}'
        cd '${PROJECT_DIR}'
        git log --oneline -1
    "
}

cmd_init_docker() {
    echo "Installing Docker on server..."

    ssh ${SERVER_USER}@${SERVER_HOST} "
        set -e

        # Already there when crm_3k is deployed on the same server
        if command -v docker &> /dev/null; then
            echo 'Docker is already installed:'
            docker --version
            docker compose version
            exit 0
        fi

        sudo apt-get update
        sudo apt-get install -y ca-certificates curl gnupg
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
        echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt-get update
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        sudo usermod -aG docker \$USER
        sudo systemctl enable docker
        sudo systemctl start docker

        echo ''
        echo 'Docker installed (log out and back in for the docker group):'
        docker --version
        docker compose version
    "
}

cmd_init_env() {
    echo "Creating .env on the server..."
    run_remote "
        set -e
        if [ -f .env ]; then
            echo '.env already exists, left untouched:'
            sed 's/^KERF_TOKEN=.*/KERF_TOKEN=***/' .env
            exit 0
        fi
        umask 077
        {
            echo 'KERF_PUBLIC_URL=http://${SERVER_HOST}:${HTTP_PORT}'
            echo \"KERF_TOKEN=\$(openssl rand -hex 32)\"
        } > .env
        echo '.env created (see ./prod.sh url for the token)'
    "
}

cmd_commit() {
    echo "Committing changes..."
    git add .
    # Don't let "nothing to commit" abort the chain under `set -e`
    if git diff --cached --quiet; then
        echo "No changes to commit — skipping commit"
    else
        git commit -m "auto"
    fi
    git push origin main
    echo "Pushed to main"
}

cmd_deploy() {
    echo "Deploying to production (with build)..."
    run_remote "
        set -e
        echo 'Pulling latest changes...'
        git pull

        echo 'Building and starting container...'
        \$DC build kerf
        \$DC up -d kerf
        docker image prune -f > /dev/null

        echo 'Deploy complete'
    "
}

cmd_pull() {
    # web/ is bind-mounted, so UI changes only need a pull; the restart makes
    # open browsers notice a new instance and reload.
    echo "Quick deploy (no build)..."
    run_remote "
        set -e
        git pull
        \$DC restart kerf
        echo 'Quick deploy complete'
    "
}

cmd_restart() {
    echo "Restarting..."
    run_remote "
        set -e
        \$DC up -d --no-build kerf
        \$DC restart kerf
        sleep 2
        \$DC ps
    "
}

cmd_stop() {
    echo "Stopping..."
    run_remote "\$DC stop kerf"
    echo "Stopped"
}

cmd_test() {
    echo "Running tests in the prod image..."
    run_remote "\$DC run --rm --no-deps -T --entrypoint python kerf -m pytest -q tests"
}

cmd_backup() {
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    LOCAL_FILE="${BACKUP_DIR}/kerf_data_${TIMESTAMP}.tar.gz"
    mkdir -p "$BACKUP_DIR"

    echo "Downloading data/ from the server..."
    ssh ${SERVER_USER}@${SERVER_HOST} "cd ${PROJECT_DIR} && tar czf - data" > "$LOCAL_FILE"
    echo -e "${GREEN}Backup saved: ${LOCAL_FILE} ($(du -h "$LOCAL_FILE" | cut -f1))${NC}"
}

cmd_push_data() {
    echo "This overwrites files in ${SERVER_HOST}:${PROJECT_DIR}/data with your local data/"
    echo "(it doesn't delete server files that don't exist locally; .session.json is skipped)."
    confirm "Continue?"
    cmd_backup
    rsync -avz --exclude '.session.json' --exclude '.DS_Store' \
        ./data/ ${SERVER_USER}@${SERVER_HOST}:${PROJECT_DIR}/data/
    echo -e "${GREEN}Done. Reopen the files in the editor to see them.${NC}"
}

cmd_pull_data() {
    echo "This overwrites files in your local data/ with the server's copies"
    echo "(it doesn't delete local files; .session.json is skipped)."
    confirm "Continue?"
    rsync -avz --exclude '.session.json' --exclude '.DS_Store' \
        ${SERVER_USER}@${SERVER_HOST}:${PROJECT_DIR}/data/ ./data/
    echo -e "${GREEN}Done${NC}"
}

cmd_logs() {
    run_remote "\$DC logs -f --tail=200 kerf"
}

cmd_status() {
    run_remote "
        \$DC ps
        echo ''
        code=\$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: ${SERVER_HOST}' http://localhost:${HTTP_PORT}/)
        [ \"\$code\" = 200 ] && echo 'editor: responding' || echo \"editor: not responding (HTTP \$code)\"
    "
}

cmd_url() {
    local TOKEN
    TOKEN=$(ssh ${SERVER_USER}@${SERVER_HOST} "grep '^KERF_TOKEN=' ${PROJECT_DIR}/.env | cut -d= -f2-")
    if [ -z "$TOKEN" ]; then
        echo -e "${RED}No KERF_TOKEN on the server. Run ./prod.sh init-env${NC}"
        exit 1
    fi
    echo "Editor (open once, it sets a login cookie):"
    echo "  http://${SERVER_HOST}:${HTTP_PORT}/?token=${TOKEN}"
    echo ""
    echo "Claude Code:"
    echo "  claude mcp add --transport sse kerf-prod http://${SERVER_HOST}:${MCP_PORT}/sse --header \"Authorization: Bearer ${TOKEN}\""
}

cmd_push_env() {
    local ENV_SRC="$1"
    if [ -z "$ENV_SRC" ] || [ ! -f "$ENV_SRC" ]; then
        echo -e "${RED}Error: provide an existing local env file${NC}"
        echo "Usage: ./prod.sh push-env <file>"
        exit 1
    fi
    echo "Pushing ${ENV_SRC} → ${SERVER_USER}@${SERVER_HOST}:${PROJECT_DIR}/.env"
    scp "$ENV_SRC" ${SERVER_USER}@${SERVER_HOST}:${PROJECT_DIR}/.env
    echo -e "${GREEN}Done (./prod.sh restart to apply)${NC}"
}

cmd_ssh() {
    ssh -t ${SERVER_USER}@${SERVER_HOST} "cd ${PROJECT_DIR} && exec \$SHELL -l"
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

if [ $# -eq 0 ]; then
    print_help
    exit 0
fi

while [ $# -gt 0 ]; do
    cmd="$1"
    shift
    case "$cmd" in
        help|--help|-h) print_help ;;
        init-ssh)       cmd_init_ssh ;;
        init-dir)       cmd_init_dir ;;
        init-docker)    cmd_init_docker ;;
        init-env)       cmd_init_env ;;
        commit)         cmd_commit ;;
        deploy)         cmd_deploy ;;
        pull|quick)     cmd_pull ;;
        restart|start)  cmd_restart ;;
        stop)           cmd_stop ;;
        test)           cmd_test ;;
        backup)         cmd_backup ;;
        push-data)      cmd_push_data ;;
        pull-data)      cmd_pull_data ;;
        logs)           cmd_logs ;;
        status|ps)      cmd_status ;;
        url|token)      cmd_url ;;
        push-env)
            cmd_push_env "$1"
            shift
            ;;
        ssh)            cmd_ssh ;;
        *)
            echo "Unknown command: $cmd"
            echo "Run './prod.sh help' for usage"
            exit 1
            ;;
    esac
done
