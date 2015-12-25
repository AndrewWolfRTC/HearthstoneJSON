#!/bin/bash
set -e

BASEDIR="$(readlink -f $(dirname $0))"
HSDATA_DIR="$BASEDIR/hs-data"
HSDATA_URL="https://github.com/HearthSim/hs-data.git"

echo "Fetching data files from $HSDATA_URL into $HSDATA_DIR"

if [ ! -e "$HSDATA_DIR" ]; then
	git clone "$HSDATA_URL" "$HSDATA_DIR"
else
	git -C "$HSDATA_DIR" fetch &&
	git -C "$HSDATA_DIR" reset --hard origin/master
fi
