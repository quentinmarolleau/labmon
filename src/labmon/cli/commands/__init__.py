"""One module per `labmon` subcommand.

Each exposes NAME, HELP, DESCRIPTION, `add_arguments(parser)` and
`run(args) -> int`, so `labmon.cli.main` can register them without
knowing what any of them does.
"""
