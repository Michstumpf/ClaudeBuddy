# claudea: Claude Code with every permission, inside a tmux session named after
# the folder, so the Buddy can dictate into it (the hub types through tmux).
#
#   source ~/personal/ClaudeBuddy/deploy/shell/claude-tmux.sh   (in ~/.bashrc)
#   claudea            # in ~/projetos/datahub: tmux session "datahub"
#   claudea /resume    # arguments go to claude
#   BUDDY_SESSION=hipaa claudea   # choose the name
#
# Already in tmux (or no tmux installed): just runs claude. When claude exits,
# the tmux session closes with it.
claudea() {
  if [ -n "$TMUX" ] || ! command -v tmux >/dev/null 2>&1; then
    command claude --dangerously-skip-permissions "$@"
    return
  fi
  local base name n=2
  base="${BUDDY_SESSION:-$(basename "$PWD")}"
  base="$(printf '%s' "$base" | tr -c 'A-Za-z0-9_-' '-' | sed 's/^-*//; s/-*$//')"
  name="${base:-claude}"
  # Another claudea in the same folder: datahub-2, datahub-3…
  while tmux has-session -t "=$name" 2>/dev/null; do
    name="$base-$n"
    n=$((n + 1))
  done
  tmux new-session -s "$name" -c "$PWD" claude --dangerously-skip-permissions "$@"
}
