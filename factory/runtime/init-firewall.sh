#!/usr/bin/env bash
# Egress firewall for the factory's runtime container.
#
# Allows loopback, replies to connections already made, DNS to the container's
# resolvers, and HTTPS to GitHub's published web, API and git ranges and to the
# hosts in /etc/factory/egress-allowlist. Everything else is rejected, so code an
# agent writes and runs can't send data where it likes. Runs as root from the
# entrypoint; checks itself before handing over.
set -euo pipefail

allowlist=/etc/factory/egress-allowlist
set_name=factory-egress

say() { echo "factory-firewall: $*" >&2; }

# Collect the allowed IPv4 addresses before anything is blocked.
ipset destroy "$set_name-new" 2>/dev/null || true
ipset create "$set_name-new" hash:net family inet

meta=$(curl -fsS --max-time 20 https://api.github.com/meta) || { say "can't fetch GitHub's address ranges"; exit 1; }
printf '%s' "$meta" | python3 -c '
import ipaddress, json, sys
meta = json.load(sys.stdin)
for key in ("web", "api", "git"):
    for net in meta.get(key, []):
        if ipaddress.ip_network(net).version == 4:
            print(net)
' > /tmp/factory-github-ranges
while read -r net; do
  ipset add "$set_name-new" "$net" -exist
done < /tmp/factory-github-ranges
rm -f /tmp/factory-github-ranges

hosts=0
while read -r host _; do
  case "$host" in ''|'#'*) continue ;; esac
  addrs=$(dig +short A "$host" | grep -E '^[0-9]+(\.[0-9]+){3}$' || true)
  if [ -z "$addrs" ]; then
    say "$host doesn't resolve; left out"
    continue
  fi
  for addr in $addrs; do
    ipset add "$set_name-new" "$addr/32" -exist
  done
  hosts=$((hosts + 1))
done < "$allowlist"

ipset create "$set_name" hash:net family inet -exist
ipset swap "$set_name-new" "$set_name"
ipset destroy "$set_name-new"

# The rules live in their own chain, so running this again replaces them.
resolvers=$(awk '/^nameserver/ {print $2}' /etc/resolv.conf)
iptables -N FACTORY_EGRESS 2>/dev/null || iptables -F FACTORY_EGRESS
iptables -A FACTORY_EGRESS -o lo -j ACCEPT
iptables -A FACTORY_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
for resolver in $resolvers; do
  case "$resolver" in *:*) continue ;; esac  # IPv6 resolvers are covered below
  iptables -A FACTORY_EGRESS -p udp -d "$resolver" --dport 53 -j ACCEPT
  iptables -A FACTORY_EGRESS -p tcp -d "$resolver" --dport 53 -j ACCEPT
done
iptables -A FACTORY_EGRESS -p tcp --dport 443 -m set --match-set "$set_name" dst -j ACCEPT
iptables -A FACTORY_EGRESS -j REJECT --reject-with icmp-admin-prohibited
iptables -C OUTPUT -j FACTORY_EGRESS 2>/dev/null || iptables -I OUTPUT 1 -j FACTORY_EGRESS

# No IPv6 egress at all, so it can't be a way around the list.
if ip6tables -L OUTPUT >/dev/null 2>&1; then
  ip6tables -N FACTORY_EGRESS 2>/dev/null || ip6tables -F FACTORY_EGRESS
  ip6tables -A FACTORY_EGRESS -o lo -j ACCEPT
  ip6tables -A FACTORY_EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  ip6tables -A FACTORY_EGRESS -j REJECT
  ip6tables -C OUTPUT -j FACTORY_EGRESS 2>/dev/null || ip6tables -I OUTPUT 1 -j FACTORY_EGRESS
fi

# Check both directions before anyone relies on it.
if curl -fsS --max-time 5 -o /dev/null https://example.com 2>/dev/null; then
  say "example.com is still reachable: the firewall isn't working"
  exit 1
fi
if ! curl -fsS --max-time 15 -o /dev/null https://api.github.com/zen; then
  say "api.github.com is unreachable: check the network and the allowlist"
  exit 1
fi
say "outbound traffic is limited to GitHub and $hosts allowlisted hosts"
