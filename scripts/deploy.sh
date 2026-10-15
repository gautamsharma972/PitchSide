#!/usr/bin/env bash
# Deploy PitchSide to Azure: infra (Bicep) -> remote image build in ACR (no local Docker) -> Container App -> smoke test.
#   ./scripts/deploy.sh [resource-group] [location]
# Needs: az CLI logged in (`az login`) and a subscription selected (`az account set -s <id>`).
set -euo pipefail
cd "$(dirname "$0")/.."
RG="${1:-pitchside-rg}"; LOC="${2:-swedencentral}"; TAG="$(date +%Y%m%d%H%M%S)"
EXTRA=("${@:3}")          # extra bicep params, e.g. modelCapacity=20 modelSku=Standard

echo "== subscription =="; az account show --query "{name:name,id:id}" -o tsv
echo "== resource group $RG ($LOC) =="; az group create -n "$RG" -l "$LOC" -o none
echo "== infra (Bicep) =="
OUT=$(az deployment group create -g "$RG" -f infra/main.bicep ${EXTRA[@]:+-p "${EXTRA[@]}"} --query properties.outputs -o json)
ACR=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['acrName']['value'])")
APP=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['appName']['value'])")
URL=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['url']['value'])")
echo "== build image in ACR ($ACR) =="; az acr build -r "$ACR" -t "pitchside:$TAG" . -o none
echo "== deploy to Container App ($APP) =="
az containerapp update -n "$APP" -g "$RG" --image "$ACR.azurecr.io/pitchside:$TAG" -o none
echo "== smoke test $URL =="
for i in $(seq 1 30); do curl -sf "$URL/api/status" >/dev/null && break; sleep 5; done
curl -s "$URL/api/status" | python3 -m json.tool | head -12
echo; echo "LIVE: $URL"
