#!/usr/bin/env bash
# 低频探测目标站点是否恢复（国外站点当前整体不可达时的等待工具）
# 用法: probe_remote.sh [间隔秒] [最大轮数]
set -u
IV=${1:-120}
MAX=${2:-15}
URL="http://www.crystallography.net/tcod/cif/40/24/00/"
LOG=/d/Project/XRD/PolyXRD-DataBase/sync_logs/sync20261019/probe.log

for i in $(seq 1 "$MAX"); do
    t0=$(date '+%F %T')
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$URL" 2>/dev/null)
    echo "$t0 round=$i http=$code" >> "$LOG"
    if [ "$code" = "200" ]; then
        echo "$t0 round=$i http=200 RECOVERED" >> "$LOG"
        echo "RECOVERED at $t0 (round $i)"
        exit 0
    fi
    sleep "$IV"
done
echo "STILL_DOWN after $MAX rounds"
exit 1
