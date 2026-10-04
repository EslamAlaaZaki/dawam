#!/bin/sh
# Writes which addresses may tell `edge` who the client is, from
# DAWAM_TRUSTED_PROXY_CIDRS (comma- or space-separated IPs or networks of a
# TLS-terminating proxy in front of DAWAM). The image's entrypoint runs this before
# it renders /etc/nginx/templates; envsubst cannot loop over a list, hence a script.
#
# - Unset (the default): no one. Every client's X-Forwarded-* is ignored; the client
#   is the connection's address and the scheme is http.
# - Set: for connections from those addresses, nginx's realip module takes the client
#   from X-Forwarded-For (the right-most untrusted entry), and their
#   X-Forwarded-Proto is believed.
set -eu

out=/etc/nginx/conf.d/00-dawam-trusted-proxies.conf
cidrs=$(printf '%s' "${DAWAM_TRUSTED_PROXY_CIDRS:-}" | tr ',' ' ')

{
  echo "# Written by $(basename "$0") from DAWAM_TRUSTED_PROXY_CIDRS."
  echo 'geo $realip_remote_addr $dawam_from_trusted_proxy {'
  echo '    default 0;'
  for cidr in $cidrs; do
    case "$cidr" in
      *[!0-9A-Fa-f.:/]*)
        echo "$0: DAWAM_TRUSTED_PROXY_CIDRS: '$cidr' is not an IP address or network" >&2
        exit 1
        ;;
    esac
    echo "    $cidr 1;"
  done
  echo '}'
  if [ -n "$cidrs" ]; then
    for cidr in $cidrs; do
      echo "set_real_ip_from $cidr;"
    done
    echo 'real_ip_header X-Forwarded-For;'
    echo 'real_ip_recursive on;'
  fi
} > "$out"

echo "$0: trusted proxies: ${cidrs:-none}"
