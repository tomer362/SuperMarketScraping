# Common configuration for supermarket scrapers

CHUNK_SIZE = 15  # Number of concurrent page/category requests per chunk
RETRY_LIMIT = 3  # Number of retries per failed request
RETRY_DELAY = 2  # Seconds to wait before retrying a failed request
