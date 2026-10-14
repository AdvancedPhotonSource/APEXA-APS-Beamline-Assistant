"""Track output identity across one invocation, without trusting old results."""
from pathlib import Path


def output_snapshot(paths) -> dict[str, tuple[int, int]]:
    result = {}
    for path in paths:
        try:
            p = Path(path)
            stat = p.stat()
            if p.is_file():
                result[str(p)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return result


def changed_outputs(paths, before: dict) -> list[Path]:
    return [Path(path) for path, identity in output_snapshot(paths).items()
            if identity[0] > 0 and before.get(path) != identity]
