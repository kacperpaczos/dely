#!/usr/bin/env bash
# Everything the tool image carries, pinned and checked.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
# Tells the tools that read it that no one is watching this shell.
export CI=1

cloud-init status --wait || true

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg jq git rsync xz-utils \
  qemu-guest-agent \
  xserver-xorg-core xserver-xorg-video-vesa xserver-xorg-video-qxl \
  xserver-xorg-input-libinput xinit openbox xterm xdotool \
  dbus-x11 x11-xserver-utils xdg-utils \
  libgtk-3-0t64 libnss3 libasound2t64 libatk-bridge2.0-0t64 libcups2t64 \
  libdrm2 libgbm1 libxkbcommon0 libpango-1.0-0 libxcomposite1 libxdamage1 \
  libxfixes3 libxrandr2 libsecret-1-0

# -- node, at the pinned version and digest ------------------------------
# Ubuntu's own node is 18, and the skills command line fails on it with a
# module error, so the image carries the pinned tarball instead of the package.
curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz"
echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c -
sudo mkdir -p /usr/local/node
sudo tar -xJf /tmp/node.tar.xz -C /usr/local/node --strip-components=1
rm -f /tmp/node.tar.xz
for binary in node npm npx; do
  sudo ln -sf "/usr/local/node/bin/${binary}" "/usr/local/bin/${binary}"
done
node --version

# -- orca, at the pinned version and digest ------------------------------
curl -fsSL -o /tmp/orca.deb "${ORCA_URL}"
echo "${ORCA_SHA256}  /tmp/orca.deb" | sha256sum -c -
sudo apt-get install -y /tmp/orca.deb
rm -f /tmp/orca.deb
test -x /opt/Orca/resources/bin/orca-ide
sudo ln -sf /opt/Orca/resources/bin/orca-ide /usr/local/bin/orca

# -- claude code, at the pinned version ----------------------------------
sudo npm install -g --no-fund --no-audit "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"
sudo ln -sf /usr/local/node/bin/claude /usr/local/bin/claude
claude --version

# -- the skills the agent must be able to reach --------------------------
# Pinned by commit, because a tag moves. The plugin is installed for the guest
# user, which is the home the run uses.
#
# Every step below is bounded. An earlier build stopped here for as long as it
# was allowed to, with nothing said; a step that cannot finish has to fail and
# say what it was doing, not hold the build open.
#
# The agent is asked its first-run questions here rather than being left to ask
# them: a fresh home has not completed onboarding, and a command that waits for
# that answer waits forever when nobody is attached.
mkdir -p "${HOME}/.claude"
if [ ! -f "${HOME}/.claude.json" ]; then
  printf '{"hasCompletedOnboarding": true}\n' > "${HOME}/.claude.json"
fi
sudo mkdir -p /opt/dely-cycle
sudo chown "$(id -u):$(id -g)" /opt/dely-cycle
git clone -q --filter=blob:none "${SUPERPOWERS_REPOSITORY}" /opt/dely-cycle/superpowers
git -C /opt/dely-cycle/superpowers checkout -q "${SUPERPOWERS_REVISION}"
test "$(git -C /opt/dely-cycle/superpowers rev-parse HEAD)" = "${SUPERPOWERS_REVISION}"
# Runs one command with stdin closed and a deadline, and says which it was.
# Stdin closed is not the fix for the stall seen here — the same command answers
# with a terminal attached — it is what keeps an unattended shell unattended.
bounded() {
  seconds="$1"
  shift
  log="$(mktemp)"
  echo "--- running (limit ${seconds}s): $*"
  # `if cmd; then ... fi` leaves $? at the *if statement's* status, which is 0
  # when the condition was false. Reading it there reports every failure as a
  # success, so the status is taken directly from the command.
  set +e
  timeout --signal=TERM --kill-after=30 "${seconds}" "$@" < /dev/null > "${log}" 2>&1
  status=$?
  set -e
  sed 's/^/    | /' "${log}"
  rm -f "${log}"
  if [ "${status}" -eq 0 ]; then
    echo "--- done: $*"
    return 0
  fi
  if [ "${status}" -eq 124 ]; then
    echo "gave up after ${seconds}s: $*" >&2
  else
    echo "exited ${status}: $*" >&2
  fi
  return "${status}"
}

# What the guest had to work with when these ran, so a failure with no message
# can still be read afterwards.
free -m || true
df -h / || true

