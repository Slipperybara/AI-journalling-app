"""Neo4j driver lifecycle and connection context manager."""
from contextlib import contextmanager

from neo4j import GraphDatabase

from .core import settings

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


@contextmanager
def graph_connect():
    """Yield a Neo4j session. Mirrors the db.connect() pattern."""
    with _get_driver().session() as session:
        yield session


def close():
    """Call on app shutdown to release driver resources."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
