#!/usr/bin/env bash
# Network policy for outbound traffic from the containers that fetch Internet content
# (api + worker on bridge ri-egress, the browser fleet on ri-browser).
# Drops every private, loopback, link-local (cloud metadata 169.254.169.254), CGNAT and
# multicast destination. The in-code checks (resolve-then-verify, per-redirect checks) are
# the first layer; this one also holds against DNS rebinding.
# Run as root after Docker has started and before the stack (or from a systemd unit).
set -euo pipefail
CHAIN=REPINTEL-EGRESS
BLOCKED=(0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12
         192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4)

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
# Replies to connections initiated from outside (Caddy → api) are not affected.
iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
# Docker's embedded resolver (127.0.0.11) forwards queries from the container's namespace to
# the host's upstream resolvers, so those cross this chain. Providers often put them on
# private addresses (Scaleway, some OVH images): allow DNS to exactly those, nothing else.
RESOLV=/run/systemd/resolve/resolv.conf; [ -r "$RESOLV" ] || RESOLV=/etc/resolv.conf
for ns in $(awk '/^nameserver/ {print $2}' "$RESOLV" | grep -v ':' | grep -v '^127\.'); do
  iptables -A "$CHAIN" -d "$ns" -p udp --dport 53 -j RETURN
  iptables -A "$CHAIN" -d "$ns" -p tcp --dport 53 -j RETURN
done
for net in "${BLOCKED[@]}"; do
  iptables -A "$CHAIN" -d "$net" -j DROP
done
iptables -A "$CHAIN" -j RETURN

for br in ri-egress ri-browser; do
  iptables -C DOCKER-USER -i "$br" -j "$CHAIN" 2>/dev/null || iptables -I DOCKER-USER -i "$br" -j "$CHAIN"
done

# IPv6: the compose networks are IPv4-only; block forwarding from these bridges entirely.
if command -v ip6tables >/dev/null; then
  for br in ri-egress ri-browser; do
    ip6tables -C FORWARD -i "$br" -j DROP 2>/dev/null || ip6tables -I FORWARD -i "$br" -j DROP
  done
fi
echo "egress policy applied to ri-egress and ri-browser"
