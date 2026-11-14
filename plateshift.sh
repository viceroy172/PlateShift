#!/bin/sh
# opens the plateshift window; give .3mf files to convert them straight away.
# no cd: relative file names must still point where the user meant
here="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m plateshift "$@"
