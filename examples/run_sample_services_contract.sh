#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

tmp_output="$(mktemp)"

PYTHONPATH=src python3 -m contract_protocols.cli run-fake \
  --text-file examples/sample_services_contract.txt \
  --user-side "Заказчик" \
  --contract-type "договор оказания услуг" \
  --goal "Подготовить протокол разногласий перед подписанием" \
  --legal-topic "неустойка статья 333 ГК РФ" \
  --legal-topic "приемка услуг мотивированный отказ" \
  --legal-topic "односторонний отказ договор оказания услуг" \
  --seed-url "https://pravo.gov.ru/proxy/ips/?docbody=&nd=102033239" \
  | tee "$tmp_output"

case_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["case_id"])' "$tmp_output")"
rm -f "$tmp_output"

PYTHONPATH=src python3 -m contract_protocols.cli case-show "$case_id"
