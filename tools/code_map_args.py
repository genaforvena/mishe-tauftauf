"""Decode the optional code-map experiment's command line only."""
import argparse


def parse(argv):
    parser = argparse.ArgumentParser(description="Explore syntactic names, not safe deletion")
    parser.add_argument("--root", action="append", default=[], metavar="FILE:LINE",
                        help="exact printed definition label to assume as a root")
    parser.add_argument("paths", nargs="+", metavar="FILE_OR_DIRECTORY")
    args = parser.parse_args(argv)
    return args.root, args.paths
