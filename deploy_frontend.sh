#!/usr/bin/env bash
set -euo pipefail

cd /root/gym_predictor

# Use the currently checked-out commit as the asset version.
VERSION=$(git rev-parse --short HEAD)

# Back up the existing production frontend.
mkdir -p /root/gymcast-backups
tar -czf "/root/gymcast-backups/frontend-${VERSION}-$(date +%Y%m%d-%H%M%S).tar.gz" \
  -C /var/www/gymcast .

# Publish the frontend.
rsync -av --checksum web/ /var/www/gymcast/

# Add cache-busting versions to the deployed HTML.
sed -i -E \
  -e "s|href=\"styles.css(\?v=[^\"]*)?\"|href=\"styles.css?v=${VERSION}\"|" \
  -e "s|src=\"app.js(\?v=[^\"]*)?\"|src=\"app.js?v=${VERSION}\"|" \
  /var/www/gymcast/index.html

echo "Deployed GymCast frontend version ${VERSION}"
