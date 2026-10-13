#!/bin/sh
set -eu
if [ "${ADMIN_GATE:-off}" = on ]; then
  if [ -z "${ADMIN_USER:-}" ] || [ -z "${ADMIN_PASS:-}" ]; then
    echo 'ADMIN_GATE=on requires ADMIN_USER and ADMIN_PASS' >&2
    exit 1
  fi
  printf '%s:%s\n' "$ADMIN_USER" "$(openssl passwd -apr1 "$ADMIN_PASS")" > /etc/nginx/admin.htpasswd
  printf '%s\n' 'auth_basic "관제";' 'auth_basic_user_file /etc/nginx/admin.htpasswd;' > /etc/nginx/admin_gate.conf
else
  printf '%s\n' '# 관리자 인증 꺼짐' > /etc/nginx/admin_gate.conf
fi
