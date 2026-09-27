#!/bin/sh
# Runs inside the tools container. Mirrors redis/redis into Gitea as poc/redis.
set -eu
GITEA_URL=${GITEA_URL:-http://gitea:3000}
USER=${GITEA_ADMIN_USER:-poc}
PASS=${GITEA_ADMIN_PASS:-poc-password-123}
SRC=${SRC_REPO:-https://github.com/redis/redis.git}
BARE=/work/redis.git

until curl -sf "$GITEA_URL/api/healthz" >/dev/null; do sleep 1; done

if [ ! -d "$BARE" ]; then
  git clone --bare "$SRC" "$BARE"
fi
git -C "$BARE" fetch origin '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*' --prune

code=$(curl -s -o /dev/null -w '%{http_code}' -u "$USER:$PASS" "$GITEA_URL/api/v1/repos/$USER/redis")
if [ "$code" = 404 ]; then
  curl -sf -u "$USER:$PASS" -H 'Content-Type: application/json' \
    -d '{"name":"redis","private":false,"default_branch":"unstable"}' \
    "$GITEA_URL/api/v1/user/repos" >/dev/null
fi

host=${GITEA_URL#http://}
git -C "$BARE" push "http://$USER:$PASS@$host/$USER/redis.git" \
  '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'

# Default branch must be unstable (redis' default).
curl -sf -u "$USER:$PASS" -X PATCH -H 'Content-Type: application/json' \
  -d '{"default_branch":"unstable"}' "$GITEA_URL/api/v1/repos/$USER/redis" >/dev/null

echo "commits on unstable: $(git -C "$BARE" rev-list --count unstable)"
echo "branches: $(git -C "$BARE" for-each-ref refs/heads | wc -l)  tags: $(git -C "$BARE" for-each-ref refs/tags | wc -l)"
