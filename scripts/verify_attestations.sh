set -euo pipefail

candidates=${1:?candidate TSV path required}
verified=${2:?verified-subject TSV path required}
: > "${verified}"

while IFS=$'\t' read -r artifact bundle bundle_name name digest; do
  [[ -n "${name}" ]] || continue
  gh attestation verify "${artifact}" --bundle "${bundle}" \
    --repo leynos/cmd-mox \
    --signer-workflow leynos/cmd-mox/.github/workflows/release.yml
  printf '%s\t%s\t%s\n' "${bundle_name}" "${name}" "${digest}" \
    >> "${verified}"
done < "${candidates}"
