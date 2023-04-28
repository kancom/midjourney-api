from prometheus_client import Counter, Gauge

REQ_BY_METHOD = Counter(
    "requests_total", "number of MJ bot request by method", ["bot", "method"]
)
REQ_BY_METHOD_ERROR = Counter(
    "requests_error", "number of request errors", ["bot", "method", "error"]
)
SUCCEED = Counter("requests_succeed", "number of successful request", ["bot"])

QUEUE_LEN = Gauge("queue_len", "Queue len by bot", ["bot", "is_priority"])

IDLE = Counter("idle_cycles", "Roughly how many secods bot was idle", ["bot"])

INC_QUEUE_LEN = Gauge("inc_queue_len", "Length of incoming queue", ["queue"])

BOT_STATE = Gauge("bot_state", "if bot is alive", ["bot"])
