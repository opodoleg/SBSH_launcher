import os
import psycopg2

os.environ["PGCLIENTENCODING"] = "UTF8"

conn = psycopg2.connect(
    dbname="myapp",
    user="postgres",
    password="123456",
    host="127.0.0.1",
    port=1488
)

conn.autocommit = True
cur = conn.cursor()