class Verifier:
    def normalize(self, text: str) -> str:
        return text.strip().lower()

    def verify(self, expected: str, actual: str) -> bool:
        return self.normalize(expected) == self.normalize(actual)


def call_verify() -> bool:
    verifier = Verifier()
    return verifier.verify("CodeOracle", " codeoracle ")
