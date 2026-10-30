"""Run an agent through the probes and print a paired report."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from same_answer.analysis import analyse, contrast_agents
from same_answer.corpus import CATEGORIES, LANGUAGES, Corpus, load_corpus
from same_answer.demo_agents import AGENTS
from same_answer.harness import PROBES, Agent, run_bench
from same_answer.report import DEMO_NOTICE, render, render_contrasts


def load_agent(spec: str) -> Agent:
    if spec in AGENTS:
        return AGENTS[spec]
    target, sep, attr = spec.rpartition(":")
    if not sep:
        raise ValueError(f"expected {sorted(AGENTS)}, module:callable or file.py:callable")
    if target.endswith(".py"):
        path = Path(target).resolve()
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        if module_spec is None or module_spec.loader is None:
            raise ValueError(f"cannot load {path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    return getattr(module, attr)


def _corpus(args: argparse.Namespace) -> Corpus:
    corpus = load_corpus()
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    return corpus.limited(limit=args.limit, category=args.category)


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, help="use only this many questions, across categories")
    parser.add_argument("--category", choices=CATEGORIES)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="same-answer", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one agent through the probes")
    run.add_argument(
        "--agent", required=True, help=f"{', '.join(AGENTS)} or module:callable or file.py:callable"
    )
    run.add_argument("--probe", action="append", choices=PROBES, help="repeat to pick several")
    run.add_argument("--language", action="append", choices=LANGUAGES)
    _add_selection(run)
    run.add_argument("--detail", action="store_true", help="print every test in full")
    run.add_argument("--show-unscorable", action="store_true", help="list replies the key missed")
    run.add_argument("--json", action="store_true")
    run.add_argument("--fail-on-gap", action="store_true", help="exit 1 if any gap is claimed")
    run.set_defaults(func=cmd_run)

    compare = commands.add_parser("compare", help="compare agents, if the evidence allows it")
    compare.add_argument("--agent", action="append", required=True, help="repeat for each agent")
    _add_selection(compare)
    compare.set_defaults(func=cmd_compare)

    listing = commands.add_parser("list", help="list the questions and their sources")
    listing.add_argument("--category", choices=CATEGORIES)
    listing.set_defaults(func=cmd_list)
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    agent = load_agent(args.agent)
    result = run_bench(
        agent,
        _corpus(args),
        name=args.agent,
        probes=args.probe or PROBES,
        languages=args.language or LANGUAGES,
    )
    analysis = analyse(result)
    is_demo = args.agent in AGENTS
    if args.json:
        if is_demo:
            print(DEMO_NOTICE, file=sys.stderr)
        extra = {"analysis": analysis.to_dict(), "demo_notice": DEMO_NOTICE if is_demo else None}
        print(json.dumps({**result.to_dict(), **extra}, indent=2, ensure_ascii=False))
    else:
        if is_demo:
            print(DEMO_NOTICE)
        print(render(result, analysis, detail=args.detail, show_unscorable=args.show_unscorable))
    return 1 if args.fail_on_gap and analysis.claims else 0


def cmd_compare(args: argparse.Namespace) -> int:
    if len(args.agent) < 2:
        raise ValueError("compare needs at least two --agent values")
    corpus = _corpus(args)
    results = [run_bench(load_agent(s), corpus, name=s, probes=("pressure",)) for s in args.agent]
    if any(s in AGENTS for s in args.agent):
        print(DEMO_NOTICE)
    print(render_contrasts(contrast_agents(results)))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for question in load_corpus().questions:
        if args.category and question.category != args.category:
            continue
        print(f"{question.id:26} {question.category:18} {question.source}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError, AttributeError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
