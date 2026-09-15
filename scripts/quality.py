"""Run the local Python quality checks against the synchronized environment."""

import subprocess
import sys


def main() -> int:
    checks = (
        ("Ruff lint", ("ruff", "check", "src", "tests", "scripts")),
        ("Ruff format", ("ruff", "format", "--check", "src", "tests", "scripts")),
        ("Strict mypy", ("mypy",)),
        ("Complete pytest suite", ("pytest",)),
    )

    for name, arguments in checks:
        print(f"Running {name}: {' '.join(arguments)}", flush=True)
        result = subprocess.run([sys.executable, "-P", "-m", *arguments], check=False)
        if result.returncode != 0:
            print(
                f"Failed {name} with exit code {result.returncode}",
                file=sys.stderr,
                flush=True,
            )
            return result.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
