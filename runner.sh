#!/bin/bash
# pb-runner: GH Actions 上的自续 bot 运行器
#   1) 从 .git/config 提取运行时 GITHUB_TOKEN (checkout 持久化凭据)
#   2) 从 draft release 拉 config.env(私有) 与 state 资产
#   3) 后台跑 bot, 到点停, 状态存回 draft release, dispatch 下一次
set -u
RUN_MIN=${RUN_MINUTES:-330}
API="https://api.github.com/repos/${GITHUB_REPOSITORY}"
log(){ echo "[runner] $(date -u +%H:%M:%S) $*"; }

# --- 取 GITHUB_TOKEN ---
GTOKEN=""
# checkout@v3 持久化凭据在 .git/config 的 extraheader 里 (base64 of "x-access-token:TOKEN")
B64=$(git config --get-all http.https://github.com/.extraheader 2>/dev/null | awk '{print $NF}' | head -1)
if [ -n "$B64" ]; then
  DEC=$(echo "$B64" | base64 -d 2>/dev/null || true)
  case "$DEC" in
    x-access-token:*) GTOKEN="${DEC#x-access-token:}" ;;
  esac
fi
# 兜底: remote url 内嵌 / .git-credentials
if [ -z "$GTOKEN" ]; then
  for ref in $(git config --get-regexp '^remote\..*\.url$' 2>/dev/null | awk '{print $2}'); do
    case "$ref" in
      https://*@*) GTOKEN=$(echo "$ref" | sed -E 's#https://[^:]+:([^@]+)@.*#\1#') ;;
    esac
    [ -n "$GTOKEN" ] && break
  done
fi
if [ -z "$GTOKEN" ]; then
  GTOKEN=$(cat "$HOME/.git-credentials" 2>/dev/null | head -1 | sed -E 's#https://[^:]+:([^@]+)@.*#\1#')
fi
if [ -z "$GTOKEN" ]; then
  log "!! 拿不到 GITHUB_TOKEN (checkout 未持久化凭据?)"; exit 1
fi
log "GITHUB_TOKEN 已提取 (${#GTOKEN} 字符)"
AUTH="Authorization: Bearer ${GTOKEN}"
ACCEPT="Accept: application/vnd.github+json"
gh(){ curl -sS -H "$AUTH" -H "$ACCEPT" "$@"; }
raw(){ curl -sS -L -H "$AUTH" -H "Accept: application/octet-stream" "$@"; }

# --- 等位锁: 有更早的 run 在跑/排队就先等, 保证同时只有一个 bot 在轮询 ---
MINE="${GITHUB_RUN_ID:-0}"
wait_turn(){
  local waited=0
  while :; do
    local n
    n=$(python3 - "$API" "$GTOKEN" "$MINE" <<'PY'
import sys, json, urllib.request
api, tok, mine = sys.argv[1], sys.argv[2], str(sys.argv[3])
def runs(status):
    req = urllib.request.Request(f"{api}/actions/workflows/main.yml/runs?status={status}&per_page=50",
                                 headers={'Authorization': 'Bearer ' + tok,
                                          'Accept': 'application/vnd.github+json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30)).get('workflow_runs', [])
    except Exception:
        return []
allr = runs('in_progress') + runs('queued')
mine_created = next((r['created_at'] for r in allr if str(r['id']) == mine), '')
if not mine_created:
    print(0); sys.exit()
print(len([r for r in allr if str(r['id']) != mine and r.get('created_at', '') < mine_created]))
PY
)
    [ "${n:-0}" = "0" ] && return 0
    log "等位中 (更早的 run=$n, 已等 ${waited}s)"
    sleep 120; waited=$((waited+120))
    if [ "$waited" -ge 10800 ]; then log "等位超时 3h，让位退出"; return 1; fi
  done
}
wait_turn || exit 0

# --- 找 draft release ---
REL_ID=$(gh "$API/releases" | python3 -c "
import json,sys
try: rs=json.load(sys.stdin)
except Exception: rs=[]
if isinstance(rs,dict): rs=[]
d=[r for r in rs if r.get('draft')]
print(d[0]['id'] if d else '')")
if [ -z "$REL_ID" ]; then
  log "!! 没有 draft release"; exit 1
fi
log "draft release id=$REL_ID"

asset_url(){ gh "$API/releases/$REL_ID/assets?per_page=100" | python3 -c "
import json,sys
try: a=json.load(sys.stdin)
except Exception: a=[]
if isinstance(a,dict): a=[]
m=[x for x in a if x['name']=='$1']
print(m[0]['url'] if m else '')"; }

# --- 拉配置与状态 ---
CFG=$(asset_url config.env)
if [ -z "$CFG" ]; then log "!! draft 里没有 config.env"; exit 1; fi
raw -o .env "$CFG"
mkdir -p data
for name in state.json subs.json nodepool.json settings.json; do
  u=$(asset_url "$name")
  [ -n "$u" ] && raw -o "data/$name" "$u"
done
KEYS=$(grep -cE '^[A-Z_]+=' .env 2>/dev/null || echo 0)
log "config+state 就绪 (.env $(wc -c < .env)B, ${KEYS} 个键)"
if [ "$KEYS" -lt 1 ]; then log "!! .env 不像配置，可能是下载失败"; head -c 120 .env; echo; exit 1; fi

# --- 跑 bot（输出实时进 Actions 日志 + bot.log），掉了自动重启 ---
start_bot(){ python3 -u bot.py 2>&1 | tee -a bot.log & BOT=$!; }
start_bot
log "bot pid=$BOT, 本轮跑 ${RUN_MIN} 分钟"
END=$(( $(date +%s) + RUN_MIN*60 ))
RESTARTS=0
while [ "$(date +%s)" -lt "$END" ]; do
  sleep 60
  if ! kill -0 "$BOT" 2>/dev/null; then
    RESTARTS=$((RESTARTS+1))
    log "bot 掉了，重启 (第 $RESTARTS 次)"
    start_bot
    if [ "$RESTARTS" -ge 40 ]; then log "重启过多，提前收尾"; break; fi
  fi
done
kill "$BOT" 2>/dev/null || true
sleep 3
log "bot 已停 (重启次数=$RESTARTS)"

# --- 状态存回 draft ---
save_asset(){
  [ -f "$2" ] || return 0
  old=$(asset_url "$1")
  if [ -n "$old" ]; then
    gh -X DELETE "$old" > /dev/null
    sleep 1
  fi
  curl -sS -X POST -H "$AUTH" -H "$ACCEPT" -H "Content-Type: application/octet-stream" \
    --data-binary @"$2" "$API/releases/$REL_ID/assets?name=$1" > /dev/null
  log "saved $1 ($(( $(wc -c < "$2") ))B)"
}
save_asset state.json data/state.json
save_asset subs.json data/subs.json
save_asset nodepool.json data/nodepool.json
save_asset settings.json data/settings.json

# --- 自续 ---
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H "$ACCEPT" \
  "$API/actions/workflows/main.yml/dispatches" -d '{"ref":"main"}')
log "self-dispatch main.yml -> HTTP $CODE"
log "--- bot.log 尾部 ---"
tail -8 bot.log 2>/dev/null || true
