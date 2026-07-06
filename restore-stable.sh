#!/bin/bash
# Restore the stable working build in case of emergency
cp /opt/ares/Vantage/dist-stable/index.html /opt/ares/Vantage/frontend/dist/index.html
cp /opt/ares/Vantage/dist-stable/index-latest.js /opt/ares/Vantage/frontend/dist/assets/index-latest.js
cp /opt/ares/Vantage/dist-stable/index-latest.css /opt/ares/Vantage/frontend/dist/assets/index-latest.css
echo 'Stable build restored'
