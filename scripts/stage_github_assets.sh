#!/usr/bin/env bash
set -euo pipefail

: "${TAG_NAME:?TAG_NAME must be set}"
: "${PYPI_INDEX_URL:?PYPI_INDEX_URL must be set}"
: "${PYPI_INDEX_PATH:?PYPI_INDEX_PATH must be set}"

mkdir -p github-state/files github-upload
gh release view "${TAG_NAME}" --json assets > release-assets.json

python3 -m scripts.release_assets_cli carryover \
  --publish dist/publish --assets release-assets.json \
  --bundles dist/attestations > github-carryover.txt
while IFS= read -r name; do
  [[ -n "${name}" ]] || continue
  [[ -f "github-state/files/${name}" ]] && continue
  gh release download "${TAG_NAME}" --pattern "${name}" \
    --dir github-state/files
done < github-carryover.txt

python3 -m scripts.release_assets_cli fetch-github \
  --publish dist/publish --index "${PYPI_INDEX_PATH}" \
  --assets release-assets.json > pypi-fetch.tsv
while IFS=$'\t' read -r name url; do
  [[ -n "${name}" ]] || continue
  curl --silent --show-error --location --fail \
    --retry 5 --retry-all-errors --retry-delay 2 \
    --connect-timeout 10 --max-time 300 \
    --output "github-upload/${name}" "${url}"
done < pypi-fetch.tsv

python3 -m scripts.release_assets_cli check-fetched \
  --publish dist/publish --index "${PYPI_INDEX_PATH}" \
  --assets release-assets.json --output github-upload
python3 -m scripts.release_assets_cli attestation-checks \
  --purpose github --publish dist/publish --index "${PYPI_INDEX_PATH}" \
  --assets release-assets.json --bundles dist/attestations \
  --github-files github-state/files --output github-upload \
  > attestation-candidates.tsv
bash scripts/verify_attestations.sh \
  attestation-candidates.tsv verified-subjects.tsv
python3 -m scripts.release_assets_cli stage-github \
  --publish dist/publish --index "${PYPI_INDEX_PATH}" \
  --assets release-assets.json --bundles dist/attestations \
  --github-files github-state/files --output github-upload \
  --verified-subjects verified-subjects.tsv
