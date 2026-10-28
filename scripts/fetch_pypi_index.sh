set -euo pipefail

: "${PYPI_INDEX_URL:?PYPI_INDEX_URL must be set}"
: "${PYPI_INDEX_PATH:?PYPI_INDEX_PATH must be set}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"

expected_publish_dir=${EXPECTED_PUBLISH_DIR:-}
release_assets_path=${RELEASE_ASSETS_PATH:-}
max_attempts=6
retry_delay=2
if [[ -n "${expected_publish_dir}" ]]; then
  : "${release_assets_path:?RELEASE_ASSETS_PATH must be set when waiting for published files}"
  if [[ ! -d "${expected_publish_dir}" ]]; then
    echo "Expected distribution directory does not exist: ${expected_publish_dir}" >&2
    exit 1
  fi
  max_attempts=10
fi

attempt=0
status=000
missing_names=""
while [[ ${attempt} -lt ${max_attempts} ]]; do
  attempt=$((attempt + 1))
  status=$(curl --silent --show-error --location \
    --connect-timeout 10 --max-time 20 \
    --output "${PYPI_INDEX_PATH}" --write-out '%{http_code}' \
    --header 'Accept: application/vnd.pypi.simple.v1+json' \
    "${PYPI_INDEX_URL}") || status=000
  retry=false
  case "${status}" in
    000|429|5[0-9][0-9])
      retry=true
      ;;
    200)
      if [[ -n "${expected_publish_dir}" ]]; then
        missing_names=$(python3 -m scripts.release_assets_cli missing-pypi \
          --publish "${expected_publish_dir}" --index "${PYPI_INDEX_PATH}" \
          --assets "${release_assets_path}")
        if [[ -n "${missing_names}" ]]; then
          retry=true
        else
          break
        fi
      else
        break
      fi
      ;;
    404)
      if [[ -n "${expected_publish_dir}" ]]; then
        printf '{"files": []}\n' > "${PYPI_INDEX_PATH}"
        missing_names=$(python3 -m scripts.release_assets_cli missing-pypi \
          --publish "${expected_publish_dir}" --index "${PYPI_INDEX_PATH}" \
          --assets "${release_assets_path}")
        retry=true
      else
        break
      fi
      ;;
    *) break ;;
  esac
  if [[ -n "${expected_publish_dir}" ]]; then
    case "${attempt}" in
      1) retry_delay=2 ;;
      2) retry_delay=4 ;;
      3) retry_delay=8 ;;
      4) retry_delay=16 ;;
      *) retry_delay=30 ;;
    esac
  fi
  if [[ "${retry}" == true && ${attempt} -lt ${max_attempts} ]]; then
    sleep "${retry_delay}"
  fi
done

printf 'attempts=%s\nstatus=%s\n' "${attempt}" "${status}" \
  >> "${GITHUB_OUTPUT}"
case "${status}" in
  200)
    if [[ -n "${missing_names}" ]]; then
      printf '::error::PyPI index did not list release files after %s attempts: %s\n' \
        "${max_attempts}" "${missing_names}" >&2
      exit 1
    fi
    ;;
  404)
    if [[ -n "${expected_publish_dir}" ]]; then
      printf '::error::PyPI index did not list release files after %s attempts: %s\n' \
        "${max_attempts}" "${missing_names}" >&2
      exit 1
    fi
    printf '{"files": []}\n' > "${PYPI_INDEX_PATH}"
    ;;
  *)
    echo "PyPI simple index returned HTTP ${status}." >&2
    exit 1
    ;;
esac
