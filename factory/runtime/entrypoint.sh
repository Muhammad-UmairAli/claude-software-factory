#!/bin/sh
# Entrypoint of the factory's runtime container.
#
# Runs as root just long enough to install the egress firewall, then becomes the
# `node` user for good, with no capabilities left in any set. factory/bin/runtime
# starts the container with --security-opt no-new-privileges, so nothing that
# user runs, agent-written code included, can get root back to loosen the rules.
set -eu
/usr/local/sbin/factory-firewall
export HOME=/home/node USER=node LOGNAME=node
exec setpriv --reuid=node --regid=node --init-groups --inh-caps=-all --bounding-set=-all -- "$@"
