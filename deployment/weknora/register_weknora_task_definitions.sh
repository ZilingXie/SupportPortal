#!/usr/bin/env bash
# Bootstrap the WeKnora ECS task definitions.
#
# What this script owns (idempotent, add-only):
#   1. /supportportal/weknora/* SSM parameters — created with generated values
#      when missing, never overwritten. Secrets are SecureString.
#   2. A dedicated CA + server certificate pair for ParadeDB TLS
#      (db_tls_* parameters) generated with openssl on first run.
#   3. The five WeKnora task definitions (paradedb, redis, docreader, app,
#      frontend) referencing images already pushed to the WeKnora ECR repo.
#
# Stdout: JSON {<service>: <task-definition-arn>, ...} for terraform tfvars.
#
# Usage:
#   register_weknora_task_definitions.sh --commit <full-sha> \
#       --admin-email <email> [--open-registration] [--output <file>]
set -Eeuo pipefail

COMMIT=""
ADMIN_EMAIL=""
OPEN_REGISTRATION=0
OUTPUT=""
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"
PARAM_PREFIX="/supportportal/weknora"
CLUSTER="supportportal-weknora"

fail() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --commit) [[ $# -ge 2 ]] || fail "--commit requires a value"; COMMIT="$2"; shift 2 ;;
    --admin-email) [[ $# -ge 2 ]] || fail "--admin-email requires a value"; ADMIN_EMAIL="$2"; shift 2 ;;
    --open-registration) OPEN_REGISTRATION=1; shift 1 ;;
    --output) [[ $# -ge 2 ]] || fail "--output requires a value"; OUTPUT="$2"; shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "--commit (full sha) is required"
[[ -n "$ADMIN_EMAIL" ]] || fail "--admin-email is required (bootstrap system admin; the account with this email is auto-promoted on first registration)"

ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)" || fail "cannot resolve AWS account"
ECR_REPO="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/supportportal/weknora"
DOCS_BUCKET="supportportal-weknora-docs-${ACCOUNT_ID}-${REGION}"
EXECUTION_ROLE_ARN="$("$AWS_CLI_BIN" iam get-role --role-name supportportal-weknora-ecs-execution --query Role.Arn --output text)" || fail "execution role missing (apply the weknora terraform foundation first)"
APP_TASK_ROLE_ARN="$("$AWS_CLI_BIN" iam get-role --role-name supportportal-weknora-app-task --query Role.Arn --output text)" || fail "app task role missing (apply the weknora terraform foundation first)"
LOG_GROUP="/ecs/supportportal/weknora"

log() { echo "[register-weknora] $*" >&2; }

# ---------------------------------------------------------------------------
# 1. SSM parameters
# ---------------------------------------------------------------------------
param_exists() { "$AWS_CLI_BIN" ssm get-parameter --name "$1" >/dev/null 2>&1; }

ensure_param() { # name type value
  if param_exists "${PARAM_PREFIX}/$1"; then
    log "ssm param $1 already exists (kept)"
  else
    "$AWS_CLI_BIN" ssm put-parameter --name "${PARAM_PREFIX}/$1" --type "$2" --value "$3" --no-overwrite >/dev/null
    log "ssm param $1 created ($2)"
  fi
}

ensure_param "db_username" "String" "weknora"
ensure_param "db_name" "String" "weknora"
ensure_param "db_password" "SecureString" "$(openssl rand -hex 16)"
ensure_param "redis_password" "SecureString" "$(openssl rand -hex 16)"
ensure_param "jwt_secret" "SecureString" "$(openssl rand -hex 24)"
ensure_param "system_aes_key" "SecureString" "$(openssl rand -hex 16)"   # 32 chars = 32 bytes key material
ensure_param "system_signing_key" "SecureString" "$(openssl rand -hex 24)"
ensure_param "bootstrap_admin_email" "String" "$ADMIN_EMAIL"

if ! param_exists "${PARAM_PREFIX}/db_tls_ca_pem"; then
  TLS_DIR="$(mktemp -d)"
  trap 'rm -rf "$TLS_DIR"' EXIT
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$TLS_DIR/ca.key" -out "$TLS_DIR/ca.crt" \
    -days 3650 -subj "/CN=supportportal-weknora-db-ca" >/dev/null 2>&1 || fail "openssl CA generation failed"
  openssl req -newkey rsa:2048 -nodes -keyout "$TLS_DIR/server.key" -out "$TLS_DIR/server.csr" \
    -subj "/CN=paradedb.weknora.supportportal.local" >/dev/null 2>&1 || fail "openssl server CSR generation failed"
  printf 'subjectAltName=DNS:paradedb.weknora.supportportal.local\n' > "$TLS_DIR/ext.cnf"
  openssl x509 -req -in "$TLS_DIR/server.csr" -CA "$TLS_DIR/ca.crt" -CAkey "$TLS_DIR/ca.key" \
    -CAcreateserial -out "$TLS_DIR/server.crt" -days 730 -extfile "$TLS_DIR/ext.cnf" >/dev/null 2>&1 || fail "openssl server cert signing failed"
  ensure_param "db_tls_ca_pem" "SecureString" "$(cat "$TLS_DIR/ca.crt")"
  ensure_param "db_tls_server_cert_pem" "SecureString" "$(cat "$TLS_DIR/server.crt")"
  ensure_param "db_tls_server_key_pem" "SecureString" "$(cat "$TLS_DIR/server.key")"
  log "paradedb TLS CA + server certificate generated (CA 10y / server 2y)"
fi

# ---------------------------------------------------------------------------
# 2. Resolve image digests from ECR (fail loudly if the build has not run)
# ---------------------------------------------------------------------------
image_digest() { # tag
  local d
  d="$("$AWS_CLI_BIN" ecr describe-images --repository-name supportportal/weknora \
    --image-ids imageTag="$1" --query 'imageDetails[0].imageDigest' --output text 2>/dev/null)" \
    || true
  [[ -n "$d" && "$d" != "None" ]] || fail "image tag $1 not found in ECR (run the CodeBuild image build first)"
  printf '%s' "$d"
}

APP_IMAGE="${ECR_REPO}@$(image_digest "app-${COMMIT}")"
FRONTEND_IMAGE="${ECR_REPO}@$(image_digest "frontend-${COMMIT}")"
DOCREADER_IMAGE="${ECR_REPO}@$(image_digest "docreader-${COMMIT}")"
PARADEDB_IMAGE="${ECR_REPO}@$(image_digest "base-paradedb-v0.22.6-pg17")"
REDIS_IMAGE="${ECR_REPO}@$(image_digest "base-redis-7.0-alpine")"
log "images resolved:"
log "  app       ${APP_IMAGE}"
log "  frontend  ${FRONTEND_IMAGE}"
log "  docreader ${DOCREADER_IMAGE}"
log "  paradedb  ${PARADEDB_IMAGE}"
log "  redis     ${REDIS_IMAGE}"

DISABLE_REGISTRATION="true"
[[ "$OPEN_REGISTRATION" -eq 1 ]] && DISABLE_REGISTRATION="false"

secret() { printf '{"name":"%s","valueFrom":"%s/%s"}' "$1" "$PARAM_PREFIX" "$2"; }
envkv()  { printf '{"name":"%s","value":"%s"}' "$1" "$2"; }
awslogs() { printf '{"logDriver":"awslogs","options":{"awslogs-group":"%s","awslogs-region":"%s","awslogs-stream-prefix":"%s","awslogs-create-group":"true"}}' "$LOG_GROUP" "$REGION" "$1"; }

register() { # family json — echoes task definition arn
  local family="$1" json="$2" arn
  arn="$("$AWS_CLI_BIN" ecs register-task-definition --family "$family" --cli-input-json "$json" \
    --query 'taskDefinition.taskDefinitionArn' --output text)" || fail "registering $family failed"
  printf '%s' "$arn"
}

# ---------------------------------------------------------------------------
# 3a. paradedb (EC2 capacity, docker volume, TLS, data-presence guard)
# ---------------------------------------------------------------------------
PARADEDB_CMD='
set -e
TLS_DIR=/var/lib/postgresql/tls
mkdir -p "$TLS_DIR"
printenv DB_TLS_SERVER_CERT_PEM > "$TLS_DIR/server.crt"
printenv DB_TLS_SERVER_KEY_PEM > "$TLS_DIR/server.key"
chmod 600 "$TLS_DIR/server.crt" "$TLS_DIR/server.key"
chown postgres:postgres "$TLS_DIR/server.crt" "$TLS_DIR/server.key"
if [ "${REQUIRE_EXISTING_PGDATA:-}" = "true" ] && [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "REQUIRE_EXISTING_PGDATA=true but $PGDATA/PG_VERSION is missing; refusing to start an empty database" >&2
  exit 1
fi
exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/var/lib/postgresql/tls/server.crt -c ssl_key_file=/var/lib/postgresql/tls/server.key
'
PARADEDB_ARN="$(register weknora-paradedb "$(jq -n \
  --arg image "$PARADEDB_IMAGE" --arg exec "$EXECUTION_ROLE_ARN" --arg region "$REGION" \
  --arg cmd "$PARADEDB_CMD" --argjson logs "$(awslogs paradedb)" \
  --argjson secrets "$(jq -n -c \
    "[$(secret POSTGRES_USER db_username),$(secret POSTGRES_PASSWORD db_password),$(secret POSTGRES_DB db_name),$(secret DB_TLS_SERVER_CERT_PEM db_tls_server_cert_pem),$(secret DB_TLS_SERVER_KEY_PEM db_tls_server_key_pem)]")" \
  '{
    family: "weknora-paradedb",
    networkMode: "awsvpc",
    requiresCompatibilities: ["EC2"],
    executionRoleArn: $exec,
    volumes: [{
      name: "paradedb-data",
      dockerVolumeConfiguration: { driver: "local", scope: "shared", autoprovision: true }
    }],
    containerDefinitions: [{
      name: "paradedb",
      image: $image,
      essential: true,
      entryPoint: ["sh", "-ec"],
      command: [$cmd],
      environment: [
        {name: "PGDATA", value: "/var/lib/postgresql/data/pgdata"}
      ],
      secrets: $secrets,
      portMappings: [{containerPort: 5432, hostPort: 5432, protocol: "tcp"}],
      mountPoints: [{sourceVolume: "paradedb-data", containerPath: "/var/lib/postgresql/data", readOnly: false}],
      memory: 8192,
      cpu: 1024,
      healthCheck: {
        command: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB || exit 1"],
        interval: 10, timeout: 5, retries: 10, startPeriod: 60
      },
      logConfiguration: $logs
    }],
    tags: [{key: "Project", value: "supportportal"}, {key: "Environment", value: "weknora"}]
  }')")"
log "registered $PARADEDB_ARN"

# ---------------------------------------------------------------------------
# 3b. redis (EC2 capacity, docker volume, appendonly + auth)
# ---------------------------------------------------------------------------
REDIS_CMD='exec docker-entrypoint.sh redis-server --appendonly yes --requirepass "$REDIS_PASSWORD"'
REDIS_ARN="$(register weknora-redis "$(jq -n \
  --arg image "$REDIS_IMAGE" --arg exec "$EXECUTION_ROLE_ARN" \
  --arg cmd "$REDIS_CMD" --argjson logs "$(awslogs redis)" \
  --argjson secrets "$(jq -n -c "[$(secret REDIS_PASSWORD redis_password)]")" \
  '{
    family: "weknora-redis",
    networkMode: "awsvpc",
    requiresCompatibilities: ["EC2"],
    executionRoleArn: $exec,
    volumes: [{
      name: "redis-data",
      dockerVolumeConfiguration: { driver: "local", scope: "shared", autoprovision: true }
    }],
    containerDefinitions: [{
      name: "redis",
      image: $image,
      essential: true,
      entryPoint: ["sh", "-ec"],
      command: [$cmd],
      secrets: $secrets,
      portMappings: [{containerPort: 6379, hostPort: 6379, protocol: "tcp"}],
      mountPoints: [{sourceVolume: "redis-data", containerPath: "/data", readOnly: false}],
      memory: 1024,
      cpu: 256,
      healthCheck: {
        command: ["CMD-SHELL", "redis-cli -a \"$REDIS_PASSWORD\" --no-auth-warning ping | grep -q PONG || exit 1"],
        interval: 10, timeout: 5, retries: 10, startPeriod: 30
      },
      logConfiguration: $logs
    }],
    tags: [{key: "Project", value: "supportportal"}, {key: "Environment", value: "weknora"}]
  }')")"
log "registered $REDIS_ARN"

# ---------------------------------------------------------------------------
# 3c. docreader (Fargate)
# ---------------------------------------------------------------------------
DOCREADER_ARN="$(register weknora-docreader "$(jq -n \
  --arg image "$DOCREADER_IMAGE" --arg exec "$EXECUTION_ROLE_ARN" \
  --argjson logs "$(awslogs docreader)" \
  '{
    family: "weknora-docreader",
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: "2048",
    memory: "4096",
    executionRoleArn: $exec,
    containerDefinitions: [{
      name: "docreader",
      image: $image,
      essential: true,
      environment: [
        {name: "TZ", value: "Asia/Shanghai"},
        {name: "LOG_LEVEL", value: "info"},
        {name: "DOCREADER_IMAGE_OUTPUT_DIR", value: "/tmp/docreader"}
      ],
      portMappings: [{containerPort: 50051, hostPort: 50051, protocol: "tcp"}],
      healthCheck: {
        command: ["CMD-SHELL", "grpc_health_probe -addr=localhost:50051 || exit 1"],
        interval: 30, timeout: 10, retries: 3, startPeriod: 60
      },
      logConfiguration: $logs
    }],
    tags: [{key: "Project", value: "supportportal"}, {key: "Environment", value: "weknora"}]
  }')")"
log "registered $DOCREADER_ARN"

# ---------------------------------------------------------------------------
# 3d. app (Fargate; S3 via task role default credential chain; DB TLS verify-ca)
# ---------------------------------------------------------------------------
APP_CMD='
set -e
if [ -n "${DB_TLS_CA_PEM:-}" ]; then printenv DB_TLS_CA_PEM > /tmp/db-ca.pem; fi
exec ./scripts/docker-entrypoint.sh ./WeKnora
'
APP_ARN="$(register weknora-app "$(jq -n \
  --arg image "$APP_IMAGE" --arg exec "$EXECUTION_ROLE_ARN" --arg taskrole "$APP_TASK_ROLE_ARN" \
  --arg cmd "$APP_CMD" --argjson logs "$(awslogs app)" \
  --arg bucket "$DOCS_BUCKET" --arg registration "$DISABLE_REGISTRATION" \
  --argjson secrets "$(jq -n -c \
    "[$(secret DB_USER db_username),$(secret DB_PASSWORD db_password),$(secret DB_NAME db_name),$(secret REDIS_PASSWORD redis_password),$(secret JWT_SECRET jwt_secret),$(secret SYSTEM_AES_KEY system_aes_key),$(secret SYSTEM_SIGNING_KEY system_signing_key),$(secret DB_TLS_CA_PEM db_tls_ca_pem),$(secret WEKNORA_BOOTSTRAP_SYSTEM_ADMIN_EMAIL bootstrap_admin_email)]")" \
  '{
    family: "weknora-app",
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: "2048",
    memory: "4096",
    executionRoleArn: $exec,
    taskRoleArn: $taskrole,
    containerDefinitions: [{
      name: "app",
      image: $image,
      essential: true,
      entryPoint: ["sh", "-ec"],
      command: [$cmd],
      environment: [
        {name: "DB_DRIVER", value: "postgres"},
        {name: "DB_HOST", value: "paradedb.weknora.supportportal.local"},
        {name: "DB_PORT", value: "5432"},
        {name: "DB_SSLMODE", value: "verify-ca"},
        {name: "DB_SSLROOT_CERT", value: "/tmp/db-ca.pem"},
        {name: "RETRIEVE_DRIVER", value: "postgres"},
        {name: "DOCREADER_ADDR", value: "docreader.weknora.supportportal.local:50051"},
        {name: "REDIS_ADDR", value: "redis.weknora.supportportal.local:6379"},
        {name: "STORAGE_TYPE", value: "s3"},
        {name: "S3_REGION", value: "us-east-1"},
        {name: "S3_BUCKET_NAME", value: $bucket},
        {name: "S3_USE_SSL", value: "true"},
        {name: "DISABLE_REGISTRATION", value: $registration},
        {name: "WEKNORA_SANDBOX_DOCKER_ENABLED", value: "false"},
        {name: "GIN_MODE", value: "release"},
        {name: "LOG_LEVEL", value: "info"},
        {name: "AUTO_MIGRATE", value: "true"},
        {name: "FRONTEND_BASE_URL", value: "https://supportcenter.stellarix.space/dashboard/weknora"},
        {name: "MAX_FILE_SIZE_MB", value: "50"},
        {name: "TZ", value: "Asia/Shanghai"}
      ],
      secrets: $secrets,
      portMappings: [{containerPort: 8080, hostPort: 8080, protocol: "tcp"}],
      healthCheck: {
        command: ["CMD-SHELL", "curl -fsS http://localhost:8080/health || exit 1"],
        interval: 30, timeout: 10, retries: 5, startPeriod: 300
      },
      logConfiguration: $logs
    }],
    tags: [{key: "Project", value: "supportportal"}, {key: "Environment", value: "weknora"}]
  }')")"
log "registered $APP_ARN"

# ---------------------------------------------------------------------------
# 3e. frontend (Fargate behind the ALB, URL_PREFIX sub-path serving)
# ---------------------------------------------------------------------------
FRONTEND_ARN="$(register weknora-frontend "$(jq -n \
  --arg image "$FRONTEND_IMAGE" --arg exec "$EXECUTION_ROLE_ARN" \
  --argjson logs "$(awslogs frontend)" \
  '{
    family: "weknora-frontend",
    networkMode: "awsvpc",
    requiresCompatibilities: ["FARGATE"],
    cpu: "512",
    memory: "1024",
    executionRoleArn: $exec,
    containerDefinitions: [{
      name: "frontend",
      image: $image,
      essential: true,
      environment: [
        {name: "MAX_FILE_SIZE_MB", value: "50"},
        {name: "APP_HOST", value: "app.weknora.supportportal.local"},
        {name: "APP_PORT", value: "8080"},
        {name: "APP_SCHEME", value: "http"},
        {name: "URL_PREFIX", value: "/dashboard/weknora"},
        {name: "TZ", value: "Asia/Shanghai"}
      ],
      portMappings: [{containerPort: 80, hostPort: 80, protocol: "tcp"}],
      healthCheck: {
        command: ["CMD-SHELL", "wget -q -O /dev/null http://localhost:80/dashboard/weknora/ || exit 1"],
        interval: 30, timeout: 5, retries: 3, startPeriod: 30
      },
      logConfiguration: $logs
    }],
    tags: [{key: "Project", value: "supportportal"}, {key: "Environment", value: "weknora"}]
  }')")"
log "registered $FRONTEND_ARN"

ARN_MAP="$(jq -n \
  --arg paradedb "$PARADEDB_ARN" --arg redis "$REDIS_ARN" --arg docreader "$DOCREADER_ARN" \
  --arg app "$APP_ARN" --arg frontend "$FRONTEND_ARN" \
  '{paradedb:$paradedb, redis:$redis, docreader:$docreader, app:$app, frontend:$frontend}')"
if [[ -n "$OUTPUT" ]]; then printf '%s\n' "$ARN_MAP" > "$OUTPUT"; log "arn map written to $OUTPUT"; fi
printf '%s\n' "$ARN_MAP"
