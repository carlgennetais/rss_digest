import httpx


class Recorder:
    """httpx.Client whose requests are answered by `handler` and recorded."""

    def __init__(self, handler):
        self.requests = []

        def wrapped(request):
            self.requests.append(request)
            return handler(request)

        self.client = httpx.Client(transport=httpx.MockTransport(wrapped))
