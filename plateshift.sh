#!/bin/sh
# opens the plateshift window; give .3mf files to convert them straight away
cd "$(dirname "$0")" && exec python3 -m plateshift "$@"
