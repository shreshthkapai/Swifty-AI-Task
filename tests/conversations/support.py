from pathlib import Path

from evals.schema import Corpus, load_corpus


CORPUS_PATH = Path(__file__).parents[2] / "evals" / "corpus.json"


def corpus() -> Corpus:
    return load_corpus(CORPUS_PATH)
