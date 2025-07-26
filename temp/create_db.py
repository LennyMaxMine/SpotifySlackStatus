import sqlite3

def init_db():
    conn = sqlite3.connect("tokens.db")
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            slack_user_id TEXT PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()
