#!/bin/bash
# pb-runner v3: GH Actions 上的自续 bot 运行器
#   1) .git/config extraheader 取运行时 GITHUB_TOKEN
#   2) draft release 拉私有 config.env + 状态资产（全部带超时）
#   3) 等位锁：同时只有一个 bot 在轮询
#   4) supervisor：bot 掉了重启；每 5 分钟回传 runner.log/bot.log 到 draft 资产
#   5) 本轮结束存状态 + dispatch 下一次（自续）
set -u
RUN_MIN=${RUN_MINUTES:-330}
API="https://api.github.com/repos/${GITHUB_REPOSITORY}"
log(){ echo "[runner] $(date -u +%H:%M:%S) $*"; }
# 自己的输出同时进 Actions 日志与 runner.log（后者每 5 分钟回传 draft，供外部实时看）
exec > >(tee -a runner.log) 2>&1

# ---------- 取 GITHUB_TOKEN ----------
GTOKEN=""
B64=$(git config --get-all http.https://github.com/.extraheader 2>/dev/null | awk '{print $NF}' | head -1)
if [ -n "$B64" ]; then
  DEC=$(echo "$B64" | base64 -d 2>/dev/null || true)
  case "$DEC" in x-access-token:*) GTOKEN="${DEC#x-access-token:}" ;; esac
fi
if [ -z "$GTOKEN" ]; then
  for ref in $(git config --get-regexp '^remote\..*\.url$' 2>/dev/null | awk '{print $2}'); do
    case "$ref" in https://*@*) GTOKEN=$(echo "$ref" | sed -E 's#https://[^:]+:([^@]+)@.*#\1#') ;; esac
    [ -n "$GTOKEN" ] && break
  done
fi
[ -z "$GTOKEN" ] && { log "!! 拿不到 GITHUB_TOKEN"; exit 1; }
log "GITHUB_TOKEN ok (${#GTOKEN} chars)"
AUTH="Authorization: Bearer ${GTOKEN}"
gh(){ curl -sS --max-time 25 --retry 2 --retry-delay 2 -H "$AUTH" -H "Accept: application/vnd.github+json" "$@"; }
raw(){ curl -sS -L --max-time 30 --retry 2 --retry-delay 2 -H "$AUTH" -H "Accept: application/octet-stream" "$@"; }

