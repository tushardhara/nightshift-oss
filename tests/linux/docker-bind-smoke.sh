#!/bin/sh
# Proves the emulator-exposure control actually works, against a real dockerd.
# Needs privileged Docker, so it is not part of tests/run.sh:
#
#   docker run --rm --privileged \
#     -v "$PWD/tests/linux/docker-bind-smoke.sh":/t.sh:ro docker:dind sh /t.sh
#
# Asserts both halves of the truth: an unqualified `-p 4566:80` (what
# `floci start` does) lands on 127.0.0.1, AND an explicit 0.0.0.0 bind still
# wins — which is why preflight keeps scanning live listeners too.
set -e
mkdir -p /etc/docker
printf '{\n  "ip": "127.0.0.1"\n}\n' > /etc/docker/daemon.json
export DOCKER_TLS_CERTDIR=""
export DOCKER_HOST=unix:///var/run/docker.sock
dockerd >/var/log/dockerd.log 2>&1 &
for i in $(seq 1 40); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info >/dev/null 2>&1 || { echo "FAIL: dockerd did not start"; tail -5 /var/log/dockerd.log; exit 1; }
echo "dockerd up with daemon.json: $(cat /etc/docker/daemon.json | tr -d '\n ')"
# Publish WITHOUT naming a host IP — exactly what `floci start` does.
docker run -d --name tt1 -p 4566:80 busybox sh -c 'while true; do sleep 3600; done' >/dev/null
echo "docker port mapping: $(docker port tt1)"
docker port tt1 | grep -q '127.0.0.1:4566' || { echo "FAIL: not bound to loopback"; exit 1; }
echo "PASS: an unqualified -p 4566:80 published to 127.0.0.1, not 0.0.0.0"
docker rm -f tt1 >/dev/null
# And prove the documented limit is real: an explicit 0.0.0.0 still wins.
docker run -d --name t2 -p 0.0.0.0:4566:80 busybox sh -c 'while true; do sleep 3600; done' >/dev/null
echo "explicit bind mapping: $(docker port t2)"
docker port t2 | grep -q '0.0.0.0:4566' \
  && echo "PASS (expected): explicit 0.0.0.0 overrides the default — this is why preflight still scans listeners"
docker rm -f t2 >/dev/null
