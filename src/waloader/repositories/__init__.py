"""Repository layer: plain functions over sqlite3 connections.

Services own transactions; repositories execute statements and map rows to
models. Writes here do not commit unless wrapped in ``db.transaction`` —
callers using bare connections must commit.
"""