# Superpowers is a skills library, and its skills go where an agent reads them.
#
# `claude plugin marketplace add` is the documented way and it is not used here:
# in this guest it hangs. Given ten minutes and stdin closed it produced no
# output and never returned, where the same command against the same checkout
# answers in under a second in a container, with and without a terminal, with
# and without Orca installed, as root and as an ordinary user. What differs is
# the guest, and that is not established. So the image does not depend on it.
#
# What this loses is the plugin's own wiring — its session hook among it. The
# skills are here and are the pinned bytes; the hook is not, and nothing here
# should be read as saying otherwise.
mkdir -p "${HOME}/.claude/skills"
for directory in /opt/dely-cycle/superpowers/skills/*/; do
  [ -f "${directory}SKILL.md" ] || continue
  cp -r "${directory}" "${HOME}/.claude/skills/"
done
ls "${HOME}/.claude/skills"

# Orca's own skills. `orca skills install` resolves to this command but passes
# --agent claude, which the skills command line rejects; it accepts claude-code.
bounded 900 npx --yes skills add https://github.com/stablyai/orca \
  --skill orca-cli --skill orchestration --global --agent claude-code -y

# What the image carries is what the run will be held to, so check it here too.
for entry in ${SKILL_DIGESTS}; do
  name="${entry%%=*}"
  want="${entry#*=}"
  found="$(sha256sum "${HOME}/.claude/skills/${name}/SKILL.md" | cut -d' ' -f1)"
  if [ "${found}" != "${want}" ]; then
    echo "skill ${name} has digest ${found}, not the pinned ${want}" >&2
    exit 4
  fi
  echo "skill ${name} matches its pin"
done

# -- a graphical session so the orca window has somewhere to appear -------
sudo tee /etc/systemd/system/dely-cycle-desktop.service > /dev/null <<UNIT
[Unit]
Description=Minimal graphical session for the dely cycle
After=systemd-user-sessions.service
Conflicts=getty@tty1.service

[Service]
User=${GUEST_USER}
PAMName=login
Type=simple
# Without a controlling terminal the display server cannot take the virtual
# terminal and dies with "Switching VT failed".
StandardInput=tty
StandardOutput=journal
StandardError=journal
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
Environment=XDG_RUNTIME_DIR=/run/user/1000
WorkingDirectory=/home/${GUEST_USER}
ExecStart=/usr/bin/startx /usr/bin/openbox-session -- :0 vt1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
UNIT

# The application is deliberately NOT started by the window manager. Launched
# from the autostart it left only a crash directory and a stale singleton lock
# and the runtime never appeared; launched as a detached command once the
# display is up, it reaches "ready". Starting it is the runner's step, so it is
# one observable command with a recorded outcome.

sudo systemctl set-default graphical.target
sudo systemctl enable dely-cycle-desktop.service
sudo systemctl enable qemu-guest-agent

# -- record what this image is ------------------------------------------
sudo tee /etc/dely-cycle-image > /dev/null <<MANIFEST
orca_version=$(dpkg-query -W -f='${Version}' orca-ide)
claude_code_version=$(claude --version | awk '{print $1}')
node_version=$(node --version)
superpowers_revision=$(git -C /opt/dely-cycle/superpowers rev-parse HEAD)
skills=$(ls "${HOME}/.claude/skills" | tr '\n' ',')
ubuntu=$(lsb_release -ds)
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MANIFEST
sudo chmod 0444 /etc/dely-cycle-image
cat /etc/dely-cycle-image

# -- leave no build credential behind ------------------------------------
sudo rm -f "/home/${GUEST_USER}/.ssh/authorized_keys"
sudo rm -rf "/home/${GUEST_USER}/.ssh"
# Nothing the application wrote during the build belongs in the image. The
# agent's own directory stays: it is where the skills were installed.
sudo rm -rf "/home/${GUEST_USER}/.config/orca"
# A login never happened during this build, and the image must be able to say so.
if [ -e "/home/${GUEST_USER}/.claude/.credentials.json" ]; then
  echo "the build left a credential in the image" >&2
  exit 5
fi
sudo cloud-init clean --logs --seed
sudo rm -rf /var/lib/cloud/instances /var/lib/cloud/instance
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/dbus/machine-id
sudo apt-get clean
sudo rm -rf /var/lib/apt/lists/*
echo "provisioning complete"
