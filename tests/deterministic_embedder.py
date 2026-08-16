import re


class Encoded(list):
    def tolist(self):
        return list(self)


class DeterministicEmbedder:
    """Small lexical embedder for deterministic offline vector-store tests."""

    dimensions = 128

    def encode(self, values):
        if isinstance(values, str):
            return Encoded(self._vector(values))
        return Encoded([self._vector(value) for value in values])

    def _vector(self, value):
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", (value or "").lower()):
            index = sum((position + 1) * ord(char) for position, char in enumerate(token)) % self.dimensions
            vector[index] += 1.0
        if not any(vector):
            vector[0] = 1.0
        return vector
