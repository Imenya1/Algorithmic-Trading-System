# Replays all six streams in data\ and writes events and charts to output\
New-Item -ItemType Directory -Force output | Out-Null
foreach ($s in "V75_H1","V75_M5","V100_M15","V100_M3","V50_H1","V50_M15") {
  $sym,$tf = $s.Split("_")
  py scripts/swing_cli.py validate --csv data/$s.csv --symbol $sym --timeframe $tf
  py scripts/swing_cli.py replay   --csv data/$s.csv --symbol $sym --timeframe $tf --out output/${s}_events.json
  py scripts/swing_cli.py chart    --csv data/$s.csv --symbol $sym --timeframe $tf --out output/${s}_chart.png
}
