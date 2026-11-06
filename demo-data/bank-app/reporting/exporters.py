"""Exporteurs de rapports."""


def to_csv(data):
    return ",".join(str(item) for item in data)


def to_text(data):
    return " ".join(str(item) for item in data)
