from prometheus_client import Counter, Gauge

REQ_BY_METHOD = Counter(
    "requests_total", "number of MJ bot request by method", ["bot", "method"]
)
REQ_ERROR = Counter(
    "requests_error", "number of request errors", ["bot", "method", "error"]
)
SUCCEED = Counter("requests_succeed", "number of successful request", ["bot"])

QUEUE_LEN = Gauge("queue_len", "Queue len by bot", ["bot", "is_priority"])

IDLE = Counter("idle_cycles", "Roughly how many seconds bot was idle", ["bot"])

INC_QUEUE_LEN = Gauge("inc_queue_len", "Length of incoming queue", ["queue"])

BOT_STATE = Gauge("bot_state", "Bot mode fast/relaxed", ["bot"])

FAST_TIME_REMAINING = Gauge(
    "fast_time_remaining", "fast time remaining seconds", ["bot"]
)

SUBSCRIPTION_REMAINING = Gauge(
    "subscription_remaining", "subscription remaining seconds", ["bot"]
)

SERVICE_ERRORS = Counter(
    "service_error", "3d-party service errors", ["service", "account", "error"]
)

SERVICE_USAGE = Counter(
    "service_usage", "3d-party service requests", ["service", "account", "measurement"]
)
