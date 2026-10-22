"""Doublures de test partagées (uniquement celles qui sont strictement identiques)."""


class FakeResponse:
    """Réponse urlopen factice : enveloppe Ollama `{"message": {"content": "{}"}}`."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b'{"message": {"content": "{}"}}'


class FakeSegment:
    """Segment faster-whisper factice : seul `text` est lu."""

    def __init__(self, text: str) -> None:
        self.text = text
