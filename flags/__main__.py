"""python -m flags replay BASE CANDIDATE [--seed N] [--overall-only]   one rollout, request by request
python -m flags bench [--runs N]                                       every upgrade and A/A, written to results/
python -m flags serve CONFIG [--port P] [--state FILE]                 the HTTP API"""
import argparse
import sys

from .canary import Canary
from .core import Platform
from .replay import ROLLOUT, bench, live, load_pool
from .server import make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flags")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("base")
    r.add_argument("candidate")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--overall-only", action="store_true")
    b = sub.add_parser("bench")
    b.add_argument("--runs", type=int, default=100)
    s = sub.add_parser("serve")
    s.add_argument("config")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--state")
    args = ap.parse_args(argv)

    if args.cmd == "bench":
        print(bench(args.runs))
    elif args.cmd == "serve":
        server = make_server(Platform.load(args.config, args.state), args.host, args.port)
        print(f"serving on http://{args.host}:{server.server_address[1]}")
        server.serve_forever()
    else:
        pool = load_pool(args.base, args.candidate)
        canary = Canary(**ROLLOUT, segments=[] if args.overall_only else pool.tasks)
        print(f"{args.base} → {args.candidate}: {len(pool.base):,} questions, control {pool.base.mean():.1%}, "
              f"candidate {pool.cand.mean():.1%}")
        out = live(pool, canary, args.seed)
        for e in canary.log:
            print(f"  request {e['at']:>6,}: {e['event']}")
        horizon = len(canary.stages) * canary.stage_requests
        print(f"The candidate served {out['served']:,} requests with {out['extra_wrong']:,} more wrong answers than control "
              f"would have given. Switching everyone over the same {horizon:,} requests: about "
              f"{horizon * (pool.base.mean() - pool.cand.mean()):,.0f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
