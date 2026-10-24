# Optional pinned dependency environment. Static-reviewed here; NOT executed with this merged entry.
# R1 supplied existing-host pure-shell evidence only; see sources.json and docs/R1-INTEGRATION.md.
{ source ? builtins.fetchTarball {
    url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1069601.e9b9cbecbf6c/nixexprs.tar.xz";
    sha256 = "sha256-kkWAS4mE+fmi+u3mxwD9/bfX6DGJ49Tv8O9kzN5r0HQ=";
  }
}:
let
  pkgs = import source { system = "x86_64-linux"; };
in
assert pkgs.python313.version == "3.13.15";
assert pkgs.ffmpeg.version == "9.0.1";
assert pkgs.imagemagick.version == "7.1.2-29";
pkgs.mkShell {
  packages = [
    pkgs.python313 pkgs.ffmpeg pkgs.imagemagick
    pkgs.curl pkgs.cacert pkgs.unzip pkgs.coreutils pkgs.tzdata
  ];
  shellHook = ''
    export PYTHONTZPATH="${pkgs.tzdata}/share/zoneinfo"
    export LANG=C.UTF-8
    export MYTV_RECIPE_REVISION=e9b9cbecbf6c25ca26c150d78570e6332694a129
    # Host kernel driver is not installed or changed by this shell.
    if [ -d /run/opengl-driver/lib ]; then
      if [ -n "$LD_LIBRARY_PATH" ]; then
        export LD_LIBRARY_PATH="/run/opengl-driver/lib:$LD_LIBRARY_PATH"
      else
        export LD_LIBRARY_PATH="/run/opengl-driver/lib"
      fi
    fi
  '';
}
