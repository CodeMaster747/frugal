#!/usr/bin/env bash
#
# First-boot bootstrap for the Frugal instance.
#
# Installs the runtime and leaves the machine ready for a deploy. It does not
# fetch the application: that needs secrets, and cloud-init user-data is stored
# unencrypted and readable by anything on the instance that can reach the
# metadata service. Deployment is a separate, authenticated step (deploy.sh).
#
# Output lands in /var/log/cloud-init-output.log.

set -euxo pipefail

# --- swap -------------------------------------------------------------------
# t3.micro has 1 GB. A Prophet fit peaks near 450 MB and OpenCV's preprocessing
# is comparable, so a worker and the API together will touch the ceiling. Swap
# turns an out-of-memory kill -- which takes the whole container down and shows
# up as a failed status check -- into a slow request.

if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >>/etc/fstab

  # Default is 60, which swaps eagerly. 10 keeps swap as the fallback it is
  # meant to be here rather than something the kernel reaches for routinely.
  sysctl -w vm.swappiness=10
  echo 'vm.swappiness=10' >/etc/sysctl.d/99-frugal-swap.conf
fi

# --- docker -----------------------------------------------------------------

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl gnupg unattended-upgrades

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  >/etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

usermod -aG docker ubuntu
systemctl enable --now docker

# Cap container logs. The json-file driver defaults to unbounded, and a chatty
# container will fill a 20 GB volume and take the application down with it --
# the disk alarm exists because this is the likeliest way that happens.
cat >/etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
JSON
systemctl restart docker

# --- unattended security updates -------------------------------------------
# Security patches only, and no automatic reboot: an unannounced reboot during a
# Celery task is worse than a patch applied a day late.

cat >/etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# --- cloudwatch agent -------------------------------------------------------
# Publishes disk and memory, which EC2 does not report on its own -- the
# hypervisor cannot see inside the guest filesystem. The disk alarm depends on
# this being alive; if the agent dies the alarm reports "missing", not "OK".

ARCH="$(dpkg --print-architecture)"
curl -fsSL -o /tmp/cw-agent.deb \
  "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/${ARCH}/latest/amazon-cloudwatch-agent.deb"
dpkg -i -E /tmp/cw-agent.deb
rm -f /tmp/cw-agent.deb

# Only the two metrics the alarms use. Every additional metric here is $0.30 a
# month, and a default agent configuration publishes dozens.
cat >/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'JSON'
{
  "agent": { "metrics_collection_interval": 300 },
  "metrics": {
    "append_dimensions": { "InstanceId": "${aws:InstanceId}" },
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent"],
        "resources": ["/"],
        "metrics_collection_interval": 300
      },
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 300
      }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/frugal/app.log",
            "log_group_name": "/frugal/app",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 7
          }
        ]
      }
    }
  }
}
JSON

mkdir -p /var/log/frugal

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# --- application directory --------------------------------------------------

mkdir -p /opt/frugal
chown ubuntu:ubuntu /opt/frugal

echo "bootstrap complete: $(date -Is)" >/opt/frugal/.bootstrapped
