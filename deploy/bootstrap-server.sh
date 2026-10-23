#!/usr/bin/env bash
# Prepare a fresh Ubuntu server for the deploy workflow. Run it once per
# server as the user GitHub Actions logs in as; repeating it is harmless:
#
#   ssh ubuntu@<server> 'bash -s' < deploy/bootstrap-server.sh
#
# Installs Docker Engine and the compose plugin from Docker's repository, lets
# the user run them, adds swap when the server has none (SWAP_MB, default
# 2048; 0 skips it), and opens HTTPS in the host firewall of Oracle Cloud
# images, which reject everything but SSH.
set -Eeuo pipefail

user="$(id -un)"
swap_mb="${SWAP_MB:-2048}"
log() { printf '[bootstrap %s] %s\n' "$(hostname)" "$*"; }

if ! command -v docker >/dev/null; then
  log "installing Docker"
  # shellcheck source=/dev/null
  . /etc/os-release
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
  sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi
sudo systemctl enable --now docker >/dev/null 2>&1
if ! id -nG "$user" | grep -qw docker; then
  log "adding $user to the docker group"
  sudo usermod -aG docker "$user"
fi

if [[ "$swap_mb" -gt 0 ]] && [[ -z "$(swapon --show=NAME --noheadings)" ]]; then
  log "adding ${swap_mb} MiB of swap"
  sudo fallocate -l "${swap_mb}M" /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo 'vm.swappiness = 10' | sudo tee /etc/sysctl.d/90-swappiness.conf >/dev/null
  sudo sysctl -q -p /etc/sysctl.d/90-swappiness.conf
fi

# Oracle Cloud images: INPUT ends with a REJECT rule, persisted in rules.v4.
ssh_rule=(-p tcp -m state --state NEW -m tcp --dport 22 -j ACCEPT)
https_rule=(-p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT)
rules=/etc/iptables/rules.v4
if sudo test -f "$rules" && ! sudo grep -q -- '--dport 443 -j ACCEPT' "$rules"; then
  log "allowing HTTPS in $rules"
  sudo sed -i '/--dport 22 -j ACCEPT/a -A INPUT -p tcp -m state --state NEW -m tcp --dport 443 -j ACCEPT' "$rules"
fi
if sudo iptables -C INPUT "${ssh_rule[@]}" 2>/dev/null \
  && ! sudo iptables -C INPUT "${https_rule[@]}" 2>/dev/null; then
  position="$(sudo iptables -L INPUT --line-numbers -n | awk '/dpt:22$/ {print $1; exit}')"
  sudo iptables -I INPUT "$((position + 1))" "${https_rule[@]}"
fi

log "ready: $(docker --version 2>/dev/null || sudo docker --version), $(sudo docker compose version --short)"
