"""Thin CLI over the same contract as the (future) HTTP API. Output is JSON."""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from hwreview import db
from hwreview.adapters.fake import FakeAdapter
from hwreview.adapters.openai_compat import OpenAICompatAdapter, TokenBudget
from hwreview.batches import create_batch, load_manifest
from hwreview.config import DEFAULT_LIMITS, Limits
from hwreview.errors import HWError
from hwreview.jobs import Queue
from hwreview.storage import BlobStore
from hwreview.worker import Worker

ADAPTER_CHOICES = ("fake", "openrouter")


def _limits() -> Limits:
    cap = os.environ.get("HWREVIEW_TOKEN_CAP")
    return Limits(**{**DEFAULT_LIMITS.__dict__, "token_cap": int(cap) if cap else None})


def make_adapter(name: str, conn, limits: Limits):
    """Live provider settings come only from the environment; values are never printed."""
    if name == "fake":
        return FakeAdapter()
    missing = [v for v in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "HWREVIEW_TOKEN_CAP")
               if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"live provider gate: set {', '.join(missing)} (values are never printed)")
    model = os.environ["OPENROUTER_MODEL"]
    return OpenAICompatAdapter(model=model, api_key=os.environ.get("OPENROUTER_API_KEY"),
                               budget=TokenBudget(conn, limits.token_cap), limits=limits)


def _open(args):
    data = Path(args.data_dir)
    return db.connect(data / "hw.sqlite3"), BlobStore(data / "blobs")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_batch_create(args):
    conn, store = _open(args)
    adapter = make_adapter(args.adapter, conn, _limits())
    spec, refs, subs = load_manifest(Path(args.manifest))
    batch = create_batch(conn, store, spec, refs, subs, adapter.config_id)
    _print({"batch_id": batch.batch_id,
            "items": [{k: (v.model_dump() if hasattr(v, "model_dump") else v)
                       for k, v in asdict(i).items()} for i in batch.items]})


def cmd_batch_show(args):
    conn, _ = _open(args)
    rows = conn.execute("SELECT * FROM batch_items WHERE batch_id = ? ORDER BY idx",
                        (args.batch_id,)).fetchall()
    queue = Queue(conn)
    items = []
    for r in rows:
        item = dict(r)
        if r["job_id"]:
            job = queue.get(r["job_id"])
            item.update(job_state=job.state, result_id=job.result_id,
                        result_version=job.result_version, job_error=job.error_code)
            if job.result_id:
                item["review_state"] = queue.get_result(job.result_id, job.result_version).review_state
        items.append(item)
    _print({"batch_id": args.batch_id, "items": items})


def cmd_worker_run(args):
    conn, store = _open(args)
    limits = _limits()
    worker = Worker(conn, store, make_adapter(args.adapter, conn, limits), limits=limits)
    if args.once:
        _print({"processed": worker.drain()})
    else:
        worker.run_forever(poll_s=args.poll)


def cmd_job_show(args):
    conn, _ = _open(args)
    job = Queue(conn).get(args.job_id)
    _print({**asdict(job), "versions": job.versions.model_dump()})


def cmd_job_cancel(args):
    conn, _ = _open(args)
    _print({"job_id": args.job_id, "state": Queue(conn).cancel(args.job_id)})


def cmd_job_rerun(args):
    conn, _ = _open(args)
    _print({"job_id": Queue(conn).rerun(args.job_id)})


def cmd_result_show(args):
    conn, _ = _open(args)
    result = Queue(conn).get_result(args.result_id, args.version)
    out = result.model_dump(mode="json")
    if args.with_evidence:
        ids = {e for issue in result.issues for e in issue.evidence_ids}
        rows = conn.execute(
            f"SELECT span_id, page, paragraph, text, status FROM spans WHERE span_id IN "
            f"({','.join('?' * len(ids))})", tuple(ids)).fetchall() if ids else []
        out["evidence"] = [dict(r) for r in rows]
    _print(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hwreview", description=__doc__)
    p.add_argument("--data-dir", default="data", help="private data folder (default: ./data)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="group", required=True)

    batch = sub.add_parser("batch").add_subparsers(dest="cmd", required=True)
    b = batch.add_parser("create", help="create a batch from an approved manifest")
    b.add_argument("manifest")
    b.add_argument("--adapter", choices=ADAPTER_CHOICES, default="fake")
    b.set_defaults(fn=cmd_batch_create)
    b = batch.add_parser("show")
    b.add_argument("batch_id")
    b.set_defaults(fn=cmd_batch_show)

    worker = sub.add_parser("worker").add_subparsers(dest="cmd", required=True)
    w = worker.add_parser("run")
    w.add_argument("--adapter", choices=ADAPTER_CHOICES, default="fake")
    w.add_argument("--once", action="store_true", help="drain the queue and exit")
    w.add_argument("--poll", type=float, default=1.0)
    w.set_defaults(fn=cmd_worker_run)

    job = sub.add_parser("job").add_subparsers(dest="cmd", required=True)
    for name, fn in (("show", cmd_job_show), ("cancel", cmd_job_cancel), ("rerun", cmd_job_rerun)):
        j = job.add_parser(name)
        j.add_argument("job_id")
        j.set_defaults(fn=fn)

    result = sub.add_parser("result").add_subparsers(dest="cmd", required=True)
    r = result.add_parser("show")
    r.add_argument("result_id")
    r.add_argument("--version", type=int)
    r.add_argument("--with-evidence", action="store_true")
    r.set_defaults(fn=cmd_result_show)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s %(message)s")
    try:
        args.fn(args)
    except HWError as e:
        _print({"error": e.info().model_dump()})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
