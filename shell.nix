{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  packages = with pkgs; [
    python312
    bluez       # runtime for bleak's BlueZ backend
    dbus        # required by bleak on Linux
  ];

  shellHook = ''
    if [ ! -d .venv ]; then
      echo "Creating .venv ..."
      python -m venv .venv
      .venv/bin/pip install --quiet --upgrade pip
      .venv/bin/pip install --quiet -e '.[dev]'
    fi
    # Activate without leaking PS1 changes if user already customised theirs.
    source .venv/bin/activate
    echo "LapTimerBLE dev shell ready. Run: laptimerble  (or: pytest)"
  '';
}
