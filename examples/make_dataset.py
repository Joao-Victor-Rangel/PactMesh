"""Generate the synthetic dataset used in the demo (seeded, reproducible)."""

import random
import sys


def make(rows: int = 500, seed: int = 7) -> bytes:
    rnd = random.Random(seed)
    lines = ["id,region,latency_ms"]
    for i in range(rows):
        v = "" if i % 97 == 0 else f"{rnd.gauss(120, 25):.3f}"  # some missing values on purpose
        lines.append(f"{i},{rnd.choice(['sa', 'na', 'eu'])},{v}")
    return ("\n".join(lines) + "\n").encode()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "examples/dataset.csv"
    with open(out, "wb") as f:
        f.write(make())
    print(out)
