import sqlite3
from contextlib import contextmanager


@contextmanager
def database():
    connection = sqlite3.connect('jobs.sqlite',timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY,value INTEGER,status TEXT,result INTEGER);
                CREATE TABLE IF NOT EXISTS heartbeat (id INTEGER PRIMARY KEY,updated REAL);
            ''')
            yield connection
    finally:
        connection.close()
