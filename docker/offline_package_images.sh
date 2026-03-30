#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "env file not found: ${ENV_FILE}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

DOC_ENGINE="${DOC_ENGINE:-elasticsearch}"
DEVICE="${DEVICE:-cpu}"
COMPOSE_PROFILES="${COMPOSE_PROFILES:-${DOC_ENGINE},${DEVICE}}"
STACK_VERSION="${STACK_VERSION:-8.11.3}"
RAGFLOW_IMAGE="${RAGFLOW_IMAGE:-infiniflow/ragflow:v0.21.1-slim}"
TEI_IMAGE_CPU="${TEI_IMAGE_CPU:-infiniflow/text-embeddings-inference:cpu-1.8}"
TEI_IMAGE_GPU="${TEI_IMAGE_GPU:-infiniflow/text-embeddings-inference:1.8}"
SANDBOX_EXECUTOR_MANAGER_IMAGE="${SANDBOX_EXECUTOR_MANAGER_IMAGE:-infiniflow/sandbox-executor-manager:latest}"
SANDBOX_BASE_PYTHON_IMAGE="${SANDBOX_BASE_PYTHON_IMAGE:-infiniflow/sandbox-base-python:latest}"
SANDBOX_BASE_NODEJS_IMAGE="${SANDBOX_BASE_NODEJS_IMAGE:-infiniflow/sandbox-base-nodejs:latest}"

OUTPUT_PATH="${1:-${SCRIPT_DIR}/ragflow-offline-images-$(date -u +%Y%m%dT%H%M%SZ).tar}"

declare -a IMAGES=()

add_image() {
  local image="$1"
  local existing=""
  for existing in "${IMAGES[@]:-}"; do
    if [[ "${existing}" == "${image}" ]]; then
      return 0
    fi
  done
  IMAGES+=("${image}")
}

profile_enabled() {
  [[ ",${COMPOSE_PROFILES}," == *",$1,"* ]]
}

add_image "${RAGFLOW_IMAGE}"
add_image "mysql:8.0.39"
add_image "quay.io/minio/minio:RELEASE.2025-06-13T11-33-47Z"
add_image "valkey/valkey:8"

case "${DOC_ENGINE}" in
  elasticsearch)
    add_image "elasticsearch:${STACK_VERSION}"
    ;;
  infinity)
    add_image "infiniflow/infinity:v0.6.2"
    ;;
  opensearch)
    add_image "hub.icert.top/opensearchproject/opensearch:2.19.1"
    ;;
  *)
    echo "unsupported DOC_ENGINE: ${DOC_ENGINE}" >&2
    exit 1
    ;;
esac

if profile_enabled "kibana"; then
  add_image "kibana:${STACK_VERSION}"
fi

if profile_enabled "tei-cpu"; then
  add_image "${TEI_IMAGE_CPU}"
fi

if profile_enabled "tei-gpu"; then
  add_image "${TEI_IMAGE_GPU}"
fi

if profile_enabled "sandbox"; then
  add_image "${SANDBOX_EXECUTOR_MANAGER_IMAGE}"
  add_image "${SANDBOX_BASE_PYTHON_IMAGE}"
  add_image "${SANDBOX_BASE_NODEJS_IMAGE}"
fi

declare -a MISSING=()
image=""
for image in "${IMAGES[@]}"; do
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    MISSING+=("${image}")
  fi
done

if (( ${#MISSING[@]} > 0 )); then
  echo "the following images are missing locally:" >&2
  printf '  - %s\n' "${MISSING[@]}" >&2
  echo "build or pull them first, then rerun this script." >&2
  exit 1
fi

mkdir -p "$(dirname "${OUTPUT_PATH}")"

echo "packaging images into ${OUTPUT_PATH}"
printf '  - %s\n' "${IMAGES[@]}"
docker save -o "${OUTPUT_PATH}" "${IMAGES[@]}"
echo "offline image bundle created: ${OUTPUT_PATH}"
