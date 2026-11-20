#!/usr/bin/env bash
# Replays all six streams in data/ and writes events and charts to output/
set -e
mkdir -p output
for s in V75_H1 V75_M5 V100_M15 V100_M3 V50_H1 V50_M15; do
  sym=${s%_*}; tf=${s#*_}
  python3 scripts/swing_cli.py validate --csv data/$s.csv --symbol $sym --timeframe $tf
  python3 scripts/swing_cli.py replay   --csv data/$s.csv --symbol $sym --timeframe $tf --out output/${s}_events.json
  python3 scripts/swing_cli.py chart    --csv data/$s.csv --symbol $sym --timeframe $tf --out output/${s}_chart.png
done
