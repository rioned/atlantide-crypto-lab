#!/bin/bash
# Update flow diagram when strategy source files change.
# Compares timestamps of source files vs the diagram.
# If sources are newer, prints a diff and exits 1 to signal "needs update".
# Run manually: ./update-flow-diagram.sh --check   (check if outdated)
# Run manually: ./update-flow-diagram.sh --rebuild (placeholder — run hermes manually to regenerate)

DIAGRAM="$HOME/workspace/crypto-lab2/static/crypto-lab2-flow.html"
SOURCES=(
  "$HOME/workspace/crypto-lab2/app/strategy.py"
  "$HOME/workspace/crypto-lab2/app/execution.py"
  "$HOME/workspace/crypto-lab2/app/config.py"
  "$HOME/workspace/crypto-lab2/app/self_learning.py"
  "$HOME/workspace/crypto-lab2/app/indicators.py"
)

case "${1:-check}" in
  --check)
    if [ ! -f "$DIAGRAM" ]; then
      echo "MISSING: $DIAGRAM"
      exit 1
    fi
    for src in "${SOURCES[@]}"; do
      if [ "$src" -nt "$DIAGRAM" ]; then
        echo "OUTDATED: $(basename $src) modified $(date -r "$src" '+%Y-%m-%d %H:%M')"
        echo "  → Diagram was last updated $(date -r "$DIAGRAM" '+%Y-%m-%d %H:%M')"
        echo "  → Run 'hermes update flow diagram' to regenerate"
        exit 1
      fi
    done
    echo "OK — diagram is current"
    exit 0
    ;;
  --rebuild)
    echo "Regeneration requires LLM analysis."
    echo "Ask Hermes: 'update the crypto-lab2 flow diagram'"
    exit 0
    ;;
  *)
    echo "Usage: $0 [--check | --rebuild]"
    exit 1
    ;;
esac