# ---------- draft release ----------
REL_ID=$(gh "$API/releases" | python3 -c "
import json,sys
try: rs=json.load(sys.stdin)
except Exception: rs=[]
if isinstance(rs,dict): rs=[]
d=[r for r in rs if r.get('draft')]
print(d[0]['id'] if d else '')")
[ -z "$REL_ID" ] && { log "!! 没有 draft release"; exit 1; }
log "draft release id=$REL_ID"

asset_field(){ gh "$API/releases/$REL_ID/assets?per_page=100" | python3 -c "
import json,sys
try: a=json.load(sys.stdin)
except Exception: a=[]
if isinstance(a,dict): a=[]
m=[x for x in a if x['name']=='$1']
k='$2'
print(m[0].get(k,'') if m else '')"; }

# ---------- 等位锁 ----------
MINE="${GITHUB_RUN_ID:-0}"
wait_turn(){
  local waited=0 n
  while :; do
    n=$(python3 - "$API" "$GTOKEN" "$MINE" <<'PY'
import sys, json, urllib.request
api, tok, mine = sys.argv[1], sys.argv[2], str(sys.argv[3])
def runs(status):
    req = urllib.request.Request(f"{api}/actions/workflows/main.yml/runs?status={status}&per_page=50",
        headers={'Authorization':'Bearer '+tok,'Accept':'application/vnd.github+json'})
    try: return json.load(urllib.request.urlopen(req, timeout=25)).get('workflow_runs',[])
    except Exception: return []
allr = runs('in_progress') + runs('queued')
mc = next((r['created_at'] for r in allr if str(r['id'])==mine), '')
if not mc:
    print(0); sys.exit()
import datetime
def age_h(ts):
    try:
        return (datetime.datetime.utcnow() - datetime.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ')).total_seconds()/3600.0
    except Exception:
        return 99.0
# 只认 6 小时内的 run，防僵尸 run 永久占位
print(len([r for r in allr if str(r['id'])!=mine
           and r.get('created_at','') < mc
           and age_h(r.get('created_at','')) < 6.0]))
PY
)
    [ "${n:-0}" = "0" ] && return 0
    log "等位 (更早 run=$n, 已等 ${waited}s)"
    sleep 90; waited=$((waited+90))
    [ "$waited" -ge 1200 ] && { log "等位 20min 超时，强制接棒（TG 侧 409 会兜底）"; return 0; }
  done
}
wait_turn || exit 0

# ---------- 配置与状态 ----------
CFG=$(asset_field config.env url)
[ -z "$CFG" ] && { log "!! draft 无 config.env"; exit 1; }
raw -o .env "$CFG" || { log "!! 下载 config.env 失败"; exit 1; }
KEYS=$(grep -cE '^[A-Z_]+=' .env 2>/dev/null || echo 0)
log ".env $(wc -c < .env)B / ${KEYS} 键"
[ "${KEYS:-0}" -lt 2 ] && { log "!! .env 不像配置"; head -c 100 .env; echo; exit 1; }
mkdir -p data
for name in state.json subs.json nodepool.json settings.json; do
  u=$(asset_field "$name" url)
  [ -n "$u" ] && raw -o "data/$name" "$u" && log "载入 $name ($(wc -c < "data/$name")B)"
done

# ---------- 状态回传 ----------
save_asset(){
  [ -f "$2" ] || return 0
  old=$(asset_field "$1" url)
  if [ -n "$old" ]; then gh -X DELETE "$old" > /dev/null; sleep 1; fi
  curl -sS --max-time 30 -X POST -H "$AUTH" -H "Accept: application/vnd.github+json" \
    -H "Content-Type: application/octet-stream" \
    --data-binary @"$2" "$API/releases/$REL_ID/assets?name=$1" > /dev/null
}
sync_logs(){ save_asset runner.log runner.log; save_asset bot.log bot.log; }
: > runner.log 2>/dev/null || true

# ---------- 跑 bot ----------
start_bot(){ python3 -u bot.py 2>&1 | tee -a bot.log & BOT=$!; }
start_bot
log "bot pid=$BOT, 本轮 ${RUN_MIN} 分钟"
END=$(( $(date +%s) + RUN_MIN*60 ))
RESTARTS=0; LAST_SYNC=$(date +%s)
while [ "$(date +%s)" -lt "$END" ]; do
  sleep 60
  if ! kill -0 "$BOT" 2>/dev/null; then
    RESTARTS=$((RESTARTS+1)); log "bot 掉了，重启 #$RESTARTS"
    start_bot
    [ "$RESTARTS" -ge 40 ] && { log "重启过多，提前收尾"; break; }
  fi
  if [ $(( $(date +%s) - LAST_SYNC )) -ge 300 ]; then
    log "同步日志到 draft…"; sync_logs; LAST_SYNC=$(date +%s)
  fi
done
kill "$BOT" 2>/dev/null || true
sleep 3
log "bot 已停 (重启 $RESTARTS 次)"

# ---------- 存状态 ----------
for f in state subs nodepool settings; do
  [ -f "data/$f.json" ] && save_asset "$f.json" "data/$f.json" && log "saved $f.json"
done
sync_logs

# ---------- 自续 ----------
CODE=$(curl -sS --max-time 25 -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H "Accept: application/vnd.github+json" \
  "$API/actions/workflows/main.yml/dispatches" -d '{"ref":"main"}')
log "self-dispatch -> HTTP $CODE"
log "--- bot.log 尾 ---"
tail -10 bot.log 2>/dev/null || true
