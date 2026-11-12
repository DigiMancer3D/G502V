#!/usr/bin/env bash
set -euo pipefail

RULE="/etc/udev/rules.d/70-g502v-input-remapper-uaccess.rules"
EXPECTED_USER="${SUDO_USER:-${USER:-$(id -un)}}"

# This script is intended to be started as the desktop user, not with sudo.
if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    EXPECTED_USER="$SUDO_USER"
fi

USER_GROUP="$(id -gn "$EXPECTED_USER")"
if [[ -z "$USER_GROUP" ]]; then
    echo "ERROR: could not determine primary group for $EXPECTED_USER"
    exit 2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

cat > "$TMP" <<RULES
# G502V alpha.6 — boot-persistent narrow access.
#
# Unlike TAG+="uaccess" alone, GROUP="$USER_GROUP" does not depend on the
# graphical login session already existing when Input Remapper creates its
# uinput devices during boot.
#
# Scope remains narrow:
#   1) Input Remapper virtual outputs; and
#   2) ONLY Input-Remapper-owned Logitech G502 proxy devices; and
#   3) actual Logitech G502 HERO (046d:c08b) event nodes, if readable.
#
# It does not match unrelated physical mice or keyboards.
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="input-remapper*", GROUP="$USER_GROUP", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{phys}=="input-remapper/*", ATTRS{name}=="Logitech G502*", GROUP="$USER_GROUP", MODE="0660", TAG+="uaccess"
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{id/vendor}=="046d", ATTRS{id/product}=="c08b", ATTRS{name}=="Logitech G502*", GROUP="$USER_GROUP", MODE="0660", TAG+="uaccess"
RULES

echo "G502V global-input access setup — alpha.6"
echo "User : $EXPECTED_USER"
echo "Group: $USER_GROUP"
echo "Rule : $RULE"
echo
echo "This grants only that user's primary group read/write access to:"
echo "  - Input Remapper virtual outputs"
echo "  - Input Remapper-owned Logitech G502 proxy devices"
echo "  - Exact Logitech G502 HERO 046d:c08b hardware nodes"
echo
echo "It does NOT add the user to Linux's broad 'input' group."
echo "It does NOT make ordinary keyboards world-readable."
echo "Running this script RESTARTS Input Remapper; don't run during a game or stream."
echo "If the existing device permissions work, you do NOT need this script."
echo "It is designed to remain valid after a reboot."
echo
read -r -p "Install/update this rule with sudo? [y/N] " answer
case "$answer" in
  y|Y|yes|YES) ;;
  *) echo "Cancelled; nothing changed."; exit 0 ;;
esac

sudo install -m 0644 "$TMP" "$RULE"
sudo udevadm control --reload-rules

echo
echo "Restarting Input Remapper so the new ownership is applied now..."
sudo systemctl restart input-remapper.service
sleep 2

# Restore Input Remapper's configured autoload only as part of this explicit
# permission repair operation. G502V's normal run.sh never changes profiles.
if command -v input-remapper-control >/dev/null 2>&1; then
    input-remapper-control --command autoload || true
fi

echo
echo "Installed rule:"
cat "$RULE"
echo
echo "PASS: boot-persistent narrow G502V/Input Remapper access installed."
echo "Next:"
echo "  bash ./doctor.sh"
echo "  ./run.sh"
