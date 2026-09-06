#!/usr/bin/env bash

AWS_RELEASE_EXPECTED_ACCOUNT_ID="891612554546"
AWS_RELEASE_EXPECTED_ARN="arn:aws:iam::891612554546:user/Zac"
AWS_RELEASE_PROFILE="${AUTOMATION_AWS_PROFILE:-${AWS_PROFILE:-default}}"
AWS_RELEASE_CLI_BIN="$(command -v aws 2>/dev/null || true)"
AWS_RELEASE_SOURCE_CONFIG_FILE="${AWS_CONFIG_FILE:-}"
AWS_RELEASE_SOURCE_SHARED_CREDENTIALS_FILE="${AWS_SHARED_CREDENTIALS_FILE:-}"

aws() {
  local -a clean_env
  clean_env=(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN
    -u AWS_SECURITY_TOKEN -u AWS_CREDENTIAL_EXPIRATION -u AWS_PROFILE
    -u AWS_CONFIG_FILE -u AWS_SHARED_CREDENTIALS_FILE)
  [[ -z "${AWS_RELEASE_SOURCE_CONFIG_FILE}" ]] \
    || clean_env+=("AWS_CONFIG_FILE=${AWS_RELEASE_SOURCE_CONFIG_FILE}")
  [[ -z "${AWS_RELEASE_SOURCE_SHARED_CREDENTIALS_FILE}" ]] \
    || clean_env+=("AWS_SHARED_CREDENTIALS_FILE=${AWS_RELEASE_SOURCE_SHARED_CREDENTIALS_FILE}")
  "${clean_env[@]}" "${AWS_RELEASE_CLI_BIN}" "$@" --profile "${AWS_RELEASE_PROFILE}"
}

verify_release_aws_mutation_ready() {
  local minimum_ttl="${1:-900}" identity account arn provider exported expiration now remaining
  identity="$(aws sts get-caller-identity --region us-east-1 --output json)"
  account="$(jq -r '.Account // ""' <<<"${identity}")"
  arn="$(jq -r '.Arn // ""' <<<"${identity}")"
  [[ "${account}" = "${AWS_RELEASE_EXPECTED_ACCOUNT_ID}" ]] \
    || { printf 'AWS account must be %s\n' "${AWS_RELEASE_EXPECTED_ACCOUNT_ID}" >&2; return 1; }
  [[ "${arn}" = "${AWS_RELEASE_EXPECTED_ARN}" ]] \
    || { printf 'AWS identity must be %s\n' "${AWS_RELEASE_EXPECTED_ARN}" >&2; return 1; }
  provider="$(aws configure list 2>/dev/null | awk '$1 == "access_key" {print $5; exit}')"
  [[ "${provider}" = "login" ]] \
    || { printf 'AWS provider must be a refreshable login profile\n' >&2; return 1; }
  exported="$(aws configure export-credentials --format process 2>/dev/null)"
  expiration="$(jq -r '.Expiration // empty' <<<"${exported}")"
  unset exported
  [[ -n "${expiration}" ]] || return 0
  now="$(date -u +%s)"
  remaining="$(python3 -c 'from datetime import datetime; import sys; print(int(datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")).timestamp())-int(sys.argv[2]))' "${expiration}" "${now}")"
  if ((remaining < minimum_ttl)); then
    # Login providers refresh on the next AWS invocation; validate that refresh now.
    aws sts get-caller-identity --region us-east-1 --output json >/dev/null
  fi
}
