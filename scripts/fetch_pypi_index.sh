set -euo pipefail

: "${PYPI_INDEX_URL:?PYPI_INDEX_URL must be set}"
: "${PYPI_INDEX_PATH:?PYPI_INDEX_PATH must be set}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"

attempt=0
status=000
while [[ ${attempt} -lt 6 ]]; do
  attempt=$((attempt + 1))
  status=$(curl --silent --show-error --location \
    --connect-timeout 10 --max-time 60 \
    --output "${PYPI_INDEX_PATH}" --write-out '%{http_code}' \
    --header 'Accept: application/vnd.pypi.simple.v1+json' \
    "${PYPI_INDEX_URL}") || status=000
  case "${status}" in
    000|429|5[0-9][0-9])
      if [[ ${attempt} -lt 6 ]]; then
        sleep 2
      fi
      ;;
    *) break ;;
  esac
done

printf 'attempts=%s\nstatus=%s\n' "${attempt}" "${status}" \
  >> "${GITHUB_OUTPUT}"
case "${status}" in
  200) ;;
  404) printf '{"files": []}\n' > "${PYPI_INDEX_PATH}" ;;
  *)
    echo "PyPI simple index returned HTTP ${status}." >&2
    exit 1
    ;;
esac
