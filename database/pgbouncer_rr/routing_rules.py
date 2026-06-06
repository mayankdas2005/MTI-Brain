import os

# Matches REDSHIFT_NUM_BACKENDS set in the container environment.
# pgbouncer is single-threaded (event-loop), so a plain mutable list is safe.
_num_backends = int(os.environ.get("REDSHIFT_NUM_BACKENDS", "1"))
_counter = [0]


# ROUTING FN - CALLED FROM PGBOUNCER-RR - DO NOT CHANGE NAME
# pycall.c passes (username, query, in_transaction) — 3 args
def routing_rules(username, query, in_transaction):
    """Round-robin across redshift_0 ... redshift_{N-1} database aliases."""
    idx = _counter[0] % _num_backends
    _counter[0] += 1
    return "redshift_%d" % idx
