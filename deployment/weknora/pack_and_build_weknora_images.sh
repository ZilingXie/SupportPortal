#!/usr/bin/env bash
# Pack the pinned WeKnora fork commit into a source archive, upload it to the
# release-evidence bucket, and start the dedicated CodeBuild image build.
#
# Usage:
#   pack_and_build_weknora_images.sh --fork-dir <path> --commit <full-sha> \
#       [--skip-build]
#
# Outputs (stdout): JSON with archive s3 key, version id, sha256, and the
# CodeBuild build id (when not --skip-build). Record all of it in the release
# evidence; the buildspec verifies WEKNORA_COMMIT_INFO inside the archive.
set -Eeuo pipefail

FORK_DIR=""
COMMIT=""
SKIP_BUILD=0
AWS_CLI_BIN="${AWS_CLI_BIN:-aws}"
REGION="${AWS_REGION:-us-east-1}"

fail() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -ge 1 ]]; do
  case "$1" in
    --fork-dir) [[ $# -ge 2 ]] || fail "--fork-dir requires a value"; FORK_DIR="$2"; shift 2 ;;
    --commit) [[ $# -ge 2 ]] || fail "--commit requires a value"; COMMIT="$2"; shift 2 ;;
    --skip-build) SKIP_BUILD=1; shift 1 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ -n "$FORK_DIR" && -n "$COMMIT" ]] || fail "--fork-dir and --commit are required"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "--commit must be a full 40-char sha"

ACCOUNT_ID="$("$AWS_CLI_BIN" sts get-caller-identity --query Account --output text)" || fail "cannot resolve AWS account"
BUCKET="supportportal-release-evidence-${ACCOUNT_ID}-${REGION}"
KEY="weknora/src/source.tar.gz"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Build the archive from the committed tree only (git archive), then add a
# provenance file so the build can verify it runs on the exact pinned commit.
# Stage into weknora-src/ (bsdtar has no --transform; staging is portable).
mkdir -p "$TMP_DIR/stage/weknora-src"
git -C "$FORK_DIR" archive --format=tar "$COMMIT" | tar -C "$TMP_DIR/stage/weknora-src" -x || fail "git archive failed"
printf 'commit=%s\narchived_at=%s\n' "$COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$TMP_DIR/stage/weknora-src/WEKNORA_COMMIT_INFO"
tar -C "$TMP_DIR/stage" -czf "$TMP_DIR/source.tar.gz" weknora-src || fail "cannot create source archive"
SHA256="$(shasum -a 256 "$TMP_DIR/source.tar.gz" | cut -d' ' -f1)"

"$AWS_CLI_BIN" s3 cp "$TMP_DIR/source.tar.gz" "s3://${BUCKET}/${KEY}" --region "$REGION" >/dev/null || fail "s3 upload failed"
VERSION_ID="$("$AWS_CLI_BIN" s3api head-object --bucket "$BUCKET" --key "$KEY" --query VersionId --output text --region "$REGION")" || fail "cannot read archive version id"
[[ -n "$VERSION_ID" && "$VERSION_ID" != "None" ]] || fail "release-evidence bucket must be versioned to pin the archive (got: '$VERSION_ID')"

BUILD_ID=""
BUILD_STATUS=""
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  BUILD_ID="$("$AWS_CLI_BIN" codebuild start-build \
    --project-name supportportal-weknora-image-build \
    --source-version "$VERSION_ID" \
    --environment-variables-override \
      name=WEKNORA_COMMIT,value="$COMMIT",type=PLAINTEXT \
    --query 'build.id' --output text --region "$REGION")" || fail "cannot start CodeBuild build"
  echo "started CodeBuild build $BUILD_ID (sourceVersion=$VERSION_ID)" >&2
  while :; do
    sleep 30
    BUILD_STATUS="$("$AWS_CLI_BIN" codebuild batch-get-builds --ids "$BUILD_ID" \
      --query 'builds[0].buildStatus' --output text --region "$REGION")"
    echo "  build status: $BUILD_STATUS" >&2
    case "$BUILD_STATUS" in
      SUCCEEDED) break ;;
      FAILED|FAULT|TIMED_OUT|STOPPED) fail "CodeBuild build ended in $BUILD_STATUS ($BUILD_ID)" ;;
    esac
  done
fi

jq -n \
  --arg commit "$COMMIT" \
  --arg bucket "$BUCKET" --arg key "$KEY" --arg versionId "$VERSION_ID" \
  --arg sha256 "$SHA256" --arg buildId "$BUILD_ID" --arg buildStatus "$BUILD_STATUS" \
  '{commit:$commit, archive:{bucket:$bucket, key:$key, versionId:$versionId, sha256:$sha256}, build:{id:$buildId, status:$buildStatus}}'
